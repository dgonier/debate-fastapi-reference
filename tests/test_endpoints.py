"""Unit tests for REST endpoints — uses mocked handler/session state."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import store
from app.handler import WebSocketDebateHandler
from app.routes.debates import _ensure_agent_warm


@pytest.fixture(autouse=True)
def _clear_store():
    """Clear the in-memory stores before each test."""
    store._sessions.clear()
    store._topics.clear()
    store._pending.clear()
    yield
    store._sessions.clear()
    store._topics.clear()
    store._pending.clear()


@pytest.fixture
def client():
    return TestClient(app)


def _make_fake_session(
    debate_id: str = "test123",
    human_side: str = "aff",
    belief_tree: dict | None = None,
    events: list | None = None,
    transcripts: dict | None = None,
    completed_speeches: list | None = None,
) -> MagicMock:
    """Create a mock ManagedDebateSession with a real handler."""
    handler = WebSocketDebateHandler()

    # Pre-populate handler state
    if belief_tree is not None:
        handler._belief_tree = belief_tree
    if events is not None:
        handler._event_history = events

    # Mock session with tracker
    session = MagicMock()
    session.connected = True
    session._handler_ref = handler

    tracker = MagicMock()
    tracker.human_side = human_side
    tracker.current_speech = "AC"
    tracker.current_speaker = "human"
    tracker.phase = "active"
    tracker.is_human_turn = True
    tracker.is_cx = False
    tracker.is_complete = False
    tracker.completed_speeches = completed_speeches or []
    tracker.transcripts = transcripts or {}
    session.tracker = tracker

    store.add(debate_id, session)
    return session


# ── Status endpoint ──────────────────────────────────────────────────

class TestStatusEndpoint:
    def test_status_returns_tracker_state(self, client):
        _make_fake_session("abc", transcripts={"AC": "speech text"}, completed_speeches=["AC"])
        resp = client.get("/debates/abc/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["debate_id"] == "abc"
        assert data["connected"] is True
        assert data["current_speech"] == "AC"
        assert data["phase"] == "active"
        assert data["is_human_turn"] is True
        assert data["status"] == "active"

    def test_status_404_when_not_found(self, client):
        resp = client.get("/debates/nonexistent/status")
        assert resp.status_code == 404

    def test_status_pending_debate(self, client):
        handler = WebSocketDebateHandler()
        pending = store.PendingDebate("pend1", None, "Test topic", handler)
        store.add_pending("pend1", pending)
        resp = client.get("/debates/pend1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "creating"
        assert data["connected"] is False


# ── Belief tree endpoints ────────────────────────────────────────────

class TestBeliefTreeEndpoint:
    def test_belief_tree_returns_tree(self, client):
        tree = {
            "beliefs": [
                {"id": "b1", "side": "aff", "claim": "Change is good"},
                {"id": "b2", "side": "neg", "claim": "Status quo works"},
            ],
            "topic": "Test topic",
        }
        _make_fake_session("bt1", belief_tree=tree)
        resp = client.get("/debates/bt1/belief-tree")
        assert resp.status_code == 200
        data = resp.json()
        assert data["debate_id"] == "bt1"
        assert data["tree"]["topic"] == "Test topic"
        assert len(data["tree"]["beliefs"]) == 2

    def test_belief_tree_null_when_not_available(self, client):
        _make_fake_session("bt2")
        resp = client.get("/debates/bt2/belief-tree")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tree"] is None

    def test_belief_tree_404(self, client):
        resp = client.get("/debates/nope/belief-tree")
        assert resp.status_code == 404

    def test_belief_tree_filtered_by_side(self, client):
        tree = {
            "beliefs": [
                {"id": "b1", "side": "aff", "claim": "Pro argument"},
                {"id": "b2", "side": "neg", "claim": "Con argument"},
                {"id": "b3", "side": "aff", "claim": "Another pro"},
            ],
        }
        _make_fake_session("bt3", belief_tree=tree)

        resp = client.get("/debates/bt3/belief-tree/aff")
        assert resp.status_code == 200
        data = resp.json()
        assert data["side"] == "aff"
        assert len(data["beliefs"]) == 2
        assert all(b["side"] == "aff" for b in data["beliefs"])

    def test_belief_tree_invalid_side(self, client):
        _make_fake_session("bt4")
        resp = client.get("/debates/bt4/belief-tree/both")
        assert resp.status_code == 400


# ── Events endpoint ──────────────────────────────────────────────────

class TestEventsEndpoint:
    def test_events_returns_history(self, client):
        events = [
            {"type": "debate_initializing", "topic": "Test", "timestamp": 1000.0},
            {"type": "debate_ready", "topic": "Test", "timestamp": 1001.0},
            {"type": "turn_signal", "speech_type": "AC", "timestamp": 1002.0},
        ]
        _make_fake_session("ev1", events=events)
        resp = client.get("/debates/ev1/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        assert len(data["events"]) == 3

    def test_events_filter_by_type(self, client):
        events = [
            {"type": "turn_signal", "speech_type": "AC", "timestamp": 1000.0},
            {"type": "speech_text", "speech_type": "AC", "timestamp": 1001.0},
            {"type": "turn_signal", "speech_type": "AC-CX", "timestamp": 1002.0},
        ]
        _make_fake_session("ev2", events=events)
        resp = client.get("/debates/ev2/events?event_type=turn_signal")
        data = resp.json()
        assert data["count"] == 2

    def test_events_filter_since(self, client):
        events = [
            {"type": "turn_signal", "timestamp": 1000.0},
            {"type": "speech_text", "timestamp": 2000.0},
            {"type": "turn_signal", "timestamp": 3000.0},
        ]
        _make_fake_session("ev3", events=events)
        resp = client.get("/debates/ev3/events?since=1500")
        data = resp.json()
        assert data["count"] == 2

    def test_events_404(self, client):
        resp = client.get("/debates/nope/events")
        assert resp.status_code == 404


# ── Transcripts endpoint ─────────────────────────────────────────────

class TestTranscriptsEndpoint:
    def test_transcripts_returns_data(self, client):
        _make_fake_session(
            "tr1",
            transcripts={"AC": "My case...", "NC": "Counter case..."},
            completed_speeches=["AC", "NC"],
        )
        resp = client.get("/debates/tr1/transcripts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["debate_id"] == "tr1"
        assert "AC" in data["transcripts"]
        assert "NC" in data["transcripts"]
        assert data["completed_speeches"] == ["AC", "NC"]

    def test_transcripts_empty(self, client):
        _make_fake_session("tr2")
        resp = client.get("/debates/tr2/transcripts")
        data = resp.json()
        assert data["transcripts"] == {}
        assert data["completed_speeches"] == []

    def test_transcripts_404(self, client):
        resp = client.get("/debates/nope/transcripts")
        assert resp.status_code == 404


# ── Topic endpoints ──────────────────────────────────────────────────

class TestTopicEndpoints:
    def test_create_topic(self, client):
        resp = client.post("/debates/topics", json={"topic": "UBI is good"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["topic"] == "UBI is good"
        assert data["topic_id"]
        assert data["status"] == "building"
        assert data["has_belief_tree"] is False
        assert data["debate_count"] == 0

    def test_list_topics(self, client):
        client.post("/debates/topics", json={"topic": "Topic A"})
        client.post("/debates/topics", json={"topic": "Topic B"})
        resp = client.get("/debates/topics")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_get_topic(self, client):
        create = client.post("/debates/topics", json={"topic": "My topic"}).json()
        resp = client.get(f"/debates/topics/{create['topic_id']}")
        assert resp.status_code == 200
        assert resp.json()["topic"] == "My topic"

    def test_get_topic_404(self, client):
        resp = client.get("/debates/topics/nonexistent")
        assert resp.status_code == 404

    def test_topic_belief_tree_empty(self, client):
        create = client.post("/debates/topics", json={"topic": "Some topic"}).json()
        resp = client.get(f"/debates/topics/{create['topic_id']}/belief-tree")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tree"] is None

    def test_topic_belief_tree_cached(self, client):
        # Simulate caching by setting it directly
        tid = "test_topic"
        topic = store.Topic(topic_id=tid, topic="Cached topic")
        topic.belief_tree = {"beliefs": [{"side": "aff", "claim": "Test"}], "topic": "Cached topic"}
        store.add_topic(tid, topic)

        resp = client.get(f"/debates/topics/{tid}/belief-tree")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tree"]["topic"] == "Cached topic"
        assert len(data["tree"]["beliefs"]) == 1

    def test_create_debate_with_topic_id(self, client):
        """Verify topic_id resolves correctly (debate creation itself is mocked)."""
        create = client.post("/debates/topics", json={"topic": "Resolved topic"}).json()
        # We can't actually create a managed debate without LiveKit, but we can
        # verify the topic resolution logic works for the request model
        topic = store.get_topic(create["topic_id"])
        assert topic is not None
        assert topic.topic == "Resolved topic"

    def test_create_debate_missing_topic_and_topic_id(self, client):
        """Must provide either topic or topic_id."""
        resp = client.post("/debates/managed", json={"human_side": "aff"})
        assert resp.status_code == 400 or resp.status_code == 422


# ── Warmup polling ──────────────────────────────────────────────────

class TestEnsureAgentWarm:
    def test_skips_when_no_warmup_url(self):
        handler = WebSocketDebateHandler()
        with patch("app.routes.debates.settings") as mock_settings:
            mock_settings.warmup_url = ""
            asyncio.get_event_loop().run_until_complete(_ensure_agent_warm(handler))
        # Should return immediately, no events forwarded
        assert handler.event_history == []

    def test_returns_immediately_when_ready(self):
        handler = WebSocketDebateHandler()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"status": "ready", "containers": 1}

        with patch("app.routes.debates.settings") as mock_settings, \
             patch("app.routes.debates.httpx.AsyncClient") as mock_client_cls:
            mock_settings.warmup_url = "https://warmup.example.com"
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            asyncio.get_event_loop().run_until_complete(_ensure_agent_warm(handler))

        # No warmup_progress events since it was ready on first poll
        assert all(e.get("type") != "warmup_progress" for e in handler.event_history)

    def test_polls_until_ready(self):
        handler = WebSocketDebateHandler()

        warming_resp = MagicMock()
        warming_resp.status_code = 200
        warming_resp.raise_for_status = MagicMock()
        warming_resp.json.return_value = {"status": "warming", "containers": 0}

        ready_resp = MagicMock()
        ready_resp.status_code = 200
        ready_resp.raise_for_status = MagicMock()
        ready_resp.json.return_value = {"status": "ready", "containers": 1}

        with patch("app.routes.debates.settings") as mock_settings, \
             patch("app.routes.debates.httpx.AsyncClient") as mock_client_cls, \
             patch("app.routes.debates.asyncio.sleep", new_callable=AsyncMock):
            mock_settings.warmup_url = "https://warmup.example.com"
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=[warming_resp, warming_resp, ready_resp])
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            asyncio.get_event_loop().run_until_complete(_ensure_agent_warm(handler))

        # Should have sent warmup_progress events for the 2 warming polls
        progress_events = [e for e in handler.event_history if e.get("type") == "warmup_progress"]
        assert len(progress_events) == 2
        assert progress_events[0]["attempt"] == 1
        assert progress_events[1]["attempt"] == 2
