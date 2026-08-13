"""Vroom Embedding Service.

A thin FastAPI proxy in front of a remote OpenAI-compatible ``/embeddings``
endpoint. It exposes ``POST /embed``, which accepts a list of texts and
returns one embedding vector per text, plus ``GET /health`` for the Docker
healthcheck.

The service used to host ``AITeamVN/Vietnamese_Embedding_v2`` in-process via
sentence-transformers; it now forwards to whichever endpoint the operator
configures, matching how the LLM adapters already work (see
``docs/adr/0012-operator-configured-embedding-endpoint.md``). That endpoint
may be a cloud API or one running inside the operator's own network. The
external contract is unchanged, so the backend and kb-worker call it
exactly as before.

The vectors land in a hard-coded ``Vector(1024)`` pgvector column, so a
provider that returns a different width would silently corrupt the index.
The service therefore probes the provider at startup and refuses to serve
traffic unless the width matches ``EMBEDDING_DIMENSIONS``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_DIMENSIONS = 1024

# Width of the pgvector column the vectors are written into, fixed by
# backend/alembic/versions/078_create_knowledge_base_tables.py. Providers that
# honour an OpenAI-style ``dimensions`` request will happily return whatever
# width they are asked for, so agreeing with the provider is not enough — the
# configured width also has to agree with the schema on the other end.
PGVECTOR_COLUMN_DIMENSIONS = 1024

# The provider rejects batches larger than 10, but the backend hands us a
# whole document's chunks in one call, so we split before going upstream.
DEFAULT_BATCH_SIZE = 10

# Statuses worth a second attempt: rate limits and transient server faults.
# A 401/403/400 means the request itself is wrong and will never succeed.
RETRYABLE_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

# This service ships no default endpoint and no default model on purpose: it
# hosts no model, and picking a vendor on an operator's behalf is not ours to
# do. Those are therefore the two config errors every new deployment can hit,
# so the message has to be enough to fix them without opening the source —
# and it reports them together, so nobody fixes one only to be stopped by the
# other on the next run.
CONFIG_ERROR_INTRO = (
    "vroom-embedding hosts no embedding model itself — it forwards to an\n"
    "OpenAI-compatible `/embeddings` endpoint that you choose. This repo picks\n"
    "no vendor on your behalf, so the settings below have no defaults."
)

MISSING_BASE_URL_PROBLEM = """EMBEDDING_API_BASE_URL is required but is empty.
    Set it to your endpoint's base URL: everything before `/embeddings`,
    normally ending in `/v1`."""

MISSING_MODEL_NAME_PROBLEM = """EMBEDDING_MODEL_NAME is required but is empty.
    Set it to the model name exactly as your endpoint spells it. Left unset,
    a wrong model surfaces later as a confusing upstream 502 ("model not
    found") — a configuration mistake disguised as a network fault."""

CONFIG_ERROR_EXAMPLES = """Two shapes that work:

  An endpoint inside your own network (vLLM, TEI, LocalAI, ...), which
  usually needs no credentials — keeps document text on your infrastructure:

      EMBEDDING_API_BASE_URL=http://vllm:8000/v1
      EMBEDDING_MODEL_NAME=BAAI/bge-m3
      EMBEDDING_API_KEY=

  A hosted OpenAI-compatible API, which does need a key:

      EMBEDDING_API_BASE_URL=https://api.your-provider.example/v1
      EMBEDDING_MODEL_NAME=text-embedding-3-small
      EMBEDDING_API_KEY=sk-...

EMBEDDING_API_KEY may stay empty — only the base URL and the model name are
mandatory. Whichever endpoint you pick must return vectors of
EMBEDDING_DIMENSIONS width (default 1024) to match the pgvector column.
See .env.example for every EMBEDDING_* setting."""


@dataclass(frozen=True)
class Settings:
    """Runtime configuration, all sourced from the environment."""

    base_url: str
    api_key: str
    model_name: str
    dimensions: int = DEFAULT_DIMENSIONS
    # Defaults are sized so the worst-case retry budget (attempts * timeout +
    # backoff) stays under the 30s that retrieval_service.py allows us. A
    # slower budget would just mean the caller gives up before we answer.
    timeout: float = 12.0
    max_retries: int = 1
    batch_size: int = DEFAULT_BATCH_SIZE
    retry_backoff: float = 0.5
    # Some OpenAI-compatible servers (notably vLLM with a non-Matryoshka
    # model) reject an explicit ``dimensions`` field with a 400.
    send_dimensions: bool = True

    @property
    def embeddings_url(self) -> str:
        """Full upstream URL, tolerant of a trailing slash on the base URL."""
        return f"{self.base_url.rstrip('/')}/embeddings"

    def validate(self) -> Settings:
        """Fail fast on missing or nonsensical configuration.

        Every problem is collected and reported in one message rather than
        raised on the first hit, so an operator fixing a fresh deployment sees
        the whole list at once instead of discovering it one restart at a time.

        Returns:
            Self, so callers can chain onto construction.

        Raises:
            ValueError: If any required setting is missing or out of range.
        """
        problems: list[str] = []

        if not self.base_url:
            problems.append(MISSING_BASE_URL_PROBLEM)
        if not self.model_name:
            problems.append(MISSING_MODEL_NAME_PROBLEM)
        # EMBEDDING_API_KEY is deliberately absent here: endpoints run inside
        # the operator's own network (vLLM, TEI, LocalAI) usually take no key.
        if self.dimensions <= 0:
            problems.append(f"EMBEDDING_DIMENSIONS must be positive, got {self.dimensions}.")
        if self.batch_size <= 0:
            problems.append(f"EMBEDDING_BATCH_SIZE must be positive, got {self.batch_size}.")
        if self.max_retries < 0:
            problems.append(
                f"EMBEDDING_MAX_RETRIES cannot be negative, got {self.max_retries}."
            )

        if not problems:
            return self

        numbered = "\n\n".join(f"({n}) {p}" for n, p in enumerate(problems, start=1))
        sections = [CONFIG_ERROR_INTRO, numbered]
        # The endpoint examples only help when an endpoint setting is what is
        # missing; appending them to a bad-integer error would just be noise.
        if not self.base_url or not self.model_name:
            sections.append(CONFIG_ERROR_EXAMPLES)
        raise ValueError("\n\n".join(sections))


def settings_from_env() -> Settings:
    """Read Settings from environment variables, applying defaults."""
    return Settings(
        base_url=os.environ.get("EMBEDDING_API_BASE_URL", ""),
        api_key=os.environ.get("EMBEDDING_API_KEY", ""),
        model_name=os.environ.get("EMBEDDING_MODEL_NAME", ""),
        dimensions=int(os.environ.get("EMBEDDING_DIMENSIONS") or DEFAULT_DIMENSIONS),
        timeout=float(os.environ.get("EMBEDDING_TIMEOUT") or Settings.timeout),
        max_retries=int(
            os.environ.get("EMBEDDING_MAX_RETRIES") or Settings.max_retries
        ),
        batch_size=int(os.environ.get("EMBEDDING_BATCH_SIZE") or DEFAULT_BATCH_SIZE),
        retry_backoff=float(os.environ.get("EMBEDDING_RETRY_BACKOFF") or 0.5),
        send_dimensions=(os.environ.get("EMBEDDING_SEND_DIMENSIONS", "true").lower()
                         not in {"false", "0", "no"}),
    )


class UpstreamError(Exception):
    """An embedding call failed, carrying the status we should report onward.

    Attributes:
        status_code: The HTTP status this service should return to its caller.
        detail: A caller-safe description, with the API key already redacted.
        retryable: Whether another attempt could plausibly succeed. This is
            tracked explicitly rather than derived from ``status_code``,
            because the outbound status is lossy: a 401 and a 503 both leave
            here as 502, yet only one of them is worth retrying.
    """

    def __init__(self, status_code: int, detail: str, *, retryable: bool = False) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.retryable = retryable


class EmbedRequest(BaseModel):
    """Request body for the /embed endpoint.

    Attributes:
        texts: List of input texts to embed. Each text should be a natural
            language string (typically Vietnamese).
    """

    texts: list[str] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="List of texts to embed (1-100 items per request)",
    )


class EmbedResponse(BaseModel):
    """Response from the /embed endpoint.

    Attributes:
        embeddings: One embedding vector per input text, in the same order
            as the request. Each vector has ``EMBEDDING_DIMENSIONS`` floats.
    """

    embeddings: list[list[float]]


class EmbeddingClient:
    """Calls the remote OpenAI-compatible embeddings endpoint.

    Handles batching, retries, and translating every upstream failure mode
    into an :class:`UpstreamError` with a meaningful status code.
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client

    def _redact(self, message: str) -> str:
        """Strip the API key out of anything we are about to return or log."""
        if self._settings.api_key:
            return message.replace(self._settings.api_key, "***")
        return message

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed every text, splitting into provider-sized batches.

        Args:
            texts: Texts to embed, in the order the caller expects back.

        Returns:
            One vector per input text, in input order.

        Raises:
            UpstreamError: If any batch fails or returns an unusable shape.
        """
        vectors: list[list[float]] = []
        batch_size = self._settings.batch_size
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            vectors.extend(await self._embed_batch(batch))
        return vectors

    async def _embed_batch(self, batch: Sequence[str]) -> list[list[float]]:
        """Embed a single provider-sized batch, retrying transient failures."""
        attempts = self._settings.max_retries + 1
        last_error: UpstreamError | None = None

        for attempt in range(attempts):
            try:
                response = await self._post(batch)
            except UpstreamError as exc:
                last_error = exc
                if not exc.retryable or attempt == attempts - 1:
                    raise
                logger.warning(
                    "Embedding attempt %d/%d failed (%d), retrying: %s",
                    attempt + 1,
                    attempts,
                    exc.status_code,
                    exc.detail,
                )
                await asyncio.sleep(self._settings.retry_backoff * (2**attempt))
                continue
            # Parsing sits outside the retry guard on purpose: a malformed or
            # wrong-width body is deterministic, so retrying only adds latency.
            return self._parse(response, expected=len(batch))

        # Unreachable: the loop either returns or raises.
        raise last_error or UpstreamError(502, "Embedding failed with no recorded cause")

    async def _post(self, batch: Sequence[str]) -> httpx.Response:
        """POST one batch upstream, mapping transport faults to UpstreamError."""
        payload: dict[str, object] = {
            "model": self._settings.model_name,
            "input": list(batch),
        }
        if self._settings.send_dimensions:
            payload["dimensions"] = self._settings.dimensions

        headers = {}
        if self._settings.api_key:
            headers["Authorization"] = f"Bearer {self._settings.api_key}"

        try:
            response = await self._client.post(
                self._settings.embeddings_url,
                json=payload,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise UpstreamError(
                504,
                f"Embedding provider timed out: {self._redact(str(exc))}",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(
                502,
                f"Could not reach embedding provider: {self._redact(str(exc))}",
                retryable=True,
            ) from exc

        if response.status_code >= 400:
            # Pass 429 through so callers can back off; everything else is a
            # provider or configuration fault, which is a bad gateway to them.
            status = 429 if response.status_code == 429 else 502
            raise UpstreamError(
                status,
                f"Embedding provider returned {response.status_code}: "
                f"{self._redact(response.text[:500])}",
                retryable=response.status_code in RETRYABLE_STATUSES,
            )
        return response

    def _parse(self, response: httpx.Response, expected: int) -> list[list[float]]:
        """Validate the provider's response shape and extract ordered vectors.

        Args:
            response: A 2xx response from the provider.
            expected: How many vectors this batch must contain.

        Returns:
            Vectors sorted back into request order.

        Raises:
            UpstreamError: If the body is unparseable, the wrong length, or
                carries vectors of the wrong width.
        """
        try:
            items = response.json()["data"]
            ordered = sorted(items, key=lambda item: item["index"])
            vectors = [list(item["embedding"]) for item in ordered]
        except (ValueError, KeyError, TypeError) as exc:
            raise UpstreamError(
                502, f"Embedding provider returned an unreadable response: {exc}"
            ) from exc

        if len(vectors) != expected:
            raise UpstreamError(
                502,
                f"Embedding provider returned {len(vectors)} vectors for {expected} texts",
            )

        dimensions = self._settings.dimensions
        for vector in vectors:
            if len(vector) != dimensions:
                raise UpstreamError(
                    502,
                    f"Embedding provider returned a {len(vector)}-dimensional vector "
                    f"but this service is configured for {dimensions}",
                )
        return vectors


async def verify_provider(client: EmbeddingClient, dimensions: int) -> None:
    """Probe the provider once at startup and refuse to run if it disagrees.

    The pgvector column is a fixed ``Vector(1024)``. Serving traffic against a
    provider that returns a different width would poison the index, so a
    mismatch has to kill the process rather than surface per request.

    Args:
        client: The configured embedding client.
        dimensions: The width this service is configured to produce.

    Raises:
        RuntimeError: If the provider is unreachable or returns another width.
    """
    if dimensions != PGVECTOR_COLUMN_DIMENSIONS:
        # Checked before the probe: providers that honour the requested width
        # will happily agree with a wrong setting, so the probe cannot catch
        # this on its own, and there is no point spending a call to find out.
        raise RuntimeError(
            f"EMBEDDING_DIMENSIONS is {dimensions} but the knowledge base chunk "
            f"tables store Vector({PGVECTOR_COLUMN_DIMENSIONS}). Every insert "
            f"would fail at the database. Set EMBEDDING_DIMENSIONS="
            f"{PGVECTOR_COLUMN_DIMENSIONS}, or change the column width in "
            f"backend/alembic/versions/078_create_knowledge_base_tables.py and "
            f"PGVECTOR_COLUMN_DIMENSIONS here to match."
        )

    try:
        vectors = await client.embed(["kiểm tra khởi động"])
    except UpstreamError as exc:
        # A width mismatch arrives here, not at the check below: EmbeddingClient
        # ._parse rejects wrong-width vectors before they are returned, and its
        # message already names both widths. This wrapper is what an operator
        # actually reads for that case.
        raise RuntimeError(
            f"Startup probe failed — embedding provider is not usable: {exc.detail}"
        ) from exc

    # Unreachable while _parse enforces the width; kept as the guard against
    # that contract being loosened later.
    actual = len(vectors[0])
    if actual != dimensions:
        raise RuntimeError(
            f"Embedding provider returned {actual}-dimensional vectors but this "
            f"service is configured for {dimensions}. The pgvector column is a "
            f"fixed Vector({dimensions}); refusing to start rather than write "
            f"vectors of the wrong width."
        )
    logger.info("Startup probe OK: provider returns %d-dimensional vectors", actual)


def create_app(
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: Configuration to use. Read from the environment when omitted.
        transport: Optional httpx transport, used by tests to mock upstream.

    Returns:
        A configured FastAPI app whose startup validates the provider.
    """
    try:
        resolved = (settings or settings_from_env()).validate()
    except ValueError as exc:
        # Surface this as a clean log block; the traceback uvicorn prints
        # afterwards is not what the operator needs to read.
        logger.error("Configuration error — vroom-embedding cannot start.\n\n%s\n", exc)
        raise

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient(
            timeout=resolved.timeout,
            transport=transport,
        ) as http_client:
            client = EmbeddingClient(resolved, http_client)
            logger.info(
                "Embedding provider: %s (model=%s, dimensions=%d)",
                resolved.base_url,
                resolved.model_name,
                resolved.dimensions,
            )
            await verify_provider(client, resolved.dimensions)
            app.state.embedding_client = client
            yield

    app = FastAPI(
        title="Vroom Embedding Service",
        description=(
            "Vietnamese text embedding service backed by a remote "
            "OpenAI-compatible embeddings API"
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        """Health check endpoint for the Docker healthcheck.

        Deliberately local: it runs every 15s and must not spend provider
        quota. Provider reachability is already proven at startup.

        Returns:
            ``{"status": "ok"}`` once the service is serving.
        """
        return {"status": "ok"}

    @app.post("/embed", response_model=EmbedResponse)
    async def embed(request: EmbedRequest) -> EmbedResponse:
        """Encode texts into embedding vectors via the remote provider.

        Args:
            request: Contains the list of texts to embed.

        Returns:
            ``EmbedResponse`` with one vector per text, in input order.

        Raises:
            HTTPException: 429 when the provider rate-limits us, 504 on
                timeout, 502 for any other upstream fault.
        """
        client: EmbeddingClient = app.state.embedding_client
        try:
            embeddings = await client.embed(request.texts)
        except UpstreamError as exc:
            logger.error("Embedding failed for %d texts: %s", len(request.texts), exc.detail)
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return EmbedResponse(embeddings=embeddings)

    return app


# Served via ``uvicorn app:create_app --factory`` so that importing this module
# has no side effects — configuration is read when the app is built, not when
# the test suite imports Settings.
