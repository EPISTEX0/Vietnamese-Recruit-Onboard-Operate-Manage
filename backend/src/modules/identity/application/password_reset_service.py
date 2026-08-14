"""Password reset service for the Identity & Auth module.

Orchestrates the forgot-password flow: generating a reset token whose
SHA-256 hash is stored in the database, emailing the reset link via the
Gmail SendService, validating a presented token, and applying a new
password with full session revocation.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.gmail.application.send_service import SendEmailParams
from src.modules.identity.domain.entities import PasswordResetToken
from src.modules.identity.domain.exceptions import InvalidResetTokenError
from src.modules.identity.infrastructure.config import AuthSettings
from src.modules.identity.infrastructure.password_utils import hash_password

if TYPE_CHECKING:
    from src.modules.gmail.application.send_service import SendService
    from src.modules.identity.infrastructure.password_reset_token_repository import (
        PasswordResetTokenRepository,
    )
    from src.modules.identity.infrastructure.refresh_token_repository import (
        RefreshTokenRepository,
    )
    from src.modules.identity.infrastructure.user_repository import UserRepository

logger = logging.getLogger(__name__)

#: Lifetime of a password reset token, per ADR 0010.
_RESET_TOKEN_EXPIRE_MINUTES = 15
#: Frontend route carrying the raw reset token in its query string.
_RESET_LINK_PATH = "/reset-password?token={token}"
#: Fixed delay applied on the "no such account" path so response timing
#: approximates the email-send path (anti-enumeration, ADR 0010).
_ANTI_ENUMERATION_DELAY_SECONDS = 0.3


def build_reset_email(reset_link: str) -> tuple[str, str, str]:
    """Compose the Vietnamese reset-password email.

    Args:
        reset_link: The full magic link containing the raw reset token.

    Returns:
        A ``(subject, body_html, body_text)`` tuple ready for
        :class:`SendEmailParams`.
    """
    subject = "Vroom HR - Đặt lại mật khẩu"
    body_html = f"""\
<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8">
</head>
<body style="margin:0;padding:0;background-color:#f4f4f5;font-family:Arial,Helvetica,sans-serif;">
<div style="max-width:560px;margin:0 auto;padding:24px;background-color:#ffffff;">
<h2 style="color:#1f2937;">Đặt lại mật khẩu</h2>
<p style="color:#374151;">Chúng tôi nhận được yêu cầu đặt lại mật khẩu
cho tài khoản Vroom HR của bạn.</p>
<p style="color:#374151;">Vui lòng nhấp vào liên kết bên dưới để tạo mật khẩu mới.
Liên kết này có hiệu lực trong <strong>15 phút</strong>:</p>
<p style="margin:24px 0;">
<a href="{reset_link}"
   style="display:inline-block;padding:12px 24px;background-color:#2563eb;color:#ffffff;
          text-decoration:none;border-radius:6px;">Đặt lại mật khẩu</a>
</p>
<p style="color:#6b7280;font-size:13px;">Nếu bạn không yêu cầu đặt lại mật khẩu,
hãy bỏ qua email này.
Liên kết sẽ hết hạn sau 15 phút kể từ khi được tạo.</p>
</div>
</body>
</html>
"""
    body_text = (
        "Vroom HR - Đặt lại mật khẩu\n\n"
        "Chúng tôi nhận được yêu cầu đặt lại mật khẩu cho tài khoản Vroom HR của bạn.\n\n"
        f"Mở liên kết sau trong vòng 15 phút để tạo mật khẩu mới:\n{reset_link}\n\n"
        "Nếu bạn không yêu cầu đặt lại mật khẩu, hãy bỏ qua email này."
    )
    return subject, body_html, body_text


class PasswordResetService:
    """Orchestrates the password reset flow.

    Coordinates reset token generation and storage, email delivery via
    the Gmail SendService, token validation, and password updates with
    full refresh token revocation.

    Args:
        settings: Auth configuration (frontend URL for the reset link).
        user_repository: Repository for user lookup and password updates.
        password_reset_token_repository: Repository for reset token persistence.
        refresh_token_repository: Repository for refresh token revocation.
        send_service: Gmail SendService for delivering the reset email.
        session: Optional async session for explicit commits.
    """

    def __init__(
        self,
        settings: AuthSettings,
        user_repository: UserRepository,
        password_reset_token_repository: PasswordResetTokenRepository,
        refresh_token_repository: RefreshTokenRepository,
        send_service: SendService,
        session: AsyncSession | None = None,
    ) -> None:
        """Initialize PasswordResetService with its dependencies."""
        self._settings = settings
        self._user_repository = user_repository
        self._token_repository = password_reset_token_repository
        self._refresh_token_repository = refresh_token_repository
        self._send_service = send_service
        self._session = session

    async def create_reset_token(self, email: str, client_ip: str | None = None) -> bool:
        """Create a reset token and email the reset link.

        Looks up the user by email. When the user exists, is active, and
        has a password set, a new token is generated — the raw token is
        only ever placed in the email link, while its SHA-256 hash is
        stored in the database. All previously active tokens for the user
        are invalidated first (single active token invariant), then the
        reset email is sent.

        Failures are intentionally indistinguishable to the caller: a
        missing, inactive, or passwordless account and an email delivery
        error all return False. Email delivery errors are logged for
        operators so the API can always answer with a generic 200.

        Args:
            email: The email address of the account requesting a reset.
            client_ip: Optional client IP address stored on the token.

        Returns:
            True when a token was created and the email was sent,
            False otherwise.
        """
        user = await self._user_repository.get_by_email(email)
        if user is None or not user.is_active or not user.password_hash:
            # Anti-enumeration: burn time approximating a real email send
            # before answering, so the negative path is not measurably
            # faster than the positive one (ADR 0010 constant-time intent).
            await asyncio.sleep(_ANTI_ENUMERATION_DELAY_SECONDS)
            return False

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = datetime.now(UTC) + timedelta(minutes=_RESET_TOKEN_EXPIRE_MINUTES)

        await self._token_repository.invalidate_all_active_for_user(user.id)
        await self._token_repository.create(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
            created_by_ip=client_ip,
        )

        reset_link = f"{self._settings.frontend_url}{_RESET_LINK_PATH.format(token=raw_token)}"
        subject, body_html, body_text = build_reset_email(reset_link)

        try:
            await self._send_service.send_email(
                user_id=user.id,
                params=SendEmailParams(
                    to=[user.email],
                    subject=subject,
                    body_html=body_html,
                    body_text=body_text,
                ),
            )
        except Exception:
            logger.error(
                "Failed to send password reset email for user %s", user.id, exc_info=True
            )
            return False

        if self._session is not None:
            await self._session.commit()
        return True

    async def validate_token(self, token: str) -> bool:
        """Check whether a raw reset token is still redeemable.

        A token is valid when it exists, has not been used, and has not
        expired.

        Args:
            token: The raw reset token from the reset link.

        Returns:
            True when the token can be used to reset the password,
            False otherwise.
        """
        return await self._find_token(token) is not None

    async def reset_password(self, token: str, new_password: str) -> None:
        """Reset a user's password with a valid reset token.

        Verifies the token, hashes the new password, updates the user's
        password hash while clearing the must_change_password flag, marks
        the token as used, and revokes all of the user's refresh tokens
        so existing sessions are terminated.

        Args:
            token: The raw reset token from the reset link.
            new_password: The new plaintext password to store.

        Raises:
            InvalidResetTokenError: If the token is unknown, already
                used, or expired.
        """
        record = await self._find_token(token)
        if record is None:
            raise InvalidResetTokenError()

        user = await self._user_repository.get_by_id(record.user_id)
        if user is None or not user.is_active:
            # Deactivated accounts must not be re-enabled through a reset
            # link; treat the token as invalid (consistent with
            # create_reset_token, which skips inactive accounts).
            raise InvalidResetTokenError()

        await self._user_repository.update_password(
            user.id,
            hash_password(new_password),
            must_change_password=False,
        )
        await self._token_repository.mark_used(record.id)
        await self._refresh_token_repository.revoke_all_for_user(user.id)

        if self._session is not None:
            await self._session.commit()

    async def _find_token(self, token: str) -> PasswordResetToken | None:
        """Resolve a raw token to a usable PasswordResetToken record.

        Returns None when the token is unknown, already used, or expired.
        """
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        record = await self._token_repository.find_by_token_hash(token_hash)
        if record is None:
            return None
        if record.used_at is not None:
            return None
        if record.expires_at <= datetime.now(UTC):
            return None
        return record
