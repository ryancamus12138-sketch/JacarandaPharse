# ruff: noqa: E501
"""读 PaddleOCR / PaddleX 格式的数据集 zip（ADR 0059）：识别（图片路径<Tab>文字）和检测
（图片路径<Tab>[{"transcription", "points"}]）。导入时检查、评测时读（ADR 0060）都用这里。

认的标注文件（格式见 PaddleX 文档"文本检测/文本识别 标注"、PaddleOCR 的 SimpleDataSet）：

| | 训练 | 验证 | 锁定测试 | 不分边 |
|---|---|---|---|---|
| 识别 | rec_gt_train.txt、train.txt | rec_gt_val.txt、val.txt | rec_gt_test.txt | rec_gt.txt（PPOCRLabel） |
| 检测 | det_gt_train.txt、train.txt | det_gt_val.txt、val.txt | det_gt_test.txt | Label.txt（PPOCRLabel） |

有分边的文件时不读"不分边"的（本系统导出的 rec_gt.txt 是训练 + 验证合起来的）。
图片路径先按标注文件所在的文件夹找，找不到再按上一层找（PPOCRLabel 的 Label.txt 在图片文件夹里，
路径却带着图片文件夹的名字）。
"""

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
"""检测标注里文字是 ### 的框不参与训练和评测（PaddleX 文档）。"""

MAX_BYTES = 1024**3
"""数据集 zip 最大 1 GB（整个放进对象存储，读的时候也整个读进内存）。"""
MAX_UNPACKED = 4 * 1024**3
"""解压后合计最多 4 GB。"""
MAX_RATIO = 200
"""单个文件的压缩比上限：图片、文字压不到这么小，超过的当成 zip 炸弹（Python 文档 zipfile
"Decompression pitfalls" 提醒的风险）。"""
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
    """zip 里的成员名。"""
    text: str | None = None
    """识别：图上的文字。"""
    boxes: tuple[Box, ...] = ()
    """检测：这张图上所有的框。"""
    group: str | None = None
    """业务单据组号（本系统导出的 manifest.jsonl 里的 document_group）；外部数据集没有。"""


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
    """zip 里每个文件都要安全：不能是绝对路径、不能带 ..（zip slip）；总大小、压缩比、文件数有上限。"""
    infos = [i for i in archive.infolist() if not i.is_dir()]
    if len(infos) > MAX_MEMBERS:
        raise HubError("invalid", f"zip 里文件太多（超过 {MAX_MEMBERS} 个）")
    total = 0
    members: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        name = info.filename.replace("\\", "/")
        parts = name.split("/")
        if name.startswith("/") or ".." in parts or ":" in parts[0]:
            raise HubError("invalid", f"zip 里有不安全的路径：{info.filename}")
        total += info.file_size
        if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
            raise HubError("invalid", f"zip 里 {info.filename} 的压缩比异常，可能是 zip 炸弹")
        if name.startswith("__MACOSX/") or posixpath.basename(name).startswith("._"):
            continue  # macOS 压缩时带的附加信息
        members[name] = info
    if total > MAX_UNPACKED:
        raise HubError("invalid", "zip 解压后太大（超过 4 GB）")
    return members


def _label_files(kind: str, members: dict[str, zipfile.ZipInfo]) -> list[tuple[str, str]]:
    """(标注文件, 哪一边)。只看最浅一层有标注文件的地方；有分边的就不读不分边的。"""
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
    """manifest.jsonl（本系统导出的）：图片 → 业务单据组号。"""
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
    """读一个数据集 zip。格式不对的行跳过并计数；一条有效的都没有时报错。"""
    if kind not in SPLIT_FILES:
        raise HubError("invalid", f"不认识的数据集种类：{kind}")
    source = io.BytesIO(data) if isinstance(data, bytes) else data
    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile as err:
        raise HubError("invalid", "不是 zip 文件") from err
    with archive:
        members = safe_members(archive)
        labels = _label_files(kind, members)
        if not labels:
            names = "、".join(n for names in SPLIT_FILES[kind].values() for n in names)
            raise HubError("invalid", f"zip 里没找到标注文件（{names}）")
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
                    parsed.problems["不是用 Tab 分开的"] += 1
                    continue
                image = _resolve(label, path, members)
                if image is None:
                    parsed.problems["图片在 zip 里找不到"] += 1
                    continue
                if (split, image) in seen:
                    parsed.problems["同一张图在同一边出现了两次（只留第一次）"] += 1
                    continue
                if verify_images and not checked.setdefault(image, _opens(archive, image)):
                    parsed.problems["图片打不开"] += 1
                    continue
                if kind == "ocr_rec":
                    if not rest or "\t" in rest:
                        parsed.problems["文字是空的或带 Tab"] += 1
                        continue
                    item = Item(split, image, text=rest, group=groups.get(image))
                else:
                    boxes = _boxes(rest)
                    if boxes is None:
                        parsed.problems["框的格式不对"] += 1
                        continue
                    item = Item(split, image, boxes=boxes, group=groups.get(image))
                seen.add((split, image))
                parsed.items.append(item)
    if not parsed.items:
        reasons = "；".join(f"{r} {n} 行" for r, n in parsed.problems.most_common()) or "没有内容"
        raise HubError("invalid", f"一条能用的都没有：{reasons}")
    return parsed


def _opens(archive: zipfile.ZipFile, name: str) -> bool:
    try:
        with Image.open(io.BytesIO(archive.read(name))) as picture:
            picture.verify()
        return True
    except Exception:  # Pillow 打不开时的错误种类很多
        return False


def images(data: bytes, items: list[Item]) -> Iterator[tuple[Item, bytes]]:
    """按顺序取出每一条的图片字节（评测时用）。"""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for item in items:
            yield item, archive.read(item.image)
