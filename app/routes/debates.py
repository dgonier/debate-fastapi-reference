"""Topic and debate endpoints."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

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


# ── Request / Response Models ────────────────────────────────────────

class CreateTopicRequest(BaseModel):
    topic: str = Field(min_length=1, description="The debate resolution/topic text")


class TopicResponse(BaseModel):
    topic_id: str
    topic: str
    has_belief_tree: bool = False
    debate_count: int = 0
    debate_ids: list[str] = []


class CreateDebateRequest(BaseModel):
    topic: str | None = Field(default=None, min_length=1, description="Inline topic (use this OR topic_id)")
    topic_id: str | None = Field(default=None, description="Reference a previously created topic")
    human_side: str = Field(default="aff", pattern=r"^(aff|neg)$")
    coaching_enabled: bool = True
    evidence_enabled: bool = True


class TokenOnlyResponse(BaseModel):
    server_url: str
    room_name: str
    participant_token: str


class ManagedResponse(BaseModel):
    debate_id: str
    topic_id: str | None = None
    topic: str
    message: str = "Session created. Connect via WebSocket."


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


# ── Topics ───────────────────────────────────────────────────────────

@router.post("/topics", response_model=TopicResponse, tags=["topics"])
async def create_topic(req: CreateTopicRequest):
    """Create a reusable topic. Run multiple debates against the same topic.

    The belief tree will be generated on the first debate and cached here
    so subsequent debates skip the ~30s prep time.
    """
    topic_id = uuid.uuid4().hex[:12]
    topic = store.Topic(topic_id=topic_id, topic=req.topic)
    store.add_topic(topic_id, topic)
    return TopicResponse(**topic.to_dict())


@router.get("/topics", response_model=list[TopicResponse], tags=["topics"])
async def list_topics():
    """List all created topics."""
    return [TopicResponse(**t.to_dict()) for t in store.all_topics()]


@router.get("/topics/{topic_id}", response_model=TopicResponse, tags=["topics"])
async def get_topic(topic_id: str):
    """Get a topic by ID."""
    topic = store.get_topic(topic_id)
    if topic is None:
        raise HTTPException(404, f"Topic {topic_id} not found")
    return TopicResponse(**topic.to_dict())


@router.get("/topics/{topic_id}/belief-tree", tags=["topics"])
async def get_topic_belief_tree(topic_id: str) -> Dict[str, Any]:
    """Get the cached belief tree for a topic.

    The tree is populated after the first debate on this topic runs
    through its belief prep phase.
    """
    topic = store.get_topic(topic_id)
    if topic is None:
        raise HTTPException(404, f"Topic {topic_id} not found")
    if topic.belief_tree is None:
        return {"topic_id": topic_id, "topic": topic.topic, "tree": None, "message": "No belief tree yet — run a debate first"}
    return {"topic_id": topic_id, "topic": topic.topic, "tree": topic.belief_tree}


# ── Mode 1: Token-Only ──────────────────────────────────────────────

@router.post("/token-only", response_model=TokenOnlyResponse)
async def create_token_only(req: CreateDebateRequest):
    """Create a debate and return LiveKit connection details.

    The frontend connects to LiveKit directly with the returned token.
    """
    topic_str = _resolve_topic(req)
    client = _make_client()
    try:
        config = DebateConfig(
            topic=topic_str,
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

    You can either:
    - Pass ``topic`` directly (one-shot)
    - Pass ``topic_id`` referencing a previously created topic (reusable)

    Connect via WebSocket at ``/debates/{debate_id}/ws`` to receive events.
    """
    topic_str = _resolve_topic(req)
    debate_id = uuid.uuid4().hex[:12]
    client = _make_client()
    handler = WebSocketDebateHandler()

    config = DebateConfig(
        topic=topic_str,
        human_side=req.human_side,
        coaching_enabled=req.coaching_enabled,
        evidence_enabled=req.evidence_enabled,
    )
    session = await client.create_managed_session(config, handler)
    session._handler_ref = handler  # type: ignore[attr-defined]
    session._client_ref = client    # type: ignore[attr-defined]
    session._topic_id = req.topic_id  # type: ignore[attr-defined]
    store.add(debate_id, session)

    # Link debate to topic if using topic_id
    if req.topic_id:
        topic = store.get_topic(req.topic_id)
        if topic:
            topic.debates.append(debate_id)
            # Set up belief tree caching: when belief_tree event arrives,
            # cache it on the topic for reuse
            handler._topic_ref = topic  # type: ignore[attr-defined]

    return ManagedResponse(
        debate_id=debate_id,
        topic_id=req.topic_id,
        topic=topic_str,
    )


def _resolve_topic(req: CreateDebateRequest) -> str:
    """Resolve topic string from either topic or topic_id."""
    if req.topic_id:
        topic_obj = store.get_topic(req.topic_id)
        if topic_obj is None:
            raise HTTPException(404, f"Topic {req.topic_id} not found")
        return topic_obj.topic
    if req.topic:
        return req.topic
    raise HTTPException(400, "Provide either 'topic' or 'topic_id'")


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


# ── Belief Tree ──────────────────────────────────────────────────────

def _get_handler(debate_id: str) -> WebSocketDebateHandler:
    session = store.get(debate_id)
    if session is None:
        raise HTTPException(404, f"Debate {debate_id} not found")
    return session._handler_ref  # type: ignore[attr-defined]


@router.get("/{debate_id}/belief-tree")
async def get_belief_tree(debate_id: str) -> Dict[str, Any]:
    """Return the full belief tree for this debate.

    The belief tree contains structured arguments for both sides:

    ```json
    {
      "tree": {
        "topic": "...",
        "beliefs": [
          {
            "id": "b1",
            "side": "aff",
            "label": "Contention 1",
            "claim": "UBI reduces poverty",
            "arguments": [
              {
                "id": "a1",
                "claim": "UBI provides a safety net",
                "warrant": "Studies show...",
                "impact": "Reduces poverty by 40%",
                "evidence": [
                  {
                    "tag": "Stanford 2023 UBI Study",
                    "fulltext": "...",
                    "source": "Stanford University",
                    "source_url": "https://..."
                  }
                ]
              }
            ]
          }
        ]
      }
    }
    ```

    Filter by side: ``GET /debates/{id}/belief-tree/aff`` or ``/neg``
    """
    handler = _get_handler(debate_id)
    tree = handler.belief_tree
    if tree is None:
        return {"debate_id": debate_id, "tree": None, "message": "Belief tree not yet available"}
    return {"debate_id": debate_id, "tree": tree}


@router.get("/{debate_id}/belief-tree/{side}")
async def get_belief_tree_by_side(debate_id: str, side: str) -> Dict[str, Any]:
    """Return beliefs filtered by side (aff or neg)."""
    if side not in ("aff", "neg"):
        raise HTTPException(400, "side must be 'aff' or 'neg'")

    handler = _get_handler(debate_id)
    tree = handler.belief_tree
    if tree is None:
        return {"debate_id": debate_id, "side": side, "beliefs": [], "message": "Belief tree not yet available"}

    beliefs = tree.get("beliefs", [])
    filtered = [b for b in beliefs if b.get("side", "").lower() == side]
    return {"debate_id": debate_id, "side": side, "beliefs": filtered}


# ── Event History ────────────────────────────────────────────────────

@router.get("/{debate_id}/events")
async def get_events(
    debate_id: str,
    event_type: Optional[str] = None,
    since: Optional[float] = None,
) -> Dict[str, Any]:
    """Return event history. Supports filtering by type and timestamp.

    Query params:
    - event_type: filter to a specific event type (e.g. "speech_text")
    - since: only events after this unix timestamp (for replay/catch-up)
    """
    handler = _get_handler(debate_id)
    events = handler.event_history

    if event_type:
        events = [e for e in events if e.get("type") == event_type]
    if since is not None:
        events = [e for e in events if e.get("timestamp", 0) > since]

    return {"debate_id": debate_id, "events": events, "count": len(events)}


# ── Transcripts ──────────────────────────────────────────────────────

@router.get("/{debate_id}/transcripts")
async def get_transcripts(debate_id: str) -> Dict[str, Any]:
    """Return all recorded speech transcripts."""
    session = store.get(debate_id)
    if session is None:
        raise HTTPException(404, f"Debate {debate_id} not found")

    t = session.tracker
    return {
        "debate_id": debate_id,
        "transcripts": t.transcripts,
        "completed_speeches": t.completed_speeches,
    }
