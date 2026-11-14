# ruff: noqa: E501
"""模型库（ADR 0059）：models/hub/<模型名>/v<版本号>/ 里的权重文件和模型卡。

概念照 MLflow 模型注册表（注册模型 → 版本，同名再导入版本号加 1）和 Hugging Face 的模型卡
（README.md，顶上 YAML 写元数据），只取概念、不用这两个软件。"在用"就是 models/selected.json（ADR 0046），
不另存。这里只管文件：检查、改名、读写模型卡；权限、审计日志在 service.py。
"""

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

KINDS: dict[str, str] = {
    "model": "大模型",
    "mmproj": "视觉模型",
    "ocr_det": "OCR 文字检测",
    "ocr_rec": "OCR 文字识别",
    "table": "表格结构模型",
}
"""和换模型（ADR 0046）的槽位同名。识别模型的字典跟着识别模型走，不单独成一种。"""

FORMATS: dict[str, str] = {
    "model": ".gguf",
    "mmproj": ".gguf",
    "ocr_det": ".onnx",
    "ocr_rec": ".onnx",
    "table": ".onnx",
}

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
"""模型名：字母、数字、点、横线、下划线，字母或数字开头（文件夹名，各系统都安全）。"""

FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ ()+-]{0,127}$")

CARD = "README.md"
INCOMING = ".incoming"
CHUNK = 1024 * 1024


class HubError(Exception):
    """业务错误。code 让 router 知道该返回哪种 HTTP 状态。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def hub_root() -> Path:
    """models/hub。每次现取 config.PROJECT_ROOT：测试会把它换成临时文件夹。"""
    return config.PROJECT_ROOT / "models" / "hub"


def relative(path: Path) -> str:
    """相对项目根目录的路径（selected.json 里存的就是这种）。"""
    return path.relative_to(config.PROJECT_ROOT).as_posix()


# ---- 模型卡 ----


@dataclass
class CardFile:
    name: str
    sha256: str
    size: int
    role: str
    """weights（权重）或 dict（识别字典）。"""


@dataclass
class ModelVersion:
    name: str
    version: int
    kind: str
    path: Path
    files: list[CardFile]
    meta: dict[str, Any] = field(default_factory=dict)
    """模型卡 YAML 头里的其他元数据：base_model、datasets、license、imported_at、imported_by、checks。"""
    description: str = ""

    @property
    def weights(self) -> Path:
        return self.path / next(f.name for f in self.files if f.role == "weights")

    @property
    def dictionary(self) -> Path | None:
        found = next((f.name for f in self.files if f.role == "dict"), None)
        return self.path / found if found else None


def read_card(path: Path) -> tuple[dict[str, Any], str]:
    """README.md → (YAML 头, 正文)。没有 YAML 头时元数据是空的。"""
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
    """模型库里所有版本（或某个模型的），按名字、版本号排。坏掉的（没有卡、文件少了）不列。"""
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
        raise HubError("not_found", "没有这个模型")
    found = _version(hub_root() / name / f"v{version}", name)
    if found is None:
        raise HubError("not_found", f"没有 {name} 的第 {version} 版")
    return found


# ---- 写入和检查 ----


def receive(chunks: Iterable[bytes], target: Path) -> tuple[str, int]:
    """边写边算 SHA-256，返回 (sha256, 大小)。"""
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
    """一次导入用的临时文件夹（models/hub/.incoming/<随机>）：和最后的位置在同一个文件系统，改名是原子的。"""
    path = hub_root() / INCOMING / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


def check_space(size: int) -> None:
    """磁盘剩余空间要有文件大小的 1.1 倍。"""
    root = hub_root()
    root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(root).free
    if size and free < size * 1.1:
        raise HubError(
            "invalid",
            f"磁盘空间不够：要 {size * 1.1 / 1e9:.1f} GB，只剩 {free / 1e9:.1f} GB",
        )


def check_gguf(path: Path) -> dict[str, Any]:
    """GGUF 规范（ggml docs/gguf.md）：开头是 GGUF 四个字节，接着 4 字节的版本号（1～3）。"""
    with path.open("rb") as f:
        head = f.read(8)
    if len(head) < 8 or head[:4] != b"GGUF":
        raise HubError("invalid", "不是 GGUF 文件（开头不是 GGUF）")
    version = struct.unpack("<I", head[4:])[0]
    if version not in (1, 2, 3):
        version = struct.unpack(">I", head[4:])[0]  # v3 起可以是大端
    if version not in (1, 2, 3):
        raise HubError("invalid", "GGUF 版本号不认识，文件可能坏了")
    return {"format": "gguf", "gguf_version": version}


def _shape(dims: Any) -> list[int | str | None]:
    return [d if isinstance(d, int) or d is None else str(d) for d in dims]


def read_dict(path: Path) -> list[str]:
    """识别字典：一行一个字，和 RapidOCR 读字典的方式一样（只去掉行尾的换行）。"""
    return [line.rstrip("\r\n") for line in path.read_bytes().decode("utf-8").splitlines()]


def check_onnx(path: Path, kind: str, dictionary: Path | None) -> dict[str, Any]:
    """ONNX：onnxruntime 能加载；输入是 4 维图片；识别模型输出 3 维、检测模型输出 4 维；
    识别模型的类别数 = 字符表行数 + 2（RapidOCR 的 CTCLabelDecode 前面插 blank、后面加空格）。"""
    import onnxruntime as ort

    try:
        session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    except Exception as err:  # onnxruntime 的错误种类很多
        raise HubError("invalid", f"onnxruntime 打不开这个文件：{str(err)[:200]}") from err
    inputs = [_shape(i.shape) for i in session.get_inputs()]
    outputs = [_shape(o.shape) for o in session.get_outputs()]
    info: dict[str, Any] = {"format": "onnx", "inputs": inputs, "outputs": outputs}
    if not inputs or len(inputs[0]) != 4:
        raise HubError("invalid", "输入不是 4 维图片（批量、通道、高、宽），不像 OCR 或表格模型")
    if kind in ("ocr_det", "ocr_rec") and outputs:
        rank = len(outputs[0])
        if kind == "ocr_rec" and rank == 4:
            raise HubError("invalid", "这个文件的输出是 4 维，像是检测模型，不是识别模型")
        if kind == "ocr_det" and rank == 3:
            raise HubError("invalid", "这个文件的输出是 3 维，像是识别模型，不是检测模型")
    if kind != "ocr_rec":
        return info
    meta = session.get_modelmeta().custom_metadata_map
    if "character" in meta:
        chars = meta["character"].splitlines()
        info["dict_source"] = "model"
        if dictionary is not None:
            info["dict_note"] = "模型自带字符表，RapidOCR 只用自带的，传的字典不会生效"
    elif dictionary is not None:
        chars = read_dict(dictionary)
        info["dict_source"] = "file"
    else:
        raise HubError(
            "invalid",
            "这个识别模型没有自带字符表，要一起传字典（.txt）。"
            "不传的话 RapidOCR 会去网上下载字典，离线用不了",
        )
    info["characters"] = len(chars)
    classes = outputs[0][-1] if outputs and outputs[0] else None
    if isinstance(classes, int):
        info["classes"] = classes
        if classes != len(chars) + 2:
            raise HubError(
                "invalid",
                f"字典和模型对不上：模型有 {classes} 类，字典 {len(chars)} 个字"
                f"（应该是 {classes - 2} 个：RapidOCR 会再加 blank 和空格两类）",
            )
    return info


def check(kind: str, weights: Path, dictionary: Path | None) -> dict[str, Any]:
    if FORMATS[kind] == ".gguf":
        return check_gguf(weights)
    return check_onnx(weights, kind, dictionary)


def safe_filename(filename: str, suffix: str) -> str:
    name = Path(filename.replace("\\", "/")).name.strip()
    if not FILE.fullmatch(name) or not name.lower().endswith(suffix):
        raise HubError(
            "invalid", f"文件名只能用字母、数字和 ._-()+，并且以 {suffix} 结尾：{filename}"
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
    """work 里已经放好了权重（和字典）：检查、写模型卡、改名成下一个版本。出错时删掉 work。"""
    try:
        if not NAME.fullmatch(name):
            raise HubError("invalid", "模型名只能用字母、数字和 ._-，字母或数字开头，最多 64 个字")
        if kind not in KINDS:
            raise HubError("invalid", f"不认识的种类：{kind}")
        if dictionary is not None and kind != "ocr_rec":
            raise HubError("invalid", "只有识别模型要字典")
        if weights == dictionary:
            raise HubError("invalid", "字典和模型不能是同一个文件")
        earlier = versions(name)
        if earlier and earlier[-1].kind != kind:
            raise HubError(
                "invalid",
                f"{name} 已经是{KINDS[earlier[-1].kind]}了，新版本要是同一种；别的种类换个名字",
            )
        checks = check(kind, work / weights, work / dictionary if dictionary else None)
        files = [_card_file(work / weights, "weights")]
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
        while True:  # 两个人同时导入同名模型：改名失败的再往后排一个
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
    """删掉一个版本的文件夹；这个模型一个版本都不剩时连模型文件夹一起删。"""
    shutil.rmtree(version.path)
    model = version.path.parent
    if model.is_dir() and not any(model.iterdir()):
        model.rmdir()


def pack(version: ModelVersion, target: Path) -> Path:
    """一个版本打成 zip（权重本来就是压缩过的，不再压，ZIP_STORED），拿到别的电脑上导入。"""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        archive.write(version.path / CARD, CARD)
        for f in version.files:
            archive.write(version.path / f.name, f.name)
    return target


def unpack(archive_path: Path, work: Path) -> tuple[str, str | None, dict[str, Any], str]:
    """导入别的电脑导出的 zip：读模型卡，取出权重和字典。返回 (权重文件名, 字典文件名, 元数据, 说明)。"""
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        if CARD not in names:
            raise HubError("invalid", "zip 里没有模型卡 README.md，不是模型库导出的")
        card = archive.read(CARD).decode("utf-8")
        tmp = work / "card.md"
        tmp.write_text(card, "utf-8")
        meta, body = read_card(tmp)
        tmp.unlink()
        listed = [f for f in meta.get("files", []) or [] if isinstance(f, dict)]
        weights = next((str(f["name"]) for f in listed if f.get("role") == "weights"), None)
        dictionary = next((str(f["name"]) for f in listed if f.get("role") == "dict"), None)
        if weights is None:
            raise HubError("invalid", "模型卡里没写权重文件")
        for f in listed:
            filename = str(f["name"])
            if filename not in names or Path(filename).name != filename or filename == CARD:
                raise HubError("invalid", f"zip 里缺文件或文件名不对：{filename}")
            with archive.open(filename) as src:
                sha, _ = receive(read_chunks(src), work / filename)
            if sha != f.get("sha256"):
                raise HubError("invalid", f"{filename} 的 SHA-256 和模型卡上的不一样，文件可能坏了")
    keep = {k: meta[k] for k in ("base_model", "datasets", "license", "imported_by") if k in meta}
    keep["kind"] = meta.get("kind")
    return weights, dictionary, keep, body.strip()
