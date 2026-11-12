"""监视文件夹（ADR 0036）：放进这个文件夹（含子文件夹）的图片和 PDF 自动导入。

- 不动、不删、不改你的文件，只读；
- 同一个文件（内容一样）只导入一次：重启之后再扫一遍，已经导入过的按文件指纹跳过；
- 刚放进去、还在复制的文件先不读：最后修改时间离现在不到几秒的，等下一轮；
- 导入时记下所在的文件夹（监视的文件夹名 + 子文件夹），文档列表可以按文件夹筛选。
"""

from __future__ import annotations

import logging
import mimetypes
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from jacara.core.db import session_scope
from jacara.core.owner import Owner
from jacara.core.queue import TaskQueue
from jacara.core.storage import ObjectStorage

log = logging.getLogger(__name__)

SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".pdf"}
SETTLE_SECONDS = 3.0
"""文件最后一次修改后至少过这么久才读（还在复制的文件不读一半）。"""


@dataclass
class Watcher:
    """扫一个文件夹，把新文件导入。scan() 扫一次；run() 一直扫。"""

    root: Path
    factory: sessionmaker[Session] | None
    storage: ObjectStorage
    queue: TaskQueue
    owner: Owner
    template: str | None = None
    """用哪个模板（我的模板的 id）；不写就用上一次用的。"""
    split: str = "auto"
    seen: dict[Path, tuple[float, int]] = field(default_factory=dict)
    """处理过的文件 → （修改时间，大小）。文件改了（时间或大小变了）会再看一次。"""
    now: Callable[[], float] = time.time

    def scan(self) -> list[tuple[Path, str]]:
        """扫一次。返回 [(文件, "导入" / "以前导入过" / 失败原因)]。"""
        from jacara.modules.documents.service import DocumentError, DocumentService

        done: list[tuple[Path, str]] = []
        for path in sorted(self.root.rglob("*")):
            if not self._wanted(path):
                continue
            stat = path.stat()
            key = (stat.st_mtime, stat.st_size)
            if self.seen.get(path) == key or self.now() - stat.st_mtime < SETTLE_SECONDS:
                continue
            relative = path.relative_to(self.root).parent
            folder = "/".join([self.root.name, *relative.parts])
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            try:
                with session_scope(self.factory) as session:
                    service = DocumentService(session, self.storage, self.queue, self.owner, None)
                    out = service.upload(
                        path.name,
                        content_type,
                        path.read_bytes(),
                        self.template,
                        folder=folder,
                        split=self.split,
                        skip_duplicate=True,
                    )
                result = "以前导入过" if out.duplicate_skipped else "导入"
            except (DocumentError, OSError) as err:
                result = getattr(err, "message", str(err))
            self.seen[path] = key
            done.append((path, result))
            log.info("%s：%s", result, path)
        return done

    def _wanted(self, path: Path) -> bool:
        if not path.is_file() or path.suffix.lower() not in SUFFIXES:
            return False
        # 隐藏文件（.DS_Store、~$ 开头的临时文件）不要
        parts = path.relative_to(self.root).parts
        return not any(p.startswith((".", "~$")) for p in parts)

    def run(self, every: float = 5.0) -> None:
        """一直扫，每隔 every 秒一次。Ctrl + C 停止。"""
        while True:
            for path, result in self.scan():
                print(f"{result}：{path.relative_to(self.root)}")
            time.sleep(every)
