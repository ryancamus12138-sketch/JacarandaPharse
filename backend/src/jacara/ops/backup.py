"""备份和恢复（ADR 0054）：数据库的每一张表和对象存储里的每一个文件，打进同一个 .tar。

为什么不用 pg_dump：
- 表按 SQLAlchemy 读出来写成 JSON 行，不依赖本机装哪个版本的 pg_dump；
- 测试里就能完整地备份一次、恢复一次、核对一次（SQLite + 假存储）。

一个备份包里有：
  manifest.json        格式、时间、jacara 版本、数据库版本（alembic）、每张表几行、每个文件的 sha256
  db/<表名>.jsonl      一行一条记录；UUID、时间、JSON 等类型带标记，恢复时还原
  objects/<存储里的路径>  原始文件、OCR 样本小图、OCR 快照、导出文件、头像……

恢复只往空的环境里恢复（数据库每张表都是空的、存储里没有文件），先核对包里每个文件的 sha256，
一处不对就什么都不动。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import IO, Any, Protocol

from sqlalchemy import MetaData, Table, func, select
from sqlalchemy.engine import Connection, Engine

FORMAT = "jacarandaparse.backup.v1"
VERSION_TABLE = "alembic_version"
CHUNK = 500


class BackupError(Exception):
    """备份或恢复做不下去：原因写给人看。"""


class FileStore(Protocol):
    """备份要的对象存储：除了存、取，还要能列出全部文件。"""

    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    def get(self, key: str) -> bytes: ...

    def keys(self) -> Iterable[str]: ...

    def content_type(self, key: str) -> str: ...


# ---- 值的编码：JSON 里没有 UUID、时间、二进制，带个标记存，恢复时还原 ----


def encode(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return {"$uuid": str(value)}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, time):
        return {"$time": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, bytes | bytearray | memoryview):
        return {"$bytes": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, dict | list):
        return {"$json": value}  # JSON 列：原样，外面包一层免得和标记混淆
    return value


def decode(value: Any) -> Any:
    if not isinstance(value, dict) or len(value) != 1:
        return value
    ((tag, raw),) = value.items()
    if tag == "$uuid":
        return uuid.UUID(raw)
    if tag == "$datetime":
        return datetime.fromisoformat(raw)
    if tag == "$date":
        return date.fromisoformat(raw)
    if tag == "$time":
        return time.fromisoformat(raw)
    if tag == "$decimal":
        return Decimal(raw)
    if tag == "$bytes":
        return base64.b64decode(raw)
    if tag == "$json":
        return raw
    return value


# ---- 备份 ----


@dataclass
class Manifest:
    created_at: str
    jacara_version: str
    revision: str | None
    tables: dict[str, int] = field(default_factory=dict)
    objects: dict[str, dict[str, Any]] = field(default_factory=dict)
    format: str = FORMAT

    def to_json(self) -> bytes:
        return json.dumps(self.__dict__, ensure_ascii=False, indent=2).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes) -> Manifest:
        raw = json.loads(data.decode("utf-8"))
        if raw.get("format") != FORMAT:
            raise BackupError(f"不认识这个备份包的格式：{raw.get('format')}")
        return cls(
            created_at=raw["created_at"],
            jacara_version=raw["jacara_version"],
            revision=raw.get("revision"),
            tables=dict(raw["tables"]),
            objects=dict(raw["objects"]),
        )


def _tables(conn: Connection) -> list[Table]:
    """数据库里的表，按外键先后排好（被引用的在前）。表结构从数据库读，不看代码。"""
    meta = MetaData()
    meta.reflect(bind=conn)
    return [t for t in meta.sorted_tables if t.name != VERSION_TABLE]


def revision_of(conn: Connection) -> str | None:
    meta = MetaData()
    meta.reflect(bind=conn, only=lambda name, _: name == VERSION_TABLE)
    if VERSION_TABLE not in meta.tables:
        return None
    return conn.execute(select(meta.tables[VERSION_TABLE].c.version_num)).scalar()


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(datetime.now(UTC).timestamp())
    tar.addfile(info, io.BytesIO(data))


def _rows(conn: Connection, table: Table) -> Iterator[bytes]:
    order = list(table.primary_key.columns) or list(table.columns)
    result = conn.execution_options(stream_results=True).execute(select(table).order_by(*order))
    for row in result.mappings():
        line = {k: encode(v) for k, v in row.items()}
        yield json.dumps(line, ensure_ascii=False).encode("utf-8") + b"\n"


def backup(
    engine: Engine,
    storage: FileStore,
    out_dir: Path,
    version: str,
    progress: Callable[[str], None] = lambda _: None,
) -> Path:
    """备份到 out_dir/jacara-<时间>.tar，返回路径。

    先写 .partial，写完才改名：半截的包不会被当成好的。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    final = out_dir / f"jacara-{stamp}.tar"
    partial = final.with_suffix(".tar.partial")
    # PostgreSQL：整个备份在同一个快照里读，读的过程中就算有人写，各张表也对得上
    snapshot = {"isolation_level": "REPEATABLE READ"} if engine.dialect.name == "postgresql" else {}
    with engine.connect().execution_options(**snapshot) as conn, tarfile.open(partial, "w") as tar:
        manifest = Manifest(
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            jacara_version=version,
            revision=revision_of(conn),
        )
        for table in _tables(conn):
            data = b"".join(_rows(conn, table))
            manifest.tables[table.name] = data.count(b"\n")
            _add(tar, f"db/{table.name}.jsonl", data)
            progress(f"表 {table.name}：{manifest.tables[table.name]} 行")
        keys = sorted(storage.keys())
        for n, key in enumerate(keys, start=1):
            data = storage.get(key)
            manifest.objects[key] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "content_type": storage.content_type(key),
            }
            _add(tar, f"objects/{key}", data)
            if n % 200 == 0 or n == len(keys):
                progress(f"文件 {n}/{len(keys)}")
        _add(tar, "manifest.json", manifest.to_json())
    partial.rename(final)
    return final


# ---- 核对备份包 ----


def read_manifest(archive: Path) -> Manifest:
    with tarfile.open(archive, "r") as tar:
        member = tar.extractfile("manifest.json")
        if member is None:
            raise BackupError("备份包里没有 manifest.json")
        return Manifest.from_json(member.read())


def check_archive(archive: Path) -> Manifest:
    """恢复之前先核对：每张表的文件都在、行数对；每个文件都在、sha256 对。不对就报错。"""
    if not archive.exists():
        raise BackupError(f"找不到备份包：{archive}")
    try:
        manifest = read_manifest(archive)
    except (tarfile.TarError, KeyError) as err:
        raise BackupError(f"备份包打不开或不完整：{err}") from err
    problems: list[str] = []
    with tarfile.open(archive, "r") as tar:
        names = set(tar.getnames())
        for table, rows in manifest.tables.items():
            name = f"db/{table}.jsonl"
            member = tar.extractfile(name) if name in names else None
            if member is None:
                problems.append(f"少了表 {table}")
            elif member.read().count(b"\n") != rows:
                problems.append(f"表 {table} 行数不对")
        for key, info in manifest.objects.items():
            name = f"objects/{key}"
            member = tar.extractfile(name) if name in names else None
            if member is None:
                problems.append(f"少了文件 {key}")
            elif hashlib.sha256(member.read()).hexdigest() != info["sha256"]:
                problems.append(f"文件 {key} 内容和备份时不一样")
    if problems:
        shown = "；".join(problems[:5]) + (f"……共 {len(problems)} 处" if len(problems) > 5 else "")
        raise BackupError(f"备份包有问题，没有恢复：{shown}")
    return manifest


# ---- 恢复 ----


def _member(tar: tarfile.TarFile, name: str) -> IO[bytes]:
    member = tar.extractfile(name)
    if member is None:
        raise BackupError(f"备份包里少了 {name}")
    return member


def is_empty(conn: Connection, storage: FileStore) -> tuple[bool, str]:
    """要恢复到的地方是不是空的：每张表都没有数据，存储里也没有文件。"""
    for table in _tables(conn):
        if conn.execute(select(func.count()).select_from(table)).scalar():
            return False, f"数据库里已经有数据（表 {table.name}）"
    if next(iter(storage.keys()), None) is not None:
        return False, "对象存储里已经有文件"
    return True, ""


def restore(
    archive: Path,
    engine: Engine,
    storage: FileStore,
    migrate: Callable[[str | None], None],
    progress: Callable[[str], None] = lambda _: None,
) -> Manifest:
    """把备份包恢复到空的环境里。

    migrate(revision) 把数据库结构变成备份时的版本（命令行用 alembic；测试里什么都不做）。
    数据在一个事务里写完：中途出错数据库回到空的。文件最后放，放之前核对过 sha256。
    """
    manifest = check_archive(archive)
    with engine.connect() as conn:
        empty, reason = is_empty(conn, storage)
    if not empty:
        raise BackupError(f"只能恢复到空的环境：{reason}")
    migrate(manifest.revision)
    with engine.begin() as conn, tarfile.open(archive, "r") as tar:
        tables = {t.name: t for t in _tables(conn)}
        missing = [name for name in manifest.tables if name not in tables]
        if missing:
            raise BackupError(
                f"数据库里没有这几张表：{'、'.join(missing)}（数据库版本和备份时不一样）"
            )
        for name, table in tables.items():
            if name not in manifest.tables:
                continue
            member = _member(tar, f"db/{name}.jsonl")
            batch: list[dict[str, Any]] = []
            for line in member:
                row = json.loads(line)
                batch.append({k: decode(v) for k, v in row.items() if k in table.c})
                if len(batch) >= CHUNK:
                    conn.execute(table.insert(), batch)
                    batch = []
            if batch:
                conn.execute(table.insert(), batch)
            progress(f"表 {name}：{manifest.tables[name]} 行")
        for name, rows in manifest.tables.items():
            got = conn.execute(select(func.count()).select_from(tables[name])).scalar()
            if got != rows:
                raise BackupError(f"表 {name} 恢复后是 {got} 行，备份里是 {rows} 行")
    keys = list(manifest.objects)
    with tarfile.open(archive, "r") as tar:
        for n, key in enumerate(keys, start=1):
            data = _member(tar, f"objects/{key}").read()
            kind = manifest.objects[key].get("content_type") or "application/octet-stream"
            storage.put(key, data, kind)
            if n % 200 == 0 or n == len(keys):
                progress(f"文件 {n}/{len(keys)}")
    return manifest
