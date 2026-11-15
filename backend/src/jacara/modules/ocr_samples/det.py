"""Detection samples: full-page images with a box for every text line, adjusted and confirmed by a
person in the system and exported for training detection (det) models.

The workflow follows PaddleOCR's annotation tool PPOCRLabel: pre-annotate with existing OCR
results, adjust page by page (drag corners, add, delete, edit text), and export only after
"confirm page" (PPOCRLabel records confirmed files in fileState.txt and only those enter
Label.txt). The export format is read directly by PaddleOCR detection training: one line per
image, `image path<Tab>[{"transcription": …, "points": [[x, y] × 4]}]`, with ### as the text of
illegible boxes (PaddleX documentation on text detection and recognition annotation)."""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from jacara.core.i18n import tr
from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.core.storage import ObjectStorage, StorageError
from jacara.modules.audit.service import record
from jacara.modules.documents.models import Document, Extraction
from jacara.modules.ocr_samples import groups
from jacara.modules.ocr_samples.crop import BAD_CHARS
from jacara.modules.ocr_samples.models import DetPage, OcrSample, OcrSampleExport
from jacara.modules.ocr_samples.schemas import (
    DetBox,
    DetExportPreview,
    DetPageIn,
    DetPageItem,
    DetPageList,
    DetPageOut,
    DetStatus,
    SplitCount,
)
from jacara.pipeline.current import template_of
from jacara.pipeline.pages import pages_json

IGNORE = "###"
MAX_BOXES = 2000
SAME_POINT = 1.0
"""Two quadrilaterals whose corresponding corners differ by at most 1 pixel are the same box (as for
identifying the same box on the samples page)."""


class DetError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _sensitive_fields(extraction: Extraction) -> set[str]:
    from jacara.modules.ocr_samples.service import _sensitive, generic_field

    template = template_of(extraction)
    return {
        v.name
        for v in extraction.values
        if v.current_value and _sensitive(template, generic_field(v.name))
    }


def sensitive_on_page(extraction: Extraction, page: int) -> bool:
    """Whether this page contains a value of a sensitive field (the full-page image would expose
    it). Values whose page is unknown count as present."""
    names = _sensitive_fields(extraction)
    if not names:
        return False
    for v in extraction.values:
        if v.name not in names:
            continue
        evidence = v.current_evidence or {}
        where = evidence.get("page")
        if where is None or int(where) == page:
            return True
    return False


def ocr_pages(extraction: Extraction) -> list[tuple[int, dict[str, Any]]]:
    """Pages of this extraction read by OCR (pages read from the PDF text layer are excluded: their
    boxes do not come from a detection model)."""
    return [
        (page, ocr)
        for page, ocr in pages_json(extraction.ocr, extraction.page)
        if ocr.get("engine", extraction.source) != "pdf-text" and ocr.get("lines") is not None
    ]


def _same(a: Any, b: Any) -> bool:
    try:
        return len(a) == len(b) == 4 and all(
            abs(float(p[0]) - float(q[0])) <= SAME_POINT
            and abs(float(p[1]) - float(q[1])) <= SAME_POINT
            for p, q in zip(a, b, strict=True)
        )
    except (TypeError, ValueError, IndexError):
        return False


def prefill(ocr: dict[str, Any], samples: list[OcrSample]) -> list[dict[str, Any]]:
    """Pre-annotation: every OCR text block (four corners and text); transcriptions corrected during
    review (OCR samples) replace the text of the same block; missed-text samples from region
    selection are added as new boxes."""
    boxes: list[dict[str, Any]] = [
        {
            "points": [[float(x), float(y)] for x, y in line["polygon"]],
            "text": str(line["text"]),
            "ignore": False,
            "origin": "ocr",
        }
        for line in ocr.get("lines", [])
        if len(line.get("polygon") or []) == 4
    ]
    for sample in samples:
        if sample.status != "ready":
            continue
        if sample.kind == "line":
            for box in boxes:
                if _same(box["points"], sample.polygon):
                    box["text"], box["origin"] = sample.transcription, "sample"
                    break
        elif sample.category == "missed":
            points = [[float(x), float(y)] for x, y in sample.polygon]
            boxes.append(
                {
                    "points": points,
                    "text": sample.transcription,
                    "ignore": False,
                    "origin": "missed",
                }
            )
    return boxes


def check_boxes(
    boxes: list[DetBox], width: int, height: int, confirming: bool
) -> list[dict[str, Any]]:
    """Validate before saving: four points, inside the image, a valid polygon (not
    self-intersecting, non-zero area); boxes not marked illegible need text, without Tab or
    newline."""
    from shapely.geometry import Polygon

    if len(boxes) > MAX_BOXES:
        raise DetError("invalid", tr("一页最多 {limit} 个框", limit=MAX_BOXES))
    cleaned: list[dict[str, Any]] = []
    for n, box in enumerate(boxes, start=1):
        points = [
            [min(max(float(x), 0.0), float(width)), min(max(float(y), 0.0), float(height))]
            for x, y in box.points
        ]
        polygon = Polygon(points)
        if not polygon.is_valid or polygon.area < 4:
            raise DetError("invalid", tr("第 {n} 个框的角点无效（交叉或过小）", n=n))
        text = box.text.strip()
        if not box.ignore and not text:
            raise DetError(
                "invalid", tr("第 {n} 个框没有文字：请填写图中文字，或勾选“无法辨认”", n=n)
            )
        if any(c in text for c in BAD_CHARS):
            raise DetError("invalid", tr("第 {n} 个框的文字包含 Tab 或换行", n=n))
        if text == IGNORE:
            box = box.model_copy(update={"ignore": True})
        cleaned.append(
            {
                "points": [[round(x, 1), round(y, 1)] for x, y in points],
                "text": "" if box.ignore else text,
                "ignore": box.ignore,
                "origin": box.origin,
            }
        )
    if confirming and not any(not b["ignore"] for b in cleaned):
        raise DetError(
            "invalid", tr("此页没有可用的文本框，无法确认；如需排除此页，请点击“排除此页”")
        )
    return cleaned


class DetPageService:
    def __init__(self, session: Session, owner: Owner, storage: ObjectStorage | None) -> None:
        self.session = session
        self.owner = owner
        self.storage = storage

    def _documents(self) -> list[Document]:
        return list(
            self.session.scalars(
                select(Document)
                .where(Document.team_id == self.owner.team_id, Document.status == "reviewed")
                .order_by(Document.reviewed_at.desc())
            )
        )

    def candidates(
        self, status: str | None = None, offset: int = 0, limit: int = 50
    ) -> DetPageList:
        """Pages eligible for detection samples: reviewed documents, pages read by OCR, no sensitive
        fields on the page."""
        require(self.owner, "value:read")
        saved = {
            (p.document_id, p.page): p
            for p in self.session.scalars(
                select(DetPage).where(DetPage.team_id == self.owner.team_id)
            )
        }
        items: list[DetPageItem] = []
        counts: Counter[str] = Counter()
        for document in self._documents():
            for extraction in document.extractions:
                for page, ocr in ocr_pages(extraction):
                    if sensitive_on_page(extraction, page):
                        counts["sensitive"] += 1
                        continue
                    found = saved.get((document.id, page))
                    state = found.status if found else "none"
                    counts[state] += 1
                    if status and state != status:
                        continue
                    items.append(
                        DetPageItem(
                            document_id=document.id,
                            filename=document.filename,
                            page=page,
                            status=cast(DetStatus, state),
                            boxes=len(found.boxes) if found else len(ocr.get("lines", [])),
                            revision=found.revision if found else 0,
                            confirmed_at=found.confirmed_at if found else None,
                        )
                    )
        return DetPageList(
            items=items[offset : offset + limit], total=len(items), counts=dict(counts)
        )

    def _locate(
        self, document_id: uuid.UUID, page: int
    ) -> tuple[Document, Extraction, dict[str, Any]]:
        document = self.session.get(Document, document_id)
        if document is None or document.team_id != self.owner.team_id:
            raise DetError("not_found", tr("文档不存在"))
        if document.status != "reviewed":
            raise DetError("invalid", tr("仅已完成审核的单据可用于检测样本"))
        for extraction in document.extractions:
            for number, ocr in ocr_pages(extraction):
                if number == page:
                    if sensitive_on_page(extraction, page):
                        raise DetError(
                            "invalid",
                            tr("此页包含敏感字段，整页图像会泄露该信息，因此不能用作检测样本"),
                        )
                    return document, extraction, ocr
        raise DetError("not_found", tr("此页没有 OCR 结果（使用 PDF 文本层的页不用作检测样本）"))

    def _saved(self, document_id: uuid.UUID, page: int) -> DetPage | None:
        return self.session.scalar(
            select(DetPage).where(DetPage.document_id == document_id, DetPage.page == page)
        )

    def _out(
        self,
        document: Document,
        page: int,
        found: DetPage | None,
        boxes: list[dict[str, Any]],
        width: int,
        height: int,
        engine: str,
    ) -> DetPageOut:
        return DetPageOut(
            document_id=document.id,
            filename=document.filename,
            page=page,
            width=width,
            height=height,
            status=cast(DetStatus, found.status if found else "none"),
            revision=found.revision if found else 0,
            boxes=[DetBox.model_validate(b) for b in boxes],
            ocr_engine=engine,
            confirmed_at=found.confirmed_at if found else None,
        )

    def get(self, document_id: uuid.UUID, page: int) -> DetPageOut:
        """Open a page: saved boxes if any; otherwise pre-annotation (not written to the database
        until the first save)."""
        require(self.owner, "value:read")
        document, extraction, ocr = self._locate(document_id, page)
        found = self._saved(document_id, page)
        if found is not None:
            return self._out(
                document,
                page,
                found,
                list(found.boxes),
                found.width,
                found.height,
                found.ocr_engine,
            )
        samples = list(
            self.session.scalars(
                select(OcrSample).where(
                    OcrSample.document_id == document_id, OcrSample.page == page
                )
            )
        )
        return self._out(
            document,
            page,
            None,
            prefill(ocr, samples),
            int(ocr.get("width") or 0),
            int(ocr.get("height") or 0),
            (extraction.ocr_engine or "")[:255],
        )

    def save(
        self, document_id: uuid.UUID, page: int, data: DetPageIn, status: str | None = None
    ) -> DetPageOut:
        """Save boxes (and change the status when given: confirmed, excluded, draft to reopen).
        Rejected when the revision differs from the stored one (someone just changed it)."""
        require(self.owner, "value:review")
        document, extraction, ocr = self._locate(document_id, page)
        found = self._saved(document_id, page)
        current = found.revision if found else 0
        if data.revision != current:
            raise DetError("conflict", tr("此页刚被他人修改，请刷新后再编辑"))
        width = found.width if found else int(ocr.get("width") or 0)
        height = found.height if found else int(ocr.get("height") or 0)
        boxes = check_boxes(data.boxes, width, height, confirming=status == "confirmed")
        team = self.owner.team_id
        if found is None:
            found = DetPage(
                document_id=document.id,
                extraction_id=extraction.id,
                page=page,
                unit_page=extraction.page,
                width=width,
                height=height,
                boxes=boxes,
                status="draft",
                revision=1,
                ocr_engine=(extraction.ocr_engine or "")[:255],
                user_id=self.owner.user_id,
                team_id=team,
            )
            self.session.add(found)
            groups.ensure_member(
                self.session, team, self.owner.user_id, (document.id, extraction.page)
            )
        else:
            found.boxes = boxes
            found.revision += 1
        if status is not None:
            found.status = status
            if status == "confirmed":
                found.confirmed_by = self.owner.user_id
                found.confirmed_at = datetime.now(UTC)
        self.session.flush()
        action = {
            "confirmed": "ocr_samples:det_confirm",
            "excluded": "ocr_samples:det_exclude",
        }.get(status or "", "ocr_samples:det_save")
        record(
            self.session,
            self.owner,
            action,
            "document",
            document.id,
            page=page,
            boxes=len(boxes),
            ignored=sum(b["ignore"] for b in boxes),
            revision=found.revision,
        )
        return self._out(document, page, found, boxes, width, height, found.ocr_engine)

    # ---- Export ----

    def _selection(self, kind: str) -> tuple[list[tuple[DetPage, uuid.UUID, str]], Counter[str]]:
        team = self.owner.team_id
        members = groups.groups_of(self.session, team)
        splits = groups.splits_of(self.session, team)
        excluded: Counter[str] = Counter()
        kept: list[tuple[DetPage, uuid.UUID, str]] = []
        pages = self.session.scalars(
            select(DetPage).where(DetPage.team_id == team).order_by(DetPage.created_at, DetPage.id)
        )
        for p in pages:
            if p.status != "confirmed":
                excluded[tr("未确认") if p.status == "draft" else tr("已排除")] += 1
                continue
            group = members.get((p.document_id, p.unit_page))
            if group is None:
                group = groups.ensure_member(
                    self.session, team, self.owner.user_id, (p.document_id, p.unit_page)
                ).group_id
            split = (splits.get(group) or groups.ensure_split(self.session, team, group)).split
            if split == "quarantined":
                excluded[tr("所在分组已被隔离（合并或拆分时发现已导出）")] += 1
            elif kind == "train_val" and split == "test_locked":
                excluded[tr("锁定测试集（单独导出）")] += 1
            elif kind == "test" and split != "test_locked":
                continue
            else:
                kept.append((p, group, split))
        return kept, excluded

    @staticmethod
    def _splits(kept: list[tuple[DetPage, uuid.UUID, str]], kind: str) -> dict[str, SplitCount]:
        names = ("test_locked",) if kind == "test" else ("train", "val")
        return {
            ("test" if n == "test_locked" else n): SplitCount(
                groups=len({g for _, g, s in kept if s == n}),
                rows=sum(1 for _, _, s in kept if s == n),
            )
            for n in names
        }

    def preview(self, kind: str = "train_val") -> DetExportPreview:
        require(self.owner, "document:export")
        kept, excluded = self._selection(kind)
        return DetExportPreview(
            kind=kind,  # type: ignore[arg-type]
            included=len(kept),
            boxes=sum(len(p.boxes) for p, _, _ in kept),
            splits=self._splits(kept, kind),
            excluded=dict(excluded),
            split_version=groups.split_version(self.session, self.owner.team_id),
        )

    def export(self, kind: str = "train_val") -> tuple[str, bytes, uuid.UUID]:
        """Confirmed pages → zip with images/, det_gt_train.txt, det_gt_val.txt (or
        det_gt_test.txt), manifest.jsonl and README.md. Records an export (required for split
        isolation). Returns (file name, bytes, export record id)."""
        from jacara.plugins.ocr.loader import page_png

        require(self.owner, "document:export")
        if self.storage is None:
            raise DetError("storage", tr("对象存储暂时不可用，请稍后再试"))
        kept, excluded = self._selection(kind)
        if not kept:
            raise DetError("invalid", tr("没有已确认且可导出的页"))
        team = self.owner.team_id
        seed = groups.split_seed(self.session, team)
        version = groups.split_version(self.session, team)
        lists: dict[str, list[str]] = defaultdict(list)
        manifest: list[str] = []
        items: list[dict[str, Any]] = []
        originals: dict[uuid.UUID, bytes] = {}
        out = io.BytesIO()
        number = 0
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for p, group, split in kept:
                document = self.session.get_one(Document, p.document_id)
                try:
                    if p.document_id not in originals:
                        originals[p.document_id] = self.storage.get(document.storage_key or "")
                    png = page_png(
                        originals[p.document_id], document.content_type == "application/pdf", p.page
                    )
                except (StorageError, IndexError, ValueError, OSError):
                    excluded[tr("原文件不存在或无法打开")] += 1
                    continue
                number += 1
                name = f"images/p_{number:04d}.png"
                part = "test" if split == "test_locked" else split
                archive.writestr(name, png)
                label = [
                    {
                        "transcription": IGNORE if b["ignore"] else b["text"],
                        "points": [[round(x), round(y)] for x, y in b["points"]],
                    }
                    for b in p.boxes
                ]
                lists[part].append(f"{name}\t{json.dumps(label, ensure_ascii=False)}\n")
                manifest.append(
                    json.dumps(
                        {
                            "image": name,
                            "document_group": groups.anonymous(seed, group),
                            "split": part,
                            "split_version": version,
                            "page_revision": p.revision,
                            "boxes": len(p.boxes),
                            "ignored": sum(b["ignore"] for b in p.boxes),
                            "source_format": "pdf"
                            if document.content_type == "application/pdf"
                            else "image",
                            "ocr_model_version": p.ocr_engine,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                items.append(
                    {
                        "det_page": str(p.id),
                        "revision": p.revision,
                        "group": str(group),
                        "split": split,
                    }
                )
            if not items:
                raise DetError("storage", tr("这些页的原文件均不存在"))
            for part, rows in lists.items():
                archive.writestr(f"det_gt_{part}.txt", "".join(rows).encode("utf-8"))
            archive.writestr("manifest.jsonl", "".join(manifest).encode("utf-8"))
            exported = [
                (p, g, s) for p, g, s in kept if str(p.id) in {i["det_page"] for i in items}
            ]
            splits = self._splits(exported, kind)
            archive.writestr(
                "README.md", _readme(exported, excluded, splits, kind, version).encode("utf-8")
            )
        export = OcrSampleExport(
            kind="det_test" if kind == "test" else "det",
            split_version=version,
            filters={},
            counts={k: v.model_dump() for k, v in splits.items()},
            items=items,
            affected=[],
            user_id=self.owner.user_id,
            team_id=team,
        )
        self.session.add(export)
        self.session.flush()
        record(
            self.session,
            self.owner,
            "ocr_samples:det_export",
            None,
            None,
            count=len(items),
            kind=kind,
            split_version=version,
        )
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        return (
            tr("检测样本测试集-{stamp}.zip", stamp=stamp)
            if kind == "test"
            else tr("检测样本-{stamp}.zip", stamp=stamp),
            out.getvalue(),
            export.id,
        )


def _readme(
    kept: list[tuple[DetPage, uuid.UUID, str]],
    excluded: Counter[str],
    splits: dict[str, SplitCount],
    kind: str,
    version: int,
) -> str:
    names = {"train": tr("训练集"), "val": tr("验证集"), "test": tr("锁定测试集")}
    boxes = sum(len(p.boxes) for p, _, _ in kept)
    ignored = sum(b["ignore"] for p, _, _ in kept for b in p.boxes)
    lines = [
        tr("# JacarandaParse 检测样本（锁定测试集）")
        if kind == "test"
        else tr("# JacarandaParse 检测样本"),
        "",
        tr(
            (
                "划分版本 {version}，导出时间 {time:%Y-%m-%d %H:%M}。共 {n} "
                "页、{boxes} 个文本框（其中无法辨认 {ignored} 个，文字为 ###"
                "）。"
            ),
            version=version,
            time=datetime.now(),
            n=len(kept),
            boxes=boxes,
            ignored=ignored,
        ),
        "",
        tr("以上为业务数据（整页单据图像），请勿提交到代码仓库或上传至公开位置。"),
        "",
        tr("## 划分"),
        "",
        *(
            tr(
                ("- {name}：{groups} 组（独立业务单据）、{rows} 页（`det_gt_{split}.txt`）"),
                name=name,
                groups=v.groups,
                rows=v.rows,
                split=k,
            )
            for k, v in splits.items()
            for name in (names[k],)
        ),
        "",
        tr("按业务单据分组：同一单据的所有页位于同一划分，与识别样本使用同一套分组。"),
        "",
        tr("## 文件"),
        "",
        tr("- `images/`：整页图像（PDF 按 216 DPI 渲染，照片按 EXIF 校正方向），文件名仅为编号。"),
        tr(
            "- `det_gt_*.txt`：每行一页，格式为图片路径、Tab、该页全部文"
            '本框的 JSON：`[{"transcription": "图中文字", "points":'
            " [[x, y], [x, y], [x, y], [x, y]]}]`，四个角点自左上角起顺时"
            "针排列，`###` 表示无法辨认、不参与训练的框。"
        ),
        tr("- `manifest.jsonl`：每页的组号、所属划分、修订号、框数、来源及预标注所用的 OCR 版本。"),
        "",
        tr("## 在 PaddleOCR 里训练检测模型"),
        "",
        tr("1. 解压至一个目录，例如 `/data/jacara_det`。"),
        tr(
            "2. 在检测训练配置中：将 `Train.dataset.data_dir`、`Eval.data"
            "set.data_dir` 设为该目录，`Train.dataset.label_file_list` 设"
            "为 `det_gt_train.txt`，`Eval.dataset.label_file_list` 设为 `"
            "det_gt_val.txt`。使用 PaddleX 训练时，按 PaddleX 文档将其重"
            "命名为 `train.txt`、`val.txt`，图片目录命名为 `images`。"
        ),
        tr(
            "3. 使用与系统当前检测模型同系列的配置和预训练权重进行微调；"
            "训练完成后导出推理模型，用 `paddlex --paddle2onnx` 转换为 ON"
            "NX，并在“模型和数据集”页导入（种类选择“OCR 文字检测”）。"
        ),
        tr("4. 启用前，请先在「模型和数据集」中用验证集或锁定测试集评测，并与当前模型对比。"),
        "",
    ]
    if excluded:
        lines += [
            tr("## 未导出"),
            "",
            *(tr("- {reason}：{n} 页", reason=r, n=n) for r, n in excluded.most_common()),
            "",
        ]
    return "\n".join(lines)
