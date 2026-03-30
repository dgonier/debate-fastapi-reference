"""End-to-end integration test: spins up the server and runs AutoDebater.

Requires:
- .env with valid LiveKit credentials
- The debate agent deployed and reachable

Run: pytest tests/test_e2e.py -v -s --timeout=2400
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx
import pytest

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.auto_debater import AutoDebater, AIObserver

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:8000")

# Skip E2E if no server is running or env not configured
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_E2E", "").lower() not in ("1", "true", "yes"),
    reason="Set RUN_E2E=1 to run end-to-end tests (requires live server)",
)


@pytest.fixture
def base_url():
    return BASE_URL


async def _create_debate(
    base_url: str,
    topic: str = "The United States should adopt universal basic income",
    human_side: str = "aff",
) -> dict:
    """POST /debates/managed and return the response."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{base_url}/debates/managed",
            json={
                "topic": topic,
                "human_side": human_side,
                "coaching_enabled": True,
                "evidence_enabled": True,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()


class TestFullDebateE2E:
    """Full 7-speech IPDA debate via AutoDebater."""

    @pytest.mark.asyncio
    async def test_full_debate_completes(self, base_url):
        """Create a debate, auto-submit all human turns, verify judge result."""
        # Step 1: Create debate
        create_resp = await _create_debate(base_url)
        debate_id = create_resp["debate_id"]
        assert debate_id

        # Step 2: Run AutoDebater
        debater = AutoDebater(
            base_url=base_url,
            debate_id=debate_id,
            human_side="aff",
            timeout=2400,
        )
        result = await debater.run()

        # Step 3: Assertions
        assert result["success"], f"Debate did not complete. Events: {[e['type'] for e in result['events']]}"
        assert result["judge_result"] is not None
        assert result["judge_result"]["winner"] in ("aff", "neg")

        # Verify AI speeches have speech_text events (NC, NR are AI speeches for aff human)
        speech_types = result["speeches_completed"]
        for speech in ["NC", "NR"]:
            assert speech in speech_types, f"AI speech {speech} missing. Got: {speech_types}"

    @pytest.mark.asyncio
    async def test_rest_endpoints_after_debate(self, base_url):
        """After a debate, REST endpoints should return valid data."""
        # Create and run a debate
        create_resp = await _create_debate(base_url)
        debate_id = create_resp["debate_id"]

        debater = AutoDebater(
            base_url=base_url,
            debate_id=debate_id,
            human_side="aff",
            timeout=2400,
        )
        result = await debater.run()
        assert result["success"], "Debate must complete for REST endpoint tests"

        async with httpx.AsyncClient() as client:
            # Status endpoint
            status = await client.get(f"{base_url}/debates/{debate_id}/status", timeout=10)
            assert status.status_code == 200
            status_data = status.json()
            assert status_data["is_complete"] is True

            # Events endpoint
            events = await client.get(f"{base_url}/debates/{debate_id}/events", timeout=10)
            assert events.status_code == 200
            events_data = events.json()
            assert events_data["count"] > 0
            event_types = {e["type"] for e in events_data["events"]}
            assert "turn_signal" in event_types
            assert "judge_result" in event_types

            # Events filtered by type
            filtered = await client.get(
                f"{base_url}/debates/{debate_id}/events?event_type=speech_text",
                timeout=10,
            )
            assert filtered.status_code == 200
            assert all(e["type"] == "speech_text" for e in filtered.json()["events"])

            # Transcripts endpoint
            transcripts = await client.get(f"{base_url}/debates/{debate_id}/transcripts", timeout=10)
            assert transcripts.status_code == 200
            tr_data = transcripts.json()
            assert len(tr_data["completed_speeches"]) >= 5  # At least 5 non-CX speeches

            # Belief tree endpoint
            tree = await client.get(f"{base_url}/debates/{debate_id}/belief-tree", timeout=10)
            assert tree.status_code == 200

    @pytest.mark.asyncio
    async def test_neg_side_debate(self, base_url):
        """Human debating as neg side."""
        create_resp = await _create_debate(base_url, human_side="neg")
        debate_id = create_resp["debate_id"]

        debater = AutoDebater(
            base_url=base_url,
            debate_id=debate_id,
            human_side="neg",
            timeout=2400,
        )
        result = await debater.run()
        assert result["success"], f"Neg-side debate failed. Events: {[e['type'] for e in result['events']]}"
        assert result["judge_result"] is not None


async def _create_ai_debate(
    base_url: str,
    topic: str = "The United States should adopt universal basic income",
) -> dict:
    """POST /debates/ai-vs-ai and return the response."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{base_url}/debates/ai-vs-ai",
            json={"topic": topic},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()


class TestAIvsAIDebateE2E:
    """Full 7-speech AI-vs-AI IPDA debate."""

    @pytest.mark.asyncio
    async def test_ai_ai_debate_completes(self, base_url):
        """Create an AI-AI debate, observe all events, verify judge result."""
        # Step 1: Create AI-AI debate
        create_resp = await _create_ai_debate(base_url)
        debate_id = create_resp["debate_id"]
        assert debate_id

        # Step 2: Observe with AIObserver (no human input)
        observer = AIObserver(
            base_url=base_url,
            debate_id=debate_id,
            timeout=2400,
        )
        result = await observer.run()

        # Step 3: Assertions
        assert result["success"], f"AI-AI debate did not complete. Events: {[e['type'] for e in result['events']]}"
        assert result["judge_result"] is not None
        assert result["judge_result"]["winner"] in ("aff", "neg")

        # All 5 non-CX speeches should have speech_text events (all AI-generated)
        speech_types = result["speeches_completed"]
        for speech in ["AC", "NC", "1AR", "NR", "2AR"]:
            assert speech in speech_types, f"AI speech {speech} missing. Got: {speech_types}"


class TestDebateCreation:
    """Test debate creation without running full flow."""

    @pytest.mark.asyncio
    async def test_create_managed_returns_debate_id(self, base_url):
        create_resp = await _create_debate(base_url)
        assert "debate_id" in create_resp
        assert len(create_resp["debate_id"]) > 0

    @pytest.mark.asyncio
    async def test_status_after_create(self, base_url):
        create_resp = await _create_debate(base_url)
        debate_id = create_resp["debate_id"]

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base_url}/debates/{debate_id}/status", timeout=10)
            assert resp.status_code == 200
            data = resp.json()
            assert data["debate_id"] == debate_id

    @pytest.mark.asyncio
    async def test_404_nonexistent_debate(self, base_url):
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base_url}/debates/nonexistent/status", timeout=10)
            assert resp.status_code == 404
