"""WebSocket bridge: forwards SDK events to a connected WebSocket client."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

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


class WebSocketDebateHandler(DebateEventHandler):
    """Routes every debate event to the WebSocket as JSON.

    If no WebSocket is connected yet, events are buffered and flushed
    once a client attaches via :meth:`attach`.
    """

    def __init__(self) -> None:
        self._ws: WebSocket | None = None
        self._buffer: List[Dict[str, Any]] = []

    async def attach(self, ws: WebSocket) -> None:
        """Attach a WebSocket and flush any buffered events."""
        self._ws = ws
        for msg in self._buffer:
            await self._safe_send(msg)
        self._buffer.clear()

    def detach(self) -> None:
        self._ws = None

    # -- internal helpers --

    async def _forward(self, data: Dict[str, Any]) -> None:
        if self._ws is not None:
            await self._safe_send(data)
        else:
            self._buffer.append(data)

    async def _safe_send(self, data: Dict[str, Any]) -> None:
        try:
            await self._ws.send_json(data)  # type: ignore[union-attr]
        except Exception:
            logger.warning("Failed to send event to WebSocket")

    # -- event handlers (all 15 + disconnect) --

    async def on_debate_initializing(self, event: DebateInitializingEvent) -> None:
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
        await self._forward({
            "type": "turn_signal",
            "speech_type": event.speech_type,
            "speaker": event.speaker,
            "is_cx": event.is_cx,
            "time_limit": event.time_limit,
            "speech_index": event.speech_index,
            "status": event.status,
        })

    async def on_speech_text(self, event: SpeechTextEvent) -> None:
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
        await self._forward({
            "type": "speech_scored",
            "speech_type": event.speech_type,
            "score": event.score,
            "feedback": event.feedback,
            "dimensions": event.dimensions,
        })

    async def on_cx_question(self, event: CXQuestionEvent) -> None:
        await self._forward({
            "type": "cx_question",
            "question": event.question,
            "turn_number": event.turn_number,
            "strategy": event.strategy,
        })

    async def on_cx_answer(self, event: CXAnswerEvent) -> None:
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
        await self._forward({
            "type": "judging_started",
            "message": event.message,
            "estimated_seconds": event.estimated_seconds,
        })

    async def on_judge_result(self, event: JudgeResultEvent) -> None:
        await self._forward({
            "type": "judge_result",
            "winner": event.winner,
            "aff_score": event.aff_score,
            "neg_score": event.neg_score,
            "margin": event.margin,
            "decision": event.decision,
            "voting_issues": event.voting_issues,
        })

    async def on_error(self, event: ErrorEvent) -> None:
        await self._forward({
            "type": "error",
            "message": event.message,
            "code": event.code,
            "recoverable": event.recoverable,
        })

    async def on_belief_tree(self, event: BeliefTreeEvent) -> None:
        await self._forward({
            "type": "belief_tree",
            "tree": event.tree,
        })

    async def on_disconnect(self, reason: str = "") -> None:
        await self._forward({
            "type": "disconnect",
            "reason": reason,
        })
