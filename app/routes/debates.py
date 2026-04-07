"""Topic and debate endpoints — all creation is non-blocking."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from debaterhub import DebateClient, DebateConfig

from ..config import settings
from ..handler import WebSocketDebateHandler
from .. import store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/debates", tags=["debates"])

# Langfuse keys for batch ingestion (optional)
_langfuse_keys: dict | None = None
_pk = os.environ.get("LANGFUSE_PUBLIC_KEY")
_sk = os.environ.get("LANGFUSE_SECRET_KEY")
_url = os.environ.get("LANGFUSE_BASE_URL")
if _pk and _sk:
    _langfuse_keys = {"public_key": _pk, "secret_key": _sk, "base_url": _url or "https://langfuse.my-desk.ai"}
    logger.info("Langfuse batch ingestion configured")


def _make_client() -> DebateClient:
    return DebateClient(
        livekit_url=settings.livekit_url,
        livekit_api_key=settings.livekit_api_key,
        livekit_api_secret=settings.livekit_api_secret,
        agent_name=settings.debate_agent_name,
        warmup_url=settings.warmup_url or None,
    )


async def _ensure_agent_warm(handler: WebSocketDebateHandler, max_wait: int = 120) -> None:
    """Poll the warmup endpoint until the agent container reports 'ready'.

    Sends progress events to the WebSocket so the client knows what's happening.
    Skips if no warmup URL is configured.
    """
    url = settings.warmup_url
    if not url:
        return

    async with httpx.AsyncClient() as client:
        for attempt in range(max_wait // 3):
            try:
                resp = await client.get(url, timeout=15.0)
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status", "")
                containers = data.get("containers", 0)

                if status == "ready" and containers > 0:
                    logger.info("Agent warm: %s", data)
                    return

                logger.info("Agent warming (attempt %d): %s", attempt + 1, data)
                await handler._forward({
                    "type": "warmup_progress",
                    "message": f"Warming up debate agent... ({status})",
                    "attempt": attempt + 1,
                    "agent_status": status,
                })
            except Exception as e:
                logger.warning("Warmup poll failed (attempt %d): %s", attempt + 1, e)

            await asyncio.sleep(3)

    logger.warning("Agent did not become ready within %ds, proceeding anyway", max_wait)


# ── Request / Response Models ────────────────────────────────────────

class CreateTopicRequest(BaseModel):
    topic: str = Field(min_length=1, description="The debate resolution/topic text")


class TopicResponse(BaseModel):
    topic_id: str
    topic: str
    status: str = "building"  # building | ready | failed
    has_belief_tree: bool = False
    debate_count: int = 0
    debate_ids: list[str] = []
    error: str | None = None


class CreateDebateRequest(BaseModel):
    topic: str | None = Field(default=None, min_length=1, description="Inline topic (use this OR topic_id)")
    topic_id: str | None = Field(default=None, description="Reference a previously created topic")
    human_side: str = Field(default="aff", pattern=r"^(aff|neg)$")
    coaching_enabled: bool = True
    evidence_enabled: bool = True


class CreateAIDebateRequest(BaseModel):
    topic: str | None = Field(default=None, min_length=1, description="Inline topic (use this OR topic_id)")
    topic_id: str | None = Field(default=None, description="Reference a previously created topic")


class TokenOnlyResponse(BaseModel):
    server_url: str
    room_name: str
    participant_token: str


class ManagedResponse(BaseModel):
    debate_id: str
    topic_id: str | None = None
    topic: str
    status: str = "creating"
    message: str = "Debate is being created. Connect via WebSocket for events."


class StatusResponse(BaseModel):
    debate_id: str
    connected: bool
    status: str  # creating | ready | failed | active | complete
    current_speech: str | None = None
    current_speaker: str | None = None
    phase: str = "creating"
    is_human_turn: bool = False
    is_cx: bool = False
    is_complete: bool = False
    completed_speeches: list[str] = []
    error: str | None = None


# ── Topics ───────────────────────────────────────────────────────────

@router.post("/topics", response_model=TopicResponse, tags=["topics"])
async def create_topic(req: CreateTopicRequest):
    """Create a topic and start building the belief tree in the background.

    **Returns immediately.** The belief tree builds asynchronously
    (~30s to 30min depending on topic complexity).

    Poll ``GET /debates/topics/{id}`` to check status:
    - ``"building"`` — tree generation in progress
    - ``"ready"`` — tree available, debates will skip prep
    - ``"failed"`` — tree generation failed (debates still work, just slower)

    You don't have to wait for ``ready`` — starting a debate on a
    ``building`` topic works fine; the debate agent handles prep itself.
    """
    topic_id = uuid.uuid4().hex[:12]
    topic = store.Topic(topic_id=topic_id, topic=req.topic)
    topic.status = "building"
    store.add_topic(topic_id, topic)

    # Kick off background prep debate to build the belief tree
    asyncio.create_task(_build_topic_tree(topic))

    return TopicResponse(**topic.to_dict())


async def _build_topic_tree(topic: store.Topic) -> None:
    """Background: start a prep debate to generate the belief tree.

    Creates a temporary managed session. When the belief_tree event
    arrives, the handler caches it on the topic. Then we disconnect.
    """
    try:
        client = _make_client()
        handler = WebSocketDebateHandler()
        handler._topic_ref = topic  # type: ignore[attr-defined]

        # Ensure agent is warm before creating topic prep session
        await _ensure_agent_warm(handler)

        config = DebateConfig(
            topic=topic.topic,
            human_side="aff",
            coaching_enabled=False,
            evidence_enabled=True,
        )
        session = await client.create_managed_session(config, handler, warmup=False)

        # Wait for the belief tree event (timeout after 10 min)
        for _ in range(600):  # 600 × 1s = 10 min
            await asyncio.sleep(1)
            if topic.belief_tree is not None:
                topic.status = "ready"
                logger.info("Topic %s tree ready", topic.topic_id)
                break
        else:
            # Didn't get a tree in time — mark ready anyway (debates will do their own prep)
            topic.status = "ready"
            logger.warning("Topic %s tree build timed out, marking ready without tree", topic.topic_id)

        # Clean up the prep session
        try:
            await session.disconnect()
        except Exception:
            pass
        try:
            await client.close()
        except Exception:
            pass

    except Exception as e:
        logger.error("Topic %s tree build failed: %s", topic.topic_id, e)
        topic.status = "failed"
        topic.error = str(e)


@router.get("/topics", response_model=list[TopicResponse], tags=["topics"])
async def list_topics():
    """List all created topics."""
    return [TopicResponse(**t.to_dict()) for t in store.all_topics()]


@router.get("/topics/{topic_id}", response_model=TopicResponse, tags=["topics"])
async def get_topic(topic_id: str):
    """Poll topic status.

    ``status`` transitions: ``building`` → ``ready`` (tree available) or ``failed``.

    Once ``ready``, ``has_belief_tree`` is true and
    ``GET /debates/topics/{id}/belief-tree`` returns the full tree.
    """
    topic = store.get_topic(topic_id)
    if topic is None:
        raise HTTPException(404, f"Topic {topic_id} not found")
    return TopicResponse(**topic.to_dict())


@router.get("/topics/{topic_id}/belief-tree", tags=["topics"])
async def get_topic_belief_tree(topic_id: str) -> Dict[str, Any]:
    """Get the cached belief tree for a topic.

    The tree is populated after the first debate on this topic
    completes its belief prep phase.
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
    Note: this endpoint blocks until the room is created (~5s).
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


# ── Mode 2: Server-Managed (non-blocking) ───────────────────────────

@router.post("/managed", response_model=ManagedResponse)
async def create_managed(req: CreateDebateRequest):
    """Create a server-managed debate session.

    **Returns immediately** — the actual setup (room creation, agent
    dispatch, connection) happens in the background.

    Connect via WebSocket at ``/debates/{debate_id}/ws`` right away.
    Events will buffer until the agent is ready, then flush.

    Poll ``GET /debates/{debate_id}/status`` to check readiness,
    or just connect the WebSocket and wait for ``debate_initializing``.
    """
    topic_str = _resolve_topic(req)
    debate_id = uuid.uuid4().hex[:12]
    handler = WebSocketDebateHandler(
        langfuse_keys=_langfuse_keys,
        debate_id=debate_id,
        topic_id=req.topic_id or "",
    )

    # Create pending debate entry so WS connections work immediately
    pending = store.PendingDebate(
        debate_id=debate_id,
        topic_id=req.topic_id,
        topic_str=topic_str,
        handler=handler,
    )
    store.add_pending(debate_id, pending)

    # Link to topic
    if req.topic_id:
        topic = store.get_topic(req.topic_id)
        if topic:
            topic.debates.append(debate_id)
            handler._topic_ref = topic  # type: ignore[attr-defined]

    # Kick off setup in background
    asyncio.create_task(
        _setup_debate_background(debate_id, topic_str, req, handler)
    )

    return ManagedResponse(
        debate_id=debate_id,
        topic_id=req.topic_id,
        topic=topic_str,
        status="creating",
    )


async def _setup_debate_background(
    debate_id: str,
    topic_str: str,
    req: CreateDebateRequest,
    handler: WebSocketDebateHandler,
) -> None:
    """Background task: create LiveKit room, dispatch agent, connect session."""
    pending = store.get_pending(debate_id)
    if not pending:
        return

    try:
        # Ensure the Modal agent container is warm before creating the session
        await _ensure_agent_warm(handler)

        client = _make_client()
        config = DebateConfig(
            topic=topic_str,
            human_side=req.human_side,
            coaching_enabled=req.coaching_enabled,
            evidence_enabled=req.evidence_enabled,
        )
        session = await client.create_managed_session(config, handler, warmup=False)
        session._handler_ref = handler  # type: ignore[attr-defined]
        session._client_ref = client    # type: ignore[attr-defined]
        session._topic_id = req.topic_id  # type: ignore[attr-defined]

        # Promote to active
        pending.session = session
        pending.client = client
        pending.status = "ready"
        store.promote_pending(debate_id)
        logger.info("Debate %s setup complete", debate_id)

    except Exception as e:
        logger.error("Debate %s setup failed: %s", debate_id, e)
        store.fail_pending(debate_id, str(e))
        await handler._forward({
            "type": "error",
            "message": f"Debate setup failed: {e}",
            "code": "SETUP_FAILED",
            "recoverable": False,
        })


def _resolve_topic(req) -> str:
    """Resolve topic string from either topic or topic_id."""
    if req.topic_id:
        topic_obj = store.get_topic(req.topic_id)
        if topic_obj is None:
            raise HTTPException(404, f"Topic {req.topic_id} not found")
        return topic_obj.topic
    if req.topic:
        return req.topic
    raise HTTPException(400, "Provide either 'topic' or 'topic_id'")


# ── Mode 3: AI-vs-AI (non-blocking) ─────────────────────────────────

@router.post("/ai-vs-ai", response_model=ManagedResponse)
async def create_ai_vs_ai(req: CreateAIDebateRequest):
    """Create an AI-vs-AI debate where both sides are LLM-generated.

    **Returns immediately** — the debate runs autonomously in the background.
    Connect via WebSocket at ``/debates/{debate_id}/ws`` to observe events.

    All 7 speeches + CX periods are AI-generated. No human input needed.
    """
    topic_str = _resolve_topic(req)
    debate_id = uuid.uuid4().hex[:12]
    handler = WebSocketDebateHandler(
        langfuse_keys=_langfuse_keys,
        debate_id=debate_id,
        topic_id=req.topic_id or "",
    )

    pending = store.PendingDebate(
        debate_id=debate_id,
        topic_id=req.topic_id,
        topic_str=topic_str,
        handler=handler,
    )
    store.add_pending(debate_id, pending)

    if req.topic_id:
        topic = store.get_topic(req.topic_id)
        if topic:
            topic.debates.append(debate_id)
            handler._topic_ref = topic  # type: ignore[attr-defined]

    asyncio.create_task(
        _setup_ai_debate_background(debate_id, topic_str, handler)
    )

    return ManagedResponse(
        debate_id=debate_id,
        topic_id=req.topic_id,
        topic=topic_str,
        status="creating",
        message="AI-vs-AI debate is being created. Connect via WebSocket to observe.",
    )


async def _setup_ai_debate_background(
    debate_id: str,
    topic_str: str,
    handler: WebSocketDebateHandler,
) -> None:
    """Background: create AI-AI debate session."""
    pending = store.get_pending(debate_id)
    if not pending:
        return

    try:
        # Ensure the Modal agent container is warm before creating the session
        await _ensure_agent_warm(handler)

        client = _make_client()
        config = DebateConfig(
            topic=topic_str,
            debate_mode="ai_ai",
            human_side="aff",  # ignored in ai_ai mode
            coaching_enabled=False,
            evidence_enabled=False,
        )
        session = await client.create_managed_session(config, handler, warmup=False)
        session._handler_ref = handler  # type: ignore[attr-defined]
        session._client_ref = client    # type: ignore[attr-defined]

        pending.session = session
        pending.client = client
        pending.status = "ready"
        store.promote_pending(debate_id)
        logger.info("AI-AI debate %s setup complete", debate_id)

    except Exception as e:
        logger.error("AI-AI debate %s setup failed: %s", debate_id, e)
        store.fail_pending(debate_id, str(e))
        await handler._forward({
            "type": "error",
            "message": f"Debate setup failed: {e}",
            "code": "SETUP_FAILED",
            "recoverable": False,
        })


# ── Status ───────────────────────────────────────────────────────────

@router.get("/{debate_id}/status", response_model=StatusResponse)
async def get_status(debate_id: str):
    """Get the current state of a debate.

    Works for both pending (creating) and active debates.
    """
    # Check active sessions first
    session = store.get(debate_id)
    if session is not None:
        t = session.tracker
        return StatusResponse(
            debate_id=debate_id,
            connected=session.connected,
            status="complete" if t.is_complete else "active",
            current_speech=t.current_speech,
            current_speaker=t.current_speaker,
            phase=t.phase,
            is_human_turn=t.is_human_turn,
            is_cx=t.is_cx,
            is_complete=t.is_complete,
            completed_speeches=t.completed_speeches,
        )

    # Check pending debates
    pending = store.get_pending(debate_id)
    if pending is not None:
        return StatusResponse(
            debate_id=debate_id,
            connected=False,
            status=pending.status,
            phase=pending.status,
            error=pending.error,
        )

    raise HTTPException(404, f"Debate {debate_id} not found")


# ── Belief Tree ──────────────────────────────────────────────────────

def _get_handler(debate_id: str) -> WebSocketDebateHandler:
    """Get handler from active session or pending debate."""
    session = store.get(debate_id)
    if session is not None:
        return session._handler_ref  # type: ignore[attr-defined]

    pending = store.get_pending(debate_id)
    if pending is not None:
        return pending.handler

    raise HTTPException(404, f"Debate {debate_id} not found")


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
    """Return event history. Supports filtering by type and timestamp."""
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
        raise HTTPException(404, f"Debate {debate_id} not found or still creating")

    t = session.tracker
    return {
        "debate_id": debate_id,
        "transcripts": t.transcripts,
        "completed_speeches": t.completed_speeches,
    }
