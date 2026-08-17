"""What ``/api/auth/setup`` is allowed to run on, proven against a real database.

Why this file exists
--------------------
Two states got the same answer from ``POST /api/auth/setup`` and both answers
were wrong, because the endpoint decided "already set up" from the existence of
an ``organization_settings`` row instead of from the accounts that exist:

*Locked out.* Wiping ``users`` while the settings row survived produced a
deployment nobody could enter and nobody could rebuild.  ``GET
/api/auth/setup-status`` answered ``{"setup_complete": false}`` -- so the
frontend showed the setup screen -- while ``POST /api/auth/setup`` answered
``409 AUTH_SETUP_ALREADY_COMPLETED``.  The only exit was ``DELETE FROM
organization_settings`` by hand.

*Wide open.* The mirror image was worse.  On the live stack, with
``sysadmin@aidia.vn`` holding ``system_admin`` and the settings row absent, an
unauthenticated ``POST /api/auth/setup`` returned ``200`` and minted
``mallory@evil.example`` a second ``system_admin`` plus a live session.  A row
in a settings table was the only thing standing between an anonymous caller and
the administrative namespace.

The rule these tests pin
------------------------
Setup is the bootstrap for a deployment that has **no accounts at all**.  Once
any account exists the deployment is bootstrapped, and a missing ``system_admin``
is repaired by *promoting* an existing account -- which is exactly what
migration ``084`` and ``_bootstrap_super_admin``/``ensure_super_admin`` already
do, via ``AUTH_SUPER_ADMIN_EMAIL``.  Minting a brand-new administrator over a
populated database is not recovery; it is account takeover.

Every refusal answers identically.  Splitting "an admin exists" from "accounts
exist but no admin" would turn a public endpoint into an oracle reporting when
a deployment is in its degraded window, the same indistinguishability rule
``test_session_ownership_integration.py`` holds sessions to.

Why the harness looks like this
-------------------------------
Every assertion here is about the *whole* database -- "no account exists
anywhere" is not a statement a mocked session can make, and the previous
``setup`` tests are ``AsyncMock``s that assert against their own stubs.  So
these run the real router, the real container wiring, and the real
repositories, and they take a **private database** on the shared container:
counting rows globally is only meaningful when no other test module is seeding
users into the same tables.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from sqlmodel import select

from src.modules.identity.api.error_handler import register_auth_error_handlers
from src.modules.identity.api.router import router as auth_router
from src.modules.identity.container import get_db_session, get_rate_limiter
from src.modules.identity.domain.entities import User, UserRole
from src.modules.recruitment.domain.entities import OrganizationSettings
from src.modules.recruitment.infrastructure.org_settings_repository import (
    OrganizationSettingsRepository,
)
from tests.conftest import _create_probe_database

pytestmark = pytest.mark.integration

SETUP_BODY = {
    "organization_name": "Recovered Org",
    "name": "Recovery Admin",
    "email": "recovery-admin@example.com",
    "password": "RecoverMe123!",
    "password_confirmation": "RecoverMe123!",
}


# ---------------------------------------------------------------------------
# A private database, because these assertions are about global state
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def setup_db_url(postgres_async_url: str) -> str:
    """Create and migrate a database this module alone writes to.

    The session-scoped database is shared, and other integration modules commit
    users into it. ``count(*) FROM users == 0`` is the precondition of the whole
    file, so it needs tables nobody else touches.
    """
    return _create_probe_database(postgres_async_url, "setup_recovery_probe")


@pytest_asyncio.fixture
async def session_maker(setup_db_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a session factory bound to the private database, emptied first."""
    engine = create_async_engine(setup_db_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        await session.execute(text("DELETE FROM refresh_tokens"))
        await session.execute(text("DELETE FROM users"))
        await session.execute(text("DELETE FROM organization_settings"))
        await session.commit()
    try:
        yield maker
    finally:
        await engine.dispose()


class Stack:
    """The real auth router over the private database, with Redis stubbed out."""

    def __init__(self, app: FastAPI, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._app = app
        self._session_maker = session_maker

    async def post_setup(self, **overrides: str) -> tuple[int, dict]:
        """POST ``/api/auth/setup`` and return (status_code, body)."""
        transport = ASGITransport(app=self._app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/auth/setup", json={**SETUP_BODY, **overrides})
        return response.status_code, (response.json() if response.content else {})

    async def get_setup_status(self) -> tuple[int, dict]:
        """GET ``/api/auth/setup-status`` and return (status_code, body)."""
        transport = ASGITransport(app=self._app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/auth/setup-status")
        return response.status_code, response.json()

    async def seed_user(self, email: str, role: UserRole) -> User:
        """Commit one account, the way a running deployment already has some."""
        user = User(email=email, name=f"Seeded {role.value}", role=role)
        async with self._session_maker() as session:
            session.add(user)
            await session.commit()
        return user

    async def seed_settings_row(self, name: str) -> None:
        """Commit the ``organization_settings`` singleton the way setup leaves it."""
        async with self._session_maker() as session:
            session.add(
                OrganizationSettings(singleton_key="default", name=name, timezone="Asia/Bangkok")
            )
            await session.commit()

    async def accounts(self) -> list[tuple[str, UserRole]]:
        """Read every account straight from the database, newest last."""
        async with self._session_maker() as session:
            result = await session.execute(select(User).order_by(User.created_at))
            return [(user.email, user.role) for user in result.scalars().all()]

    async def organization_names(self) -> list[str]:
        """Read the name on every ``organization_settings`` row."""
        async with self._session_maker() as session:
            result = await session.execute(select(OrganizationSettings))
            return [row.name for row in result.scalars().all()]


class _AlwaysAllowRateLimiter:
    """Stands in for the Redis-backed limiter; rate limiting is not under test."""

    async def check_rate_limit(self, ip: str) -> bool:
        return True

    async def check_rate_limit_for(self, *args: Any, **kwargs: Any) -> bool:
        return True


@pytest.fixture
def stack(session_maker: async_sessionmaker[AsyncSession]) -> Stack:
    """Mount the real auth router; only the session source and Redis are swapped."""
    app = FastAPI()
    app.include_router(auth_router)
    register_auth_error_handlers(app)

    async def _session_override() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db_session] = _session_override
    app.dependency_overrides[get_rate_limiter] = _AlwaysAllowRateLimiter
    return Stack(app, session_maker)


# ---------------------------------------------------------------------------
# The state the owner got stuck in
# ---------------------------------------------------------------------------


async def test_setup_recovers_a_deployment_with_settings_but_no_accounts(stack: Stack) -> None:
    """A surviving settings row must not outlive the accounts as a setup gate.

    This is the reported lockout, byte for byte: the singleton row still says
    "Aidia Test Org" and ``users`` is empty. Nobody can log in, so the only way
    back is through setup itself.
    """
    await stack.seed_settings_row("Aidia Test Org")

    status_code, body = await stack.post_setup()

    assert status_code == 200, body
    assert body["user"]["role"] == UserRole.SYSTEM_ADMIN
    assert body["user"]["email"] == SETUP_BODY["email"]
    assert await stack.accounts() == [(SETUP_BODY["email"], UserRole.SYSTEM_ADMIN)]


async def test_setup_status_is_false_while_settings_survive_without_accounts(
    stack: Stack,
) -> None:
    """``setup-status`` and ``setup`` must not disagree about the same database.

    They did: the status endpoint reported ``false`` (frontend shows the setup
    screen) while the setup endpoint answered ``409``. The screen was reachable
    and useless.
    """
    await stack.seed_settings_row("Aidia Test Org")

    _, status_body = await stack.get_setup_status()
    setup_code, _ = await stack.post_setup()

    assert status_body == {"setup_complete": False}
    assert setup_code == 200


async def test_recovered_setup_renames_the_surviving_organization(stack: Stack) -> None:
    """Recovery adopts the singleton row rather than leaving a second one."""
    await stack.seed_settings_row("Aidia Test Org")

    await stack.post_setup(organization_name="Recovered Org")

    assert await stack.organization_names() == ["Recovered Org"]


# ---------------------------------------------------------------------------
# The account-minting surface
# ---------------------------------------------------------------------------


async def test_setup_refuses_while_a_system_admin_exists_and_settings_are_absent(
    stack: Stack,
) -> None:
    """The live takeover, as a test.

    ``sysadmin@aidia.vn`` holds ``system_admin`` and the settings row is gone.
    Before the fix this returned ``200`` and handed the caller a second
    ``system_admin`` with a session cookie.
    """
    await stack.seed_user("sysadmin@example.com", UserRole.SYSTEM_ADMIN)

    status_code, body = await stack.post_setup(email="mallory@evil.example")

    assert status_code == 409, body
    assert body["error"]["code"] == "AUTH_SETUP_ALREADY_COMPLETED"
    assert await stack.accounts() == [("sysadmin@example.com", UserRole.SYSTEM_ADMIN)]


async def test_setup_refuses_while_a_system_admin_exists_with_settings_present(
    stack: Stack,
) -> None:
    """The ordinary already-set-up deployment keeps being refused."""
    await stack.seed_settings_row("Aidia Test Org")
    await stack.seed_user("sysadmin@example.com", UserRole.SYSTEM_ADMIN)

    status_code, body = await stack.post_setup(email="mallory@evil.example")

    assert status_code == 409, body
    assert body["error"]["code"] == "AUTH_SETUP_ALREADY_COMPLETED"
    assert await stack.accounts() == [("sysadmin@example.com", UserRole.SYSTEM_ADMIN)]


@pytest.mark.parametrize("survivor_role", [UserRole.HR, UserRole.USER])
@pytest.mark.parametrize("settings_present", [True, False])
async def test_setup_refuses_when_accounts_survive_without_a_system_admin(
    stack: Stack, survivor_role: UserRole, settings_present: bool
) -> None:
    """Partial breakage is repaired by promotion, not by minting a new admin.

    A deployment holding real accounts and real employee data must not hand
    ``system_admin`` to whoever posts first. Migration ``084`` and
    ``ensure_super_admin`` recover this state by promoting a *named existing*
    account through ``AUTH_SUPER_ADMIN_EMAIL``; setup deliberately does not
    offer a second, unauthenticated road to the same role.
    """
    if settings_present:
        await stack.seed_settings_row("Aidia Test Org")
    await stack.seed_user("survivor@example.com", survivor_role)

    status_code, body = await stack.post_setup(email="mallory@evil.example")

    assert status_code == 409, body
    assert body["error"]["code"] == "AUTH_SETUP_ALREADY_COMPLETED"
    assert await stack.accounts() == [("survivor@example.com", survivor_role)]


@pytest.mark.parametrize(
    ("survivor_role", "expect_hint"),
    [
        pytest.param(UserRole.HR, True, id="no-admin-left-hint-is-the-only-way-back"),
        pytest.param(UserRole.SYSTEM_ADMIN, False, id="admin-still-there-hint-would-be-wrong"),
    ],
)
async def test_the_promotion_hint_reaches_the_log_and_only_when_it_applies(
    stack: Stack, caplog: pytest.LogCaptureFixture, survivor_role: UserRole, expect_hint: bool
) -> None:
    """The log is the *only* channel telling a locked-out operator the way back.

    Refusals are deliberately uniform over the wire, so nothing in the response
    distinguishes "you are already set up" from "your deployment lost every
    administrator". Without this the hint can be deleted, or fired at a
    deployment whose administrator is alive and well, with nothing going red.
    """
    await stack.seed_user("survivor@example.com", survivor_role)

    with caplog.at_level("WARNING", logger="src.modules.identity.application.auth_service"):
        status_code, _ = await stack.post_setup(email="mallory@evil.example")

    hinted = [record for record in caplog.records if "AUTH_SUPER_ADMIN_EMAIL" in record.message]
    assert status_code == 409
    assert bool(hinted) is expect_hint, caplog.text


async def test_every_refusal_is_indistinguishable_from_the_others(stack: Stack) -> None:
    """A public endpoint must not report which degraded state a deployment is in.

    "An admin exists" and "accounts exist but no admin" have to read the same
    from outside, or ``/setup`` becomes a probe telling an attacker exactly when
    the administrative namespace is empty.
    """
    await stack.seed_user("sysadmin@example.com", UserRole.SYSTEM_ADMIN)
    with_admin = await stack.post_setup(email="mallory@evil.example")

    async with stack._session_maker() as session:  # noqa: SLF001 - fixture-owned handle
        await session.execute(text("UPDATE users SET role = 'hr'"))
        await session.commit()
    without_admin = await stack.post_setup(email="mallory@evil.example")

    assert with_admin == without_admin


# ---------------------------------------------------------------------------
# Concurrency: the settings row is no longer the only serialization point
# ---------------------------------------------------------------------------


async def test_concurrent_recovery_setups_produce_exactly_one_admin(stack: Stack) -> None:
    """Two recoveries racing on a surviving settings row must not both win.

    Before, the unique ``singleton_key`` insert was what made concurrent setups
    serialize. In recovery the row already exists, so no insert happens and that
    protection is gone; the losing request has to be stopped by re-reading the
    account gate under the row lock.
    """
    await stack.seed_settings_row("Aidia Test Org")

    first, second = await asyncio.gather(
        stack.post_setup(email="first@example.com"),
        stack.post_setup(email="second@example.com"),
        return_exceptions=True,
    )

    codes = sorted(
        outcome[0] for outcome in (first, second) if not isinstance(outcome, BaseException)
    )
    assert codes == [200, 409], (first, second)
    assert len(await stack.accounts()) == 1


async def test_concurrent_first_run_setups_produce_exactly_one_admin(stack: Stack) -> None:
    """The same guarantee on a genuinely empty deployment, with no settings row."""
    first, second = await asyncio.gather(
        stack.post_setup(email="first@example.com"),
        stack.post_setup(email="second@example.com"),
        return_exceptions=True,
    )

    codes = sorted(
        outcome[0] for outcome in (first, second) if not isinstance(outcome, BaseException)
    )
    assert codes == [200, 409], (first, second)
    assert len(await stack.accounts()) == 1


async def _wait_for_lock_waiter(
    session_maker: async_sessionmaker[AsyncSession], timeout: float = 5.0
) -> None:
    """Block until PostgreSQL reports a backend waiting on a lock, or fail.

    Asking the database who is *actually* blocked is what makes the serialization
    test deterministic. Sleeping a fixed interval and asserting "the other
    request has not finished yet" passes just as well when that request never
    reached the lock at all.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        async with session_maker() as session:
            waiters = await session.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND wait_event_type = 'Lock' AND state = 'active'"
                )
            )
            if waiters.scalar_one() > 0:
                return
        await asyncio.sleep(0.02)
    raise AssertionError(
        "No backend ever blocked on a lock: the second setup was free to run "
        "its account check against a claim another transaction already held."
    )


@pytest.mark.parametrize(
    "seeded_organization_name",
    [
        pytest.param(SETUP_BODY["organization_name"], id="name-unchanged-by-the-claim"),
        pytest.param("Aidia Test Org", id="name-rewritten-by-the-claim"),
    ],
)
async def test_a_second_recovery_waits_on_the_claim_instead_of_racing_it(
    stack: Stack,
    session_maker: async_sessionmaker[AsyncSession],
    seeded_organization_name: str,
) -> None:
    """The claim on the settings row has to be a lock, not a side effect of writing.

    Re-reading the account gate after claiming only helps if the claim kept the
    other request out meanwhile. Here one transaction claims the singleton and
    writes its administrator without committing, exactly as an in-flight setup
    request does; a second setup arrives over HTTP and must wait rather than read
    a ``users`` table that is about to stop being empty.

    Both organization names matter, and only one of them is load-bearing. When
    the claim *renames* the row, the UPDATE it emits takes the row lock by
    itself, so this passes whether or not anyone asked for a lock. When the name
    the caller submits already matches the stored one, nothing is dirty, no
    UPDATE is emitted, and an explicit ``FOR UPDATE`` is the only thing left
    holding the two requests apart -- a recovering admin re-submitting the
    organization name already on screen is the ordinary case, not a corner one.
    """
    await stack.seed_settings_row(seeded_organization_name)

    async with session_maker() as holder:
        # An in-flight setup request: singleton claimed, admin written, uncommitted.
        await OrganizationSettingsRepository(holder).create_for_setup("Holder Org")
        holder.add(User(email="holder@example.com", name="Holder", role=UserRole.SYSTEM_ADMIN))
        await holder.flush()

        latecomer = asyncio.create_task(stack.post_setup(email="latecomer@example.com"))
        await _wait_for_lock_waiter(session_maker)
        assert not latecomer.done(), "the second setup ran straight past the held claim"

        await holder.commit()
        status_code, body = await latecomer

    assert status_code == 409, body
    assert await stack.accounts() == [("holder@example.com", UserRole.SYSTEM_ADMIN)]
