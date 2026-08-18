"""Safe rollout boundary for versioned Job Application classification.

The boundary chooses which classifier result may affect production workflow.  A
shadow candidate is observable through telemetry, but its result is never
returned to the caller that persists Job Applications or Recruitment Inbox
work items.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

from src.modules.gmail.infrastructure.ai_classifier import ClassificationResult


class BusinessPolicy(str, Enum):
    """Organization-facing policy names; numeric thresholds stay system-owned."""

    RECALL_FIRST = "recall_first"


_POLICY_REVIEW_THRESHOLDS: dict[BusinessPolicy, float] = {
    BusinessPolicy.RECALL_FIRST: 0.50,
}

# Operational limits are system-owned guardrails, not Organization-tunable
# thresholds. They protect the self-hosted worker from a candidate provider that
# is technically accurate but unsafe to operate in production.
_MAX_P95_LATENCY_MS = 2_000
_MAX_PROVIDER_ERROR_RATE = 0.01
_MAX_RETRY_FAILURE_RATE = 0.05


class RolloutMode(str, Enum):
    """Production rollout stages for a candidate classifier and policy."""

    STABLE = "stable"
    SHADOW = "shadow"
    CANARY = "canary"
    FULL = "full"


@dataclass(frozen=True)
class RolloutConfig:
    """Versioned rollout state loaded from Organization AI Configuration."""

    mode: RolloutMode
    business_policy: BusinessPolicy
    policy_version: str
    stable_classifier_version: str
    candidate_classifier_version: str | None = None
    canary_percentage: int = 0
    prompt_version: str = "unknown"


@dataclass(frozen=True)
class ReleaseMetrics:
    """Versioned evaluation/production telemetry used by release gates."""

    job_application_recall: float
    baseline_recall: float
    needs_classification_rate: float
    no_cv_recall: float | None
    correction_rate: float
    review_rate: float
    p95_latency_ms: int
    provider_error_rate: float
    duplicate_count: int
    retry_failure_rate: float = 0.0


@dataclass(frozen=True)
class ReleaseGateDecision:
    """Machine-readable decision for promoting a candidate to full rollout."""

    allowed: bool
    failures: tuple[str, ...]


def evaluate_release_gates(metrics: ReleaseMetrics) -> ReleaseGateDecision:
    """Apply the non-negotiable Job Application release gates from the spec."""
    failures: list[str] = []
    if metrics.job_application_recall < 0.98:
        failures.append("job_application_recall_below_98_percent")
    if metrics.needs_classification_rate > 0.15:
        failures.append("needs_classification_above_15_percent")
    if metrics.job_application_recall < metrics.baseline_recall:
        failures.append("recall_regression")
    if metrics.no_cv_recall is None:
        failures.append("no_cv_report_missing")
    if metrics.duplicate_count > 0:
        failures.append("duplicates_detected")
    if metrics.p95_latency_ms > _MAX_P95_LATENCY_MS:
        failures.append("p95_latency_above_2000ms")
    if metrics.provider_error_rate > _MAX_PROVIDER_ERROR_RATE:
        failures.append("provider_error_rate_above_1_percent")
    if metrics.retry_failure_rate > _MAX_RETRY_FAILURE_RATE:
        failures.append("retry_failure_rate_above_5_percent")
    return ReleaseGateDecision(allowed=not failures, failures=tuple(failures))


@dataclass(frozen=True)
class RolloutTelemetryEvent:
    """One comparison/selection event without durable email content."""

    gmail_message_id: str
    mode: RolloutMode
    selected_classifier_version: str
    stable_intent: str
    candidate_intent: str | None
    policy_version: str

    has_cv: bool
    stable_latency_ms: int
    candidate_latency_ms: int | None
    candidate_provider_error: bool = False
    prompt_version: str = "unknown"
    retry_count: int = 0
    retry_failure: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


ClassifierCall = Callable[[], Awaitable[ClassificationResult]]
TelemetryRecorder = Callable[[RolloutTelemetryEvent], Awaitable[None]]


def _token_count(result: ClassificationResult | None, name: str) -> int:
    """Read provider usage without persisting provider response content."""
    if result is None:
        return 0
    value = result.token_usage.get(name, 0)
    return value if isinstance(value, int) and value >= 0 else 0


class ClassificationRollout:
    """Select a production result while isolating shadow side effects."""

    def __init__(
        self,
        config: RolloutConfig,
        *,
        record: TelemetryRecorder | None = None,
    ) -> None:
        self.config = config
        self._record = record

    @property
    def review_threshold(self) -> float:
        """Resolve the hidden calibrated threshold for the selected business policy."""
        return _POLICY_REVIEW_THRESHOLDS[self.config.business_policy]

    @staticmethod
    def partition(gmail_message_id: str) -> int:
        """Return a deterministic 0..99 partition for an immutable Gmail ID."""
        digest = hashlib.sha256(gmail_message_id.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") % 100

    async def classify(
        self,
        *,
        gmail_message_id: str,
        stable: ClassifierCall,
        candidate: ClassifierCall,
        has_cv: bool,
    ) -> ClassificationResult:
        """Select one production classifier, or compare both in shadow mode."""
        use_candidate = self.config.mode == RolloutMode.FULL or (
            self.config.mode == RolloutMode.CANARY
            and self.partition(gmail_message_id) < self.config.canary_percentage
        )

        candidate_provider_error = False
        if self.config.mode == RolloutMode.SHADOW:
            stable_started = time.monotonic()
            stable_result = await stable()
            stable_latency_ms = int((time.monotonic() - stable_started) * 1000)
            candidate_started = time.monotonic()
            try:
                candidate_result = await candidate()
            except Exception:
                # Intentionally silent here: candidate_provider_error is carried into
                # the RolloutTelemetryEvent recorded below, persisted per-message by
                # ClassificationRolloutRepository, and aggregated into
                # provider_error_rate (classification_telemetry.py) which
                # evaluate_release_gates() checks against _MAX_PROVIDER_ERROR_RATE
                # before a candidate can be promoted out of shadow mode. A log line
                # here would duplicate that audit trail without adding a channel
                # anyone reads — shadow-mode candidate failures are expected and
                # never affect the production result (stable_result, selected below).
                candidate_result = None
                candidate_provider_error = True
            candidate_latency_ms = int((time.monotonic() - candidate_started) * 1000)
            selected = stable_result
            selected_version = self.config.stable_classifier_version
        elif use_candidate:
            candidate_started = time.monotonic()
            selected_version = self.config.candidate_classifier_version or ""
            try:
                candidate_result = await candidate()
            except Exception:
                candidate_latency_ms = int((time.monotonic() - candidate_started) * 1000)
                if self._record is not None:
                    await self._record(
                        RolloutTelemetryEvent(
                            gmail_message_id=gmail_message_id,
                            mode=self.config.mode,
                            selected_classifier_version=selected_version,
                            stable_intent="",
                            candidate_intent=None,
                            policy_version=self.config.policy_version,
                            prompt_version=self.config.prompt_version,
                            has_cv=has_cv,
                            stable_latency_ms=0,
                            retry_failure=True,
                            candidate_latency_ms=candidate_latency_ms,
                            candidate_provider_error=True,
                        )
                    )
                raise
            candidate_latency_ms = int((time.monotonic() - candidate_started) * 1000)
            stable_result = None
            stable_latency_ms = 0
            selected = candidate_result
        else:
            stable_started = time.monotonic()
            stable_result = await stable()
            stable_latency_ms = int((time.monotonic() - stable_started) * 1000)
            candidate_result = None
            candidate_latency_ms = None
            selected = stable_result
            selected_version = self.config.stable_classifier_version

        if self._record is not None:
            await self._record(
                RolloutTelemetryEvent(
                    gmail_message_id=gmail_message_id,
                    mode=self.config.mode,
                    selected_classifier_version=selected_version,
                    stable_intent=stable_result.category.value if stable_result else "",
                    candidate_intent=(
                        candidate_result.category.value if candidate_result else None
                    ),
                    policy_version=self.config.policy_version,
                    prompt_version=self.config.prompt_version,
                    has_cv=has_cv,
                    stable_latency_ms=stable_latency_ms,
                    prompt_tokens=_token_count(candidate_result or stable_result, "prompt_tokens"),
                    completion_tokens=_token_count(
                        candidate_result or stable_result, "completion_tokens"
                    ),
                    candidate_latency_ms=candidate_latency_ms,
                    candidate_provider_error=candidate_provider_error,
                )
            )
        return selected
