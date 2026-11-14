# ruff: noqa: E501
"""模型和数据集库（ADR 0059）、OCR 模型评测（ADR 0060）的业务逻辑：权限、检查、审计日志。"""

from __future__ import annotations

import contextlib
import hashlib
import re
import shutil
import tempfile
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jacara.core import config
from jacara.core.config import Settings
from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.core.queue import QueueError, TaskQueue
from jacara.core.storage import ObjectStorage, StorageError
from jacara.modules.audit.service import record
from jacara.modules.hub import datasets as dataset_files
from jacara.modules.hub import registry
from jacara.modules.hub.models import Dataset, OcrEvalRun
from jacara.modules.hub.registry import HubError, ModelVersion
from jacara.modules.hub.schemas import (
    CardIn,
    DatasetOut,
    EvalIn,
    EvalOut,
    FileOut,
    FinishIn,
    HubModels,
    ModelOut,
    ModelRef,
    UploadedFile,
    UploadOut,
    VersionOut,
)

UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")
STALE_SECONDS = 24 * 3600
"""传了一半、一天都没完成的上传文件夹，下次有人开始上传时清掉。"""

SLOT_ENV = {
    "model": "jacara_model",
    "mmproj": "jacara_mmproj",
    "ocr_det": "jacara_ocr_det",
    "ocr_rec": "jacara_ocr_rec",
    "table": "jacara_table_model",
}


def version_out(v: ModelVersion, in_use: bool = False) -> VersionOut:
    datasets = v.meta.get("datasets") or []
    return VersionOut(
        version=v.version,
        kind=v.kind,  # type: ignore[arg-type]
        files=[FileOut(**f.__dict__) for f in v.files],
        path=registry.relative(v.weights),
        base_model=_text(v.meta.get("base_model")),
        datasets=[str(d) for d in datasets] if isinstance(datasets, list) else [str(datasets)],
        license=_text(v.meta.get("license")),
        imported_at=_text(v.meta.get("imported_at")),
        imported_by=_text(v.meta.get("imported_by")),
        description=v.description,
        checks=v.meta.get("checks") or {},
        in_use=in_use,
    )


def _text(value: object) -> str | None:
    return None if value in (None, "") else str(value)


def import_files(
    name: str,
    kind: str,
    weights: Path,
    dictionary: Path | None,
    meta: dict[str, Any],
    description: str = "",
) -> ModelVersion:
    """命令行导入（jacara models import，权重离线导入）：把本机的文件拷进模型库，检查后成为新版本。"""
    if kind not in registry.KINDS:
        raise HubError("invalid", f"不认识的种类：{kind}，可选：{'、'.join(registry.KINDS)}")
    weights_name = registry.safe_filename(weights.name, registry.FORMATS[kind])
    dict_name = registry.safe_filename(dictionary.name, ".txt") if dictionary else None
    registry.check_space(weights.stat().st_size + (dictionary.stat().st_size if dictionary else 0))
    work = registry.incoming()
    shutil.copyfile(weights, work / weights_name)
    if dictionary is not None and dict_name is not None:
        shutil.copyfile(dictionary, work / dict_name)
    return registry.add_version(name, kind, work, weights_name, dict_name, meta, description)


class HubService:
    def __init__(
        self,
        session: Session,
        owner: Owner,
        settings: Settings,
        storage: ObjectStorage | None = None,
        queue: TaskQueue | None = None,
    ) -> None:
        self.session = session
        self.owner = owner
        self.settings = settings
        self.storage = storage
        self.queue = queue

    # ---- 模型库 ----

    def _who(self) -> str:
        """模型卡上写的导入人：用户名；个人模式没有用户，写"个人模式"。"""
        from jacara.modules.auth.models import User

        user = self.session.get(User, self.owner.user_id)
        return user.username if user is not None else "个人模式"

    def _in_use(self) -> set[str]:
        """现在生效的模型文件（相对路径）：.env、selected.json、自动找到的，和换模型页一样算。"""
        s = self.settings
        found = [s.find_model(), s.find_mmproj(), s.table_model(), *s.ocr_models().values()]
        used: set[str] = set()
        for path in found:
            if path is None:
                continue
            try:
                used.add(path.resolve().relative_to(config.PROJECT_ROOT.resolve()).as_posix())
            except ValueError:
                continue
        return used

    def _env_locked(self) -> list[str]:
        return [slot for slot, attr in SLOT_ENV.items() if getattr(self.settings, attr) is not None]

    def models(self) -> HubModels:
        require(self.owner, "document:read")
        used = self._in_use()
        grouped: dict[str, list[ModelVersion]] = {}
        for v in registry.versions():
            grouped.setdefault(v.name, []).append(v)
        models = [
            ModelOut(
                name=name,
                kind=versions[-1].kind,  # type: ignore[arg-type]
                kind_label=registry.KINDS[versions[-1].kind],
                versions=[
                    version_out(v, registry.relative(v.weights) in used)
                    for v in sorted(versions, key=lambda v: -v.version)
                ],
            )
            for name, versions in grouped.items()
        ]
        return HubModels(models=models, kinds=registry.KINDS, env_locked=self._env_locked())

    def _upload_dir(self, upload_id: str) -> Path:
        if not UPLOAD_ID.fullmatch(upload_id):
            raise HubError("not_found", "没有这次上传")
        path = registry.hub_root() / registry.INCOMING / upload_id
        if not path.is_dir():
            raise HubError("not_found", "没有这次上传（可能已经完成、取消或过期了）")
        return path

    def start_upload(self) -> UploadOut:
        require(self.owner, "system:manage")
        root = registry.hub_root() / registry.INCOMING
        if root.is_dir():
            for old in root.iterdir():
                if old.is_dir() and time.time() - old.stat().st_mtime > STALE_SECONDS:
                    shutil.rmtree(old, ignore_errors=True)
        work = registry.incoming()
        return UploadOut(upload_id=work.name)

    async def receive_file(
        self, upload_id: str, filename: str, chunks: AsyncIterator[bytes], length: int | None
    ) -> UploadedFile:
        """收一个文件：请求体就是文件本身（不经过 multipart），边收边写进这次上传的文件夹、边算 SHA-256，
        几个 GB 的大模型也不会先在系统临时目录里存一份。"""
        require(self.owner, "system:manage")
        work = self._upload_dir(upload_id)
        suffix = Path(filename).suffix.lower()
        if suffix not in (".gguf", ".onnx", ".txt", ".zip"):
            raise HubError("invalid", "只收 .gguf、.onnx、识别字典 .txt，或模型库导出的 .zip")
        name = registry.safe_filename(filename, suffix)
        if length:
            registry.check_space(length)
        target = work / name
        digest = hashlib.sha256()
        size = 0
        try:
            with target.open("wb") as out:
                async for chunk in chunks:
                    digest.update(chunk)
                    size += len(chunk)
                    out.write(chunk)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        if size == 0:
            target.unlink(missing_ok=True)
            raise HubError("invalid", "文件是空的")
        return UploadedFile(name=name, size=size, sha256=digest.hexdigest())

    def cancel_upload(self, upload_id: str) -> None:
        require(self.owner, "system:manage")
        shutil.rmtree(self._upload_dir(upload_id), ignore_errors=True)

    def finish(self, upload_id: str, data: FinishIn) -> VersionOut:
        require(self.owner, "system:manage")
        work = self._upload_dir(upload_id)
        meta: dict[str, Any] = {
            "base_model": data.base_model,
            "datasets": data.datasets,
            "license": data.license,
            "imported_by": self._who(),
        }
        description = data.description
        if data.archive:
            archive = work / registry.safe_filename(data.archive, ".zip")
            if not archive.is_file():
                raise HubError("invalid", f"还没传 {data.archive}")
            weights, dictionary, card, body = registry.unpack(archive, work)
            archive.unlink()
            kind = str(card.pop("kind", ""))
            meta = {**card, **{k: v for k, v in meta.items() if v not in (None, "", [])}}
            meta["imported_by"] = self._who()
            description = description or body
        else:
            if data.kind is None or data.weights is None:
                raise HubError("invalid", "要选种类和权重文件")
            kind, weights, dictionary = data.kind, data.weights, data.dict_file
            for name in (weights, dictionary):
                if name is not None and not (work / name).is_file():
                    raise HubError("invalid", f"还没传 {name}")
            suffix = registry.FORMATS[kind]
            if not weights.lower().endswith(suffix):
                raise HubError("invalid", f"{registry.KINDS[kind]}要 {suffix} 文件")
            if dictionary is not None and not dictionary.lower().endswith(".txt"):
                raise HubError("invalid", "字典要 .txt 文件")
        extra = {p.name for p in work.iterdir()} - {weights, dictionary}
        for name in extra:  # 传了但没用上的文件
            (work / name).unlink(missing_ok=True)
        version = registry.add_version(
            data.name, kind, work, weights, dictionary, meta, description
        )
        record(
            self.session,
            self.owner,
            "hub:model_import",
            "model",
            f"{version.name}@v{version.version}",
            kind=version.kind,
            files=[{"name": f.name, "sha256": f.sha256, "size": f.size} for f in version.files],
        )
        return version_out(version)

    def update_card(self, name: str, number: int, data: CardIn) -> VersionOut:
        require(self.owner, "system:manage")
        version = registry.get_version(name, number)
        card = version.path / registry.CARD
        meta, body = registry.read_card(card)
        changed = data.model_dump(exclude_none=True)
        for key in ("base_model", "datasets", "license"):
            if key in changed:
                meta[key] = changed[key]
        if "description" in changed:
            body = changed["description"].strip() + "\n"
        registry.write_card(card, meta, body)
        record(self.session, self.owner, "hub:model_card", "model", f"{name}@v{number}")
        return version_out(registry.get_version(name, number), self._is_used(version))

    def _is_used(self, version: ModelVersion) -> bool:
        return registry.relative(version.weights) in self._in_use()

    def use(self, name: str, number: int) -> HubModels:
        """用这个版本：写进 models/selected.json（ADR 0046），识别模型连字典一起换。"""
        from jacara.modules.model_admin.service import ModelAdminService, ModelError, ModelsIn

        require(self.owner, "system:manage")
        version = registry.get_version(name, number)
        if version.kind in self._env_locked() or (
            version.kind == "ocr_rec" and self.settings.jacara_ocr_dict is not None
        ):
            raise HubError("invalid", f"{registry.KINDS[version.kind]}由 .env 指定，网页上换不了")
        choice: dict[str, str] = {version.kind: registry.relative(version.weights)}
        if version.kind == "ocr_rec":
            dictionary = version.dictionary
            choice["ocr_dict"] = registry.relative(dictionary) if dictionary else ""
        try:
            ModelAdminService(self.session, self.owner, self.settings).choose(ModelsIn(**choice))
        except ModelError as err:
            raise HubError("invalid", err.message) from err
        record(self.session, self.owner, "hub:model_use", "model", f"{name}@v{number}", **choice)
        return self.models()

    def delete_version(self, name: str, number: int) -> None:
        require(self.owner, "system:manage")
        version = registry.get_version(name, number)
        if self._is_used(version):
            raise HubError("invalid", "这个版本正在用，先换成别的再删")
        registry.remove(version)
        record(
            self.session,
            self.owner,
            "hub:model_delete",
            "model",
            f"{name}@v{number}",
            files=[{"name": f.name, "sha256": f.sha256} for f in version.files],
        )

    def pack(self, name: str, number: int) -> tuple[str, Path]:
        """打包下载一个版本。返回 (下载文件名, 临时 zip 的路径)：调用方发完以后删掉。"""
        require(self.owner, "system:manage")
        version = registry.get_version(name, number)
        tmp = Path(tempfile.mkdtemp(prefix="jacara-model-")) / f"{name}-v{number}.zip"
        registry.pack(version, tmp)
        record(self.session, self.owner, "hub:model_export", "model", f"{name}@v{number}")
        return tmp.name, tmp

    # ---- 数据集库 ----

    def dataset_out(self, d: Dataset) -> DatasetOut:
        return DatasetOut(
            id=d.id,
            name=d.name,
            version=d.version,
            kind=d.kind,  # type: ignore[arg-type]
            source=d.source,
            description=d.description,
            sha256=d.sha256,
            size=d.size,
            counts={k: int(v) for k, v in (d.counts or {}).items()},
            groups={k: int(v) for k, v in (d.groups or {}).items()},
            files=list(d.files or []),
            problems={k: int(v) for k, v in (d.problems or {}).items()},
            created_at=d.created_at,
        )

    def datasets(self) -> list[DatasetOut]:
        require(self.owner, "document:export")
        rows = self.session.scalars(
            select(Dataset)
            .where(Dataset.team_id == self.owner.team_id)
            .order_by(Dataset.name, Dataset.version.desc())
        ).all()
        return [self.dataset_out(d) for d in rows]

    def _get_dataset(self, dataset_id: uuid.UUID) -> Dataset:
        found = self.session.get(Dataset, dataset_id)
        if found is None or found.team_id != self.owner.team_id:
            raise HubError("not_found", "没有这个数据集")
        return found

    def import_dataset(self, name: str, kind: str, description: str, data: bytes) -> DatasetOut:
        require(self.owner, "value:review")
        if len(data) > dataset_files.MAX_BYTES:
            raise HubError("invalid", "数据集 zip 最大 1 GB")
        dataset = self.save_dataset(name, kind, "upload", description, data)
        return self.dataset_out(dataset)

    def save_dataset(
        self,
        name: str,
        kind: str,
        source: str,
        description: str,
        data: bytes,
        export_id: uuid.UUID | None = None,
    ) -> Dataset:
        """检查 zip、先存对象存储再写数据库（CLAUDE.md 的写入顺序）。调用方负责权限检查。"""
        name = " ".join(name.split())
        if not name or len(name) > 64 or "/" in name:
            raise HubError("invalid", "名字要 1～64 个字，不能有 /")
        if self.storage is None:
            raise HubError("storage", "对象存储暂时不可用，请稍后再试")
        parsed = dataset_files.parse(data, kind)
        team = self.owner.team_id
        version = 1 + (
            self.session.scalar(
                select(func.max(Dataset.version)).where(
                    Dataset.team_id == team, Dataset.name == name
                )
            )
            or 0
        )
        dataset_id = uuid.uuid4()
        key = f"datasets/{team}/{dataset_id}.zip"
        try:
            self.storage.put(key, data, "application/zip")
        except StorageError as err:
            raise HubError("storage", "对象存储暂时不可用，请稍后再试") from err
        dataset = Dataset(
            id=dataset_id,
            name=name,
            version=version,
            kind=kind,
            source=source,
            description=description.strip(),
            storage_key=key,
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
            counts=parsed.counts,
            groups=parsed.groups,
            files=parsed.files,
            problems=dict(parsed.problems),
            export_id=export_id,
            user_id=self.owner.user_id,
            team_id=team,
        )
        self.session.add(dataset)
        self.session.flush()
        record(
            self.session,
            self.owner,
            "hub:dataset_save" if source != "upload" else "hub:dataset_import",
            "dataset",
            dataset.id,
            name=name,
            version=version,
            kind=kind,
            source=source,
            counts=parsed.counts,
            sha256=dataset.sha256,
        )
        return dataset

    def dataset_file(self, dataset_id: uuid.UUID) -> tuple[str, bytes]:
        require(self.owner, "document:export")
        dataset = self._get_dataset(dataset_id)
        if self.storage is None:
            raise HubError("storage", "对象存储暂时不可用，请稍后再试")
        try:
            data = self.storage.get(dataset.storage_key)
        except StorageError as err:
            raise HubError("storage", "对象存储里取不到这个数据集") from err
        record(self.session, self.owner, "hub:dataset_download", "dataset", dataset.id)
        return f"{dataset.name}-v{dataset.version}.zip", data

    def delete_dataset(self, dataset_id: uuid.UUID) -> None:
        """删数据集，连同用它做的评测（先删数据库并提交，再删对象存储，和删文档一样）。"""
        require(self.owner, "value:review")
        dataset = self._get_dataset(dataset_id)
        runs = self.session.scalars(
            select(OcrEvalRun).where(OcrEvalRun.dataset_id == dataset.id)
        ).all()
        if any(r.status in ("queued", "running") for r in runs):
            raise HubError("invalid", "这个数据集有评测正在跑，等它跑完再删")
        keys = [dataset.storage_key, *(r.lines_key for r in runs if r.lines_key)]
        for run in runs:
            self.session.delete(run)
        record(
            self.session,
            self.owner,
            "hub:dataset_delete",
            "dataset",
            dataset.id,
            name=dataset.name,
            version=dataset.version,
            sha256=dataset.sha256,
        )
        self.session.delete(dataset)
        self.session.commit()
        if self.storage is not None:
            for key in keys:
                with contextlib.suppress(StorageError):  # 数据库里已经没有了；剩下的文件不影响使用
                    self.storage.delete(key)

    # ---- 评测 ----

    def create_eval(self, data: EvalIn) -> EvalOut:
        require(self.owner, "value:review")
        dataset = self._get_dataset(data.dataset_id)
        if not (dataset.counts or {}).get(data.split):
            raise HubError("invalid", f"这个数据集没有 {data.split} 这一边")
        task = "rec" if dataset.kind == "ocr_rec" else "det"
        arms = [
            resolve_arm(self.settings, "A", data.a, task),
            resolve_arm(self.settings, "B", data.b, task),
        ]
        if {k: arms[0][k] for k in ("det", "rec", "dict")} == {
            k: arms[1][k] for k in ("det", "rec", "dict")
        }:
            raise HubError("invalid", "两边是同一个模型，没什么可比的")
        if self.queue is None:
            raise HubError("queue", "任务队列暂时不可用，请稍后再试")
        run = OcrEvalRun(
            dataset_id=dataset.id,
            split=data.split,
            task=task,
            arms=arms,
            status="queued",
            user_id=self.owner.user_id,
            team_id=self.owner.team_id,
        )
        self.session.add(run)
        self.session.flush()
        record(
            self.session,
            self.owner,
            "hub:eval",
            "ocr_eval",
            run.id,
            dataset=f"{dataset.name}@v{dataset.version}",
            split=data.split,
            arms=[{"label": a["label"], "sha256": a["sha256"]} for a in arms],
        )
        self.session.commit()  # 放进队列之前先提交：worker 用自己的连接查
        try:
            self.queue.enqueue_eval(run.id)
        except QueueError as err:
            run.status = "failed"
            run.error = "任务队列（Valkey）连不上，没有开始"
            self.session.commit()
            raise HubError("queue", "任务队列（Valkey）连不上，请确认 jacara start 在运行") from err
        return self._eval_out(run, dataset)

    def _eval_out(self, run: OcrEvalRun, dataset: Dataset | None = None) -> EvalOut:
        dataset = dataset or self.session.get(Dataset, run.dataset_id)
        return EvalOut(
            id=run.id,
            dataset_id=run.dataset_id,
            dataset=f"{dataset.name} v{dataset.version}" if dataset else "（已删除）",
            split=run.split,
            task=run.task,
            arms=list(run.arms or []),
            status=run.status,
            progress=run.progress,
            summary=run.summary,
            error=run.error,
            created_at=run.created_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )

    def evals(self) -> list[EvalOut]:
        require(self.owner, "document:export")
        runs = self.session.scalars(
            select(OcrEvalRun)
            .where(OcrEvalRun.team_id == self.owner.team_id)
            .order_by(OcrEvalRun.created_at.desc())
            .limit(100)
        ).all()
        return [self._eval_out(r) for r in runs]

    def _get_run(self, run_id: uuid.UUID) -> OcrEvalRun:
        run = self.session.get(OcrEvalRun, run_id)
        if run is None or run.team_id != self.owner.team_id:
            raise HubError("not_found", "没有这次评测")
        return run

    def eval_run(self, run_id: uuid.UUID) -> EvalOut:
        require(self.owner, "document:export")
        return self._eval_out(self._get_run(run_id))

    def eval_lines(self, run_id: uuid.UUID) -> tuple[str, bytes]:
        require(self.owner, "document:export")
        run = self._get_run(run_id)
        if not run.lines_key or self.storage is None:
            raise HubError("not_found", "这次评测还没有逐条结果")
        try:
            data = self.storage.get(run.lines_key)
        except StorageError as err:
            raise HubError("storage", "对象存储里取不到逐条结果") from err
        return f"OCR评测-{run.id.hex[:8]}.jsonl", data


def resolve_arm(settings: Settings, name: str, ref: ModelRef, task: str) -> dict[str, Any]:
    """把"在用 / 自带 / 模型库某版本"解析成具体文件，记下 SHA-256。"""
    slot = "ocr_rec" if task == "rec" else "ocr_det"
    det = rec = dictionary = None
    if ref.source == "current":
        own = settings.ocr_models()
        det = own.get("det")
        rec, dictionary = own.get("rec"), own.get("dict")
        label = "现在用的"
    elif ref.source == "builtin":
        label = "RapidOCR 自带的"
    else:
        if ref.name is None or ref.version is None:
            raise HubError("invalid", "要选模型库里的模型和版本")
        version = registry.get_version(ref.name, ref.version)
        if version.kind != slot:
            raise HubError(
                "invalid",
                f"{ref.name} 是{registry.KINDS[version.kind]}，这个数据集要比{registry.KINDS[slot]}",
            )
        if slot == "ocr_rec":
            rec, dictionary = version.weights, version.dictionary
        else:
            det = version.weights
        label = f"{ref.name} v{ref.version}"
    paths = {"det": det, "rec": rec, "dict": dictionary}
    if task == "rec":
        paths["det"] = None  # 识别评测不用检测模型
    else:
        paths["rec"] = paths["dict"] = None
    arm: dict[str, Any] = {"name": name, "label": label, "sha256": {}}
    for key, path in paths.items():
        arm[key] = _shown(path) if path is not None else None
        if path is not None:
            arm["sha256"][key] = _sha256(path)
    return arm


def _shown(path: Path) -> str:
    """相对项目根目录的路径；.env 指到项目外面的，用绝对路径。"""
    try:
        return registry.relative(path)
    except ValueError:
        return str(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in registry.read_chunks(f):
            digest.update(chunk)
    return digest.hexdigest()
