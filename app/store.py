"""In-memory session store mapping debate_id → ManagedDebateSession."""

from __future__ import annotations

from typing import Dict

from debaterhub import ManagedDebateSession

_sessions: Dict[str, ManagedDebateSession] = {}


def add(debate_id: str, session: ManagedDebateSession) -> None:
    _sessions[debate_id] = session


def get(debate_id: str) -> ManagedDebateSession | None:
    return _sessions.get(debate_id)


def remove(debate_id: str) -> None:
    _sessions.pop(debate_id, None)


def all_ids() -> list[str]:
    return list(_sessions.keys())
