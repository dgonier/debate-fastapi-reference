"""Debate creation endpoints: Mode 1 (token-only) and Mode 2 (managed)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from debaterhub import DebateClient, DebateConfig

from ..config import settings
from ..handler import WebSocketDebateHandler
from .. import store

router = APIRouter(prefix="/debates", tags=["debates"])


def _make_client() -> DebateClient:
    return DebateClient(
        livekit_url=settings.livekit_url,
        livekit_api_key=settings.livekit_api_key,
        livekit_api_secret=settings.livekit_api_secret,
        agent_name=settings.debate_agent_name,
        warmup_url=settings.warmup_url or None,
    )


class CreateDebateRequest(BaseModel):
    topic: str = Field(min_length=1)
    human_side: str = Field(default="aff", pattern=r"^(aff|neg)$")
    coaching_enabled: bool = True
    evidence_enabled: bool = True


class TokenOnlyResponse(BaseModel):
    server_url: str
    room_name: str
    participant_token: str


class ManagedResponse(BaseModel):
    debate_id: str
    message: str = "Session created. Connect via WebSocket at /debates/{debate_id}/ws"


class StatusResponse(BaseModel):
    debate_id: str
    connected: bool
    current_speech: str | None
    current_speaker: str | None
    phase: str
    is_human_turn: bool
    is_cx: bool
    is_complete: bool
    completed_speeches: list[str]


# ── Mode 1: Token-Only ──────────────────────────────────────────────

@router.post("/token-only", response_model=TokenOnlyResponse)
async def create_token_only(req: CreateDebateRequest):
    """Create a debate and return LiveKit connection details.

    The frontend connects to LiveKit directly with the returned token.
    """
    client = _make_client()
    try:
        config = DebateConfig(
            topic=req.topic,
            human_side=req.human_side,
            coaching_enabled=req.coaching_enabled,
            evidence_enabled=req.evidence_enabled,
        )
        details = await client.create_session(config)
        return TokenOnlyResponse(
            server_url=details.server_url,
            room_name=details.room_name,
            participant_token=details.participant_token,
        )
    finally:
        await client.close()


# ── Mode 2: Server-Managed ──────────────────────────────────────────

@router.post("/managed", response_model=ManagedResponse)
async def create_managed(req: CreateDebateRequest):
    """Create a server-managed debate session.

    The SDK joins the LiveKit room on the backend. Connect via WebSocket
    at ``/debates/{debate_id}/ws`` to receive events and send actions.
    """
    debate_id = uuid.uuid4().hex[:12]
    client = _make_client()
    handler = WebSocketDebateHandler()

    config = DebateConfig(
        topic=req.topic,
        human_side=req.human_side,
        coaching_enabled=req.coaching_enabled,
        evidence_enabled=req.evidence_enabled,
    )
    session = await client.create_managed_session(config, handler)
    # Store both session and handler (handler attached to session via closure)
    session._handler_ref = handler  # type: ignore[attr-defined]
    session._client_ref = client    # type: ignore[attr-defined]
    store.add(debate_id, session)

    return ManagedResponse(debate_id=debate_id)


# ── Status ───────────────────────────────────────────────────────────

@router.get("/{debate_id}/status", response_model=StatusResponse)
async def get_status(debate_id: str):
    """Get the current state of a managed debate session."""
    session = store.get(debate_id)
    if session is None:
        raise HTTPException(404, f"Debate {debate_id} not found")

    t = session.tracker
    return StatusResponse(
        debate_id=debate_id,
        connected=session.connected,
        current_speech=t.current_speech,
        current_speaker=t.current_speaker,
        phase=t.phase,
        is_human_turn=t.is_human_turn,
        is_cx=t.is_cx,
        is_complete=t.is_complete,
        completed_speeches=t.completed_speeches,
    )
