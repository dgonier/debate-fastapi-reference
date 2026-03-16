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

    **Receiving events:** The server pushes JSON event objects as they arrive
    from the debate agent. Each has a ``type`` field.

    **Sending actions:** The client sends JSON with an ``action`` field:

    - ``{"action": "submit_speech", "speech_type": "AC", "transcript": "..."}``
    - ``{"action": "cx_question", "question": "...", "turn_number": 1}``
    - ``{"action": "cx_answer", "answer": "...", "question_ref": "..."}``
    - ``{"action": "end_cx", "speech_type": "AC-CX"}``
    - ``{"action": "skip_cx", "speech_type": "AC-CX"}``
    - ``{"action": "end_prep_time"}``
    - ``{"action": "request_coaching", "for_speech": "1AR"}``
    - ``{"action": "request_evidence", "query": "...", "limit": 5}``
    """
    session = store.get(debate_id)
    if session is None:
        await ws.close(code=4004, reason=f"Debate {debate_id} not found")
        return

    handler: WebSocketDebateHandler = session._handler_ref  # type: ignore[attr-defined]

    await ws.accept()
    await handler.attach(ws)
    logger.info("WebSocket attached for debate %s", debate_id)

    try:
        while True:
            data = await ws.receive_json()
            action = data.get("action", "")

            logger.info("WS action: %s", action)

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
