"""Object storage for uploaded originals (images, PDFs).

Application code depends only on the ObjectStorage interface: S3Storage (SeaweedFS) in production
and development, an in-memory fake in tests."""

from __future__ import annotations

from functools import lru_cache
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


@lru_cache
def get_storage() -> ObjectStorage:
    return S3Storage(get_settings())
