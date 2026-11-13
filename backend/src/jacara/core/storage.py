"""对象存储：保存上传的原始文件（图片、PDF）。

业务代码只认 ObjectStorage 接口：生产和开发用 S3Storage（连 SeaweedFS），测试用内存里的假存储。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol

from jacara.core.config import Settings, get_settings


class StorageError(Exception):
    """对象存储连不上或出错。API 会把它变成 503（服务暂时不可用）。"""


class ObjectStorage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None:
        """存一个文件。key 是它在存储里的"路径"，例如 documents/<id>/receipt.jpg。"""
        ...

    def get(self, key: str) -> bytes:
        """取回一个文件。"""
        ...

    def delete(self, key: str) -> None:
        """删掉一个文件（删除文档时）。文件本来就不在也不算错。"""
        ...


class S3Storage:
    """通过 S3 接口访问对象存储（SeaweedFS、MinIO、AWS S3 都可以）。"""

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
        """存储桶不存在就创建。只在第一次存文件时检查一次。"""
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
            raise StorageError(f"保存文件失败：{err}") from err

    def get(self, key: str) -> bytes:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except (BotoCoreError, ClientError) as err:
            raise StorageError(f"读取文件失败：{err}") from err

    def delete(self, key: str) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError) as err:
            raise StorageError(f"删除文件失败：{err}") from err

    def keys(self) -> list[str]:
        """存储桶里全部文件的路径（备份用，ADR 0054）。桶还没建就是空的。"""
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
            raise StorageError(f"列出文件失败：{err}") from err

    def content_type(self, key: str) -> str:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            head = self._client.head_object(Bucket=self.bucket, Key=key)
            return str(head.get("ContentType") or "application/octet-stream")
        except (BotoCoreError, ClientError) as err:
            raise StorageError(f"读取文件信息失败：{err}") from err


@lru_cache
def get_storage() -> ObjectStorage:
    return S3Storage(get_settings())
