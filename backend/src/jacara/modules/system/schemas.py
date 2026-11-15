"""Public schemas of system information."""

from __future__ import annotations

from pydantic import BaseModel


class ServiceCheck(BaseModel):
    """Reachability of one service (same check as `jacara doctor`)."""

    name: str
    ok: bool
    detail: str
    hint: str = ""


class ModelInfo(BaseModel):
    server: str
    """Model server address."""
    file: str | None
    """Model file name used by `jacara start` (from models/ or JACARA_MODEL)."""
    loaded: str | None
    """Model currently loaded by the model server (queried from it; empty if unreachable)."""
    context: int | None
    """Context length (tokens)."""
    vision_file: str | None = None
    """Vision projector file (mmproj) loaded together by `jacara start`; null if none."""
    sees_images: bool = False
    """Whether the model server can currently process images (vision projector loaded)."""
    vision_mode: str = "auto"
    """When the page image is shown to the model: auto, always, off (JACARA_VISION)."""


class VisionInfo(BaseModel):
    """Whether the model can process images."""

    sees_images: bool
    mode: str
    """When the page image is shown to the model: auto, always, off (JACARA_VISION)."""
    file: str | None
    """Vision projector file (mmproj); null if none."""


class OcrInfo(BaseModel):
    engine: str
    version: str | None


class StructureInfo(BaseModel):
    """Models used for page structure analysis."""

    layout: str | None
    """Layout model; null when disabled (JACARA_LAYOUT=false)."""
    table: str | None
    """Table structure model file name; null when no model is installed (table cells are derived
    from text positions)."""


class TemplateInfo(BaseModel):
    name: str
    title: str
    version: int


class DataInfo(BaseModel):
    documents: int
    pages: int
    fields: int
    revisions: int
    """Number of manual review records (edits, confirmations, row additions and deletions)."""
    document_bytes: int
    """Total size of uploaded originals."""
    exports: int
    export_bytes: int
    """Total size of stored export files."""


class ProcessStatus(BaseModel):
    """One background process of the desktop app."""

    name: str
    """model, worker-read, worker-extract, worker-all or watch."""
    state: str
    """running, restarting, paused or stopped."""
    starts: int
    """Times started since the background services started (more than one: restarted)."""
    note: str | None
    """The last line the launcher noted for it (why it stopped, for example)."""


class DesktopStatus(BaseModel):
    data_dir: str
    log_dir: str
    processes: list[ProcessStatus]


class Diagnostics(BaseModel):
    text: str
    """Plain text to paste into a problem report: versions, system, models, processes, services;
    no document content, no names of documents, the home folder written as ~."""


class LicenseText(BaseModel):
    text: str


class SystemInfo(BaseModel):
    version: str
    mode: str
    """personal or team."""
    model: ModelInfo
    ocr: OcrInfo
    structure: StructureInfo | None = None
    watch_dir: str | None = None
    """Watch folder whose files are imported automatically; null if not set."""
    templates: list[TemplateInfo]
    data: DataInfo
    services: list[ServiceCheck]
    notice: str = ""
    """Attribution from the NOTICE file shipped with the program (Apache 2.0 section 4(d))."""
    license: str = "Apache-2.0"
    third_party: bool = False
    """The list of third-party licenses (THIRD_PARTY_LICENSES) is shipped with this build."""
    desktop: DesktopStatus | None = None
    """Desktop app only (and administrators): folders and background processes."""
