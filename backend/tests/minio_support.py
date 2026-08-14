"""One place to construct the object storage the knowledge-base tests need.

The knowledge-base endpoints upload, replace and delete files, so PostgreSQL
alone is not enough: without a reachable bucket every request handler dies on
``ClientError: 404 HeadBucket``. These tests used to read whatever S3 endpoint
the ambient environment pointed at and skip when nothing answered, which meant
25 tests guarded nothing on a normal checkout.

This module starts MinIO the same way ``tests.postgres_support`` starts
PostgreSQL -- from the test process, through testcontainers -- so the suite
brings its own infrastructure instead of inheriting a developer's
``docker compose`` stack.

*Client.* The product talks to object storage through ``aioboto3``
(``src/modules/employee/infrastructure/minio_client.py``), which pulls in
``boto3`` via ``aiobotocore[boto3]``. ``testcontainers.minio`` would instead
require the separate ``minio`` SDK, so the container is built from
testcontainers' generic ``DockerContainer`` and driven with the same boto3
stack the product already depends on. No second S3 SDK enters the project.

*Credentials.* Pinned for the same reason ``tests.postgres_support`` pins
them: the backend calls ``load_dotenv()`` at import time, so any ``MINIO_*``
value in the repo-root ``.env`` would otherwise leak into fixtures depending
on import order. MinIO refuses a root password shorter than eight characters,
hence the padding.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

# Pinned rather than ``latest`` so a MinIO release cannot silently change what
# CI tests. This is the digest behind ``minio/minio:latest`` at the time of
# writing, which is the tag the deployment compose file runs.
MINIO_IMAGE = "minio/minio:RELEASE.2025-09-07T16-13-09Z"

# Deliberately boring. MinIO rejects a root password under 8 characters.
MINIO_ACCESS_KEY = "testminio"
MINIO_SECRET_KEY = "testminio"
MINIO_BUCKET = "test-knowledge-base"

MINIO_PORT = 9000

# MinIO answers on the S3 port before it is ready to serve; the poll below
# retries until it does. Generous because CI may be pulling the image.
_READY_TIMEOUT_SECONDS = 60.0
_POLL_INTERVAL_SECONDS = 0.25


def make_minio_container(image: str = MINIO_IMAGE) -> Any:
    """Build an unstarted MinIO ``DockerContainer`` with pinned credentials.

    Args:
        image: Container image to run.

    Returns:
        An unstarted ``DockerContainer`` ready for use as a context manager.
    """
    import pytest

    container_module = pytest.importorskip("testcontainers.core.container")

    return (
        container_module.DockerContainer(image)
        .with_env("MINIO_ROOT_USER", MINIO_ACCESS_KEY)
        .with_env("MINIO_ROOT_PASSWORD", MINIO_SECRET_KEY)
        .with_exposed_ports(MINIO_PORT)
        .with_command('server /data --console-address ":9001"')
    )


def make_s3_client(endpoint: str) -> Any:
    """Build a synchronous boto3 S3 client for ``endpoint``.

    The same credentials and signature version the product's aioboto3 client
    uses, so a bucket this client can see is a bucket the app can see.

    Args:
        endpoint: ``host:port`` of the MinIO server, without a scheme.

    Returns:
        A configured ``boto3`` S3 client.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=f"http://{endpoint}",
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        config=Config(
            connect_timeout=2,
            read_timeout=2,
            retries={"max_attempts": 1},
            signature_version="s3v4",
        ),
    )


def wait_for_bucket(endpoint: str, bucket: str = MINIO_BUCKET) -> None:
    """Block until MinIO serves S3 at ``endpoint``, then ensure ``bucket`` exists.

    Polls a real ``list_buckets`` rather than the port: MinIO accepts TCP
    connections before it is ready to answer S3 calls, and a port check would
    hand the tests a server that 503s on the first upload.

    Args:
        endpoint: ``host:port`` of the MinIO server, without a scheme.
        bucket: Bucket to create if it is not already there.

    Raises:
        TimeoutError: If MinIO does not answer within the readiness budget.
    """
    client = make_s3_client(endpoint)

    deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            client.list_buckets()
        except Exception as exc:  # noqa: BLE001 - not ready yet, or not up yet
            last_error = exc
            time.sleep(_POLL_INTERVAL_SECONDS)
            continue
        break
    else:  # pragma: no cover - only on a genuinely broken host
        raise TimeoutError(f"MinIO at {endpoint} did not become ready: {last_error}")

    try:
        client.head_bucket(Bucket=bucket)
    except Exception:  # noqa: BLE001 - absent bucket is the expected case
        client.create_bucket(Bucket=bucket)


def start_kb_object_storage() -> Iterator[str]:
    """Run MinIO for the knowledge base and point ``KB_*`` settings at it.

    Sets the ``KB_MINIO_*`` environment variables and drops the container's
    cached settings and client, so a ``KnowledgeBaseSettings()`` built after
    this call reaches the test container rather than whatever endpoint
    ``.env`` names.  The previous values are restored on teardown.

    Yields:
        The ``host:port`` endpoint of the running MinIO server.
    """
    import os

    with make_minio_container() as container:
        endpoint = f"{container.get_container_host_ip()}:{container.get_exposed_port(MINIO_PORT)}"
        wait_for_bucket(endpoint)

        overrides = {
            "KB_MINIO_ENDPOINT": endpoint,
            "KB_MINIO_ACCESS_KEY": MINIO_ACCESS_KEY,
            "KB_MINIO_SECRET_KEY": MINIO_SECRET_KEY,
            "KB_MINIO_BUCKET": MINIO_BUCKET,
        }
        previous = {name: os.environ.get(name) for name in overrides}
        os.environ.update(overrides)
        _clear_kb_container_caches()

        try:
            yield endpoint
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            _clear_kb_container_caches()


def _clear_kb_container_caches() -> None:
    """Drop the cached settings and MinIO client built from the old endpoint."""
    from tests.container_cache_support import reset_cached_app_containers

    reset_cached_app_containers()
