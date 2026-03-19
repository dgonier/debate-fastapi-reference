"""In-memory stores for topics and debate sessions."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from debaterhub import ManagedDebateSession


# ── Topic Store ──────────────────────────────────────────────────────

class Topic:
    """A debate topic with optional cached belief tree."""

    def __init__(self, topic_id: str, topic: str) -> None:
        self.topic_id = topic_id
        self.topic = topic
        self.belief_tree: Optional[Dict[str, Any]] = None
        self.debates: List[str] = []  # debate_ids created from this topic

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "topic": self.topic,
            "has_belief_tree": self.belief_tree is not None,
            "debate_count": len(self.debates),
            "debate_ids": self.debates,
        }


_topics: Dict[str, Topic] = {}


def add_topic(topic_id: str, topic: Topic) -> None:
    _topics[topic_id] = topic


def get_topic(topic_id: str) -> Topic | None:
    return _topics.get(topic_id)


def all_topics() -> List[Topic]:
    return list(_topics.values())


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
