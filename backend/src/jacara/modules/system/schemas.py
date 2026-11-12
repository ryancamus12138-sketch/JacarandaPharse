"""系统信息的对外格式。"""

from __future__ import annotations

from pydantic import BaseModel


class ServiceCheck(BaseModel):
    """一个服务连不连得上（和 jacara doctor 一样的检查）。"""

    name: str
    ok: bool
    detail: str
    hint: str = ""


class ModelInfo(BaseModel):
    server: str
    """模型服务地址。"""
    file: str | None
    """jacara start 会用的模型文件名（models/ 里的或 JACARA_MODEL）。"""
    loaded: str | None
    """模型服务现在加载的模型（问模型服务得到的；连不上就是空）。"""
    context: int | None
    """上下文长度（token）。"""
    vision_file: str | None = None
    """jacara start 会一起加载的视觉模型文件（mmproj，ADR 0038）；没有是 null。"""
    sees_images: bool = False
    """模型服务现在能不能看图（加载了视觉模型）。"""
    vision_mode: str = "auto"
    """什么时候看原图：auto、always、off（JACARA_VISION）。"""


class OcrInfo(BaseModel):
    engine: str
    version: str | None


class StructureInfo(BaseModel):
    """页面结构分析用的模型（ADR 0034）。"""

    layout: str | None
    """版面模型；关掉了（JACARA_LAYOUT=false）是 null。"""
    table: str | None
    """表格结构模型文件名；没有放模型是 null（表格的格子按文字位置划分）。"""


class TemplateInfo(BaseModel):
    name: str
    title: str
    version: int


class DataInfo(BaseModel):
    documents: int
    pages: int
    fields: int
    revisions: int
    """人工审核记录的条数（修改、确认、增删行）。"""
    document_bytes: int
    """上传的原始文件一共多大。"""
    exports: int
    export_bytes: int
    """存下来的导出文件一共多大。"""


class SystemInfo(BaseModel):
    version: str
    mode: str
    """personal（个人模式）或 team（团队模式）。"""
    model: ModelInfo
    ocr: OcrInfo
    structure: StructureInfo | None = None
    watch_dir: str | None = None
    """监视文件夹（ADR 0036）：放进去的文件自动导入；没设是 null。"""
    templates: list[TemplateInfo]
    data: DataInfo
    services: list[ServiceCheck]
