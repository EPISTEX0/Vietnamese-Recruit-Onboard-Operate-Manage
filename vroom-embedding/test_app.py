"""Tests for the vroom-embedding service.

The service is a thin proxy in front of an OpenAI-compatible ``/embeddings``
endpoint, so every test here mocks the upstream via ``httpx.MockTransport``.
No test performs real network I/O.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app import Settings, create_app

DIMENSIONS = 1024


def make_settings(**overrides: Any) -> Settings:
    """Build a Settings object with test-friendly defaults."""
    defaults: dict[str, Any] = {
        "base_url": "https://upstream.test/v1",
        "api_key": "sk-test",
        "model_name": "text-embedding-v4",
        "dimensions": DIMENSIONS,
        "timeout": 5.0,
        "max_retries": 2,
        "batch_size": 10,
        "retry_backoff": 0.0,
        "send_dimensions": True,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def embedding_payload(count: int, dim: int = DIMENSIONS, start_index: int = 0) -> dict[str, Any]:
    """Build an OpenAI-shaped embeddings response body."""
    return {
        "object": "list",
        "model": "text-embedding-v4",
        "data": [
            {
                "object": "embedding",
                "index": start_index + i,
                "embedding": [float(start_index + i)] + [0.01] * (dim - 1),
            }
            for i in range(count)
        ],
    }


class UpstreamRecorder:
    """A MockTransport handler that records requests and replays scripted responses.

    ``responses`` may hold ``httpx.Response`` objects or exceptions to raise.
    The last entry is reused once the script is exhausted, so a single-entry
    script serves an unlimited number of calls. With no script at all, every
    request gets an auto-generated 200 sized to match its own ``input`` list.
    """

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    @property
    def bodies(self) -> list[dict[str, Any]]:
        """Parsed JSON bodies of every recorded request."""
        return [json.loads(r.content) for r in self.requests]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            return ok(len(json.loads(request.content)["input"]))
        index = min(len(self.requests) - 1, len(self._responses) - 1)
        result = self._responses[index]
        if isinstance(result, Exception):
            raise result
        return result

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def ok(count: int, dim: int = DIMENSIONS, start_index: int = 0) -> httpx.Response:
    """A 200 response carrying ``count`` embeddings."""
    return httpx.Response(200, json=embedding_payload(count, dim, start_index))


def build_client(recorder: UpstreamRecorder, **setting_overrides: Any) -> TestClient:
    """Create a TestClient wired to the recorder's mock transport."""
    app = create_app(settings=make_settings(**setting_overrides), transport=recorder.transport)
    return TestClient(app)


class TestStartupValidation:
    """The service probes the provider on startup and refuses to run if it lies."""

    def test_startup_succeeds_when_dimension_matches(self) -> None:
        recorder = UpstreamRecorder(ok(1))
        with build_client(recorder) as client:
            assert client.get("/health").status_code == 200
        assert len(recorder.requests) >= 1, "startup must probe the provider"

    def test_startup_fails_when_provider_returns_wrong_dimension(self) -> None:
        """A 768-dim provider must not be allowed to write into a Vector(1024) column."""
        recorder = UpstreamRecorder(ok(1, dim=768))
        with pytest.raises(RuntimeError, match="768"):
            with build_client(recorder):
                pass

    def test_startup_failure_message_names_both_dimensions(self) -> None:
        recorder = UpstreamRecorder(ok(1, dim=512))
        with pytest.raises(RuntimeError) as excinfo:
            with build_client(recorder):
                pass
        message = str(excinfo.value)
        assert "512" in message and str(DIMENSIONS) in message

    def test_startup_fails_when_provider_unreachable(self) -> None:
        recorder = UpstreamRecorder(httpx.ConnectError("no route"))
        with pytest.raises(RuntimeError):
            with build_client(recorder):
                pass

    def test_startup_fails_when_api_key_rejected(self) -> None:
        recorder = UpstreamRecorder(httpx.Response(401, json={"error": "bad key"}))
        with pytest.raises(RuntimeError):
            with build_client(recorder):
                pass

    def test_dimensions_disagreeing_with_pgvector_column_is_fatal(self) -> None:
        """This provider honours the requested width, so agreeing with it is not
        enough: a width the Vector(1024) column cannot hold must stop startup,
        otherwise every kb-worker insert fails later at the database instead."""
        recorder = UpstreamRecorder(ok(1, dim=512))
        with pytest.raises(RuntimeError, match="Vector\\(1024\\)"):
            with build_client(recorder, dimensions=512):
                pass

    def test_matching_dimensions_start_cleanly(self) -> None:
        recorder = UpstreamRecorder(ok(1))
        with build_client(recorder) as client:
            assert client.get("/health").status_code == 200

    def test_empty_api_key_is_allowed_for_keyless_endpoints(self) -> None:
        """Local endpoints (vLLM, TEI) commonly need no key at all."""
        make_settings(api_key="").validate()

    def test_keyless_requests_omit_the_authorization_header(self) -> None:
        recorder = UpstreamRecorder(ok(1))
        with build_client(recorder, api_key="") as client:
            client.post("/embed", json={"texts": ["Xin chào"]})
        assert "authorization" not in recorder.requests[-1].headers

    def test_negative_max_retries_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="EMBEDDING_MAX_RETRIES"):
            make_settings(max_retries=-1).validate()

    def test_missing_base_url_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="EMBEDDING_API_BASE_URL"):
            make_settings(base_url="").validate()

    def test_missing_base_url_stops_app_creation(self) -> None:
        """The failure must land at startup, not on the first /embed call."""
        recorder = UpstreamRecorder(ok(1))
        with pytest.raises(ValueError, match="EMBEDDING_API_BASE_URL"):
            create_app(settings=make_settings(base_url=""), transport=recorder.transport)
        assert recorder.requests == [], "must fail before touching the network"

    def test_missing_base_url_message_is_actionable(self) -> None:
        """An operator reading the log should be able to fix it without
        opening the source: what to set, its shape, and both routes."""
        with pytest.raises(ValueError) as excinfo:
            make_settings(base_url="").validate()
        message = str(excinfo.value)

        assert "EMBEDDING_API_BASE_URL=" in message, "must show the variable being assigned"
        assert "/v1" in message, "must show the expected shape of the value"
        assert "vllm" in message.lower(), "must show the local-endpoint route"
        assert "EMBEDDING_API_KEY" in message, "must clarify the key is separate"

    def test_missing_model_name_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="EMBEDDING_MODEL_NAME"):
            make_settings(model_name="").validate()

    def test_missing_model_name_stops_app_creation(self) -> None:
        recorder = UpstreamRecorder(ok(1))
        with pytest.raises(ValueError, match="EMBEDDING_MODEL_NAME"):
            create_app(settings=make_settings(model_name=""), transport=recorder.transport)
        assert recorder.requests == [], "must fail before touching the network"

    def test_missing_model_name_message_names_the_disguised_502(self) -> None:
        """An unset model shows up upstream as 'model not found'; the message
        must say it is a config error so nobody debugs it as a network fault."""
        with pytest.raises(ValueError) as excinfo:
            make_settings(model_name="").validate()
        message = str(excinfo.value)

        assert "EMBEDDING_MODEL_NAME=" in message, "must show the variable being assigned"
        assert "502" in message, "must name the symptom it prevents"
        assert "bge-m3" in message, "must give a local-endpoint model example"
        assert "text-embedding-3-small" in message, "must give a hosted model example"

    def test_both_missing_are_reported_in_one_message(self) -> None:
        """Reporting one at a time would make the operator restart to discover
        the second — the whole point of collecting problems."""
        with pytest.raises(ValueError) as excinfo:
            make_settings(base_url="", model_name="").validate()
        message = str(excinfo.value)

        assert "EMBEDDING_API_BASE_URL is required" in message
        assert "EMBEDDING_MODEL_NAME is required" in message
        assert "(1)" in message and "(2)" in message, "both must be enumerated"

    def test_only_the_actually_missing_setting_is_reported(self) -> None:
        message = str(pytest.raises(ValueError, make_settings(model_name="").validate).value)
        assert "EMBEDDING_MODEL_NAME is required" in message
        assert "EMBEDDING_API_BASE_URL is required" not in message

    def test_range_errors_skip_the_endpoint_examples(self) -> None:
        """Endpoint examples are noise when the endpoint is configured fine."""
        with pytest.raises(ValueError) as excinfo:
            make_settings(max_retries=-1).validate()
        assert "Two shapes that work" not in str(excinfo.value)

    def test_base_url_and_model_name_both_valid_starts_cleanly(self) -> None:
        recorder = UpstreamRecorder(ok(1))
        with build_client(
            recorder, base_url="http://vllm:8000/v1", model_name="BAAI/bge-m3", api_key=""
        ) as client:
            response = client.post("/embed", json={"texts": ["Xin chào"]})
            assert response.status_code == 200
            assert len(response.json()["embeddings"][0]) == DIMENSIONS
        assert recorder.bodies[-1]["model"] == "BAAI/bge-m3"

    def test_model_name_has_no_default_value(self) -> None:
        """`text-embedding-v4` was an ai-box-specific name; no vendor model
        may be assumed any more than a vendor endpoint."""
        import dataclasses

        field = Settings.__dataclass_fields__["model_name"]
        assert field.default is dataclasses.MISSING
        assert field.default_factory is dataclasses.MISSING

    def test_unset_model_name_env_produces_empty_not_a_vendor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app import settings_from_env

        monkeypatch.delenv("EMBEDDING_MODEL_NAME", raising=False)
        assert settings_from_env().model_name == ""

    def test_base_url_has_no_default_value(self) -> None:
        """Removing the vendor default is the point of this change: base_url
        must stay a required field so no endpoint can be assumed."""
        import dataclasses

        field = Settings.__dataclass_fields__["base_url"]
        assert field.default is dataclasses.MISSING
        assert field.default_factory is dataclasses.MISSING

    def test_unset_base_url_env_produces_empty_not_a_vendor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app import settings_from_env

        monkeypatch.delenv("EMBEDDING_API_BASE_URL", raising=False)
        monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
        assert settings_from_env().base_url == ""

    def test_empty_key_with_valid_base_url_serves_requests(self) -> None:
        """The keyless local-endpoint path must work end to end, not merely
        pass validation."""
        recorder = UpstreamRecorder(ok(1))
        with build_client(recorder, api_key="", base_url="http://vllm:8000/v1") as client:
            response = client.post("/embed", json={"texts": ["Xin chào"]})
            assert response.status_code == 200
            assert len(response.json()["embeddings"][0]) == DIMENSIONS
        assert "authorization" not in recorder.requests[-1].headers


class TestHealthEndpoint:
    """Tests for GET /health — the Docker healthcheck target."""

    def test_health_returns_ok(self) -> None:
        recorder = UpstreamRecorder(ok(1))
        with build_client(recorder) as client:
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}

    def test_health_does_not_call_upstream(self) -> None:
        """Healthchecks run every 15s; they must not burn provider quota."""
        recorder = UpstreamRecorder(ok(1))
        with build_client(recorder) as client:
            calls_after_startup = len(recorder.requests)
            client.get("/health")
            client.get("/health")
            assert len(recorder.requests) == calls_after_startup


class TestEmbedHappyPath:
    """POST /embed — the contract the backend and kb-worker depend on."""

    def test_single_text_returns_one_vector_of_configured_dimension(self) -> None:
        recorder = UpstreamRecorder(ok(1))
        with build_client(recorder) as client:
            response = client.post("/embed", json={"texts": ["Xin chào"]})
            assert response.status_code == 200
            embeddings = response.json()["embeddings"]
            assert len(embeddings) == 1
            assert len(embeddings[0]) == DIMENSIONS

    def test_vietnamese_diacritics_are_forwarded_intact(self) -> None:
        recorder = UpstreamRecorder(ok(1), ok(2))
        text = "Người lao động được nghỉ phép năm 12 ngày"
        with build_client(recorder) as client:
            response = client.post(
                "/embed",
                json={"texts": [text, "Quy chế phúc lợi áp dụng từ 01/01/2025"]},
            )
            assert response.status_code == 200
            assert len(response.json()["embeddings"]) == 2
        assert recorder.bodies[-1]["input"][0] == text

    def test_request_carries_model_dimensions_and_auth(self) -> None:
        recorder = UpstreamRecorder(ok(1))
        with build_client(recorder) as client:
            client.post("/embed", json={"texts": ["Xin chào"]})
        request = recorder.requests[-1]
        body = recorder.bodies[-1]
        assert request.url.path.endswith("/embeddings")
        assert body["model"] == "text-embedding-v4"
        assert body["dimensions"] == DIMENSIONS
        assert request.headers["authorization"] == "Bearer sk-test"

    def test_dimensions_can_be_omitted_for_providers_that_reject_it(self) -> None:
        """vLLM 400s on `dimensions` for non-Matryoshka models."""
        recorder = UpstreamRecorder(ok(1))
        with build_client(recorder, send_dimensions=False) as client:
            client.post("/embed", json={"texts": ["Xin chào"]})
        assert "dimensions" not in recorder.bodies[-1]

    def test_base_url_trailing_slash_does_not_double_up(self) -> None:
        recorder = UpstreamRecorder(ok(1))
        with build_client(recorder, base_url="https://upstream.test/v1/") as client:
            client.post("/embed", json={"texts": ["Xin chào"]})
        assert recorder.requests[-1].url.path == "/v1/embeddings"

    def test_vectors_are_returned_in_request_order(self) -> None:
        """Order matters: the backend zips embeddings back onto its chunks."""
        recorder = UpstreamRecorder(ok(1), ok(3))
        with build_client(recorder) as client:
            response = client.post("/embed", json={"texts": ["a", "b", "c"]})
            embeddings = response.json()["embeddings"]
            assert [emb[0] for emb in embeddings] == [0.0, 1.0, 2.0]

    def test_out_of_order_provider_response_is_resorted(self) -> None:
        """OpenAI's spec does not guarantee ordering; we must sort by index."""
        payload = embedding_payload(3)
        payload["data"] = [payload["data"][2], payload["data"][0], payload["data"][1]]
        recorder = UpstreamRecorder(ok(1), httpx.Response(200, json=payload))
        with build_client(recorder) as client:
            response = client.post("/embed", json={"texts": ["a", "b", "c"]})
            embeddings = response.json()["embeddings"]
            assert [emb[0] for emb in embeddings] == [0.0, 1.0, 2.0]


class TestUpstreamBatching:
    """The provider caps batches at 10; the backend sends a whole document at once."""

    def test_batch_larger_than_provider_limit_is_split(self) -> None:
        recorder = UpstreamRecorder()
        texts = [f"chunk {i}" for i in range(25)]
        with build_client(recorder, batch_size=10) as client:
            response = client.post("/embed", json={"texts": texts})
            assert response.status_code == 200
            assert len(response.json()["embeddings"]) == 25

        embed_bodies = recorder.bodies[1:]  # drop the startup probe
        assert [len(b["input"]) for b in embed_bodies] == [10, 10, 5]

    def test_no_upstream_request_exceeds_batch_size(self) -> None:
        recorder = UpstreamRecorder()
        with build_client(recorder, batch_size=4) as client:
            client.post("/embed", json={"texts": [f"t{i}" for i in range(30)]})
        for body in recorder.bodies[1:]:
            assert len(body["input"]) <= 4

    def test_split_batches_preserve_global_order(self) -> None:
        recorder = UpstreamRecorder()
        texts = [f"chunk {i}" for i in range(23)]
        with build_client(recorder, batch_size=10) as client:
            response = client.post("/embed", json={"texts": texts})

        sent = [text for body in recorder.bodies[1:] for text in body["input"]]
        assert sent == texts
        assert len(response.json()["embeddings"]) == 23

    def test_batch_at_exactly_the_limit_is_one_request(self) -> None:
        recorder = UpstreamRecorder()
        with build_client(recorder, batch_size=10) as client:
            client.post("/embed", json={"texts": [f"t{i}" for i in range(10)]})
        assert len(recorder.bodies[1:]) == 1


class TestUpstreamFailures:
    """Upstream problems must surface as meaningful status codes, never be swallowed."""

    def test_upstream_server_error_maps_to_502(self) -> None:
        recorder = UpstreamRecorder(ok(1), httpx.Response(500, text="boom"))
        with build_client(recorder, max_retries=0) as client:
            response = client.post("/embed", json={"texts": ["Xin chào"]})
            assert response.status_code == 502

    def test_upstream_timeout_maps_to_504(self) -> None:
        recorder = UpstreamRecorder(ok(1), httpx.ReadTimeout("too slow"))
        with build_client(recorder, max_retries=0) as client:
            response = client.post("/embed", json={"texts": ["Xin chào"]})
            assert response.status_code == 504

    def test_upstream_connect_error_maps_to_502(self) -> None:
        recorder = UpstreamRecorder(ok(1), httpx.ConnectError("refused"))
        with build_client(recorder, max_retries=0) as client:
            response = client.post("/embed", json={"texts": ["Xin chào"]})
            assert response.status_code == 502

    def test_rate_limit_is_passed_through_as_429(self) -> None:
        """Callers can only back off correctly if 429 survives the hop."""
        recorder = UpstreamRecorder(ok(1), httpx.Response(429, text="slow down"))
        with build_client(recorder, max_retries=0) as client:
            response = client.post("/embed", json={"texts": ["Xin chào"]})
            assert response.status_code == 429

    def test_auth_failure_maps_to_502_not_401(self) -> None:
        """A bad provider key is our misconfiguration, not the caller's."""
        recorder = UpstreamRecorder(ok(1), httpx.Response(401, text="bad key"))
        with build_client(recorder, max_retries=0) as client:
            response = client.post("/embed", json={"texts": ["Xin chào"]})
            assert response.status_code == 502

    def test_error_detail_is_not_swallowed(self) -> None:
        recorder = UpstreamRecorder(ok(1), httpx.Response(500, text="upstream exploded"))
        with build_client(recorder, max_retries=0) as client:
            response = client.post("/embed", json={"texts": ["Xin chào"]})
            assert "upstream" in response.json()["detail"].lower()

    def test_api_key_never_leaks_into_error_detail(self) -> None:
        recorder = UpstreamRecorder(ok(1), httpx.Response(500, text="fail"))
        with build_client(recorder, max_retries=0) as client:
            response = client.post("/embed", json={"texts": ["Xin chào"]})
            assert "sk-test" not in response.text


class TestRetryBehaviour:
    """Transient failures are retried; deterministic ones are not."""

    def test_transient_server_error_is_retried_then_succeeds(self) -> None:
        recorder = UpstreamRecorder(ok(1), httpx.Response(503, text="unavailable"), ok(1))
        with build_client(recorder, max_retries=2) as client:
            response = client.post("/embed", json={"texts": ["Xin chào"]})
            assert response.status_code == 200
        assert len(recorder.requests) == 3  # probe + failure + retry

    def test_timeout_is_retried_then_succeeds(self) -> None:
        recorder = UpstreamRecorder(ok(1), httpx.ReadTimeout("slow"), ok(1))
        with build_client(recorder, max_retries=2) as client:
            response = client.post("/embed", json={"texts": ["Xin chào"]})
            assert response.status_code == 200

    def test_auth_failure_is_not_retried(self) -> None:
        """Retrying a 401 just wastes time — it will never start working."""
        recorder = UpstreamRecorder(ok(1), httpx.Response(401, text="bad key"))
        with build_client(recorder, max_retries=3) as client:
            client.post("/embed", json={"texts": ["Xin chào"]})
        assert len(recorder.requests) == 2  # probe + one attempt, no retries

    def test_retries_are_bounded(self) -> None:
        recorder = UpstreamRecorder(ok(1), httpx.Response(500, text="always down"))
        with build_client(recorder, max_retries=2) as client:
            response = client.post("/embed", json={"texts": ["Xin chào"]})
            assert response.status_code == 502
        assert len(recorder.requests) == 4  # probe + 1 attempt + 2 retries


class TestResponseIntegrity:
    """Malformed provider responses must never reach the pgvector column."""

    def test_wrong_dimension_at_runtime_maps_to_502(self) -> None:
        recorder = UpstreamRecorder(ok(1), ok(1, dim=768))
        with build_client(recorder, max_retries=0) as client:
            response = client.post("/embed", json={"texts": ["Xin chào"]})
            assert response.status_code == 502
            assert "768" in response.json()["detail"]

    def test_missing_vectors_maps_to_502(self) -> None:
        recorder = UpstreamRecorder(ok(1), ok(2))
        with build_client(recorder, max_retries=0) as client:
            response = client.post("/embed", json={"texts": ["a", "b", "c"]})
            assert response.status_code == 502

    def test_unparseable_response_maps_to_502(self) -> None:
        recorder = UpstreamRecorder(ok(1), httpx.Response(200, text="not json"))
        with build_client(recorder, max_retries=0) as client:
            response = client.post("/embed", json={"texts": ["Xin chào"]})
            assert response.status_code == 502

    def test_response_missing_data_key_maps_to_502(self) -> None:
        recorder = UpstreamRecorder(ok(1), httpx.Response(200, json={"object": "list"}))
        with build_client(recorder, max_retries=0) as client:
            response = client.post("/embed", json={"texts": ["Xin chào"]})
            assert response.status_code == 502


class TestRequestValidation:
    """The inbound schema is unchanged from the self-hosted service."""

    def test_empty_texts_rejected(self) -> None:
        recorder = UpstreamRecorder(ok(1))
        with build_client(recorder) as client:
            assert client.post("/embed", json={"texts": []}).status_code == 422

    def test_missing_texts_field_rejected(self) -> None:
        recorder = UpstreamRecorder(ok(1))
        with build_client(recorder) as client:
            assert client.post("/embed", json={}).status_code == 422

    def test_more_than_hundred_texts_rejected(self) -> None:
        recorder = UpstreamRecorder()
        with build_client(recorder) as client:
            response = client.post("/embed", json={"texts": ["t"] * 101})
            assert response.status_code == 422

    def test_hundred_texts_accepted(self) -> None:
        recorder = UpstreamRecorder()
        with build_client(recorder) as client:
            response = client.post("/embed", json={"texts": ["t"] * 100})
            assert response.status_code == 200
            assert len(response.json()["embeddings"]) == 100


class TestSettingsFromEnv:
    """Environment wiring, including the 1024 default that pgvector requires."""

    def test_dimensions_default_to_1024(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import settings_from_env

        monkeypatch.delenv("EMBEDDING_DIMENSIONS", raising=False)
        monkeypatch.setenv("EMBEDDING_API_BASE_URL", "https://upstream.test/v1")
        monkeypatch.setenv("EMBEDDING_API_KEY", "sk-x")
        assert settings_from_env().dimensions == 1024

    def test_default_worst_case_latency_fits_the_retrieval_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """retrieval_service.py calls us with timeout=30.0. If our own retry
        budget outlasts that, the caller gives up before we ever answer."""
        from app import settings_from_env

        for var in ("EMBEDDING_TIMEOUT", "EMBEDDING_MAX_RETRIES", "EMBEDDING_RETRY_BACKOFF"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("EMBEDDING_API_BASE_URL", "https://upstream.test/v1")
        monkeypatch.setenv("EMBEDDING_API_KEY", "sk-x")
        settings = settings_from_env()

        attempts = settings.max_retries + 1
        backoff = sum(settings.retry_backoff * (2**i) for i in range(settings.max_retries))
        assert attempts * settings.timeout + backoff < 30.0

    def test_env_overrides_are_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import settings_from_env

        monkeypatch.setenv("EMBEDDING_API_BASE_URL", "https://other.test/v1")
        monkeypatch.setenv("EMBEDDING_API_KEY", "sk-y")
        monkeypatch.setenv("EMBEDDING_MODEL_NAME", "some-model")
        monkeypatch.setenv("EMBEDDING_DIMENSIONS", "256")
        settings = settings_from_env()
        assert settings.base_url == "https://other.test/v1"
        assert settings.api_key == "sk-y"
        assert settings.model_name == "some-model"
        assert settings.dimensions == 256
