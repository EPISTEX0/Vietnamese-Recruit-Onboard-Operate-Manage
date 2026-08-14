"""Serialising assistant events as Server-Sent Events.

Both assistant routers turn the same event dicts into the same wire format and
handle a mid-stream failure the same way, so they do it here once.

The error handling is the part worth centralising.  An exception raised after
the response has started cannot become an HTTP status — the status line is long
gone — so it has to arrive as an ``error`` event inside the stream, or the
client sees a connection that simply stops and waits out its timeout.  The
frontend reader treats ``error`` as terminal exactly like ``done``.
"""

from __future__ import annotations

import json
import logging
import typing

from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)


def sse_response(
    events: typing.AsyncGenerator[dict[str, typing.Any], None],
    *,
    log_label: str,
) -> StreamingResponse:
    """Wrap an assistant event generator in an SSE ``StreamingResponse``.

    Args:
        events: Generator of ``{"event": name, "data": payload}`` dicts.
        log_label: Identifies the failing stream in the logs.

    Returns:
        A streaming response emitting ``event:``/``data:`` frames.
    """

    async def _frames() -> typing.AsyncGenerator[bytes, None]:
        try:
            async for event in events:
                payload = json.dumps(event["data"], ensure_ascii=False)
                yield f"event: {event['event']}\ndata: {payload}\n\n".encode()
        except Exception as exc:
            logger.exception("%s stream error", log_label)
            error_data = json.dumps({"error": str(exc)}, ensure_ascii=False)
            yield f"event: error\ndata: {error_data}\n\n".encode()

    return StreamingResponse(
        _frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx buffers proxied responses by default, which holds every
            # delta back until the stream ends — the opposite of streaming.
            "X-Accel-Buffering": "no",
        },
    )
