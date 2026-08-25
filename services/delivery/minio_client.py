from __future__ import annotations

import io
import logging
import zipfile
from typing import Optional

logger = logging.getLogger(__name__)


class MinIOClient:
    """Async-compatible MinIO/S3 upload client (boto3 via run_in_executor)."""

    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str) -> None:
        self.endpoint   = endpoint.rstrip("/")
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket     = bucket

    async def upload_bundle(self, key: str, files: dict[str, str]) -> str:
        """Zip all files and upload to MinIO. Returns object URL."""
        import asyncio
        data = self._create_zip(files)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._upload_sync, key, data)
        return f"{self.endpoint}/{self.bucket}/{key}"

    def _create_zip(self, files: dict[str, str]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, content in files.items():
                zf.writestr(path, content)
        return buf.getvalue()

    def _upload_sync(self, key: str, data: bytes) -> None:
        import boto3
        from botocore.config import Config
        s3 = boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=Config(signature_version="s3v4"),
        )
        s3.put_object(Bucket=self.bucket, Key=key, Body=data)


def build_minio_client() -> Optional[MinIOClient]:
    """Build from MINIO_ENDPOINT/ACCESS_KEY/SECRET_KEY env vars, or None."""
    import os
    endpoint   = os.environ.get("MINIO_ENDPOINT",   "").strip()
    access_key = os.environ.get("MINIO_ACCESS_KEY", "").strip()
    secret_key = os.environ.get("MINIO_SECRET_KEY", "").strip()
    bucket     = os.environ.get("MINIO_BUCKET", "vibeforge-artifacts").strip()
    if endpoint and access_key and secret_key:
        logger.info("[MinIOClient] configured — %s / %s", endpoint, bucket)
        return MinIOClient(endpoint, access_key, secret_key, bucket)
    logger.debug("[MinIOClient] not configured — stub keys will be used")
    return None