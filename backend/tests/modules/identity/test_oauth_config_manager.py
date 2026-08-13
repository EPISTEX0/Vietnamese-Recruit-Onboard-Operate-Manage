"""Unit tests for OAuthConfigManager service."""

import base64
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import respx

from src.modules.identity.application.oauth_config_manager import (
    GOOGLE_DISCOVERY_URL,
    EffectiveCredentials,
    OAuthConfigManager,
    OAuthConfigResponse,
    OAuthConfigValidationError,
)
from src.modules.identity.domain.entities import OAuthConfig, User, UserRole
from src.modules.identity.infrastructure.crypto_utils import CryptoUtils


def _make_crypto() -> CryptoUtils:
    """Create a CryptoUtils instance with a random valid key."""
    key = base64.b64encode(os.urandom(32)).decode("ascii")
    return CryptoUtils(key)


def _make_admin() -> User:
    """Create a mock admin user."""
    user = User(
        id=uuid4(),
        email="admin@example.com",
        name="Admin User",
        google_sub="google-sub-123",
        role=UserRole.SYSTEM_ADMIN,
    )
    return user


def _make_db_config(crypto: CryptoUtils) -> OAuthConfig:
    """Create a mock OAuthConfig entity with encrypted secret."""
    return OAuthConfig(
        id=uuid4(),
        provider="google",
        client_id="db-client-id",
        client_secret_enc=crypto.encrypt("db-client-secret"),
        redirect_uri="https://example.com/callback",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        updated_by_user_id=uuid4(),
    )


def _make_manager(
    repo_get_active_return=None,
    repo_upsert_side_effect=None,
) -> tuple[OAuthConfigManager, AsyncMock]:
    """Create an OAuthConfigManager with mocked repository."""
    crypto = _make_crypto()
    repo = AsyncMock()
    repo.get_active = AsyncMock(return_value=repo_get_active_return)

    if repo_upsert_side_effect:
        repo.upsert = AsyncMock(side_effect=repo_upsert_side_effect)
    else:
        repo.upsert = AsyncMock(side_effect=lambda config: config)

    manager = OAuthConfigManager(
        repository=repo,
        crypto=crypto,
        google_client_id="env-client-id",
        google_client_secret="env-client-secret",
        google_redirect_uri="http://localhost:8000/api/auth/callback",
    )
    return manager, repo


DISCOVERY_RESPONSE = {
    "issuer": "https://accounts.google.com",
    "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_endpoint": "https://oauth2.googleapis.com/token",
    "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
}


class TestMaskSecret:
    """Tests for the mask_secret static method."""

    def test_masks_long_secret(self) -> None:
        result = OAuthConfigManager.mask_secret("my-super-secret-key")
        assert result.endswith("-key")
        assert result.startswith("*")
        assert "my-super-secret" not in result

    def test_masks_exactly_4_chars(self) -> None:
        result = OAuthConfigManager.mask_secret("abcd")
        # 4 chars or fewer: all asterisks
        assert result == "****"

    def test_masks_short_secret(self) -> None:
        result = OAuthConfigManager.mask_secret("ab")
        assert result == "**"

    def test_masks_5_char_secret(self) -> None:
        result = OAuthConfigManager.mask_secret("12345")
        assert result == "*2345"

    def test_shows_last_4_of_long_secret(self) -> None:
        result = OAuthConfigManager.mask_secret("abcdefghijklmnop")
        assert result[-4:] == "mnop"
        assert len(result) == 16


class TestGetActiveConfig:
    """Tests for get_active_config method."""

    @pytest.mark.asyncio
    async def test_returns_db_config_when_exists(self) -> None:
        crypto = _make_crypto()
        db_config = _make_db_config(crypto)
        repo = AsyncMock()
        repo.get_active = AsyncMock(return_value=db_config)

        manager = OAuthConfigManager(
            repository=repo,
            crypto=crypto,
            google_client_id="env-id",
            google_client_secret="env-secret",
            google_redirect_uri="http://localhost/callback",
        )

        result = await manager.get_active_config()

        assert isinstance(result, OAuthConfigResponse)
        assert result.client_id == "db-client-id"
        assert result.source == "database"
        # Secret should be masked
        assert "db-client-secret" not in result.client_secret_masked
        assert result.client_secret_masked.endswith("cret")

    @pytest.mark.asyncio
    async def test_falls_back_to_env_when_no_db_config(self) -> None:
        manager, repo = _make_manager(repo_get_active_return=None)

        result = await manager.get_active_config()

        assert isinstance(result, OAuthConfigResponse)
        assert result.client_id == "env-client-id"
        assert result.source == "environment"
        assert result.updated_at is None

    @pytest.mark.asyncio
    async def test_masks_env_secret(self) -> None:
        manager, _ = _make_manager(repo_get_active_return=None)

        result = await manager.get_active_config()

        assert "env-client-secret" not in result.client_secret_masked
        assert result.client_secret_masked.endswith("cret")


class TestUpdateConfig:
    """Tests for update_config method."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_rejects_empty_client_id(self) -> None:
        manager, _ = _make_manager()
        admin = _make_admin()

        with pytest.raises(OAuthConfigValidationError, match="client_id must not be empty"):
            await manager.update_config("", "secret", "https://example.com/cb", admin)

    @respx.mock
    @pytest.mark.asyncio
    async def test_rejects_whitespace_client_id(self) -> None:
        manager, _ = _make_manager()
        admin = _make_admin()

        with pytest.raises(OAuthConfigValidationError, match="client_id must not be empty"):
            await manager.update_config("   ", "secret", "https://example.com/cb", admin)

    @respx.mock
    @pytest.mark.asyncio
    async def test_rejects_invalid_redirect_uri(self) -> None:
        manager, _ = _make_manager()
        admin = _make_admin()

        with pytest.raises(OAuthConfigValidationError, match="redirect_uri must be a valid URL"):
            await manager.update_config("client-id", "secret", "not-a-url", admin)

    @respx.mock
    @pytest.mark.asyncio
    async def test_rejects_when_google_validation_fails(self) -> None:
        respx.get(GOOGLE_DISCOVERY_URL).mock(return_value=httpx.Response(500))

        manager, _ = _make_manager()
        admin = _make_admin()

        with pytest.raises(
            OAuthConfigValidationError, match="Could not verify credentials with Google"
        ):
            await manager.update_config("client-id", "secret", "https://example.com/cb", admin)

    @respx.mock
    @pytest.mark.asyncio
    async def test_persists_valid_config(self) -> None:
        respx.get(GOOGLE_DISCOVERY_URL).mock(
            return_value=httpx.Response(200, json=DISCOVERY_RESPONSE)
        )

        manager, repo = _make_manager()
        admin = _make_admin()

        result = await manager.update_config(
            "new-client-id", "new-secret", "https://example.com/callback", admin
        )

        assert result.client_id == "new-client-id"
        assert result.source == "database"
        assert result.updated_by_email == admin.email
        # Secret should be masked
        assert result.client_secret_masked.endswith("cret")
        assert "new-secret" not in result.client_secret_masked
        # Repository upsert should have been called
        repo.upsert.assert_called_once()

    @respx.mock
    @pytest.mark.asyncio
    async def test_updates_existing_config(self) -> None:
        respx.get(GOOGLE_DISCOVERY_URL).mock(
            return_value=httpx.Response(200, json=DISCOVERY_RESPONSE)
        )

        crypto = _make_crypto()
        existing = _make_db_config(crypto)
        repo = AsyncMock()
        repo.get_active = AsyncMock(return_value=existing)
        repo.upsert = AsyncMock(side_effect=lambda config: config)

        manager = OAuthConfigManager(
            repository=repo,
            crypto=crypto,
            google_client_id="env-id",
            google_client_secret="env-secret",
            google_redirect_uri="http://localhost/callback",
        )
        admin = _make_admin()

        result = await manager.update_config(
            "updated-id", "updated-secret", "https://new.example.com/cb", admin
        )

        assert result.client_id == "updated-id"
        assert result.redirect_uri == "https://new.example.com/cb"
        repo.upsert.assert_called_once()


class TestValidateCredentials:
    """Tests for validate_credentials method."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_true_for_valid_discovery(self) -> None:
        respx.get(GOOGLE_DISCOVERY_URL).mock(
            return_value=httpx.Response(200, json=DISCOVERY_RESPONSE)
        )

        manager, _ = _make_manager()
        result = await manager.validate_credentials("some-client-id")
        assert result is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_false_for_non_200_response(self) -> None:
        respx.get(GOOGLE_DISCOVERY_URL).mock(return_value=httpx.Response(404))

        manager, _ = _make_manager()
        result = await manager.validate_credentials("some-client-id")
        assert result is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_false_for_missing_authorization_endpoint(self) -> None:
        respx.get(GOOGLE_DISCOVERY_URL).mock(
            return_value=httpx.Response(200, json={"token_endpoint": "https://example.com"})
        )

        manager, _ = _make_manager()
        result = await manager.validate_credentials("some-client-id")
        assert result is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_false_for_missing_token_endpoint(self) -> None:
        respx.get(GOOGLE_DISCOVERY_URL).mock(
            return_value=httpx.Response(200, json={"authorization_endpoint": "https://example.com"})
        )

        manager, _ = _make_manager()
        result = await manager.validate_credentials("some-client-id")
        assert result is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_false_on_timeout(self) -> None:
        respx.get(GOOGLE_DISCOVERY_URL).mock(side_effect=httpx.TimeoutException("timeout"))

        manager, _ = _make_manager()
        result = await manager.validate_credentials("some-client-id")
        assert result is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_false_on_connection_error(self) -> None:
        respx.get(GOOGLE_DISCOVERY_URL).mock(side_effect=httpx.ConnectError("connection refused"))

        manager, _ = _make_manager()
        result = await manager.validate_credentials("some-client-id")
        assert result is False


class TestGetEffectiveCredentials:
    """Tests for get_effective_credentials method."""

    @pytest.mark.asyncio
    async def test_returns_db_credentials_when_active_config_exists(self) -> None:
        crypto = _make_crypto()
        db_config = _make_db_config(crypto)
        repo = AsyncMock()
        repo.get_active = AsyncMock(return_value=db_config)

        manager = OAuthConfigManager(
            repository=repo,
            crypto=crypto,
            google_client_id="env-id",
            google_client_secret="env-secret",
            google_redirect_uri="http://localhost/callback",
        )

        result = await manager.get_effective_credentials()

        assert isinstance(result, EffectiveCredentials)
        assert result.client_id == "db-client-id"
        assert result.client_secret == "db-client-secret"
        assert result.redirect_uri == "https://example.com/callback"

    @pytest.mark.asyncio
    async def test_falls_back_to_env_credentials(self) -> None:
        manager, _ = _make_manager(repo_get_active_return=None)

        result = await manager.get_effective_credentials()

        assert isinstance(result, EffectiveCredentials)
        assert result.client_id == "env-client-id"
        assert result.client_secret == "env-client-secret"
        assert result.redirect_uri == "http://localhost:8000/api/auth/callback"
