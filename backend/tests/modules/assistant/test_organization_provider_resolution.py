"""Regression tests for Organization provider resolution."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.identity.application.organization_ai_config_service import (
    OrganizationAIConfigService,
    OrganizationAIConfigValidationError,
)
from src.modules.identity.domain.entities import OrganizationAIConfiguration


@pytest.mark.asyncio
async def test_runtime_config_uses_enabled_organization_provider() -> None:
    config = OrganizationAIConfiguration(
        provider="openai-completions",
        base_url="https://provider.example/v1",
        model="org-model",
        api_key_enc="encrypted",
        ai_assistant_enabled=True,
    )
    repository = MagicMock()
    repository.get = AsyncMock(return_value=config)
    crypto = MagicMock()
    crypto.decrypt.return_value = "org-api-key"

    runtime = await OrganizationAIConfigService(repository, crypto).get_runtime_config(
        capability="assistant"
    )

    assert runtime is not None
    assert runtime.base_url == "https://provider.example/v1"
    assert runtime.model == "org-model"
    assert runtime.api_key == "org-api-key"
    crypto.decrypt.assert_called_once_with("encrypted")


@pytest.mark.asyncio
async def test_runtime_config_rejects_disabled_capability() -> None:
    config = OrganizationAIConfiguration(
        provider="openai-completions",
        base_url="https://provider.example/v1",
        model="org-model",
        api_key_enc="encrypted",
        ai_assistant_enabled=False,
    )
    repository = MagicMock()
    repository.get = AsyncMock(return_value=config)

    with pytest.raises(OrganizationAIConfigValidationError, match="disabled"):
        await OrganizationAIConfigService(repository, MagicMock()).get_runtime_config(
            capability="assistant"
        )


@pytest.mark.asyncio
async def test_assistant_settings_dependency_uses_organization_runtime(monkeypatch) -> None:
    from src.modules.assistant import container
    from src.modules.identity.application.organization_ai_config_service import (
        AIProviderRuntimeConfig,
    )

    class FakeService:
        async def get_runtime_config(self, *, capability: str):
            assert capability == "assistant"
            return AIProviderRuntimeConfig(
                provider="openai-completions",
                base_url="https://provider.example/v1",
                model="org-model",
                api_key="org-api-key",
            )

    monkeypatch.setattr(container, "OrganizationAIConfigService", lambda **_: FakeService())

    settings = await container.get_configured_assistant_settings(MagicMock())

    assert settings.base_url == "https://provider.example/v1"
    assert settings.model == "org-model"
    assert settings.api_key == "org-api-key"
