"""Tests for the session-wide environment isolation installed by ``conftest``.

These tests are the regression net for the leak described in
``tests/env_isolation``: without it, every settings class in the repo reads
whatever the developer's ``.env`` and shell happen to hold, and the assertions
about *default* values in the per-module config tests silently stop meaning
anything.

Every test here is deterministic on a bare CI checkout. Nothing asserts against
the repo-root ``.env``, because CI has no ``.env`` -- an assertion that reads it
would pass vacuously there, which is the exact failure mode being fixed.

``TestNoShadowingBackendDotenv`` is the one deliberate exception: it asserts on
the developer's filesystem, so it is *not* a check CI can perform. That is the
point rather than an oversight. The file it guards against, ``backend/.env``,
only ever appears on a dev machine, and its damage -- silently shadowing the
repo-root ``.env`` -- lands on that machine alone. A test CI could run would
have to be vacuous here. It still passes deterministically on a bare checkout,
so it costs CI nothing; it simply cannot fail there.
"""

from __future__ import annotations

import os
from pathlib import Path

import dotenv
import pytest

from tests.env_isolation import (
    PINNED_SETTINGS_ENV,
    discover_settings_env_prefixes,
    isolate_environment,
    required_settings_env_vars,
    strip_ambient_settings_env,
)


class TestPrefixDiscovery:
    """The prefix list must come from the code, never from a hand-kept list."""

    def test_finds_every_settings_prefix_declared_in_src(self) -> None:
        prefixes = discover_settings_env_prefixes()

        assert prefixes >= {
            "AUTH_",
            "RECRUITMENT_",
            "GMAIL_",
            "EMPLOYEE_",
            "KB_",
            "ONBOARDING_",
            "ASSISTANT_LLM_",
        }

    def test_finds_prefix_declared_with_a_plain_dict(self) -> None:
        """``AssistantSettings`` uses ``model_config = {...}``, not ``SettingsConfigDict``.

        Discovery reads the resolved ``model_config`` off the class, so the two
        spellings are indistinguishable to it. A grep for ``SettingsConfigDict``
        would miss this one -- that is precisely why discovery is not a grep.
        """
        assert "ASSISTANT_LLM_" in discover_settings_env_prefixes()


class TestDotenvNeutralized:
    """Layer 1: ``.env`` must never reach ``os.environ`` inside pytest."""

    def test_load_dotenv_does_not_import_a_real_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """This dies if ``conftest`` stops calling ``isolate_environment()``.

        A genuine ``load_dotenv`` would read the file and set the sentinel; the
        neutralized one reports "nothing loaded" and leaves ``os.environ`` alone.
        """
        env_file = tmp_path / ".env"
        env_file.write_text("AUTH_JWT_SECRET_KEY=sentinel-from-a-real-dotenv-file\n")
        monkeypatch.delenv("AUTH_JWT_SECRET_KEY", raising=False)

        loaded = dotenv.load_dotenv(env_file, override=True)

        assert loaded is False
        assert "AUTH_JWT_SECRET_KEY" not in os.environ

    def test_neutralization_survives_a_fresh_from_import(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``src`` uses ``from dotenv import load_dotenv``, so patch the attribute.

        Binding happens at *src import* time, which is after conftest ran, so a
        fresh ``from``-import must still resolve to the neutralized callable.
        """
        from dotenv import load_dotenv

        env_file = tmp_path / ".env"
        env_file.write_text("KB_MINIO_PUBLIC_ENDPOINT=sentinel-endpoint\n")
        monkeypatch.delenv("KB_MINIO_PUBLIC_ENDPOINT", raising=False)

        assert load_dotenv(env_file, override=True) is False
        assert "KB_MINIO_PUBLIC_ENDPOINT" not in os.environ


class TestAmbientStripping:
    """Layer 2: a var exported in the developer's shell must not reach settings."""

    def test_strips_only_keys_carrying_a_settings_prefix(self) -> None:
        environ = {
            "AUTH_JWT_SECRET_KEY": "leaked",
            "KB_MINIO_PUBLIC_ENDPOINT": "leaked",
            "PATH": "/usr/bin",
            "POSTGRES_PASSWORD": "not-a-settings-prefix",
        }

        removed = strip_ambient_settings_env(environ, {"AUTH_", "KB_"})

        assert removed == ["AUTH_JWT_SECRET_KEY", "KB_MINIO_PUBLIC_ENDPOINT"]
        assert environ == {"PATH": "/usr/bin", "POSTGRES_PASSWORD": "not-a-settings-prefix"}

    def test_leaves_an_environment_without_settings_vars_untouched(self) -> None:
        environ = {"PATH": "/usr/bin"}

        assert strip_ambient_settings_env(environ, {"AUTH_"}) == []
        assert environ == {"PATH": "/usr/bin"}

    def test_isolate_environment_removes_a_poisoned_ambient_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-running isolation must clear a prefixed var set after session start."""
        monkeypatch.setenv("ONBOARDING_SENTINEL_FROM_SHELL", "poison")

        removed = isolate_environment()

        assert "ONBOARDING_SENTINEL_FROM_SHELL" in removed
        assert "ONBOARDING_SENTINEL_FROM_SHELL" not in os.environ


class TestPinnedBaseline:
    """Stripping is only safe because a known-good baseline replaces it."""

    def test_every_required_settings_var_is_pinned(self) -> None:
        """The tripwire for a settings class gaining a required field.

        ``AuthSettings`` is built at import time by onboarding's config, so an
        unpinned required field is a collection error across many modules. This
        turns that into one readable failure naming the missing variable.
        """
        missing = required_settings_env_vars() - set(PINNED_SETTINGS_ENV)

        assert not missing, (
            f"New required settings field(s) {sorted(missing)} are not in "
            "PINNED_SETTINGS_ENV. Add a deterministic test value there."
        )

    def test_pinned_values_are_present_for_the_whole_session(self) -> None:
        """Whatever the developer's shell or CI holds, these are what tests see."""
        for name, value in PINNED_SETTINGS_ENV.items():
            assert os.environ[name] == value

    def test_pinning_beats_a_poisoned_ambient_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An ambient override of a pinned var must not survive isolation."""
        monkeypatch.setenv("AUTH_JWT_SECRET_KEY", "poisoned-from-ambient-env")

        isolate_environment()

        assert os.environ["AUTH_JWT_SECRET_KEY"] == PINNED_SETTINGS_ENV["AUTH_JWT_SECRET_KEY"]


class TestNoShadowingBackendDotenv:
    """``backend/.env`` must not exist -- it would shadow the repo-root ``.env``.

    Unlike every other test in this module, this one asserts on the developer's
    filesystem; see the module docstring for why that exception is deliberate.
    """

    def test_backend_dotenv_does_not_exist(self) -> None:
        backend_dotenv = Path(__file__).resolve().parents[1] / ".env"

        # ``.exists()`` only -- never read this file. If it is here, it holds
        # real secrets, and this failure message must not carry them.
        assert not backend_dotenv.exists(), (
            f"{backend_dotenv} exists and must be removed.\n"
            "python-dotenv's find_dotenv() walks upward from the calling file and "
            "stops at the FIRST .env it finds; load_dotenv() then reads only that "
            "one file. So backend/.env does not merge with the repo-root .env and "
            "is not a fallback for it -- it replaces it wholesale, and every key "
            "absent from backend/.env is simply unset.\n"
            "Fix: move any keys you need into the repo-root .env, then delete "
            "backend/.env. See backend/.env.example for the settings catalogue."
        )
