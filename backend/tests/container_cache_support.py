"""Invalidation for the DI containers' cached singletons.

Both container modules build their settings, database engine and storage
client through ``functools.lru_cache``, which is right for a process that
reads its environment once at boot and wrong for a test suite that repoints
that environment per fixture.

The trap is that it is invisible in isolation. A fixture that exports
``AUTH_DATABASE_URL`` and reloads ``src.main`` looks correct when its module
runs alone, because it is the first thing to build the cached engine. Run the
same module after anything else that already touched the container and the
export is ignored -- the app keeps the engine built from the ambient ``.env``
and the failure surfaces as ``InvalidPasswordError`` from inside the lifespan
handler, nowhere near the fixture that caused it.

``reset_cached_app_containers()`` is the counterpart to exporting the
variables: call it after changing them, and again after restoring them.
"""

from __future__ import annotations


def reset_cached_app_containers() -> None:
    """Drop every ``lru_cache``d singleton that reads process environment.

    Reloading ``src.main`` is not enough on its own: the container modules are
    already in ``sys.modules``, so a reload re-executes ``main`` without
    rebuilding the caches the app actually reads through.
    """
    from src.modules.identity import container as identity_container
    from src.modules.knowledge_base import container as kb_container

    for cached in (
        identity_container.get_settings,
        identity_container.get_jwt_utils,
        identity_container.get_crypto_utils,
        identity_container.get_redis_client,
        identity_container.get_rate_limiter,
        identity_container._get_async_session_maker,
        kb_container.get_kb_settings,
        kb_container.get_kb_minio_client,
    ):
        cached.cache_clear()
