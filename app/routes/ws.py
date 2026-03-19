"""WebSocket endpoint for Mode 2 event streaming and action dispatch."""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..handler import WebSocketDebateHandler
from .. import store

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/debates/{debate_id}/ws")
async def debate_websocket(ws: WebSocket, debate_id: str):
    """Stream debate events to the client and accept actions.

    Can connect immediately after ``POST /debates/managed`` — events
    buffer during setup and flush when the WebSocket attaches.

    **Receiving events:** JSON objects with a ``type`` field.
    **Sending actions:** JSON with an ``action`` field.
    """
    # Find handler from active session or pending debate
    handler = _find_handler(debate_id)
    if handler is None:
        await ws.close(code=4004, reason=f"Debate {debate_id} not found")
        return

    await ws.accept()
    await handler.attach(ws)
    logger.info("WebSocket attached for debate %s", debate_id)

    try:
        while True:
            data = await ws.receive_json()
            action = data.get("action", "")
            logger.info("WS action: %s", action)

            # Get session — may not exist yet if still creating
            session = store.get(debate_id)
            if session is None:
                pending = store.get_pending(debate_id)
                if pending and pending.status == "creating":
                    await ws.send_json({
                        "type": "error",
                        "message": "Debate is still being set up. Actions will be available shortly.",
                        "code": "NOT_READY",
                        "recoverable": True,
                    })
                    continue
                elif pending and pending.status == "failed":
                    await ws.send_json({
                        "type": "error",
                        "message": f"Debate setup failed: {pending.error}",
                        "code": "SETUP_FAILED",
                        "recoverable": False,
                    })
                    continue
                else:
                    await ws.send_json({
                        "type": "error",
                        "message": "Debate session not found",
                        "code": "NOT_FOUND",
                        "recoverable": False,
                    })
                    continue

            if action == "submit_speech":
                await session.submit_speech(
                    speech_type=data["speech_type"],
                    transcript=data["transcript"],
                    duration_seconds=data.get("duration_seconds", 0.0),
                    word_count=data.get("word_count"),
                )
            elif action == "cx_question":
                await session.submit_cx_question(
                    question=data["question"],
                    turn_number=data.get("turn_number", 0),
                )
            elif action == "cx_answer":
                await session.submit_cx_answer(
                    answer=data["answer"],
                    question_ref=data.get("question_ref"),
                )
            elif action == "end_cx":
                await session.end_cx(data["speech_type"])
            elif action == "skip_cx":
                await session.skip_cx(data["speech_type"])
            elif action == "end_prep_time":
                await session.end_prep_time()
            elif action == "request_coaching":
                await session.request_coaching(data["for_speech"])
            elif action == "request_evidence":
                await session.request_evidence(
                    query=data["query"],
                    limit=data.get("limit", 5),
                )
            else:
                await ws.send_json({"type": "error", "message": f"Unknown action: {action}"})

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for debate %s", debate_id)
    finally:
        handler.detach()


def _find_handler(debate_id: str) -> WebSocketDebateHandler | None:
    """Find the handler from active session or pending debate."""
    session = store.get(debate_id)
    if session is not None:
        return session._handler_ref  # type: ignore[attr-defined]

    pending = store.get_pending(debate_id)
    if pending is not None:
        return pending.handler

    return None
