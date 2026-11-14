"""Business logic of the model and dataset library and OCR model evaluation: permissions,
validation, audit logging."""

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
from jacara.core.i18n import join_items, render, stored, tr
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
    RESTART_HINT,
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
"""Upload folders left incomplete for more than a day are removed when the next upload starts."""

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
    """CLI import (`jacara models import`, offline weight import): copy local files into the library
    and, after validation, create a new version."""
    if kind not in registry.KINDS:
        raise HubError(
            "invalid",
            tr("未知种类：{kind}，可选值：{kinds}", kind=kind, kinds=join_items(registry.KINDS)),
        )
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

    # ---- Model library ----

    def _who(self) -> str:
        """Importer recorded on the model card: the user name; "personal" in personal mode, which
        has no users."""
        from jacara.modules.auth.models import User

        user = self.session.get(User, self.owner.user_id)
        return user.username if user is not None else tr("个人模式")

    def _in_use(self) -> set[str]:
        """Model files currently in effect (relative paths), resolved from .env, selected.json and
        auto-detection as on the model selection page."""
        s = self.settings
        found = [s.find_model(), s.find_mmproj(), s.table_model(), *s.ocr_models().values()]
        used: set[str] = set()
        for path in found:
            if path is None:
                continue
            try:
                used.add(path.resolve().relative_to(config.data_home().resolve()).as_posix())
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
                kind_label=tr(registry.KINDS[versions[-1].kind]),
                versions=[
                    version_out(v, registry.relative(v.weights) in used)
                    for v in sorted(versions, key=lambda v: -v.version)
                ],
            )
            for name, versions in grouped.items()
        ]
        return HubModels(
            models=models,
            kinds={key: tr(name) for key, name in registry.KINDS.items()},
            env_locked=self._env_locked(),
            restart_hint=tr(RESTART_HINT),
        )

    def _upload_dir(self, upload_id: str) -> Path:
        if not UPLOAD_ID.fullmatch(upload_id):
            raise HubError("not_found", tr("上传不存在"))
        path = registry.hub_root() / registry.INCOMING / upload_id
        if not path.is_dir():
            raise HubError("not_found", tr("上传不存在（可能已完成、取消或过期）"))
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
        """Receive one file: the request body is the file itself (no multipart). It is written to
        this upload's folder while computing SHA-256, so multi-gigabyte models are never staged
        in the system temporary directory first."""
        require(self.owner, "system:manage")
        work = self._upload_dir(upload_id)
        suffix = Path(filename).suffix.lower()
        if suffix not in (".gguf", ".onnx", ".txt", ".zip"):
            raise HubError("invalid", tr("仅支持 .gguf、.onnx、识别字典 .txt，或模型库导出的 .zip"))
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
            raise HubError("invalid", tr("文件为空"))
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
                raise HubError("invalid", tr("尚未上传 {archive}", archive=data.archive))
            weights, dictionary, card, body = registry.unpack(archive, work)
            archive.unlink()
            kind = str(card.pop("kind", ""))
            meta = {**card, **{k: v for k, v in meta.items() if v not in (None, "", [])}}
            meta["imported_by"] = self._who()
            description = description or body
        else:
            if data.kind is None or data.weights is None:
                raise HubError("invalid", tr("请选择种类和权重文件"))
            kind, weights, dictionary = data.kind, data.weights, data.dict_file
            for name in (weights, dictionary):
                if name is not None and not (work / name).is_file():
                    raise HubError("invalid", tr("尚未上传 {name}", name=name))
            suffix = registry.FORMATS[kind]
            if not weights.lower().endswith(suffix):
                raise HubError(
                    "invalid",
                    tr("{kind}需要 {suffix} 文件", kind=tr(registry.KINDS[kind]), suffix=suffix),
                )
            if dictionary is not None and not dictionary.lower().endswith(".txt"):
                raise HubError("invalid", tr("字典需为 .txt 文件"))
        extra = {p.name for p in work.iterdir()} - {weights, dictionary}
        for name in extra:  # Files uploaded but not used.
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
        """Use this version: write it to models/selected.json; recognition models switch their
        dictionary too."""
        from jacara.modules.model_admin.service import ModelAdminService, ModelError, ModelsIn

        require(self.owner, "system:manage")
        version = registry.get_version(name, number)
        if version.kind in self._env_locked() or (
            version.kind == "ocr_rec" and self.settings.jacara_ocr_dict is not None
        ):
            raise HubError(
                "invalid",
                tr("{kind}由 .env 指定，无法在网页中更换", kind=tr(registry.KINDS[version.kind])),
            )
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
            raise HubError("invalid", tr("此版本正在使用，请先切换到其他版本再删除"))
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
        """Package one version for download. Returns (download file name, temporary zip path); the
        caller deletes it after sending."""
        require(self.owner, "system:manage")
        version = registry.get_version(name, number)
        tmp = Path(tempfile.mkdtemp(prefix="jacara-model-")) / f"{name}-v{number}.zip"
        registry.pack(version, tmp)
        record(self.session, self.owner, "hub:model_export", "model", f"{name}@v{number}")
        return tmp.name, tmp

    # ---- Dataset library ----

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
            problems={render(k) or k: int(v) for k, v in (d.problems or {}).items()},
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
            raise HubError("not_found", tr("数据集不存在"))
        return found

    def import_dataset(self, name: str, kind: str, description: str, data: bytes) -> DatasetOut:
        require(self.owner, "value:review")
        if len(data) > dataset_files.MAX_BYTES:
            raise HubError("invalid", tr("数据集 zip 最大 1 GB"))
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
        """Validate the zip, store it in object storage, then write the database (storage before
        database). The caller checks permissions."""
        name = " ".join(name.split())
        if not name or len(name) > 64 or "/" in name:
            raise HubError("invalid", tr("名称需为 1～64 个字符，且不能包含 /"))
        if self.storage is None:
            raise HubError("storage", tr("对象存储暂时不可用，请稍后再试"))
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
            raise HubError("storage", tr("对象存储暂时不可用，请稍后再试")) from err
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
            raise HubError("storage", tr("对象存储暂时不可用，请稍后再试"))
        try:
            data = self.storage.get(dataset.storage_key)
        except StorageError as err:
            raise HubError("storage", tr("无法从对象存储读取此数据集")) from err
        record(self.session, self.owner, "hub:dataset_download", "dataset", dataset.id)
        return f"{dataset.name}-v{dataset.version}.zip", data

    def delete_dataset(self, dataset_id: uuid.UUID) -> None:
        """Delete a dataset together with its evaluations (database first and committed, then object
        storage, as with documents)."""
        require(self.owner, "value:review")
        dataset = self._get_dataset(dataset_id)
        runs = self.session.scalars(
            select(OcrEvalRun).where(OcrEvalRun.dataset_id == dataset.id)
        ).all()
        if any(r.status in ("queued", "running") for r in runs):
            raise HubError("invalid", tr("此数据集有正在运行的评测，请在评测结束后删除"))
        keys = [dataset.storage_key, *(r.lines_key for r in runs if r.lines_key)]
        for run in runs:
            self.session.delete(run)
        # The runs reference the dataset and there is no relationship() between them, so the unit
        # of work does not order these deletes; remove the runs first, as the foreign key requires.
        self.session.flush()
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
                # Already removed from the database; leftover files are harmless.
                with contextlib.suppress(StorageError):
                    self.storage.delete(key)

    # ---- Evaluation ----

    def create_eval(self, data: EvalIn) -> EvalOut:
        require(self.owner, "value:review")
        dataset = self._get_dataset(data.dataset_id)
        if not (dataset.counts or {}).get(data.split):
            raise HubError("invalid", tr("此数据集没有 {split} 划分", split=data.split))
        task = "rec" if dataset.kind == "ocr_rec" else "det"
        arms = [
            resolve_arm(self.settings, "A", data.a, task),
            resolve_arm(self.settings, "B", data.b, task),
        ]
        if {k: arms[0][k] for k in ("det", "rec", "dict")} == {
            k: arms[1][k] for k in ("det", "rec", "dict")
        }:
            raise HubError("invalid", tr("两侧为同一模型，无需对比"))
        if self.queue is None:
            raise HubError("queue", tr("任务队列暂时不可用，请稍后再试"))
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
        # Commit before enqueueing: the worker queries over its own connection.
        self.session.commit()
        try:
            self.queue.enqueue_eval(run.id)
        except QueueError as err:
            run.status = "failed"
            run.error = stored("无法连接任务队列（Valkey），评测未开始")
            self.session.commit()
            raise HubError(
                "queue", tr("无法连接任务队列（Valkey），请确认 jacara start 正在运行")
            ) from err
        return self._eval_out(run, dataset)

    def _eval_out(self, run: OcrEvalRun, dataset: Dataset | None = None) -> EvalOut:
        dataset = dataset or self.session.get(Dataset, run.dataset_id)
        return EvalOut(
            id=run.id,
            dataset_id=run.dataset_id,
            dataset=f"{dataset.name} v{dataset.version}" if dataset else tr("（已删除）"),
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
            raise HubError("not_found", tr("评测不存在"))
        return run

    def eval_run(self, run_id: uuid.UUID) -> EvalOut:
        require(self.owner, "document:export")
        return self._eval_out(self._get_run(run_id))

    def eval_lines(self, run_id: uuid.UUID) -> tuple[str, bytes]:
        require(self.owner, "document:export")
        run = self._get_run(run_id)
        if not run.lines_key or self.storage is None:
            raise HubError("not_found", tr("此次评测尚无逐条结果"))
        try:
            data = self.storage.get(run.lines_key)
        except StorageError as err:
            raise HubError("storage", tr("无法从对象存储读取逐条结果")) from err
        return tr("OCR评测-{run}.jsonl", run=run.id.hex[:8]), data


def resolve_arm(settings: Settings, name: str, ref: ModelRef, task: str) -> dict[str, Any]:
    """Resolve "in use / bundled / library version" to concrete files and record their SHA-256."""
    slot = "ocr_rec" if task == "rec" else "ocr_det"
    det = rec = dictionary = None
    if ref.source == "current":
        own = settings.ocr_models()
        det = own.get("det")
        rec, dictionary = own.get("rec"), own.get("dict")
        label = tr("当前使用")
    elif ref.source == "builtin":
        label = tr("RapidOCR 内置模型")
    else:
        if ref.name is None or ref.version is None:
            raise HubError("invalid", tr("请选择模型库中的模型和版本"))
        version = registry.get_version(ref.name, ref.version)
        if version.kind != slot:
            raise HubError(
                "invalid",
                tr(
                    "{name} 是{kind}，此数据集需对比{wanted}",
                    name=ref.name,
                    kind=tr(registry.KINDS[version.kind]),
                    wanted=tr(registry.KINDS[slot]),
                ),
            )
        if slot == "ocr_rec":
            rec, dictionary = version.weights, version.dictionary
        else:
            det = version.weights
        label = f"{ref.name} v{ref.version}"
    paths = {"det": det, "rec": rec, "dict": dictionary}
    if task == "rec":
        paths["det"] = None  # Recognition evaluation does not use a detection model.
    else:
        paths["rec"] = paths["dict"] = None
    arm: dict[str, Any] = {"name": name, "label": label, "sha256": {}}
    for key, path in paths.items():
        arm[key] = _shown(path) if path is not None else None
        if path is not None:
            arm["sha256"][key] = _sha256(path)
    return arm


def _shown(path: Path) -> str:
    """Path relative to the project root; absolute for .env paths outside the project."""
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
