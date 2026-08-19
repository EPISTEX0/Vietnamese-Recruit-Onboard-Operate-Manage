"""Dependency injection container for the Identity & Auth module.

Provides FastAPI dependency functions that wire together all services,
repositories, and infrastructure components using async database sessions
and Redis connections.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from functools import lru_cache
from uuid import UUID

import redis.asyncio as redis
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.modules.identity.application.audit_service import AuditService
from src.modules.identity.application.auth_service import AuthService
from src.modules.identity.application.oauth_config_manager import OAuthConfigManager
from src.modules.identity.application.oauth_service import OAuthService
from src.modules.identity.application.password_reset_service import PasswordResetService
from src.modules.identity.application.role_service import RoleService
from src.modules.identity.application.token_service import TokenService
from src.modules.identity.domain.entities import User
from src.modules.identity.domain.exceptions import InvalidTokenError
from src.modules.identity.infrastructure.audit_log_repository import AuditLogRepository
from src.modules.identity.infrastructure.config import AuthSettings
from src.modules.identity.infrastructure.crypto_utils import CryptoUtils
from src.modules.identity.infrastructure.jwt_utils import JWTUtils
from src.modules.identity.infrastructure.oauth_config_repository import OAuthConfigRepository
from src.modules.identity.infrastructure.oauth_grant_repository import OAuthGrantRepository
from src.modules.identity.infrastructure.password_reset_token_repository import (
    PasswordResetTokenRepository,
)
from src.modules.identity.infrastructure.rate_limiter import RateLimiter
from src.modules.identity.infrastructure.refresh_token_repository import RefreshTokenRepository
from src.modules.identity.infrastructure.user_repository import UserRepository
from src.modules.recruitment.infrastructure.org_settings_repository import (
    OrganizationSettingsRepository,
)

logger = logging.getLogger(__name__)


@lru_cache
def get_settings() -> AuthSettings:
    """Load and cache AuthSettings from environment variables.

    Returns:
        The AuthSettings singleton loaded from AUTH_* env vars.
    """
    return AuthSettings()  # type: ignore[call-arg]


@lru_cache
def get_jwt_utils() -> JWTUtils:
    """Create and cache the JWTUtils singleton.

    Returns:
        JWTUtils configured with the JWT secret key and algorithm.
    """
    settings = get_settings()
    return JWTUtils(secret_key=settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


@lru_cache
def get_crypto_utils() -> CryptoUtils:
    """Create and cache the CryptoUtils singleton.

    Returns:
        CryptoUtils configured with the OAuth token encryption key.
    """
    settings = get_settings()
    return CryptoUtils(settings.oauth_token_encryption_key)


@lru_cache
def get_redis_client() -> redis.Redis:
    """Create and cache the async Redis client singleton.

    Returns:
        An async Redis client connected to the configured Redis URL.
    """
    settings = get_settings()
    return redis.from_url(settings.redis_url, decode_responses=True)  # type: ignore[no-any-return, no-untyped-call]


@lru_cache
def get_rate_limiter() -> RateLimiter:
    """Create and cache the RateLimiter singleton.

    Returns:
        RateLimiter configured with Redis client and rate limit settings.
    """
    settings = get_settings()
    redis_client = get_redis_client()
    return RateLimiter(redis_client=redis_client, settings=settings)


@lru_cache
def _get_async_session_maker() -> async_sessionmaker[AsyncSession]:
    """Create and cache the async session factory.

    Returns:
        An async_sessionmaker bound to the configured database engine.
    """
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional async database session.

    Yields an AsyncSession that commits on success or rolls back on
    exception. Used as a FastAPI dependency.

    Yields:
        An AsyncSession for database operations.
    """
    session_maker = _get_async_session_maker()
    async with session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_user_repository(
    session: AsyncSession = Depends(get_db_session),
) -> UserRepository:
    """Provide a UserRepository instance.

    Args:
        session: The async database session from DI.

    Returns:
        A UserRepository bound to the current session.
    """
    return UserRepository(session)


async def get_oauth_grant_repository(
    session: AsyncSession = Depends(get_db_session),
) -> OAuthGrantRepository:
    """Provide an OAuthGrantRepository instance.

    Args:
        session: The async database session from DI.

    Returns:
        An OAuthGrantRepository bound to the current session.
    """
    return OAuthGrantRepository(session)


async def get_refresh_token_repository(
    session: AsyncSession = Depends(get_db_session),
) -> RefreshTokenRepository:
    """Provide a RefreshTokenRepository instance.

    Args:
        session: The async database session from DI.

    Returns:
        A RefreshTokenRepository bound to the current session.
    """
    return RefreshTokenRepository(session)


async def get_token_service(
    refresh_token_repo: RefreshTokenRepository = Depends(get_refresh_token_repository),
) -> TokenService:
    """Provide a TokenService instance.

    Args:
        refresh_token_repo: The refresh token repository from DI.

    Returns:
        A TokenService configured with JWT utils and settings.
    """
    return TokenService(
        jwt_utils=get_jwt_utils(),
        settings=get_settings(),
        refresh_token_repository=refresh_token_repo,
    )


async def get_oauth_service(
    oauth_grant_repo: OAuthGrantRepository = Depends(get_oauth_grant_repository),
) -> OAuthService:
    """Provide an OAuthService instance.

    Args:
        oauth_grant_repo: The OAuth grant repository from DI.

    Returns:
        An OAuthService configured with settings, crypto, and repository.
    """
    return OAuthService(
        settings=get_settings(),
        crypto=get_crypto_utils(),
        grant_repository=oauth_grant_repo,
    )


async def get_password_reset_token_repository(
    session: AsyncSession = Depends(get_db_session),
) -> PasswordResetTokenRepository:
    """Provide a PasswordResetTokenRepository instance.

    Args:
        session: The async database session from DI.

    Returns:
        A PasswordResetTokenRepository bound to the current session.
    """
    return PasswordResetTokenRepository(session)


async def get_auth_service(
    user_repo: UserRepository = Depends(get_user_repository),
    refresh_token_repo: RefreshTokenRepository = Depends(get_refresh_token_repository),
    token_service: TokenService = Depends(get_token_service),
    password_reset_token_repo: PasswordResetTokenRepository = Depends(
        get_password_reset_token_repository
    ),
    session: AsyncSession = Depends(get_db_session),
) -> AuthService:
    """Provide local AuthService instance."""
    return AuthService(
        settings=get_settings(),
        token_service=token_service,
        user_repository=user_repo,
        refresh_token_repository=refresh_token_repo,
        organization_repository=OrganizationSettingsRepository(session),
        password_reset_token_repository=password_reset_token_repo,
        session=session,
    )


async def get_password_reset_service(
    user_repo: UserRepository = Depends(get_user_repository),
    token_repo: PasswordResetTokenRepository = Depends(get_password_reset_token_repository),
    refresh_token_repo: RefreshTokenRepository = Depends(get_refresh_token_repository),
    session: AsyncSession = Depends(get_db_session),
) -> PasswordResetService:
    """Provide a PasswordResetService instance.

    Lazily imports the Gmail SendService builder to avoid a circular
    import between the identity and gmail DI containers.

    Uses ``build_send_service`` rather than the ``get_send_service`` provider:
    this runs one dependency deep, where nothing resolves ``Depends`` defaults.
    See that builder for why calling a provider from here is unsafe (#327).

    Args:
        user_repo: The user repository from DI.
        token_repo: The password reset token repository from DI.
        refresh_token_repo: The refresh token repository from DI.
        session: The async database session from DI.

    Returns:
        A PasswordResetService configured with all dependencies.
    """
    from src.modules.gmail.container import build_send_service

    send_service = await build_send_service(session)
    return PasswordResetService(
        settings=get_settings(),
        user_repository=user_repo,
        password_reset_token_repository=token_repo,
        refresh_token_repository=refresh_token_repo,
        send_service=send_service,
        session=session,
    )


async def get_current_user(
    request: Request,
    token_service: TokenService = Depends(get_token_service),
    user_repo: UserRepository = Depends(get_user_repository),
) -> User:
    """Extract and validate the current authenticated user from the request.

    Reads the JWT access token from the ``access_token`` cookie, decodes
    and validates it, then looks up the corresponding user in the database.

    Args:
        request: The incoming FastAPI request object.
        token_service: Service for JWT token verification.
        user_repo: Repository for user lookup by ID.

    Returns:
        The authenticated User entity.

    Raises:
        InvalidTokenError: If the token is missing, invalid, expired,
            or the user cannot be found.
    """
    token = request.cookies.get("access_token")
    if not token:
        raise InvalidTokenError()

    try:
        payload = token_service.verify_access_token(token)
    except InvalidTokenError:
        raise

    user = await user_repo.get_by_id(payload.sub)
    if user is None:
        raise InvalidTokenError()

    return user


# ---------------------------------------------------------------------------
# Admin module dependencies
# ---------------------------------------------------------------------------


async def get_oauth_config_repository(
    session: AsyncSession = Depends(get_db_session),
) -> OAuthConfigRepository:
    """Provide an OAuthConfigRepository instance.

    Args:
        session: The async database session from DI.

    Returns:
        An OAuthConfigRepository bound to the current session.
    """
    return OAuthConfigRepository(session)


async def get_audit_log_repository(
    session: AsyncSession = Depends(get_db_session),
) -> AuditLogRepository:
    """Provide an AuditLogRepository instance.

    Args:
        session: The async database session from DI.

    Returns:
        An AuditLogRepository bound to the current session.
    """
    return AuditLogRepository(session)


async def get_role_service(
    session: AsyncSession = Depends(get_db_session),
) -> RoleService:
    """Provide a RoleService instance.

    Args:
        session: The async database session from DI.

    Returns:
        A RoleService configured with the super admin email from settings.
    """
    settings = get_settings()
    return RoleService(session=session, super_admin_email=settings.super_admin_email)


async def get_oauth_config_manager(
    oauth_config_repo: OAuthConfigRepository = Depends(get_oauth_config_repository),
) -> OAuthConfigManager:
    """Provide an OAuthConfigManager instance.

    Args:
        oauth_config_repo: The OAuth config repository from DI.

    Returns:
        An OAuthConfigManager configured with crypto utils and env fallback values.
    """
    settings = get_settings()
    return OAuthConfigManager(
        repository=oauth_config_repo,
        crypto=get_crypto_utils(),
        google_client_id=settings.google_client_id,
        google_client_secret=settings.google_client_secret,
        google_redirect_uri=settings.google_redirect_uri,
    )


async def get_audit_service(
    audit_log_repo: AuditLogRepository = Depends(get_audit_log_repository),
) -> AuditService:
    """Provide an AuditService instance.

    Args:
        audit_log_repo: The audit log repository from DI.

    Returns:
        An AuditService for recording and querying admin actions.
    """
    return AuditService(repository=audit_log_repo)


async def get_current_employee_id(
    request: Request,
    token_service: TokenService = Depends(get_token_service),
) -> UUID | None:
    """Extract the employee_id from the current JWT access token.

    Returns the linked employee UUID if present in the token payload,
    otherwise returns None (e.g. HR admin without employee link).

    Args:
        request: The incoming FastAPI request object.
        token_service: Service for JWT token verification.

    Returns:
        The employee UUID if linked, None otherwise.
    """
    token = request.cookies.get("access_token")
    if not token:
        return None

    try:
        payload = token_service.verify_access_token(token)
    except InvalidTokenError:
        return None

    return payload.employee_id
