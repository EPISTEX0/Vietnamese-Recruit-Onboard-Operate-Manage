"""Identity module configuration.

Loads authentication settings from environment variables with the AUTH_ prefix.
"""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Supported JWT signing algorithms.
_VALID_JWT_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})


class AuthSettings(BaseSettings):
    """Identity module configuration loaded from environment variables.

    All environment variables are prefixed with ``AUTH_``. For example,
    ``google_client_id`` maps to ``AUTH_GOOGLE_CLIENT_ID``.

    Attributes:
        google_client_id: Google OAuth2 client ID.
        google_client_secret: Google OAuth2 client secret.
        google_redirect_uri: OAuth2 callback URL registered with Google.
        jwt_secret_key: Secret key used to sign JWT tokens.
        jwt_algorithm: Algorithm for JWT signing (HS256, HS384, or HS512).
        access_token_expire_minutes: Lifetime of JWT access tokens in minutes.
        refresh_token_expire_days: Lifetime of refresh tokens in days.
        oauth_token_encryption_key: Base64-encoded 32-byte AES-256-GCM key.
        rate_limit_login_max: Maximum login attempts per window.
        rate_limit_login_window_seconds: Rate limit sliding window in seconds.
        rate_limit_forgot_password_ip_max: Max forgot-password requests per IP.
        rate_limit_forgot_password_ip_window_seconds: Forgot-password IP window.
        rate_limit_forgot_password_email_max: Max forgot-password requests per email.
        rate_limit_forgot_password_email_window_seconds: Forgot-password email window.
        super_admin_email: Email promoted to admin at startup.
    """

    model_config = SettingsConfigDict(env_prefix="AUTH_")

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/vroom_hr"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Google OAuth2
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str = "http://localhost:8000/api/auth/callback"

    # JWT
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=15, gt=0)
    refresh_token_expire_days: int = Field(default=7, gt=0)

    # Encryption
    oauth_token_encryption_key: str  # 32-byte key, base64-encoded

    # Rate limiting
    rate_limit_login_max: int = Field(default=5, gt=0)
    rate_limit_login_window_seconds: int = Field(default=60, gt=0)
    rate_limit_forgot_password_ip_max: int = Field(default=3, gt=0)
    rate_limit_forgot_password_ip_window_seconds: int = Field(default=900, gt=0)
    rate_limit_forgot_password_email_max: int = Field(default=2, gt=0)
    rate_limit_forgot_password_email_window_seconds: int = Field(default=900, gt=0)

    # Super Admin
    super_admin_email: str | None = None

    # Dev bootstrap
    # AI Deployment
    ai_deployment_key: str | None = None  # Optional deployment-wide API key for AI provider

    # Frontend
    frontend_url: str = "http://localhost:3000"

    @field_validator("jwt_algorithm")
    @classmethod
    def validate_jwt_algorithm(cls, v: str) -> str:
        """Ensure jwt_algorithm is a supported HMAC signing algorithm.

        Args:
            v: The algorithm string to validate.

        Returns:
            The validated algorithm string (uppercased).

        Raises:
            ValueError: If the algorithm is not one of HS256, HS384, HS512.
        """
        upper = v.upper()
        if upper not in _VALID_JWT_ALGORITHMS:
            raise ValueError(
                f"jwt_algorithm must be one of {sorted(_VALID_JWT_ALGORITHMS)}, got '{v}'"
            )
        return upper
