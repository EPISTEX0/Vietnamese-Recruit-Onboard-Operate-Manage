from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from urllib.parse import urlencode, urlparse
from uuid import uuid4

import httpx

from src.modules.gmail.infrastructure.sync_cursor_repository import SyncCursorRepository
from src.modules.identity.application.audit_service import AuditService
from src.modules.identity.application.oauth_config_manager import OAuthConfigManager
from src.modules.identity.domain.entities import AuditActionType, OrganizationGoogleConnection, User
from src.modules.identity.domain.exceptions import (
    DomainAccessDeniedError,
    GoogleAuthError,
    InvalidStateError,
)
from src.modules.identity.infrastructure.connection_state_repository import (
    OrganizationGoogleConnectionRepository,
)
from src.modules.identity.infrastructure.crypto_utils import CryptoUtils
from src.modules.identity.infrastructure.jwt_utils import JWTUtils
from src.modules.identity.infrastructure.oauth_grant_repository import OAuthGrantRepository
from src.modules.recruitment.infrastructure.org_settings_repository import (
    OrganizationSettingsRepository,
)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
REQUIRED_SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
]
VALID_CONNECTION_STATUSES = frozenset(
    {"disconnected", "connected", "degraded", "reauthorization_required"}
)

logger = logging.getLogger(__name__)


@dataclass
class OrganizationGoogleConnectionResponse:
    status: str
    email: str | None = None
    has_secret: bool = False
    redirect_url: str | None = None
    selected_calendar_id: str | None = None


class OrganizationGoogleConnectionService:
    def __init__(
        self,
        *,
        connection_repo: OrganizationGoogleConnectionRepository,
        oauth_config_manager: OAuthConfigManager,
        oauth_grant_repo: OAuthGrantRepository,
        audit_service: AuditService,
        crypto: CryptoUtils,
        state_jwt: JWTUtils,
        org_settings_repo: OrganizationSettingsRepository,
        http_client: httpx.AsyncClient,
        sync_cursor_repo: SyncCursorRepository | None = None,
    ) -> None:
        self._connection_repo = connection_repo
        self._oauth_config_manager = oauth_config_manager
        self._oauth_grant_repo = oauth_grant_repo
        self._audit_service = audit_service
        self._crypto = crypto
        self._state_jwt = state_jwt
        self._org_settings_repo = org_settings_repo
        self._http_client = http_client
        self._sync_cursor_repo = sync_cursor_repo

    def _state_hash(self, state: str) -> str:
        return sha256(state.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_valid_redirect_uri(redirect_uri: str) -> bool:
        parsed = urlparse(redirect_uri)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    async def _reconcile_legacy_grants(self) -> bool:
        """Revoke legacy HR-owned grants before exposing connection state."""
        revoke_all = getattr(self._oauth_grant_repo, "revoke_all", None)
        if not callable(revoke_all):
            return False
        owner_ids = await revoke_all()
        if isinstance(owner_ids, bool):
            has_legacy_grants = owner_ids
        elif isinstance(owner_ids, (list, tuple, set)):
            has_legacy_grants = bool(owner_ids)
        elif isinstance(owner_ids, int):
            has_legacy_grants = owner_ids > 0
        else:
            has_legacy_grants = False
        if not has_legacy_grants:
            return False
        if self._sync_cursor_repo is not None:
            await self._sync_cursor_repo.clear_cursor()

        mark_reauthorization = getattr(self._connection_repo, "mark_reauthorization_required", None)
        if callable(mark_reauthorization):
            await mark_reauthorization()
            return True

        current = await self._connection_repo.get_singleton()
        if current is None:
            await self._connection_repo.upsert_singleton(
                OrganizationGoogleConnection(status="reauthorization_required")
            )
            return True
        await self._connection_repo.upsert_singleton(
            OrganizationGoogleConnection(
                status="reauthorization_required",
                connected_by_user_id=getattr(current, "connected_by_user_id", None),
            )
        )
        return True

    async def get_status(self) -> OrganizationGoogleConnectionResponse:
        await self._reconcile_legacy_grants()
        current = await self._connection_repo.get_singleton()
        if current is None:
            return OrganizationGoogleConnectionResponse(
                status="disconnected",
                selected_calendar_id=None,
            )
        status = current.status if current.status in VALID_CONNECTION_STATUSES else "degraded"
        return OrganizationGoogleConnectionResponse(
            status=status,
            email=current.email,
            has_secret=bool(current.client_secret_enc),
            selected_calendar_id=current.selected_calendar_id,
        )

    async def initiate(self, hr: User) -> OrganizationGoogleConnectionResponse:
        await self._reconcile_legacy_grants()
        config = await self._oauth_config_manager.get_effective_credentials()
        if (
            not config.client_id
            or not config.client_secret
            or not config.redirect_uri
            or not self._is_valid_redirect_uri(config.redirect_uri)
        ):
            raise GoogleAuthError("Google OAuth config missing or invalid")

        state_nonce = str(uuid4())
        state = self._state_jwt.create_state_token(
            {
                "nonce": state_nonce,
                "client_id": config.client_id,
                "redirect_uri": config.redirect_uri,
            }
        )
        current = await self._connection_repo.get_singleton()
        await self._connection_repo.upsert_singleton(
            OrganizationGoogleConnection(
                status=current.status if current else "disconnected",
                email=current.email if current else None,
                google_sub=current.google_sub if current else None,
                email_domain=current.email_domain if current else None,
                selected_calendar_id=current.selected_calendar_id if current else None,
                credential_format_version=current.credential_format_version if current else 1,
                credential_key_version=current.credential_key_version if current else 1,
                access_token_enc=current.access_token_enc if current else None,
                refresh_token_enc=current.refresh_token_enc if current else None,
                client_secret_enc=current.client_secret_enc if current else None,
                oauth_state_hash=self._state_hash(state),
                oauth_state_nonce=state_nonce,
                oauth_state_session_id=None,
                oauth_state_expires_at=datetime.now(UTC) + timedelta(minutes=10),
                token_expires_at=current.token_expires_at if current else None,
                connected_by_user_id=current.connected_by_user_id if current else None,
            )
        )
        params = urlencode(
            {
                "client_id": config.client_id,
                "redirect_uri": config.redirect_uri,
                "response_type": "code",
                "scope": " ".join(REQUIRED_SCOPES),
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
                "include_granted_scopes": "false",
            }
        )
        return OrganizationGoogleConnectionResponse(
            status=current.status if current else "disconnected",
            redirect_url=f"{GOOGLE_AUTH_URL}?{params}",
        )

    async def callback(
        self, *, hr: User, state: str, code: str
    ) -> OrganizationGoogleConnectionResponse:
        payload = self._state_jwt.verify_state_token(state)
        current = await self._connection_repo.get_singleton()
        state_hash = self._state_hash(state)
        if (
            current is None
            or current.oauth_state_hash != state_hash
            or current.oauth_state_nonce != payload.get("nonce")
            or payload.get("client_id") is None
            or payload.get("redirect_uri") is None
        ):
            raise InvalidStateError()
        if current.oauth_state_expires_at is None or current.oauth_state_expires_at < datetime.now(
            UTC
        ):
            raise InvalidStateError()

        config = await self._oauth_config_manager.get_effective_credentials()
        if (
            not config.client_id
            or not config.client_secret
            or not config.redirect_uri
            or not self._is_valid_redirect_uri(config.redirect_uri)
            or payload["client_id"] != config.client_id
            or payload["redirect_uri"] != config.redirect_uri
        ):
            raise InvalidStateError("OAuth redirect configuration changed")

        consume_state = getattr(self._connection_repo, "consume_oauth_state", None)
        if callable(consume_state):
            current = await consume_state(state_hash)
            if current is None:
                raise InvalidStateError()
        else:
            await self._connection_repo.upsert_singleton(
                OrganizationGoogleConnection(
                    status=current.status,
                    email=current.email,
                    google_sub=current.google_sub,
                    email_domain=current.email_domain,
                    selected_calendar_id=current.selected_calendar_id,
                    credential_format_version=current.credential_format_version,
                    credential_key_version=current.credential_key_version,
                    access_token_enc=current.access_token_enc,
                    refresh_token_enc=current.refresh_token_enc,
                    client_secret_enc=current.client_secret_enc,
                    oauth_state_hash=None,
                    oauth_state_nonce=None,
                    oauth_state_session_id=None,
                    oauth_state_expires_at=None,
                    token_expires_at=current.token_expires_at,
                    connected_by_user_id=current.connected_by_user_id,
                )
            )

        token = await self._http_client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "redirect_uri": config.redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token.status_code != 200:
            raise GoogleAuthError("OAuth token exchange failed")
        data = token.json()
        access_token = data.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise GoogleAuthError("OAuth token exchange returned no access token")
        granted = set(str(data.get("scope", "")).split())
        if "https://www.googleapis.com/auth/userinfo.email" in granted:
            granted.add("email")
        if not set(REQUIRED_SCOPES).issubset(granted):
            raise GoogleAuthError("Missing required Google scopes")
        userinfo = await self._http_client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if userinfo.status_code != 200:
            raise GoogleAuthError("Google identity verification failed")
        profile = userinfo.json()
        email = profile.get("email")
        hd = profile.get("hd")
        if not isinstance(email, str) or not email:
            raise DomainAccessDeniedError()
        domains = await self._org_settings_repo.get_allowed_domains()
        allowed_domains = {str(domain).lower().lstrip("@") for domain in domains}
        if allowed_domains and (not isinstance(hd, str) or hd.lower() not in allowed_domains):
            raise DomainAccessDeniedError()
        email_domain = (
            hd.lower() if isinstance(hd, str) and hd else email.rsplit("@", 1)[-1].lower()
        )
        refresh_token = data.get("refresh_token")
        if not refresh_token and current.refresh_token_enc:
            try:
                refresh_token = self._crypto.decrypt(current.refresh_token_enc)
            except Exception as exc:
                raise GoogleAuthError("Existing refresh token is unavailable") from exc
        connection = OrganizationGoogleConnection(
            status="connected",
            email=email,
            google_sub=profile.get("sub"),
            email_domain=email_domain,
            selected_calendar_id=current.selected_calendar_id,
            credential_format_version=current.credential_format_version,
            credential_key_version=current.credential_key_version,
            access_token_enc=self._crypto.encrypt(access_token),
            refresh_token_enc=(
                self._crypto.encrypt(refresh_token)
                if isinstance(refresh_token, str) and refresh_token
                else current.refresh_token_enc
            ),
            client_secret_enc=self._crypto.encrypt(config.client_secret),
            token_expires_at=datetime.now(UTC)
            + timedelta(seconds=int(data.get("expires_in", 3600))),
            connected_by_user_id=hr.id,
        )
        if self._sync_cursor_repo is not None:
            await self._sync_cursor_repo.clear_cursor()
        await self._connection_repo.upsert_singleton(connection)
        action_type = AuditActionType.ORG_GOOGLE_CONNECT
        if current.email and current.email != email:
            action_type = AuditActionType.ORG_GOOGLE_SWITCH_ACCOUNT
        elif current.status == "connected":
            action_type = AuditActionType.ORG_GOOGLE_RECONNECT
        await self._audit_service.log_action(
            admin=hr,
            action_type=action_type,
            details={"result": "connected"},
        )
        return OrganizationGoogleConnectionResponse(
            status="connected", email=email, has_secret=True
        )

    async def disconnect(self, hr: User) -> OrganizationGoogleConnectionResponse:
        """Revoke the Organization's Google grant and clear the local connection.

        The local ``disconnected`` state and its audit record must only ever
        assert what actually happened at Google, not what we merely attempted.
        Google's ``/revoke`` returns 200 when it confirms the grant is gone,
        and 400 when the token was already invalid/expired/revoked -- in both
        cases the grant no longer exists, so both count as a genuine
        disconnect. Any other outcome (network failure, decrypt failure, a
        5xx or unexpected status) means we could not confirm the revoke, so
        we raise instead of clearing local state: doing so keeps the stored
        refresh token around so a retry can still attempt the revoke, and
        keeps this method from ever writing an audit record that claims
        "disconnected" for a grant that may still be live at Google.
        """
        current = await self._connection_repo.get_singleton()
        revoke_outcome = "not_attempted"
        if current and current.refresh_token_enc:
            try:
                response = await self._http_client.post(
                    GOOGLE_REVOKE_URL,
                    data={"token": self._crypto.decrypt(current.refresh_token_enc)},
                )
            except Exception as exc:
                logger.exception("Google token revoke request failed during disconnect")
                raise GoogleAuthError("Unable to confirm Google token revocation") from exc
            if response.status_code == 200:
                revoke_outcome = "revoked"
            elif response.status_code == 400:
                # Google's documented behaviour for an already-invalid/expired/
                # revoked token. The grant is already gone, so refusing to
                # disconnect here would trap the user with a connection that's
                # dead at Google but stuck "connected" locally.
                revoke_outcome = "already_revoked"
            else:
                logger.error(
                    "Google token revoke returned unexpected status %s during disconnect",
                    response.status_code,
                )
                raise GoogleAuthError(
                    f"Unable to confirm Google token revocation (status {response.status_code})"
                )
        if self._sync_cursor_repo is not None:
            await self._sync_cursor_repo.clear_cursor()
        await self._connection_repo.disconnect()
        await self._audit_service.log_action(
            admin=hr,
            action_type=AuditActionType.ORG_GOOGLE_DISCONNECT,
            details={"result": "disconnected", "google_revoke": revoke_outcome},
        )
        return OrganizationGoogleConnectionResponse(status="disconnected")

    async def list_calendars(self, hr: User) -> list[dict[str, object]]:
        """List available calendars via the Organization Google Connection.

        Uses the stored access token to call the Calendar API calendarList.list
        endpoint. Returns calendars the connection has at least ``writer`` access
        to so the HR user can select one for interview scheduling.

        Args:
            hr: The acting HR user (used only for audit/naming).

        Returns:
            List of calendar dicts with ``id``, ``summary``, ``description``,
            ``primary``, and ``accessRole`` keys.

        Raises:
            GoogleAuthError: If the connection is not active.
        """
        current = await self._connection_repo.get_singleton()
        if current is None or current.status != "connected":
            raise DomainAccessDeniedError("Google connection is not active")
        if not current.access_token_enc:
            raise GoogleAuthError("No access token available")

        access_token = self._crypto.decrypt(current.access_token_enc)
        from src.modules.recruitment.infrastructure.calendar_adapter import CalendarAdapter
        from src.modules.recruitment.infrastructure.config import RecruitmentSettings

        adapter = CalendarAdapter(RecruitmentSettings(), self._http_client)
        try:
            calendars = await adapter.list_calendars(access_token)
            return calendars
        except Exception as exc:
            raise GoogleAuthError(f"Failed to list calendars: {exc}") from exc

    async def update_selected_calendar(self, calendar_id: str, hr: User) -> None:
        """Save the selected calendar ID on the Organization Google Connection.

        Args:
            calendar_id: The Google Calendar ID to set as selected.
            hr: The acting HR user.

        Raises:
            GoogleAuthError: If the connection is not active.
        """
        current = await self._connection_repo.get_singleton()
        if current is None or current.status != "connected":
            raise DomainAccessDeniedError("Google connection is not active")
        connection = OrganizationGoogleConnection(
            status=current.status,
            email=current.email,
            google_sub=current.google_sub,
            email_domain=current.email_domain,
            selected_calendar_id=calendar_id,
            credential_format_version=current.credential_format_version,
            credential_key_version=current.credential_key_version,
            access_token_enc=current.access_token_enc,
            refresh_token_enc=current.refresh_token_enc,
            client_secret_enc=current.client_secret_enc,
            token_expires_at=current.token_expires_at,
            connected_by_user_id=current.connected_by_user_id,
        )
        await self._connection_repo.upsert_singleton(connection)
        await self._audit_service.log_action(
            admin=hr,
            action_type=AuditActionType.ORG_GOOGLE_CONNECT,
            details={"result": "calendar_selected", "calendar_id": calendar_id},
        )
