"""Organization-owned policy and classifier rollout configuration tests."""

from uuid import uuid4

import pytest

from src.modules.gmail.application.classification_rollout import (
    BusinessPolicy,
    ReleaseMetrics,
    RolloutMode,
)
from src.modules.identity.application.organization_ai_config_service import (
    ClassificationRolloutCandidate,
    OrganizationAIConfigService,
    OrganizationAIConfigValidationError,
)
from src.modules.identity.domain.entities import OrganizationAIConfiguration, User
from src.modules.identity.infrastructure.config import AuthSettings
from src.modules.identity.infrastructure.crypto_utils import CryptoUtils


class FakeRepository:
    def __init__(self, config: OrganizationAIConfiguration) -> None:
        self.config = config

    async def get(self) -> OrganizationAIConfiguration:
        return self.config

    async def save(self, config: OrganizationAIConfiguration) -> OrganizationAIConfiguration:
        self.config = config
        return config


def _service() -> tuple[OrganizationAIConfigService, FakeRepository]:
    config = OrganizationAIConfiguration(
        provider="openai",
        base_url="https://api.example.test/v1",
        model="model-v1",
        stable_classifier_version="classifier-v1",
        classification_policy=BusinessPolicy.RECALL_FIRST.value,
        classification_policy_version="recall-first-v1",
    )
    repository = FakeRepository(config)
    crypto = CryptoUtils(__import__("base64").b64encode(b"x" * 32).decode())
    settings = AuthSettings.model_construct(ai_deployment_key=None)
    return OrganizationAIConfigService(repository, crypto, settings), repository  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_full_rollout_is_blocked_until_release_gates_pass() -> None:
    service, repository = _service()
    admin = User(id=uuid4(), email="hr@example.com", role="system_admin")
    candidate = ClassificationRolloutCandidate(
        mode=RolloutMode.FULL,
        business_policy=BusinessPolicy.RECALL_FIRST,
        policy_version="recall-first-v2",
        classifier_version="classifier-v2",
        canary_percentage=100,
        release_metrics=ReleaseMetrics(
            job_application_recall=0.97,
            baseline_recall=0.98,
            needs_classification_rate=0.10,
            no_cv_recall=0.99,
            correction_rate=0.02,
            review_rate=0.10,
            p95_latency_ms=900,
            provider_error_rate=0.001,
            duplicate_count=0,
        ),
    )

    with pytest.raises(OrganizationAIConfigValidationError, match="recall"):
        await service.configure_classification_rollout(candidate, admin)

    assert repository.config.stable_classifier_version == "classifier-v1"
    assert repository.config.rollout_mode == RolloutMode.STABLE.value


@pytest.mark.asyncio
async def test_operational_guardrail_failure_auto_rolls_back_active_candidate() -> None:
    service, repository = _service()
    admin = User(id=uuid4(), email="hr@example.com", role="system_admin")
    repository.config.candidate_classifier_version = "classifier-v2"
    repository.config.candidate_classification_policy = BusinessPolicy.RECALL_FIRST.value
    repository.config.candidate_classification_policy_version = "recall-first-v2"
    repository.config.rollout_mode = RolloutMode.CANARY.value
    repository.config.canary_percentage = 25

    result = await service.enforce_classification_guardrails(
        ReleaseMetrics(
            job_application_recall=0.99,
            baseline_recall=0.98,
            needs_classification_rate=0.10,
            no_cv_recall=0.99,
            correction_rate=0.02,
            review_rate=0.10,
            p95_latency_ms=2_001,
            provider_error_rate=0.0,
            duplicate_count=0,
        ),
        admin,
    )

    assert repository.config.rollout_mode == RolloutMode.STABLE.value
    assert repository.config.candidate_classifier_version is None
    assert result.audit_details["action"] == "classification_rollout_auto_rollback"
    assert result.audit_details["failures"] == ["p95_latency_above_2000ms"]


@pytest.mark.asyncio
async def test_rollback_restores_stable_versions_and_audits_policy() -> None:
    service, repository = _service()
    admin = User(id=uuid4(), email="hr@example.com", role="system_admin")
    repository.config.candidate_classifier_version = "classifier-v2"
    repository.config.candidate_classification_policy = BusinessPolicy.RECALL_FIRST.value
    repository.config.candidate_classification_policy_version = "recall-first-v2"
    repository.config.rollout_mode = RolloutMode.CANARY.value
    repository.config.canary_percentage = 25

    result = await service.rollback_classification_rollout(admin)

    assert repository.config.stable_classifier_version == "classifier-v1"
    assert repository.config.classification_policy_version == "recall-first-v1"
    assert repository.config.candidate_classifier_version is None
    assert repository.config.rollout_mode == RolloutMode.STABLE.value
    assert result.audit_details == {
        "action": "classification_rollout_rollback",
        "restored_classifier_version": "classifier-v1",
        "restored_policy_version": "recall-first-v1",
        "rolled_back_classifier_version": "classifier-v2",
        "rolled_back_policy_version": "recall-first-v2",
    }
