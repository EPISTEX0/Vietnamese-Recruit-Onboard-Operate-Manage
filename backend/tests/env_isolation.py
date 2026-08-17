"""One place to cut the test session off from the developer's environment.

Every settings class in ``src`` declares an ``env_prefix`` and **no**
``env_file``, so each one reads ``os.environ`` directly. That makes the process
environment a shared, mutable input to roughly every config assertion in the
suite, and two separate things fill it:

*The ``.env`` file.* ``src/main.py`` and the three workers call
``load_dotenv()`` at import time. Importing ``src.main`` or any worker -- even
transitively, three import hops down -- therefore dumps the whole repo-root
``.env`` into ``os.environ`` for the rest of the session. A test that asserts a
*default* value then reads the deployment value instead, and whether it does
depends on collection order. This is not hypothetical:
``tests/modules/identity/test_config.py::test_default_values`` failed in the
full suite and passed on its own until commit ``0857ac5``.

*The developer's shell.* An ``export AUTH_JWT_SECRET_KEY=...`` typed before
``pytest`` reaches the same settings classes by the same route, and no amount
of care inside ``src`` would stop it.

CI sees neither: there is no ``.env`` on a fresh checkout and no exports in the
runner, so ``load_dotenv()`` is a no-op and every affected test passes for the
wrong reason. Green CI is not evidence about this class of bug.

Both leaks are closed here, at session start, rather than by fixtures in the
test modules that happen to have been bitten. A local fixture only protects the
file that remembers to ask for it, and a settings class added next month would
be unprotected without anyone noticing. The same reasoning already shaped
``tests.postgres_support`` and ``tests.minio_support``, which pin their
credentials instead of inheriting ambient ones -- and, like them, this module
changes nothing in ``src``. ``load_dotenv()`` is a real production convenience
for running ``uvicorn`` on a host, and its ``override=False`` default makes it
inert in Docker, where compose has already put the values in the process.

The prefix list is *derived*, never hand-kept: ``discover_settings_env_prefixes``
imports the settings modules and reads the prefixes back off the classes. A new
``src/modules/<name>/infrastructure/config.py`` is covered the moment it exists,
with no edit here. Reading the resolved ``model_config`` also means the two
spellings in the repo -- ``SettingsConfigDict(env_prefix=...)`` and a plain
``{"env_prefix": ...}`` (``AssistantSettings``) -- are handled identically,
which a grep for ``SettingsConfigDict`` would not be.

Stripping alone would be wrong, and finding out why is the interesting part.
``AuthSettings`` is the one class in the repo with *required* fields, and
``src/modules/onboarding/infrastructure/config.py`` constructs it at **import**
time, so a bare ``AUTH_`` environment makes several test modules fail during
collection. Both environments were quietly feeding it: the repo-root ``.env``
locally, and ``AUTH_*`` job-level variables in ``.github/workflows/ci.yml``
remotely. Neither is a fact the test suite should depend on. So the ambient
values are removed and a pinned baseline is put back --
``PINNED_SETTINGS_ENV`` -- which is the same move ``tests.postgres_support``
makes with its database credentials, applied to settings instead.

``required_settings_env_vars`` keeps that baseline honest: it derives what the
classes actually demand, and ``tests/test_env_isolation.py`` fails with an
actionable message the day someone adds a required field without pinning it,
rather than leaving a collection error for the next person to debug.

Import order matters: ``conftest`` must call ``isolate_environment()`` before
anything imports ``src`` and before test modules run their own module-level
``os.environ.setdefault(...)`` calls. Stripping first is what turns those
``setdefault`` calls back into the defaults they are written to be, instead of
"whatever the developer already had".
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import dotenv

if TYPE_CHECKING:
    from collections.abc import Iterable, MutableMapping

# backend/src -- the package holding the settings classes.
SRC_DIR = Path(__file__).resolve().parents[1] / "src"

# Every settings class in the repo lives at this path. Globbing rather than
# listing modules is what makes a newly added module automatic.
SETTINGS_MODULE_GLOB = "modules/*/infrastructure/config.py"

# The only settings values the suite guarantees. These four are ``AuthSettings``
# required fields, which ``onboarding``'s config builds at import time, so the
# suite cannot collect without them. They are obviously fake so a real
# credential can never be mistaken for one.
#
# ``AUTH_OAUTH_TOKEN_ENCRYPTION_KEY`` decodes to 32 bytes, the length
# ``CryptoUtils`` requires (#333). It is the same key several ``get_crypto_utils``
# call sites build by hand as ``_TEST_KEY_B64`` to pre-encrypt fixture data, so
# ciphertext they write decrypts through the real, unpatched singleton too.
PINNED_SETTINGS_ENV = {
    "AUTH_GOOGLE_CLIENT_ID": "test-client-id",
    "AUTH_GOOGLE_CLIENT_SECRET": "test-client-secret",
    "AUTH_JWT_SECRET_KEY": "test-secret-key-32-chars-min-for-hs256",
    "AUTH_OAUTH_TOKEN_ENCRYPTION_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
}


def _no_dotenv(*_args: Any, **_kwargs: Any) -> bool:
    """Stand in for ``dotenv.load_dotenv``; load nothing, report nothing loaded."""
    return False


def neutralize_dotenv() -> None:
    """Make every ``load_dotenv()`` call in this process a no-op.

    ``src`` binds the function with ``from dotenv import load_dotenv`` at its
    own import time, which is *after* conftest runs, so replacing the attribute
    on the package is enough for the callers that matter.
    ``dotenv.main`` is patched too, for anyone importing from the submodule.
    """
    dotenv.load_dotenv = _no_dotenv
    dotenv.main.load_dotenv = _no_dotenv


def _settings_module_names() -> list[str]:
    """Return the dotted names of every settings module under ``src``."""
    return [
        ".".join(("src", *path.relative_to(SRC_DIR).with_suffix("").parts))
        for path in sorted(SRC_DIR.glob(SETTINGS_MODULE_GLOB))
    ]


def _all_subclasses(root: type) -> Iterable[type]:
    """Yield every subclass of ``root``, at any depth."""
    for subclass in root.__subclasses__():
        yield subclass
        yield from _all_subclasses(subclass)


def discover_settings_env_prefixes() -> frozenset[str]:
    """Return the ``env_prefix`` of every settings class reachable from ``src``.

    Imports the settings modules first so the classes exist to be walked. That
    import is cheap (the modules pull in nothing but ``pydantic_settings``) and
    it happens once per session.
    """
    from pydantic_settings import BaseSettings

    for name in _settings_module_names():
        importlib.import_module(name)

    return frozenset(
        prefix
        for subclass in _all_subclasses(BaseSettings)
        if (prefix := subclass.model_config.get("env_prefix"))
    )


def required_settings_env_vars() -> frozenset[str]:
    """Return the env var names every settings class requires to instantiate.

    Derived from the classes rather than listed, so ``PINNED_SETTINGS_ENV`` can
    be checked against reality instead of trusted.
    """
    from pydantic_settings import BaseSettings

    for name in _settings_module_names():
        importlib.import_module(name)

    return frozenset(
        f"{prefix}{field_name.upper()}"
        for subclass in _all_subclasses(BaseSettings)
        if (prefix := subclass.model_config.get("env_prefix"))
        for field_name, field in subclass.model_fields.items()
        if field.is_required()
    )


def strip_ambient_settings_env(
    environ: MutableMapping[str, str], prefixes: Iterable[str]
) -> list[str]:
    """Delete every ``environ`` key carrying one of ``prefixes``; return their names.

    Only prefixed keys go. Unprefixed infrastructure variables such as
    ``DATABASE_URL`` or ``POSTGRES_PASSWORD`` are left alone: no settings class
    claims them, and the container fixtures already pin what they need.
    """
    matches = tuple(prefixes)
    removed = sorted(key for key in environ if key.startswith(matches))
    for key in removed:
        del environ[key]
    return removed


def isolate_environment() -> list[str]:
    """Close both leaks for this process; return the variables that were stripped.

    Order is deliberate. ``load_dotenv`` is neutralized *before* the settings
    modules are imported, so discovery cannot itself be what pulls ``.env`` in;
    and the pinned baseline is applied *after* stripping, or it would be the
    first thing removed.
    """
    neutralize_dotenv()
    stripped = strip_ambient_settings_env(os.environ, discover_settings_env_prefixes())
    os.environ.update(PINNED_SETTINGS_ENV)
    return stripped
