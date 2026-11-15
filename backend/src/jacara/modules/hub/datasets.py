"""Read PaddleOCR / PaddleX dataset archives: recognition (image path<Tab>text) and detection (image
path<Tab>[{"transcription", "points"}]). Used both for validation on import and for reading
during evaluation.

Recognized label files (formats per the PaddleX documentation on text detection and recognition
annotation, and PaddleOCR SimpleDataSet):

| Task | Train                       | Val                     | Locked test     | Unsplit
|
|------|-----------------------------|-------------------------|-----------------|-------------------------|
| rec  | rec_gt_train.txt, train.txt | rec_gt_val.txt, val.txt | rec_gt_test.txt | rec_gt.txt
(PPOCRLabel) |
| det  | det_gt_train.txt, train.txt | det_gt_val.txt, val.txt | det_gt_test.txt | Label.txt
(PPOCRLabel)  |

When split files exist, unsplit files are ignored (rec_gt.txt exported by this system combines
train and val). Image paths are resolved relative to the folder of the label file first and then
its parent (PPOCRLabel's Label.txt sits inside the image folder but its paths include the image
folder name)."""

from __future__ import annotations

import io
import json
import posixpath
import zipfile
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from jacara.core.i18n import join_items, join_notes, stored
from jacara.modules.hub.registry import HubError

SPLIT_FILES: dict[str, dict[str, tuple[str, ...]]] = {
    "ocr_rec": {
        "train": ("rec_gt_train.txt", "train.txt"),
        "val": ("rec_gt_val.txt", "val.txt"),
        "test": ("rec_gt_test.txt",),
        "all": ("rec_gt.txt",),
    },
    "ocr_det": {
        "train": ("det_gt_train.txt", "train.txt"),
        "val": ("det_gt_val.txt", "val.txt"),
        "test": ("det_gt_test.txt",),
        "all": ("Label.txt",),
    },
}

SPLITS = ("train", "val", "test", "all")

IGNORE = "###"
"""Detection boxes whose text is ### are excluded from training and evaluation (PaddleX
documentation)."""

MAX_BYTES = 1024**3
"""Maximum dataset archive size: 1 GB (stored whole in object storage and read whole into
memory)."""
MAX_UNPACKED = 4 * 1024**3
"""Maximum total uncompressed size: 4 GB."""
MAX_RATIO = 200
"""Maximum compression ratio per member: images and text never compress this far, so anything above
is treated as a zip bomb (the risk described under "Decompression pitfalls" in the Python zipfile
documentation)."""
MAX_MEMBERS = 200_000


@dataclass(frozen=True)
class Box:
    points: tuple[tuple[float, float], ...]
    text: str

    @property
    def ignore(self) -> bool:
        return self.text == IGNORE


@dataclass(frozen=True)
class Item:
    split: str
    image: str
    """Member name in the archive."""
    text: str | None = None
    """Recognition: text in the image."""
    boxes: tuple[Box, ...] = ()
    """Detection: all boxes in the image."""
    group: str | None = None
    """Business document group (document_group in manifest.jsonl exported by this system); absent in
    external datasets."""


@dataclass
class Parsed:
    kind: str
    items: list[Item]
    files: list[dict[str, str]]
    problems: Counter[str] = field(default_factory=Counter)

    @property
    def counts(self) -> dict[str, int]:
        found = Counter(i.split for i in self.items)
        return {s: found.get(s, 0) for s in SPLITS}

    @property
    def groups(self) -> dict[str, int]:
        found: dict[str, set[str]] = {}
        for i in self.items:
            if i.group:
                found.setdefault(i.split, set()).add(i.group)
        return {s: len(g) for s, g in found.items()}

    def split(self, name: str) -> list[Item]:
        return [i for i in self.items if i.split == name]


def safe_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """Every member must be safe: no absolute paths, no .. (zip slip); limits on total size,
    compression ratio and member count."""
    infos = [i for i in archive.infolist() if not i.is_dir()]
    if len(infos) > MAX_MEMBERS:
        raise HubError("invalid", stored("zip 中文件过多（超过 {limit} 个）", limit=MAX_MEMBERS))
    total = 0
    members: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        name = info.filename.replace("\\", "/")
        parts = name.split("/")
        if name.startswith("/") or ".." in parts or ":" in parts[0]:
            raise HubError(
                "invalid", stored("zip 中存在不安全的路径：{filename}", filename=info.filename)
            )
        total += info.file_size
        if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
            raise HubError(
                "invalid",
                stored("zip 中 {filename} 的压缩比异常，可能为 zip 炸弹", filename=info.filename),
            )
        if name.startswith("__MACOSX/") or posixpath.basename(name).startswith("._"):
            continue  # Metadata added by the macOS archiver.
        members[name] = info
    if total > MAX_UNPACKED:
        raise HubError("invalid", stored("zip 解压后过大（超过 4 GB）"))
    return members


def _label_files(kind: str, members: dict[str, zipfile.ZipInfo]) -> list[tuple[str, str]]:
    """(Label file, split). Only the shallowest level containing label files is considered; split
    files take precedence over unsplit ones."""
    known = {name: split for split, names in SPLIT_FILES[kind].items() for name in names}
    found = [(m, known[posixpath.basename(m)]) for m in members if posixpath.basename(m) in known]
    if not found:
        return []
    depth = min(m.count("/") for m, _ in found)
    found = [(m, s) for m, s in found if m.count("/") == depth]
    if any(s != "all" for _, s in found):
        found = [(m, s) for m, s in found if s != "all"]
    return sorted(found)


def _resolve(label: str, path: str, members: dict[str, zipfile.ZipInfo]) -> str | None:
    path = path.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    if not path or path.startswith("/") or ".." in path.split("/"):
        return None
    base = posixpath.dirname(label)
    for root in (base, posixpath.dirname(base)):
        name = posixpath.normpath(posixpath.join(root, path)) if root else posixpath.normpath(path)
        if name in members:
            return name
    return None


def _groups(members: dict[str, zipfile.ZipInfo], archive: zipfile.ZipFile) -> dict[str, str]:
    """manifest.jsonl (exported by this system): image → business document group."""
    found: dict[str, str] = {}
    for name in members:
        if posixpath.basename(name) != "manifest.jsonl":
            continue
        base = posixpath.dirname(name)
        for line in archive.read(name).decode("utf-8", "replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("image") and row.get("document_group"):
                image = posixpath.normpath(posixpath.join(base, str(row["image"])))
                found[image] = str(row["document_group"])
    return found


def _boxes(raw: str) -> tuple[Box, ...] | None:
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    boxes: list[Box] = []
    for box in data:
        if not isinstance(box, dict) or "points" not in box:
            return None
        try:
            points = tuple((float(x), float(y)) for x, y in box["points"])
        except (TypeError, ValueError):
            return None
        if len(points) < 4:
            return None
        boxes.append(Box(points, str(box.get("transcription", ""))))
    return tuple(boxes)


def parse(
    data: bytes | io.BufferedIOBase | Any, kind: str, *, verify_images: bool = True
) -> Parsed:
    """Read a dataset archive. Malformed lines are skipped and counted; raises if no valid entry
    remains."""
    if kind not in SPLIT_FILES:
        raise HubError("invalid", stored("未知的数据集种类：{kind}", kind=kind))
    source = io.BytesIO(data) if isinstance(data, bytes) else data
    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile as err:
        raise HubError("invalid", stored("不是 zip 文件")) from err
    with archive:
        members = safe_members(archive)
        labels = _label_files(kind, members)
        if not labels:
            names = join_items(n for names in SPLIT_FILES[kind].values() for n in names)
            raise HubError("invalid", stored("zip 中未找到标注文件（{names}）", names=names))
        groups = _groups(members, archive)
        parsed = Parsed(kind, [], [{"file": m, "split": s} for m, s in labels])
        seen: set[tuple[str, str]] = set()
        checked: dict[str, bool] = {}
        for label, split in labels:
            text = archive.read(label).decode("utf-8-sig", "replace")
            for line in text.splitlines():
                if not line.strip():
                    continue
                path, tab, rest = line.partition("\t")
                if not tab:
                    parsed.problems[stored("未使用 Tab 分隔")] += 1
                    continue
                image = _resolve(label, path, members)
                if image is None:
                    parsed.problems[stored("zip 中缺少该图片")] += 1
                    continue
                if (split, image) in seen:
                    parsed.problems[stored("同一图像在同一划分中出现两次（仅保留首次）")] += 1
                    continue
                if verify_images and not checked.setdefault(image, _opens(archive, image)):
                    parsed.problems[stored("无法打开图片")] += 1
                    continue
                if kind == "ocr_rec":
                    if not rest or "\t" in rest:
                        parsed.problems[stored("文字为空或包含 Tab")] += 1
                        continue
                    item = Item(split, image, text=rest, group=groups.get(image))
                else:
                    boxes = _boxes(rest)
                    if boxes is None:
                        parsed.problems[stored("文本框格式错误")] += 1
                        continue
                    item = Item(split, image, boxes=boxes, group=groups.get(image))
                seen.add((split, image))
                parsed.items.append(item)
    if not parsed.items:
        reasons = join_notes(
            *(stored("{reason} {n} 行", reason=r, n=n) for r, n in parsed.problems.most_common())
        )
        raise HubError(
            "invalid",
            stored("没有可用的数据：{reasons}", reasons=reasons) if reasons else stored("没有内容"),
        )
    return parsed


def _opens(archive: zipfile.ZipFile, name: str) -> bool:
    try:
        with Image.open(io.BytesIO(archive.read(name))) as picture:
            picture.verify()
        return True
    except Exception:  # Pillow raises many error types for unreadable images.
        return False


def images(data: bytes, items: list[Item]) -> Iterator[tuple[Item, bytes]]:
    """Image bytes of each entry in order (for evaluation)."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for item in items:
            yield item, archive.read(item.image)
