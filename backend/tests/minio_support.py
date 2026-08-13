"""Availability probe for the object storage the knowledge-base tests need.

``testcontainers.minio`` needs the ``minio`` package, which this project does
not depend on, so these tests read whatever S3 endpoint the environment points
at. When no bucket is there the failure surfaces as ``ClientError: 404
HeadBucket`` from deep inside a request handler -- 25 collection errors that
say nothing about what is missing.

The probe turns that into one honest skip naming the endpoint and bucket. It
is deliberately a real HeadBucket rather than a port check: a reachable MinIO
with no bucket fails these tests exactly like an absent one.
"""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def kb_bucket_status() -> tuple[bool, str]:
    """Report whether the configured knowledge-base bucket can be reached.

    Returns:
        ``(available, reason)``. ``reason`` names the endpoint and bucket so a
        skipped run says which dependency was missing.
    """
    try:
        import boto3
        from botocore.config import Config

        from src.modules.knowledge_base.infrastructure.config import (
            KnowledgeBaseSettings,
        )
    except Exception as exc:  # noqa: BLE001 - any import problem means "cannot probe"
        return False, f"object storage probe unavailable: {exc}"

    settings = KnowledgeBaseSettings()
    endpoint = settings.minio_endpoint
    url = endpoint if endpoint.startswith(("http://", "https://")) else f"http://{endpoint}"

    try:
        client = boto3.client(
            "s3",
            endpoint_url=url,
            aws_access_key_id=settings.minio_access_key,
            aws_secret_access_key=settings.minio_secret_key,
            config=Config(
                connect_timeout=2,
                read_timeout=2,
                retries={"max_attempts": 1},
                signature_version="s3v4",
            ),
        )
        client.head_bucket(Bucket=settings.minio_bucket)
    except Exception as exc:  # noqa: BLE001 - unreachable, unauthorized, or absent
        return False, (
            f"object storage bucket '{settings.minio_bucket}' at {url} "
            f"is not available ({type(exc).__name__}); "
            "start MinIO and create the bucket to run these tests"
        )

    return True, ""
