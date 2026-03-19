"""In-memory stores for topics and debate sessions."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from debaterhub import ManagedDebateSession


# ── Topic Store ──────────────────────────────────────────────────────

class Topic:
    """A debate topic with optional cached belief tree."""

    def __init__(self, topic_id: str, topic: str) -> None:
        self.topic_id = topic_id
        self.topic = topic
        self.status: str = "pending"  # pending | ready | failed
        self.belief_tree: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self.debates: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "topic": self.topic,
            "status": self.status,
            "has_belief_tree": self.belief_tree is not None,
            "debate_count": len(self.debates),
            "debate_ids": self.debates,
            "error": self.error,
        }


_topics: Dict[str, Topic] = {}


def add_topic(topic_id: str, topic: Topic) -> None:
    _topics[topic_id] = topic


def get_topic(topic_id: str) -> Topic | None:
    return _topics.get(topic_id)


def all_topics() -> List[Topic]:
    return list(_topics.values())


# ── Pending Debate Store ─────────────────────────────────────────────

class PendingDebate:
    """A debate that's being set up in the background."""

    def __init__(
        self,
        debate_id: str,
        topic_id: Optional[str],
        topic_str: str,
        handler: Any,  # WebSocketDebateHandler
    ) -> None:
        self.debate_id = debate_id
        self.topic_id = topic_id
        self.topic_str = topic_str
        self.handler = handler
        self.status: str = "creating"  # creating | ready | failed
        self.error: Optional[str] = None
        self.session: Optional[ManagedDebateSession] = None
        self.client: Any = None  # DebateClient


_pending: Dict[str, PendingDebate] = {}


def add_pending(debate_id: str, pending: PendingDebate) -> None:
    _pending[debate_id] = pending


def get_pending(debate_id: str) -> PendingDebate | None:
    return _pending.get(debate_id)


def promote_pending(debate_id: str) -> None:
    """Move a pending debate to the active session store."""
    pending = _pending.pop(debate_id, None)
    if pending and pending.session:
        _sessions[debate_id] = pending.session


def fail_pending(debate_id: str, error: str) -> None:
    pending = _pending.get(debate_id)
    if pending:
        pending.status = "failed"
        pending.error = error


# ── Session Store ────────────────────────────────────────────────────

_sessions: Dict[str, ManagedDebateSession] = {}


def add(debate_id: str, session: ManagedDebateSession) -> None:
    _sessions[debate_id] = session


def get(debate_id: str) -> ManagedDebateSession | None:
    return _sessions.get(debate_id)


def remove(debate_id: str) -> None:
    _sessions.pop(debate_id, None)


def all_ids() -> list[str]:
    return list(_sessions.keys())
