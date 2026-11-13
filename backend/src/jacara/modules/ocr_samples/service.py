"""OCR 纠错样本（ADR 0050）：审核时收集、样本页管理、导出成 PaddleOCR 识别训练数据。"""

from __future__ import annotations

import io
import json
import logging
import uuid
import zipfile
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jacara.core.owner import Owner
from jacara.core.permissions import allowed, require
from jacara.core.storage import ObjectStorage, StorageError
from jacara.modules.audit.service import record
from jacara.modules.documents.models import Document, Extraction
from jacara.modules.fields.models import ExtractedValue
from jacara.modules.ocr_samples.crop import (
    CATEGORIES,
    TAGS,
    box_polygon,
    category_of,
    clean,
    crop_polygon,
    error_tags,
    lines_in_box,
    png_bytes,
    sha256,
)
from jacara.modules.ocr_samples.models import OcrSample, TeamSetting
from jacara.modules.ocr_samples.schemas import (
    Excluded,
    ExportPreview,
    SampleIn,
    SampleList,
    SampleOut,
    SampleSettings,
)
from jacara.pipeline.current import labels_of, template_of
from jacara.pipeline.pages import ocr_page_json
from jacara.plugins.extractors.base import DISCOVER, split_cell_name
from jacara.templates import Template

log = logging.getLogger(__name__)

SETTING = "collect_ocr_samples"
FORMAT_VERSION = 1
"""导出格式的版本号，写进 README。"""

PADDING = 0.3
"""框选的样本四周多留多少（和"重新识别这块"一样，见 pipeline/region.py）。"""


class SampleError(Exception):
    """业务错误。code 让 router 知道该返回哪种 HTTP 状态。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---- 开关 ----


def collecting(session: Session, team_id: uuid.UUID) -> bool:
    """这个团队开没开"自动收集纠错样本"。默认关。"""
    setting = session.get(TeamSetting, (team_id, SETTING))
    return bool(setting.value) if setting is not None else False


# ---- 审核时收集 ----


def generic_field(name: str) -> str:
    """表格的格子去掉行号：items.2.amount → items.amount。"""
    cell = split_cell_name(name)
    return f"{cell[0]}.{cell[2]}" if cell else name


def _sensitive(template: Template | None, field: str) -> bool:
    if template is None:
        return False
    if any(f.name == field and f.sensitive for f in template.fields):
        return True
    return any(
        f"{table.name}.{column.name}" == field and getattr(column, "sensitive", False)
        for table in template.tables
        for column in table.columns
    )


def _template_title(extraction: Extraction, template: Template | None) -> str:
    if extraction.template == DISCOVER:
        return "抽取所有键值对"
    return template.title if template else extraction.template


def collect(
    session: Session,
    storage: ObjectStorage | None,
    owner: Owner,
    value: ExtractedValue,
    revision_id: uuid.UUID | None,
    page: int,
    transcription: str,
    *,
    line: dict[str, Any] | None = None,
    box: Sequence[float] | None = None,
) -> OcrSample | None:
    """保存审核的同时收集一条样本。不该收集的（开关关着、敏感字段、PDF 自带的文字……）返回 None。

    裁图出错不影响审核：样本记成"裁图失败"，样本页可以重试。
    """
    if not collecting(session, value.team_id):
        return None
    truth = clean(transcription)
    if truth is None:
        return None
    extraction = value.extraction
    document = extraction.document
    if document.storage_key is None or storage is None:
        return None
    ocr = ocr_page_json(extraction.ocr, page, extraction.page) or {}
    if ocr.get("engine", extraction.source) == "pdf-text":
        return None  # 这一页的字是 PDF 自带的，不是 OCR 认的
    template = template_of(extraction)
    field = generic_field(value.name)
    if _sensitive(template, field):
        return None
    if line is not None:
        kind, polygon = "line", [[float(x), float(y)] for x, y in line["polygon"]]
        ocr_text: str | None = str(line["text"])
    elif box is not None:
        kind, polygon = "box", box_polygon(box)
        inside = lines_in_box(ocr.get("lines", []), box)
        ocr_text = " ".join(str(ln["text"]) for ln in inside) if inside else None
    else:
        return None
    labels = labels_of(extraction)
    sample = OcrSample(
        id=uuid.uuid4(),
        created_at=datetime.now(UTC),  # 精确到微秒：同一秒收集的几条也能排出先后
        document_id=document.id,
        value_id=value.id,
        revision_id=revision_id,
        page=page,
        kind=kind,
        polygon=polygon,
        transcription=truth,
        ocr_text=ocr_text,
        category=category_of(kind, ocr_text, truth),
        error_tags=error_tags(ocr_text, truth),
        template=_template_title(extraction, template)[:200],
        field=field[:200],
        field_label=(labels.get(field) or value.label or value.name)[:200],
        source_format="pdf" if document.content_type == "application/pdf" else "image",
        ocr_engine=(extraction.ocr_engine or "")[:128],
        status="ready",
        error="",
        user_id=owner.user_id,
        team_id=value.team_id,
    )
    _crop(storage, sample, document)
    session.add(sample)
    return sample


def _crop(storage: ObjectStorage, sample: OcrSample, document: Document) -> None:
    """裁出小图存进对象存储。出错记在样本上（status=failed），不往外抛。"""
    from jacara.pipeline.region import crop_region
    from jacara.plugins.ocr.loader import page_png

    try:
        if document.storage_key is None:
            raise ValueError("这个文档没有保存原始文件")
        data = storage.get(document.storage_key)
        page = page_png(data, document.content_type == "application/pdf", sample.page)
        if sample.kind == "line":
            image = crop_polygon(page, sample.polygon)
        else:
            (x0, y0), (x1, y1) = sample.polygon[0], sample.polygon[2]
            image = crop_region(page, [x0, y0, x1, y1], PADDING)
        png = png_bytes(image)
        key = f"ocr-samples/{sample.team_id}/{sample.id}.png"
        storage.put(key, png, "image/png")
    except Exception as err:  # 裁图只是顺带：文件读不了、框不对、存储连不上，都不影响审核
        log.warning("OCR 样本裁图失败：%s 第 %s 页", document.filename, sample.page, exc_info=True)
        sample.status = "failed"
        sample.error = _reason(err)
        return
    sample.crop_key = key
    sample.crop_sha256 = sha256(png)
    sample.width, sample.height = image.size
    sample.status = "ready"
    sample.error = ""


def _reason(err: Exception) -> str:
    if isinstance(err, StorageError):
        return "对象存储暂时不可用"
    return str(err) or type(err).__name__


# ---- 样本页 ----


class OcrSampleService:
    def __init__(
        self, session: Session, owner: Owner, storage: ObjectStorage | None = None
    ) -> None:
        self.session = session
        self.owner = owner
        self.storage = storage

    def settings(self) -> SampleSettings:
        return SampleSettings(
            collect=collecting(self.session, self.owner.team_id),
            can_change="system:manage" in allowed(self.owner),
        )

    def set_settings(self, collect: bool) -> SampleSettings:
        require(self.owner, "system:manage")
        setting = self.session.get(TeamSetting, (self.owner.team_id, SETTING))
        if setting is None:
            self.session.add(TeamSetting(team_id=self.owner.team_id, key=SETTING, value=collect))
        else:
            setting.value = collect
        record(self.session, self.owner, "ocr_samples:settings", None, None, collect=collect)
        self.session.flush()
        return self.settings()

    def _query(
        self,
        category: Sequence[str] | None = None,
        status: str | None = None,
        source_format: str | None = None,
        template: str | None = None,
    ) -> Any:
        query = select(OcrSample).where(OcrSample.team_id == self.owner.team_id)
        if category:
            query = query.where(OcrSample.category.in_(list(category)))
        if status:
            query = query.where(OcrSample.status == status)
        if source_format:
            query = query.where(OcrSample.source_format == source_format)
        if template:
            query = query.where(OcrSample.template == template)
        return query

    def list(
        self,
        category: Sequence[str] | None = None,
        status: str | None = None,
        source_format: str | None = None,
        template: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> SampleList:
        require(self.owner, "value:read")
        query = self._query(category, status, source_format, template)
        total = self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        samples = list(
            self.session.scalars(
                query.order_by(OcrSample.created_at.desc(), OcrSample.id)
                .limit(limit)
                .offset(offset)
            )
        )
        team = OcrSample.team_id == self.owner.team_id
        categories = dict(
            self.session.execute(
                select(OcrSample.category, func.count())
                .where(team, OcrSample.status != "withdrawn")
                .group_by(OcrSample.category)
            ).all()
        )
        statuses = dict(
            self.session.execute(
                select(OcrSample.status, func.count()).where(team).group_by(OcrSample.status)
            ).all()
        )
        return SampleList(
            items=self._out(samples),
            total=total,
            categories={k: int(v) for k, v in categories.items()},
            statuses={k: int(v) for k, v in statuses.items()},
        )

    def templates(self) -> list[str]:
        """有样本的模板名，筛选用。"""
        require(self.owner, "value:read")
        rows = self.session.scalars(
            select(OcrSample.template)
            .where(OcrSample.team_id == self.owner.team_id)
            .distinct()
            .order_by(OcrSample.template)
        )
        return [r for r in rows if r]

    def _out(self, samples: Sequence[OcrSample]) -> list[SampleOut]:
        ids = {s.document_id for s in samples}
        names: dict[uuid.UUID, str] = (
            dict(
                self.session.execute(
                    select(Document.id, Document.filename).where(Document.id.in_(ids))
                ).all()
            )
            if ids
            else {}
        )
        return [
            SampleOut(
                id=s.id,
                created_at=s.created_at,
                document_id=s.document_id,
                filename=names.get(s.document_id, ""),
                page=s.page,
                kind=s.kind,
                transcription=s.transcription,
                ocr_text=s.ocr_text,
                category=s.category,  # type: ignore[arg-type]
                error_tags=list(s.error_tags or []),
                template=s.template,
                field=s.field,
                field_label=s.field_label,
                source_format=s.source_format,  # type: ignore[arg-type]
                ocr_engine=s.ocr_engine,
                width=s.width,
                height=s.height,
                status=s.status,  # type: ignore[arg-type]
                error=s.error,
            )
            for s in samples
        ]

    def _find(self, sample_id: uuid.UUID) -> OcrSample:
        sample = self.session.get(OcrSample, sample_id)
        if sample is None or sample.team_id != self.owner.team_id:
            raise SampleError("not_found", "找不到这条样本")
        return sample

    def image(self, sample_id: uuid.UUID) -> bytes:
        require(self.owner, "value:read")
        sample = self._find(sample_id)
        if sample.crop_key is None or self.storage is None:
            raise SampleError("not_found", "这条样本没有裁图（裁图失败了，可以重试）")
        try:
            return self.storage.get(sample.crop_key)
        except StorageError as err:
            raise SampleError("storage", "对象存储暂时不可用，请稍后再试") from err

    def update(self, sample_id: uuid.UUID, change: SampleIn) -> SampleOut:
        require(self.owner, "value:review")
        sample = self._find(sample_id)
        detail: dict[str, Any] = {}
        if change.transcription is not None:
            truth = clean(change.transcription)
            if truth is None:
                raise SampleError("invalid", "图中原文不能是空的，也不能有 Tab、换行")
            if truth != sample.transcription:
                detail["transcription"] = [sample.transcription, truth]
                sample.transcription = truth
                sample.error_tags = error_tags(sample.ocr_text, truth)
                if change.category is None and sample.kind == "line":
                    sample.category = category_of(sample.kind, sample.ocr_text, truth)
        if change.category is not None and change.category != sample.category:
            detail["category"] = [sample.category, change.category]
            sample.category = change.category
        if change.status is not None and change.status != sample.status:
            if sample.status == "failed":
                raise SampleError("invalid", "裁图失败的样本先重试")
            detail["status"] = [sample.status, change.status]
            sample.status = change.status
        if detail:
            record(
                self.session, self.owner, "ocr_samples:update", "ocr_sample", sample.id, **detail
            )
        self.session.flush()
        return self._out([sample])[0]

    def retry(self, sample_id: uuid.UUID) -> SampleOut:
        """裁图失败的重新裁一次。"""
        require(self.owner, "value:review")
        sample = self._find(sample_id)
        if sample.status != "failed":
            raise SampleError("invalid", "这条样本没有失败，不用重试")
        document = self.session.get(Document, sample.document_id)
        if document is None or self.storage is None:
            raise SampleError("not_found", "找不到原来的文档")
        _crop(self.storage, sample, document)
        self.session.flush()
        if sample.status == "failed":
            raise SampleError("storage", f"还是没裁出来：{sample.error}")
        return self._out([sample])[0]

    # ---- 导出 ----

    def _selection(
        self, category: Sequence[str] | None, source_format: str | None, template: str | None
    ) -> tuple[list[OcrSample], Counter[str]]:
        """要导出的样本，和不导出的原因（原因 → 几条）。"""
        samples = list(
            self.session.scalars(
                self._query(category, None, source_format, template).order_by(
                    OcrSample.created_at, OcrSample.id
                )
            )
        )
        excluded: Counter[str] = Counter()
        usable: list[OcrSample] = []
        for sample in samples:
            if sample.status == "withdrawn":
                excluded["撤回了"] += 1
            elif sample.status != "ready" or not sample.crop_key or not sample.crop_sha256:
                excluded["裁图失败，没有图"] += 1
            elif clean(sample.transcription) != sample.transcription:
                excluded["图中原文是空的或有 Tab、换行"] += 1
            else:
                usable.append(sample)
        by_image: dict[str, list[OcrSample]] = defaultdict(list)
        for sample in usable:
            by_image[str(sample.crop_sha256)].append(sample)
        kept: list[OcrSample] = []
        for group in by_image.values():
            if len({s.transcription for s in group}) > 1:
                excluded["同一张图有两种写法"] += len(group)
                continue
            kept.append(group[0])
            if len(group) > 1:
                excluded["同一张图重复，只留一份"] += len(group) - 1
        kept.sort(key=lambda s: (s.created_at, str(s.id)))
        return kept, excluded

    def preview(
        self,
        category: Sequence[str] | None = None,
        source_format: str | None = None,
        template: str | None = None,
    ) -> ExportPreview:
        require(self.owner, "document:export")
        kept, excluded = self._selection(category, source_format, template)
        return ExportPreview(
            included=len(kept),
            categories=dict(Counter(s.category for s in kept)),
            excluded=[Excluded(reason=r, count=n) for r, n in excluded.most_common()],
        )

    def export(
        self,
        category: Sequence[str] | None = None,
        source_format: str | None = None,
        template: str | None = None,
    ) -> tuple[str, bytes]:
        """样本 → zip（文件名, 内容）：crop_img/、rec_gt.txt、manifest.jsonl、README.md。"""
        require(self.owner, "document:export")
        if self.storage is None:
            raise SampleError("storage", "对象存储暂时不可用，请稍后再试")
        kept, excluded = self._selection(category, source_format, template)
        if not kept:
            raise SampleError("invalid", "没有能导出的样本")
        groups: dict[uuid.UUID, str] = {}
        labels: list[str] = []
        manifest: list[str] = []
        exported: list[OcrSample] = []
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for sample in kept:
                try:
                    png = self.storage.get(str(sample.crop_key))
                    with Image.open(io.BytesIO(png)) as picture:
                        picture.verify()  # 图片能解码
                except Exception:  # 存储里的图丢了或坏了：这条不导出
                    excluded["图片找不到或打不开"] += 1
                    continue
                number = len(exported) + 1
                name = f"crop_img/s_{number:04d}.png"
                group = groups.setdefault(sample.document_id, f"g_{len(groups) + 1:04d}")
                archive.writestr(name, png)
                labels.append(f"{name}\t{sample.transcription}\n")
                manifest.append(
                    json.dumps(
                        {
                            "sample_id": f"s_{number:04d}",
                            "image": name,
                            "transcription": sample.transcription,
                            "ocr_text": sample.ocr_text,
                            "category": sample.category,
                            "error_tags": list(sample.error_tags or []),
                            "template": sample.template,
                            "field": sample.field,
                            "document_group": group,
                            "source_format": sample.source_format,
                            "ocr_model_version": sample.ocr_engine,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                exported.append(sample)
            if not exported:
                raise SampleError("storage", "样本的图片都找不到了")
            archive.writestr("rec_gt.txt", "".join(labels).encode("utf-8"))
            archive.writestr("manifest.jsonl", "".join(manifest).encode("utf-8"))
            archive.writestr("README.md", _readme(exported, excluded, len(groups)).encode("utf-8"))
        record(
            self.session,
            self.owner,
            "ocr_samples:export",
            None,
            None,
            count=len(exported),
            categories=sorted(set(category or [])),
            source_format=source_format,
            template=template,
        )
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        return f"OCR样本-{stamp}.zip", out.getvalue()


def _readme(samples: Sequence[OcrSample], excluded: Counter[str], documents: int) -> str:
    categories = Counter(s.category for s in samples)
    tags = Counter(t for s in samples for t in (s.error_tags or []))
    formats = Counter(s.source_format for s in samples)
    lines = [
        "# JacarandaParse OCR 样本",
        "",
        f"格式版本 {FORMAT_VERSION}，导出时间 {datetime.now():%Y-%m-%d %H:%M}。",
        f"一共 {len(samples)} 条，来自 {documents} 张单据"
        f"（PDF {formats.get('pdf', 0)} 条，照片 {formats.get('image', 0)} 条）。",
        "",
        "这是你自己的业务数据，不要放进代码仓库、不要上传到公开的地方。",
        "",
        "## 文件",
        "",
        "- `crop_img/`：每条一张单行小图，文件名只是编号。",
        "- `rec_gt.txt`：每行一条，图片路径、一个 Tab、图上原样的文字（UTF-8，无 BOM）。",
        "- `manifest.jsonl`：每行一条的详细信息：OCR 当时读成什么（`ocr_text`）、分类、错误标签、"
        "模板、字段、单据编号（`document_group`，同一张单据的样本编号一样，只在这个导出里有效）、"
        "来源（pdf / image）、首轮 OCR 的版本。",
        "",
        "## 分类",
        "",
        *(f"- {CATEGORIES[c]}（`{c}`）：{categories.get(c, 0)} 条" for c in CATEGORIES),
        "",
        "认错字、漏认是错例；认对的行是审核时核对过、OCR 本来就读对的。微调时错例和认对的行一起用，"
        '只用错例模型会把原来认得好的字"忘掉"。原因未确定的：图和首轮识别时的不完全一样，看过再决定用不用。',
        "",
    ]
    if tags:
        lines += [
            "错误标签：" + "，".join(f"{TAGS[t]} {n}" for t, n in tags.most_common() if t in TAGS),
            "",
        ]
    if excluded:
        lines += [
            "## 没有导出的",
            "",
            *(f"- {reason}：{n} 条" for reason, n in excluded.most_common()),
            "",
        ]
    lines += [
        "## 在 PaddleOCR 里训练识别模型",
        "",
        "1. 解压到一个文件夹，例如 `/data/jacara_ocr`。",
        "2. 识别训练配置里：`Train.dataset.data_dir` 和 `Eval.dataset.data_dir` 写这个文件夹，"
        "`label_file_list` 写 `rec_gt.txt`（`SimpleDataSet` 直接能读）。",
        "3. 用和系统现在用的识别模型同一系列的配置、字典和预训练权重做微调；"
        "字典里没有的字训练时会被跳过，最好先检查一遍。",
        "4. 训练好后导出推理模型、转成 ONNX，放进项目的 `models/ocr/`（文件名带 rec），"
        '在系统信息页"换模型"里选上，jacara stop、jacara start 后生效。',
        "",
        "这一版还没有分训练集和验证集。验收要用没参与训练的单据，按 `document_group` 整组分开，"
        "同一张单据的样本不要一部分在训练集、一部分在验证集。",
        "",
    ]
    return "\n".join(lines)
