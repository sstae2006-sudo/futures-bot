"""Dashboard controls for the existing paper-trading engine. Paper only,
enforced at runtime in `api/live_session.py`, not just by convention -- see
that module's docstring and `tests/test_api_routes.py::TestNoUnsafeTradingControls`.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..live_session import get_live_session_manager
from ..schemas import LiveSessionStartRequest, LiveSessionStatusOut

router = APIRouter(tags=["live"])

#: How often the SSE status stream re-checks the session -- a locked
#: in-memory dict read, not a query, so a short interval costs nothing.
_POLL_INTERVAL_SECONDS = 1.0


@router.post("/api/live/start", response_model=LiveSessionStatusOut)
def start_live_session(req: LiveSessionStartRequest) -> LiveSessionStatusOut:
    manager = get_live_session_manager()
    return LiveSessionStatusOut(**manager.start(req.live_symbol, req.resolution, req.poll_seconds))


@router.post("/api/live/stop", response_model=LiveSessionStatusOut)
def stop_live_session() -> LiveSessionStatusOut:
    manager = get_live_session_manager()
    return LiveSessionStatusOut(**manager.stop())


@router.get("/api/live/status", response_model=LiveSessionStatusOut)
def get_live_status() -> LiveSessionStatusOut:
    manager = get_live_session_manager()
    return LiveSessionStatusOut(**manager.status())


@router.get("/api/live/stream")
def stream_live_status(request: Request) -> StreamingResponse:
    """SSE: one frame per status change, streaming indefinitely (unlike a
    job's stream, a live session doesn't have a guaranteed terminal state
    to stop at -- the client closes this when it navigates away).

    Runs as an `async def` generator specifically so the disconnect check
    below can actually stop it. A plain `def` generator here gets driven by
    Starlette in a threadpool thread; cancelling the async task that awaits
    it (which is what happens when the client goes away) doesn't stop that
    thread -- it's blocked in a synchronous `time.sleep`, immune to
    cancellation, and loops forever. One such thread leaked per client
    disconnect, forever, was the bug. `await request.is_disconnected()` polls
    the ASGI connection state directly and lets the loop exit on its own.
    """
    manager = get_live_session_manager()

    async def event_stream() -> AsyncIterator[str]:
        last_payload = None
        while not await request.is_disconnected():
            status = manager.status()
            payload = json.dumps(status, default=str)
            if payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
