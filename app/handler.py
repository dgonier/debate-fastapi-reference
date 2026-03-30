"""WebSocket bridge: forwards SDK events to a connected WebSocket client.

Includes Langfuse tracing — collects all debate events and flushes
a structured trace on debate completion.

Langfuse schema:
  Trace: "ipda_debate" (session_id=debate_id)
    ├── Span: "AC" (output=speech text)
    ├── Span: "AC-CX" with CX Q&A in metadata
    ├── Span: "NC" (output=speech text, model=bedrock/claude-sonnet-4)
    ├── ...
    └── Span: "judge_evaluation" (output=winner/scores)
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import WebSocket

from debaterhub import (
    BeliefTreeEvent,
    CoachingHintEvent,
    CXAnswerEvent,
    CXQuestionEvent,
    DebateEventHandler,
    DebateInitializingEvent,
    DebateReadyEvent,
    ErrorEvent,
    EvidenceResultEvent,
    FlowUpdateEvent,
    JudgeResultEvent,
    JudgingStartedEvent,
    SpeechProgressEvent,
    SpeechScoredEvent,
    SpeechTextEvent,
    TurnSignalEvent,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WebSocketDebateHandler(DebateEventHandler):
    """Routes debate events to WebSocket + collects for Langfuse batch ingestion."""

    def __init__(self, langfuse_keys: Optional[Dict[str, str]] = None, debate_id: str = "", topic_id: str = "") -> None:
        self._ws: WebSocket | None = None
        self._buffer: List[Dict[str, Any]] = []
        self._event_history: List[Dict[str, Any]] = []
        self._belief_tree: Optional[Dict[str, Any]] = None
        self._debate_config: Optional[Dict[str, Any]] = None

        # Langfuse batch ingestion
        self._lf_keys = langfuse_keys  # {public_key, secret_key, base_url}
        self._debate_id = debate_id
        self._topic_id = topic_id
        self._trace_id = str(uuid.uuid4())
        self._speech_observations: List[Dict[str, Any]] = []
        self._speech_start_times: Dict[str, str] = {}
        self._topic_str = ""

    @property
    def event_history(self) -> List[Dict[str, Any]]:
        return list(self._event_history)

    @property
    def belief_tree(self) -> Optional[Dict[str, Any]]:
        return self._belief_tree

    @property
    def debate_config(self) -> Optional[Dict[str, Any]]:
        return self._debate_config

    async def attach(self, ws: WebSocket) -> None:
        self._ws = ws
        for msg in self._buffer:
            await self._safe_send(msg)
        self._buffer.clear()

    def detach(self) -> None:
        self._ws = None

    # -- Langfuse batch helpers --

    def _add_observation(self, obs_type: str, body: Dict[str, Any]):
        """Queue an observation for batch ingestion."""
        self._speech_observations.append({"type": obs_type, "body": body})

    def _flush_to_langfuse(self):
        """Send all collected observations to Langfuse in one batch."""
        if not self._lf_keys or not self._speech_observations:
            return

        base_url = self._lf_keys.get("base_url", "https://langfuse.my-desk.ai")
        pk = self._lf_keys.get("public_key", "")
        sk = self._lf_keys.get("secret_key", "")

        # Build the trace event
        events = [
            {
                "type": "trace-create",
                "id": str(uuid.uuid4()),
                "timestamp": _now_iso(),
                "body": {
                    "id": self._trace_id,
                    "name": "ipda_debate",
                    "sessionId": self._debate_id,
                    "input": {"topic": self._topic_str, "debate_id": self._debate_id},
                    "metadata": {
                        "debate_id": self._debate_id,
                        "topic_id": self._topic_id,
                        "format": "IPDA",
                        "debate_mode": self._debate_config.get("debate_mode", "unknown") if self._debate_config else "unknown",
                    },
                },
            }
        ]

        # Add all queued observations
        for obs in self._speech_observations:
            obs["body"]["traceId"] = self._trace_id
            events.append({
                "type": obs["type"],
                "id": str(uuid.uuid4()),
                "timestamp": obs["body"].get("startTime", _now_iso()),
                "body": obs["body"],
            })

        # Send batch
        try:
            resp = httpx.post(
                f"{base_url}/api/public/ingestion",
                json={"batch": events},
                auth=(pk, sk),
                timeout=15.0,
            )
            if resp.status_code < 300:
                logger.info(f"Langfuse: flushed {len(events)} events for debate {self._debate_id}")
            else:
                logger.warning(f"Langfuse ingestion failed: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            logger.warning(f"Langfuse flush failed: {e}")

    # -- internal helpers --

    def _record(self, data: Dict[str, Any]) -> None:
        entry = {**data, "timestamp": time.time()}
        self._event_history.append(entry)

    async def _forward(self, data: Dict[str, Any]) -> None:
        self._record(data)
        if self._ws is not None:
            await self._safe_send(data)
        else:
            self._buffer.append(data)

    async def _safe_send(self, data: Dict[str, Any]) -> None:
        try:
            await self._ws.send_json(data)  # type: ignore[union-attr]
        except Exception:
            logger.warning("Failed to send event to WebSocket")

    # -- event handlers --

    async def on_debate_initializing(self, event: DebateInitializingEvent) -> None:
        self._debate_config = {
            "topic": event.topic,
            "human_side": event.human_side,
            "debate_mode": getattr(event, "debate_mode", "ai_human"),
        }
        self._topic_str = event.topic
        await self._forward({
            "type": "debate_initializing",
            "topic": event.topic,
            "human_side": event.human_side,
            "message": event.message,
            "estimated_seconds": event.estimated_seconds,
        })

    async def on_debate_ready(self, event: DebateReadyEvent) -> None:
        await self._forward({
            "type": "debate_ready",
            "topic": event.topic,
            "human_side": event.human_side,
            "speech_order": event.speech_order,
            "speech_time_limits": event.speech_time_limits,
        })

    async def on_turn_signal(self, event: TurnSignalEvent) -> None:
        speech_type = event.speech_type
        if event.status == "active":
            self._speech_start_times[speech_type] = _now_iso()

        await self._forward({
            "type": "turn_signal",
            "speech_type": speech_type,
            "speaker": event.speaker,
            "is_cx": event.is_cx,
            "time_limit": event.time_limit,
            "speech_index": event.speech_index,
            "status": event.status,
        })

    async def on_speech_text(self, event: SpeechTextEvent) -> None:
        # Record as a generation observation
        start = self._speech_start_times.get(event.speech_type, _now_iso())
        obs_id = str(uuid.uuid4())
        self._add_observation("generation-create", {
            "id": obs_id,
            "name": event.speech_type,
            "startTime": start,
            "endTime": _now_iso(),
            "model": "bedrock/claude-sonnet-4",
            "input": {"speech_type": event.speech_type, "debate_id": self._debate_id},
            "output": event.text,
            "metadata": {
                "word_count": event.word_count,
                "speech_type": event.speech_type,
                "debate_id": self._debate_id,
            },
            "usage": {"output": event.word_count},
        })

        await self._forward({
            "type": "speech_text",
            "speech_type": event.speech_type,
            "text": event.text,
            "word_count": event.word_count,
        })

    async def on_speech_progress(self, event: SpeechProgressEvent) -> None:
        await self._forward({
            "type": "speech_progress",
            "speech_type": event.speech_type,
            "stage": event.stage,
            "message": event.message,
        })

    async def on_flow_update(self, event: FlowUpdateEvent) -> None:
        await self._forward({
            "type": "flow_update",
            "speech_type": event.speech_type,
            "flow": event.flow,
        })

    async def on_coaching_hint(self, event: CoachingHintEvent) -> None:
        await self._forward({
            "type": "coaching_hint",
            "for_speech": event.for_speech,
            "hints": event.hints,
        })

    async def on_speech_scored(self, event: SpeechScoredEvent) -> None:
        self._add_observation("event-create", {
            "id": str(uuid.uuid4()),
            "name": f"scored_{event.speech_type}",
            "startTime": _now_iso(),
            "metadata": {
                "speech_type": event.speech_type,
                "score": event.score,
                "feedback": (event.feedback or "")[:200],
            },
        })
        await self._forward({
            "type": "speech_scored",
            "speech_type": event.speech_type,
            "score": event.score,
            "feedback": event.feedback,
            "dimensions": event.dimensions,
        })

    async def on_cx_question(self, event: CXQuestionEvent) -> None:
        self._add_observation("event-create", {
            "id": str(uuid.uuid4()),
            "name": f"cx_question_{event.turn_number}",
            "startTime": _now_iso(),
            "input": event.question,
            "metadata": {"turn_number": event.turn_number, "strategy": event.strategy},
        })
        await self._forward({
            "type": "cx_question",
            "question": event.question,
            "turn_number": event.turn_number,
            "strategy": event.strategy,
        })

    async def on_cx_answer(self, event: CXAnswerEvent) -> None:
        self._add_observation("event-create", {
            "id": str(uuid.uuid4()),
            "name": "cx_answer",
            "startTime": _now_iso(),
            "input": event.answer,
            "metadata": {"question_ref": event.question_ref},
        })
        await self._forward({
            "type": "cx_answer",
            "answer": event.answer,
            "question_ref": event.question_ref,
        })

    async def on_evidence_result(self, event: EvidenceResultEvent) -> None:
        await self._forward({
            "type": "evidence_result",
            "query": event.query,
            "cards": event.cards,
            "total_results": event.total_results,
        })

    async def on_judging_started(self, event: JudgingStartedEvent) -> None:
        self._speech_start_times["judge"] = _now_iso()
        await self._forward({
            "type": "judging_started",
            "message": event.message,
            "estimated_seconds": event.estimated_seconds,
        })

    async def on_judge_result(self, event: JudgeResultEvent) -> None:
        start = self._speech_start_times.get("judge", _now_iso())
        self._add_observation("generation-create", {
            "id": str(uuid.uuid4()),
            "name": "judge_evaluation",
            "startTime": start,
            "endTime": _now_iso(),
            "model": "bedrock/claude-sonnet-4",
            "input": {"action": "judge_all_speeches"},
            "output": {
                "winner": event.winner,
                "aff_score": event.aff_score,
                "neg_score": event.neg_score,
                "margin": event.margin,
                "decision": event.decision,
                "voting_issues": event.voting_issues,
            },
            "metadata": {"debate_id": self._debate_id},
        })

        # Add scores
        for name, value in [("aff_score", event.aff_score), ("neg_score", event.neg_score)]:
            self._speech_observations.append({
                "type": "score-create",
                "body": {
                    "id": str(uuid.uuid4()),
                    "traceId": self._trace_id,
                    "name": name,
                    "value": value,
                },
            })
        self._speech_observations.append({
            "type": "score-create",
            "body": {
                "id": str(uuid.uuid4()),
                "traceId": self._trace_id,
                "name": "winner",
                "value": 1 if event.winner == "aff" else 0,
                "comment": f"Winner: {event.winner}",
            },
        })

        await self._forward({
            "type": "judge_result",
            "winner": event.winner,
            "aff_score": event.aff_score,
            "neg_score": event.neg_score,
            "margin": event.margin,
            "decision": event.decision,
            "voting_issues": event.voting_issues,
        })

        # Flush to Langfuse immediately after judge result (debate is complete)
        self._flush_to_langfuse()

    async def on_error(self, event: ErrorEvent) -> None:
        await self._forward({
            "type": "error",
            "message": event.message,
            "code": event.code,
            "recoverable": event.recoverable,
        })

    async def on_belief_tree(self, event: BeliefTreeEvent) -> None:
        self._belief_tree = event.tree
        topic_ref = getattr(self, "_topic_ref", None)
        if topic_ref is not None and topic_ref.belief_tree is None:
            topic_ref.belief_tree = event.tree
        await self._forward({
            "type": "belief_tree",
            "tree": event.tree,
        })

    async def on_disconnect(self, reason: str = "") -> None:
        # Flush all collected observations to Langfuse as one batch
        self._flush_to_langfuse()
        await self._forward({
            "type": "disconnect",
            "reason": reason,
        })
