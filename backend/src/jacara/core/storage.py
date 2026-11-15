"""Object storage for uploaded originals (images, PDFs).

Application code depends only on the ObjectStorage interface: S3Storage (SeaweedFS) for the server
deployment, LocalStorage (files/ in the data directory) for the desktop edition, an in-memory fake
in tests. JACARA_STORAGE selects between the first two."""

from __future__ import annotations

import mimetypes
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from jacara.core.config import Settings, get_settings
from jacara.core.i18n import tr


class StorageError(Exception):
    """Object storage is unreachable or returned an error. The API maps it to 503 (service
    unavailable)."""


class ObjectStorage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None:
        """Store an object. key is its path in the store, for example documents/<id>/receipt.jpg."""
        ...

    def get(self, key: str) -> bytes:
        """Retrieve an object."""
        ...

    def delete(self, key: str) -> None:
        """Delete an object (when a document is deleted). A missing object is not an error."""
        ...

    def keys(self) -> list[str]:
        """Keys of all stored objects (for backups)."""
        ...

    def content_type(self, key: str) -> str:
        """Media type recorded for an object (for backups)."""
        ...


class S3Storage:
    """Object storage over the S3 API (SeaweedFS, MinIO and AWS S3 all work)."""

    def __init__(self, settings: Settings) -> None:
        import boto3

        self.bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
            region_name="us-east-1",
        )
        self._bucket_ready = False

    def _ensure_bucket(self) -> None:
        """Create the bucket if it does not exist. Checked once, on the first write."""
        if self._bucket_ready:
            return
        existing = [b["Name"] for b in self._client.list_buckets().get("Buckets", [])]
        if self.bucket not in existing:
            self._client.create_bucket(Bucket=self.bucket)
        self._bucket_ready = True

    def put(self, key: str, data: bytes, content_type: str) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._ensure_bucket()
            self._client.put_object(
                Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
            )
        except (BotoCoreError, ClientError) as err:
            raise StorageError(tr("保存文件失败：{err}", err=err)) from err

    def get(self, key: str) -> bytes:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except (BotoCoreError, ClientError) as err:
            raise StorageError(tr("读取文件失败：{err}", err=err)) from err

    def delete(self, key: str) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError) as err:
            raise StorageError(tr("删除文件失败：{err}", err=err)) from err

    def keys(self) -> list[str]:
        """Keys of all objects in the bucket (for backups). Empty if the bucket does not exist
        yet."""
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            existing = [b["Name"] for b in self._client.list_buckets().get("Buckets", [])]
            if self.bucket not in existing:
                return []
            found: list[str] = []
            for page in self._client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket):
                found += [item["Key"] for item in page.get("Contents", [])]
            return found
        except (BotoCoreError, ClientError) as err:
            raise StorageError(tr("列出文件失败：{err}", err=err)) from err

    def content_type(self, key: str) -> str:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            head = self._client.head_object(Bucket=self.bucket, Key=key)
            return str(head.get("ContentType") or "application/octet-stream")
        except (BotoCoreError, ClientError) as err:
            raise StorageError(tr("读取文件信息失败：{err}", err=err)) from err


class LocalStorage:
    """Object storage in a local folder: key a/b/c.jpg is the file <root>/a/b/c.jpg.

    - Keys containing "..", absolute paths or backslashes are rejected, so no key can reach outside
      the folder.
    - Writes go to a temporary file in the same folder first and are then moved into place with
      os.replace, which is atomic on the same file system (Python documentation); an interrupted
      write never leaves a half-written file under the real name.
    - The media type is derived from the file extension (mimetypes). The type of each document is
      also stored in the database (documents.content_type); the storage type is only used by
      backups."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        parts = key.split("/")
        if (
            not key
            or "\\" in key
            or key.startswith("/")
            or any(part in ("", ".", "..") for part in parts)
            or ":" in parts[0]
        ):
            raise StorageError(tr("文件路径无效：{key}", key=key))
        return self.root.joinpath(*parts)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=".", suffix=TEMPORARY, delete=False
            ) as handle:
                handle.write(data)
                temporary = Path(handle.name)
            os.replace(temporary, path)
        except OSError as err:
            raise StorageError(tr("保存文件失败：{err}", err=err)) from err

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except OSError as err:
            raise StorageError(tr("读取文件失败：{err}", err=err)) from err

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError as err:
            raise StorageError(tr("删除文件失败：{err}", err=err)) from err

    def keys(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file() and not path.name.endswith(TEMPORARY)
        )

    def content_type(self, key: str) -> str:
        return mimetypes.guess_type(self._path(key).name)[0] or "application/octet-stream"


TEMPORARY = ".jacara-tmp"
"""Suffix of files being written; never reported as stored objects."""

LOCAL_FILES = "files"
"""Folder of LocalStorage in the data directory."""


@lru_cache
def get_storage() -> ObjectStorage:
    settings = get_settings()
    if settings.jacara_storage == "local":
        from jacara.core.config import data_home

        return LocalStorage(data_home() / LOCAL_FILES)
    return S3Storage(settings)
