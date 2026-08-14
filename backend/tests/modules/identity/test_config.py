"""Tests for AuthSettings configuration."""

import pytest
from pydantic import ValidationError

from src.modules.identity.infrastructure.config import AuthSettings

# Minimal required env vars for AuthSettings to instantiate.
_REQUIRED_ENV = {
    "AUTH_GOOGLE_CLIENT_ID": "test-client-id",
    "AUTH_GOOGLE_CLIENT_SECRET": "test-client-secret",
    "AUTH_JWT_SECRET_KEY": "super-secret-key",
    "AUTH_OAUTH_TOKEN_ENCRYPTION_KEY": "dGVzdC1lbmNyeXB0aW9uLWtleS0zMmJ5dGVz",
}


class TestAuthSettingsDefaults:
    """Verify default values are applied correctly."""

    def test_loads_with_required_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, value in _REQUIRED_ENV.items():
            monkeypatch.setenv(key, value)

        settings = AuthSettings()

        assert settings.google_client_id == "test-client-id"
        assert settings.google_client_secret == "test-client-secret"
        assert settings.jwt_secret_key == "super-secret-key"
        assert settings.oauth_token_encryption_key == "dGVzdC1lbmNyeXB0aW9uLWtleS0zMmJ5dGVz"

    def test_default_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, value in _REQUIRED_ENV.items():
            monkeypatch.setenv(key, value)

        settings = AuthSettings()

        assert settings.google_redirect_uri == "http://localhost:8000/api/auth/callback"
        assert settings.jwt_algorithm == "HS256"
        assert settings.access_token_expire_minutes == 15
        assert settings.refresh_token_expire_days == 7
        assert settings.rate_limit_login_max == 5
        assert settings.rate_limit_login_window_seconds == 60
        assert settings.rate_limit_forgot_password_ip_max == 3
        assert settings.rate_limit_forgot_password_ip_window_seconds == 900
        assert settings.rate_limit_forgot_password_email_max == 2
        assert settings.rate_limit_forgot_password_email_window_seconds == 900
        assert settings.frontend_url == "http://localhost:3000"


class TestAuthSettingsEnvPrefix:
    """Verify AUTH_ prefix is used for environment variable mapping."""

    def test_env_prefix_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, value in _REQUIRED_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("AUTH_FRONTEND_URL", "http://custom:4000")

        settings = AuthSettings()

        assert settings.frontend_url == "http://custom:4000"

    def test_forgot_password_limits_mapped_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, value in _REQUIRED_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("AUTH_RATE_LIMIT_FORGOT_PASSWORD_IP_MAX", "7")
        monkeypatch.setenv("AUTH_RATE_LIMIT_FORGOT_PASSWORD_IP_WINDOW_SECONDS", "600")
        monkeypatch.setenv("AUTH_RATE_LIMIT_FORGOT_PASSWORD_EMAIL_MAX", "4")
        monkeypatch.setenv("AUTH_RATE_LIMIT_FORGOT_PASSWORD_EMAIL_WINDOW_SECONDS", "1200")

        settings = AuthSettings()

        assert settings.rate_limit_forgot_password_ip_max == 7
        assert settings.rate_limit_forgot_password_ip_window_seconds == 600
        assert settings.rate_limit_forgot_password_email_max == 4
        assert settings.rate_limit_forgot_password_email_window_seconds == 1200


class TestAuthSettingsValidation:
    """Verify field validation rules."""

    def test_valid_jwt_algorithms(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, value in _REQUIRED_ENV.items():
            monkeypatch.setenv(key, value)

        for algo in ("HS256", "HS384", "HS512"):
            monkeypatch.setenv("AUTH_JWT_ALGORITHM", algo)
            settings = AuthSettings()
            assert settings.jwt_algorithm == algo

    def test_jwt_algorithm_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, value in _REQUIRED_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("AUTH_JWT_ALGORITHM", "hs384")

        settings = AuthSettings()

        assert settings.jwt_algorithm == "HS384"

    def test_invalid_jwt_algorithm_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, value in _REQUIRED_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("AUTH_JWT_ALGORITHM", "RS256")

        with pytest.raises(ValidationError, match="jwt_algorithm"):
            AuthSettings()

    def test_access_token_expire_must_be_positive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, value in _REQUIRED_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("AUTH_ACCESS_TOKEN_EXPIRE_MINUTES", "0")

        with pytest.raises(ValidationError):
            AuthSettings()

    def test_refresh_token_expire_must_be_positive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, value in _REQUIRED_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("AUTH_REFRESH_TOKEN_EXPIRE_DAYS", "-1")

        with pytest.raises(ValidationError):
            AuthSettings()

    def test_rate_limit_max_must_be_positive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, value in _REQUIRED_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("AUTH_RATE_LIMIT_LOGIN_MAX", "0")

        with pytest.raises(ValidationError):
            AuthSettings()

    def test_forgot_password_rate_limits_must_be_positive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key, value in _REQUIRED_ENV.items():
            monkeypatch.setenv(key, value)

        for env_key in (
            "AUTH_RATE_LIMIT_FORGOT_PASSWORD_IP_MAX",
            "AUTH_RATE_LIMIT_FORGOT_PASSWORD_IP_WINDOW_SECONDS",
            "AUTH_RATE_LIMIT_FORGOT_PASSWORD_EMAIL_MAX",
            "AUTH_RATE_LIMIT_FORGOT_PASSWORD_EMAIL_WINDOW_SECONDS",
        ):
            monkeypatch.setenv(env_key, "0")
            with pytest.raises(ValidationError):
                AuthSettings()
            monkeypatch.delenv(env_key, raising=False)

    def test_missing_required_field_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Only set some required fields, omit google_client_id
        monkeypatch.delenv("AUTH_GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.setenv("AUTH_GOOGLE_CLIENT_SECRET", "secret")
        monkeypatch.setenv("AUTH_JWT_SECRET_KEY", "key")
        monkeypatch.setenv("AUTH_OAUTH_TOKEN_ENCRYPTION_KEY", "enc-key")

        with pytest.raises(ValidationError):
            AuthSettings()
