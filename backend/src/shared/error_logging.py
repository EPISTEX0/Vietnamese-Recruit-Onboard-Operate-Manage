"""Server-side recording for domain exceptions surfaced as HTTP responses.

``resolve_error_message`` deliberately keeps the instance ``message`` out of
the response body, because infrastructure adapters build it by interpolating
a third-party exception. That detail is still the most useful thing an
on-call engineer has, so the handlers record it here instead of publishing it.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("src.shared.error_logging")


def log_domain_exception(exc: Exception, *, module: str) -> None:
    """Record a domain exception with the full message the client will not see.

    Args:
        exc: The domain exception being turned into an HTTP response.
        module: Name of the module whose handler caught it, for filtering.
    """
    code = getattr(exc, "error_code", type(exc).__name__)
    status = getattr(exc, "status_code", 500)
    detail = getattr(exc, "message", "") or str(exc)

    logger.error(
        "%s error %s (HTTP %s): %s",
        module,
        code,
        status,
        detail,
        exc_info=exc,
    )
