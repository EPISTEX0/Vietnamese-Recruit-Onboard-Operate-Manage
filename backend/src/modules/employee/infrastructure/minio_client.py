"""MinIO client adapter for employee document storage.

Provides async S3-compatible operations for uploading, downloading,
and deleting files in MinIO via aioboto3.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from io import BytesIO
from typing import Any

import aioboto3

from src.modules.employee.infrastructure.config import EmployeeSettings


class MinIOClient:
    """Async MinIO client using aioboto3 for S3-compatible operations.

    The bucket is auto-created on first upload if it doesn't exist.

    Args:
        settings: EmployeeSettings instance with MinIO connection details.
    """

    def __init__(self, settings: EmployeeSettings) -> None:
        self._settings = settings
        self._session = aioboto3.Session()
        self._endpoint_url = f"http://{settings.minio_endpoint}"

    @asynccontextmanager
    async def _client_context(self) -> AsyncIterator[Any]:
        async with self._session.client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._settings.minio_access_key,
            aws_secret_access_key=self._settings.minio_secret_key,
        ) as client:
            yield client

    async def _ensure_bucket(self, client: Any) -> None:
        """Create the bucket if it doesn't already exist."""
        try:
            await client.head_bucket(Bucket=self._settings.minio_bucket)
        except client.exceptions.ClientError:
            await client.create_bucket(Bucket=self._settings.minio_bucket)

    async def upload_file(self, path: str, file_data: bytes, content_type: str) -> str:
        """Upload a file to MinIO.

        Args:
            path: The storage path (key) within the bucket.
            file_data: Raw file bytes to upload.
            content_type: MIME type of the file.

        Returns:
            The storage path where the file was stored.
        """
        async with self._client_context() as client:
            await self._ensure_bucket(client)
            await client.upload_fileobj(
                BytesIO(file_data),
                self._settings.minio_bucket,
                path,
                ExtraArgs={"ContentType": content_type},
            )
        return path

    async def download_file(self, path: str) -> bytes:
        """Download a file from MinIO.

        Args:
            path: The storage path (key) within the bucket.

        Returns:
            The raw file bytes.
        """
        async with self._client_context() as client:
            response = await client.get_object(
                Bucket=self._settings.minio_bucket,
                Key=path,
            )
            data = await response["Body"].read()
        return bytes(data)

    async def delete_file(self, path: str) -> None:
        """Delete a file from MinIO.

        Args:
            path: The storage path (key) within the bucket.
        """
        async with self._client_context() as client:
            await client.delete_object(
                Bucket=self._settings.minio_bucket,
                Key=path,
            )
