"""Model library: weight files and model cards in models/hub/<model name>/v<version>/.

Concepts follow the MLflow Model Registry (registered model → versions; importing the same name
again increments the version) and Hugging Face model cards (README.md with YAML metadata at the
top); only the concepts are used, not the software. "In use" is models/selected.json and is not
stored separately. This module handles files only (validation, renaming, reading and writing
model cards); permissions and audit logging are in service.py."""

from __future__ import annotations

import hashlib
import re
import shutil
import struct
import uuid
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

import yaml

from jacara.core import config
from jacara.core.i18n import N_, join_items, render, tr

KINDS: dict[str, str] = {
    "model": N_("大模型"),
    "mmproj": N_("视觉模型"),
    "ocr_det": N_("OCR 文字检测"),
    "ocr_rec": N_("OCR 文字识别"),
    "table": N_("表格结构模型"),
}
"""Same names as the model selection slots. The recognition dictionary belongs to the recognition
model and is not a kind of its own."""

FORMATS: dict[str, str] = {
    "model": ".gguf",
    "mmproj": ".gguf",
    "ocr_det": ".onnx",
    "ocr_rec": ".onnx",
    "table": ".onnx",
}

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
"""Model name: letters, digits, period, hyphen and underscore, starting with a letter or digit (safe
as a folder name on every system)."""

FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ ()+-]{0,127}$")

SPLIT = re.compile(r"^(?P<stem>.+)-(?P<part>\d{5})-of-(?P<count>\d{5})\.gguf$", re.IGNORECASE)
"""A GGUF model split into several files: llama.cpp's gguf-split names them
<stem>-00001-of-00003.gguf, … (llama_split_path, "%s-%05d-of-%05d.gguf"), and llama-server is given
the first one and loads the others from the same folder."""

CARD = "README.md"
INCOMING = ".incoming"
CHUNK = 1024 * 1024


class HubError(Exception):
    """Business error. code tells the router which HTTP status to return."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def hub_root() -> Path:
    """models/hub in the data directory. Resolved on every call because tests replace the data
    directory with a temporary one."""
    return config.data_home() / "models" / "hub"


def relative(path: Path) -> str:
    """Path relative to the data directory (the form stored in selected.json)."""
    return path.relative_to(config.data_home()).as_posix()


# ---- Model card ----


@dataclass
class CardFile:
    name: str
    sha256: str
    size: int
    role: str
    """weights, shard (the further files of a split GGUF model) or dict (recognition
    dictionary)."""


@dataclass
class ModelVersion:
    name: str
    version: int
    kind: str
    path: Path
    files: list[CardFile]
    meta: dict[str, Any] = field(default_factory=dict)
    """Other metadata in the model card YAML header: base_model, datasets, license, imported_at,
    imported_by, checks."""
    description: str = ""

    @property
    def weights(self) -> Path:
        return self.path / next(f.name for f in self.files if f.role == "weights")

    @property
    def dictionary(self) -> Path | None:
        found = next((f.name for f in self.files if f.role == "dict"), None)
        return self.path / found if found else None


def read_card(path: Path) -> tuple[dict[str, Any], str]:
    """README.md → (YAML header, body). Metadata is empty without a YAML header."""
    text = path.read_text("utf-8")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    meta = yaml.safe_load(text[4:end]) or {}
    return (meta if isinstance(meta, dict) else {}), text[end + 5 :]


def write_card(path: Path, meta: dict[str, Any], body: str) -> None:
    head = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
    path.write_text(f"---\n{head}---\n{body}", "utf-8")


def _version(path: Path, name: str) -> ModelVersion | None:
    match = re.fullmatch(r"v(\d+)", path.name)
    card = path / CARD
    if match is None or not card.is_file():
        return None
    try:
        meta, body = read_card(card)
    except (OSError, ValueError, yaml.YAMLError):
        return None
    files = [
        CardFile(str(f["name"]), str(f["sha256"]), int(f["size"]), str(f.get("role", "weights")))
        for f in meta.pop("files", []) or []
        if isinstance(f, dict) and (path / str(f.get("name", ""))).is_file()
    ]
    kind = str(meta.pop("kind", ""))
    if kind not in KINDS or not any(f.role == "weights" for f in files):
        return None
    return ModelVersion(name, int(match[1]), kind, path, files, meta, body.strip())


def versions(name: str | None = None) -> list[ModelVersion]:
    """All versions in the library (or of one model), ordered by name and version. Broken versions
    (missing card or files) are omitted."""
    root = hub_root()
    if not root.is_dir():
        return []
    found: list[ModelVersion] = []
    for model in sorted(root.iterdir()):
        if not model.is_dir() or model.name.startswith(".") or (name and model.name != name):
            continue
        for path in model.iterdir():
            if path.is_dir() and (v := _version(path, model.name)) is not None:
                found.append(v)
    return sorted(found, key=lambda v: (v.name.lower(), v.version))


def get_version(name: str, version: int) -> ModelVersion:
    if not NAME.fullmatch(name):
        raise HubError("not_found", tr("模型不存在"))
    found = _version(hub_root() / name / f"v{version}", name)
    if found is None:
        raise HubError("not_found", tr("{name} 不存在第 {version} 版", name=name, version=version))
    return found


# ---- Writing and validation ----


def receive(chunks: Iterable[bytes], target: Path) -> tuple[str, int]:
    """Compute SHA-256 while writing; return (sha256, size)."""
    digest = hashlib.sha256()
    size = 0
    with target.open("wb") as out:
        for chunk in chunks:
            digest.update(chunk)
            size += len(chunk)
            out.write(chunk)
    return digest.hexdigest(), size


def read_chunks(stream: IO[bytes]) -> Iterator[bytes]:
    while chunk := stream.read(CHUNK):
        yield chunk


def incoming() -> Path:
    """Temporary folder for one import (models/hub/.incoming/<random>): on the same file system as
    the final location, so the rename is atomic."""
    path = hub_root() / INCOMING / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


def check_space(size: int) -> None:
    """Free disk space must be at least 1.1 times the file size."""
    root = hub_root()
    root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(root).free
    if size and free < size * 1.1:
        raise HubError(
            "invalid",
            tr(
                "磁盘空间不足：需要 {needed:.1f} GB，仅剩 {free:.1f} GB",
                needed=size * 1.1 / 1e9,
                free=free / 1e9,
            ),
        )


def check_gguf(path: Path) -> dict[str, Any]:
    """GGUF specification (ggml docs/gguf.md): the magic bytes GGUF followed by a 4-byte version
    (1–3)."""
    with path.open("rb") as f:
        head = f.read(8)
    if len(head) < 8 or head[:4] != b"GGUF":
        raise HubError("invalid", tr("不是 GGUF 文件（文件头不是 GGUF）"))
    version = struct.unpack("<I", head[4:])[0]
    if version not in (1, 2, 3):
        version = struct.unpack(">I", head[4:])[0]  # Big-endian is allowed from v3.
    if version not in (1, 2, 3):
        raise HubError("invalid", tr("无法识别 GGUF 版本号，文件可能已损坏"))
    return {"format": "gguf", "gguf_version": version}


def _shape(dims: Any) -> list[int | str | None]:
    return [d if isinstance(d, int) or d is None else str(d) for d in dims]


def read_dict(path: Path) -> list[str]:
    """Recognition dictionary: one character per line, read the same way as RapidOCR (only the
    trailing newline is stripped)."""
    return [line.rstrip("\r\n") for line in path.read_bytes().decode("utf-8").splitlines()]


def check_onnx(path: Path, kind: str, dictionary: Path | None) -> dict[str, Any]:
    """ONNX: loadable by onnxruntime; the input is a 4-D image; recognition models output 3-D and
    detection models 4-D; for recognition models the class count equals dictionary lines + 2
    (RapidOCR CTCLabelDecode prepends blank and appends space)."""
    import onnxruntime as ort

    try:
        session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    except Exception as err:  # onnxruntime raises many error types.
        raise HubError(
            "invalid", tr("onnxruntime 无法打开此文件：{detail}", detail=str(err)[:200])
        ) from err
    inputs = [_shape(i.shape) for i in session.get_inputs()]
    outputs = [_shape(o.shape) for o in session.get_outputs()]
    info: dict[str, Any] = {"format": "onnx", "inputs": inputs, "outputs": outputs}
    if not inputs or len(inputs[0]) != 4:
        raise HubError(
            "invalid", tr("输入不是 4 维图像（批量、通道、高、宽），不符合 OCR 或表格模型的特征")
        )
    if kind in ("ocr_det", "ocr_rec") and outputs:
        rank = len(outputs[0])
        if kind == "ocr_rec" and rank == 4:
            raise HubError("invalid", tr("此文件的输出为 4 维，应为检测模型而非识别模型"))
        if kind == "ocr_det" and rank == 3:
            raise HubError("invalid", tr("此文件的输出为 3 维，应为识别模型而非检测模型"))
    if kind != "ocr_rec":
        return info
    meta = session.get_modelmeta().custom_metadata_map
    if "character" in meta:
        chars = meta["character"].splitlines()
        info["dict_source"] = "model"
        if dictionary is not None:
            info["dict_note"] = tr(
                "模型已内置字符表，RapidOCR 仅使用内置字符表，上传的字典不会生效"
            )
    elif dictionary is not None:
        chars = read_dict(dictionary)
        info["dict_source"] = "file"
    else:
        raise HubError(
            "invalid",
            tr(
                "此识别模型未内置字符表，需同时上传字典（.txt）。否则 RapidOC"
                "R 将尝试联网下载字典，离线环境下无法使用"
            ),
        )
    info["characters"] = len(chars)
    classes = outputs[0][-1] if outputs and outputs[0] else None
    if isinstance(classes, int):
        info["classes"] = classes
        if classes != len(chars) + 2:
            raise HubError(
                "invalid",
                tr(
                    (
                        "字典与模型不匹配：模型有 {classes} 类，字典有 {n} 个字符（应"
                        "为 {expected} 个：RapidOCR 会另加 blank 和空格两类）"
                    ),
                    classes=classes,
                    n=len(chars),
                    expected=classes - 2,
                ),
            )
    return info


def check(kind: str, weights: Path, dictionary: Path | None) -> dict[str, Any]:
    if FORMATS[kind] == ".gguf":
        return check_gguf(weights)
    return check_onnx(weights, kind, dictionary)


def split_parts(weights: str) -> list[str]:
    """The further files of a split GGUF model whose first file is weights; empty when it is not
    the first file of a split model."""
    match = SPLIT.fullmatch(weights)
    if match is None or int(match["part"]) != 1:
        return []
    stem, count = match["stem"], int(match["count"])
    suffix = weights[-5:]  # .gguf as written.
    return [f"{stem}-{n:05d}-of-{count:05d}{suffix}" for n in range(2, count + 1)]


def precheck(name: str, kind: str) -> None:
    """The checks of add_version that do not need the files: a valid name and kind, and the same
    kind as the earlier versions of this name."""
    if not NAME.fullmatch(name):
        raise HubError(
            "invalid",
            tr("模型名称只能包含字母、数字和 ._-，需以字母或数字开头，最多 64 个字符"),
        )
    if kind not in KINDS:
        raise HubError("invalid", tr("未知种类：{kind}", kind=kind))
    earlier = versions(name)
    if earlier and earlier[-1].kind != kind:
        raise HubError(
            "invalid",
            tr(
                "{name} 已是{kind}，新版本需为同一种类；其他种类请使用新名称",
                name=name,
                kind=tr(KINDS[earlier[-1].kind]),
            ),
        )


def safe_filename(filename: str, suffix: str) -> str:
    name = Path(filename.replace("\\", "/")).name.strip()
    if not FILE.fullmatch(name) or not name.lower().endswith(suffix):
        raise HubError(
            "invalid",
            tr(
                ("文件名只能包含字母、数字和 ._-()+，且需以 {suffix} 结尾：{filename}"),
                suffix=suffix,
                filename=filename,
            ),
        )
    return name


def add_version(
    name: str,
    kind: str,
    work: Path,
    weights: str,
    dictionary: str | None,
    meta: dict[str, Any],
    description: str,
) -> ModelVersion:
    """work already contains the weights (and dictionary; for a split GGUF model, every file of
    it): validate, write the model card and rename to the next version. On error, work is
    deleted."""
    try:
        precheck(name, kind)
        if dictionary is not None and kind != "ocr_rec":
            raise HubError("invalid", tr("仅识别模型需要字典"))
        if weights == dictionary:
            raise HubError("invalid", tr("字典与模型不能为同一文件"))
        parts = split_parts(weights) if FORMATS[kind] == ".gguf" else []
        missing = [p for p in parts if not (work / p).is_file()]
        if missing:
            raise HubError(
                "invalid",
                tr("分片模型缺少文件：{files}", files=render(join_items(missing))),
            )
        checks = check(kind, work / weights, work / dictionary if dictionary else None)
        files = [_card_file(work / weights, "weights")]
        files += [_card_file(work / p, "shard") for p in parts]
        if dictionary:
            files.append(_card_file(work / dictionary, "dict"))
        card = {
            "kind": kind,
            "files": [f.__dict__ for f in files],
            **{k: v for k, v in meta.items() if v not in (None, "", [])},
            "imported_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "checks": checks,
        }
        write_card(work / CARD, card, description.strip() + "\n" if description.strip() else "")
        model = hub_root() / name
        model.mkdir(parents=True, exist_ok=True)
        # Two users importing the same model name concurrently: if the rename fails, take the next
        # version.
        while True:
            number = 1 + max((v.version for v in versions(name)), default=0)
            number = max(number, 1 + max(_numbers(model), default=0))
            try:
                work.rename(model / f"v{number}")
                break
            except OSError:
                if (model / f"v{number}").exists():
                    continue
                raise
        return get_version(name, number)
    finally:
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)


def _numbers(model: Path) -> Iterator[int]:
    for path in model.iterdir():
        if match := re.fullmatch(r"v(\d+)", path.name):
            yield int(match[1])


def _card_file(path: Path, role: str) -> CardFile:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in read_chunks(f):
            digest.update(chunk)
    return CardFile(path.name, digest.hexdigest(), path.stat().st_size, role)


def remove(version: ModelVersion) -> None:
    """Delete the folder of one version; when no version of the model remains, delete the model
    folder too."""
    shutil.rmtree(version.path)
    model = version.path.parent
    if model.is_dir() and not any(model.iterdir()):
        model.rmdir()


def pack(version: ModelVersion, target: Path) -> Path:
    """Pack one version into a zip for import on another machine (weights are already compressed, so
    ZIP_STORED is used)."""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        archive.write(version.path / CARD, CARD)
        for f in version.files:
            archive.write(version.path / f.name, f.name)
    return target


def unpack(archive_path: Path, work: Path) -> tuple[str, str | None, dict[str, Any], str]:
    """Import a zip exported on another machine: read the model card and extract weights and
    dictionary. Returns (weights file name, dictionary file name, metadata, description)."""
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        if CARD not in names:
            raise HubError("invalid", tr("zip 中缺少模型卡 README.md，不是本程序导出的模型文件"))
        card = archive.read(CARD).decode("utf-8")
        tmp = work / "card.md"
        tmp.write_text(card, "utf-8")
        meta, body = read_card(tmp)
        tmp.unlink()
        listed = [f for f in meta.get("files", []) or [] if isinstance(f, dict)]
        weights = next((str(f["name"]) for f in listed if f.get("role") == "weights"), None)
        dictionary = next((str(f["name"]) for f in listed if f.get("role") == "dict"), None)
        if weights is None:
            raise HubError("invalid", tr("模型卡中未指定权重文件"))
        for f in listed:
            filename = str(f["name"])
            if filename not in names or Path(filename).name != filename or filename == CARD:
                raise HubError(
                    "invalid", tr("zip 中缺少文件或文件名错误：{filename}", filename=filename)
                )
            with archive.open(filename) as src:
                sha, _ = receive(read_chunks(src), work / filename)
            if sha != f.get("sha256"):
                raise HubError(
                    "invalid",
                    tr("{filename} 的 SHA-256 与模型卡不一致，文件可能已损坏", filename=filename),
                )
    keep = {k: meta[k] for k in ("base_model", "datasets", "license", "imported_by") if k in meta}
    keep["kind"] = meta.get("kind")
    return weights, dictionary, keep, body.strip()
