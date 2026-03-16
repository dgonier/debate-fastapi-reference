"""Mock human auto-debater for end-to-end testing.

Connects via WebSocket and automatically responds to turn signals,
CX questions, and other prompts — enabling full 7-speech IPDA flow
validation without manual intervention.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import websockets

logger = logging.getLogger(__name__)

# Canned speeches for each speech type (human side)
CANNED_SPEECHES: Dict[str, str] = {
    "AC": (
        "The resolution should be affirmed. My first contention is that the status quo "
        "fails to address the core issue. The evidence clearly shows that change is "
        "necessary and beneficial. My second contention focuses on the practical benefits "
        "of the affirmative position. Studies demonstrate measurable improvements when "
        "these policies are adopted. In conclusion, the affirmative case is strong because "
        "we have both the moral imperative and the practical evidence to support change."
    ),
    "NC": (
        "The resolution should be negated. My first contention is that the affirmative "
        "case overstates the problem and understates the risks of change. The current "
        "system, while imperfect, provides stability that the affirmative would undermine. "
        "My second contention addresses the unintended consequences of the proposed change. "
        "Historical precedent shows that similar interventions have produced more harm than "
        "good. The negative position preserves what works while avoiding unnecessary risk."
    ),
    "1AR": (
        "Extending my affirmative case. First, on my first contention, the negative "
        "dropped the key evidence about systemic failure. This argument flows affirmative. "
        "Second, the negative's stability argument is non-unique — the status quo is "
        "already unstable. Third, on unintended consequences, the negative's historical "
        "examples are disanalogous because they occurred under fundamentally different "
        "conditions. The affirmative case still stands on every major issue."
    ),
    "NR": (
        "Extending the negative case. First, the 1AR's claim that I dropped the systemic "
        "failure argument is incorrect — I addressed it through my stability contention. "
        "Second, the non-uniqueness argument fails because the current system's challenges "
        "are manageable while the affirmative's proposal introduces entirely new risks. "
        "Third, my historical examples are directly analogous and the 1AR's attempt to "
        "distinguish them is unpersuasive. The negative case remains intact."
    ),
    "2AR": (
        "In this final speech, let me crystallize the debate. The key voting issue is "
        "whether the benefits of change outweigh the risks. The affirmative has shown "
        "concrete evidence of systemic problems. The negative's responses relied on "
        "speculation about risks rather than evidence. On balance, the affirmative "
        "position is better supported and should be affirmed."
    ),
}

CANNED_CX_QUESTIONS: List[str] = [
    "Can you explain the warrant behind your main contention?",
    "What specific evidence supports that claim?",
    "How do you respond to the counterargument that the status quo is adequate?",
    "Doesn't your position assume facts not in evidence?",
    "Can you quantify the impact you're claiming?",
]

CANNED_CX_ANSWERS: List[str] = [
    "The warrant is based on the empirical evidence I cited in my speech.",
    "Multiple studies support this position, as I outlined in my contention.",
    "The status quo is clearly inadequate given the evidence I've presented.",
    "No, my position is grounded in the evidence and analysis I provided.",
    "The impact is significant and well-documented in the literature.",
]

# Per-speech timeout (seconds) — generous to allow AI generation
SPEECH_TIMEOUT = 300


class AutoDebater:
    """WebSocket client that auto-responds to debate turn signals.

    Usage::

        debater = AutoDebater(base_url="http://localhost:8000", debate_id="abc123")
        result = await debater.run()
    """

    def __init__(
        self,
        base_url: str,
        debate_id: str,
        human_side: str = "aff",
        timeout: float = 1800,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.debate_id = debate_id
        self.human_side = human_side
        self.timeout = timeout

        self.events: List[Dict[str, Any]] = []
        self.judge_result: Optional[Dict[str, Any]] = None
        self._cx_question_count = 0
        self._cx_answer_count = 0
        self._completed = asyncio.Event()

    @property
    def ws_url(self) -> str:
        http_url = self.base_url
        ws_url = http_url.replace("http://", "ws://").replace("https://", "wss://")
        return f"{ws_url}/debates/{self.debate_id}/ws"

    async def run(self) -> Dict[str, Any]:
        """Connect and auto-debate until judge_result or timeout.

        Returns dict with keys: success, events, judge_result, speeches_completed.
        """
        logger.info("AutoDebater connecting to %s", self.ws_url)

        try:
            async with websockets.connect(self.ws_url) as ws:
                receive_task = asyncio.create_task(self._receive_loop(ws))
                try:
                    await asyncio.wait_for(self._completed.wait(), timeout=self.timeout)
                except asyncio.TimeoutError:
                    logger.error("AutoDebater timed out after %ss", self.timeout)
                finally:
                    receive_task.cancel()
                    try:
                        await receive_task
                    except asyncio.CancelledError:
                        pass
        except Exception as e:
            logger.error("AutoDebater connection error: %s", e)
            return {
                "success": False,
                "error": str(e),
                "events": self.events,
                "judge_result": None,
                "speeches_completed": self._get_completed_speeches(),
            }

        return {
            "success": self.judge_result is not None,
            "events": self.events,
            "judge_result": self.judge_result,
            "speeches_completed": self._get_completed_speeches(),
        }

    async def _receive_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Listen for events and auto-respond."""
        async for raw in ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Non-JSON message: %s", raw[:100])
                continue

            self.events.append(data)
            event_type = data.get("type", "")
            logger.info("AutoDebater received: %s", event_type)

            try:
                await self._handle_event(data, ws)
            except Exception as e:
                logger.error("AutoDebater error handling %s: %s", event_type, e)

    async def _handle_event(
        self, data: Dict[str, Any], ws: websockets.WebSocketClientProtocol
    ) -> None:
        event_type = data.get("type", "")

        if event_type == "turn_signal":
            await self._handle_turn_signal(data, ws)
        elif event_type == "cx_question":
            await self._handle_cx_question(data, ws)
        elif event_type == "judge_result":
            self.judge_result = data
            self._completed.set()
        elif event_type == "error":
            logger.warning("Debate error: %s", data.get("message"))
        elif event_type == "disconnect":
            logger.info("Debate disconnected: %s", data.get("reason"))
            self._completed.set()

    async def _handle_turn_signal(
        self, data: Dict[str, Any], ws: websockets.WebSocketClientProtocol
    ) -> None:
        speech_type = data.get("speech_type", "")
        speaker = data.get("speaker", "")
        is_cx = data.get("is_cx", False)
        status = data.get("status", "")

        # Handle prep time — send end_prep_time once per prep phase
        if status == "prep_time":
            if not getattr(self, '_prep_ended', False):
                self._prep_ended = True
                logger.info("AutoDebater ending prep time for %s", speech_type)
                await asyncio.sleep(2)
                await ws.send(json.dumps({"action": "end_prep_time"}))
            return

        # Reset prep flag when entering active status
        if status == "active":
            self._prep_ended = False

        # Only act on human turns that are active
        if speaker != "human" or status != "active":
            return

        if is_cx:
            await self._handle_cx_turn(speech_type, ws)
        else:
            await self._submit_speech(speech_type, ws)

    async def _submit_speech(
        self, speech_type: str, ws: websockets.WebSocketClientProtocol
    ) -> None:
        """Submit a canned speech for the given speech type."""
        transcript = CANNED_SPEECHES.get(speech_type, f"Auto-generated speech for {speech_type}.")
        logger.info("AutoDebater submitting speech: %s (%d chars)", speech_type, len(transcript))

        # Small delay to simulate human typing
        await asyncio.sleep(1)

        await ws.send(json.dumps({
            "action": "submit_speech",
            "speech_type": speech_type,
            "transcript": transcript,
            "duration_seconds": 60.0,
            "word_count": len(transcript.split()),
        }))

    async def _handle_cx_turn(
        self, speech_type: str, ws: websockets.WebSocketClientProtocol
    ) -> None:
        """Handle a CX turn — ask questions then end CX."""
        # Determine if we're asking or answering based on speech type and side
        # AC-CX: neg asks, aff answers. NC-CX: aff asks, neg answers.
        if speech_type == "AC-CX":
            is_questioner = self.human_side == "neg"
        else:  # NC-CX
            is_questioner = self.human_side == "aff"

        if is_questioner:
            # Ask 2-3 questions then end CX
            for i in range(min(3, len(CANNED_CX_QUESTIONS))):
                await asyncio.sleep(0.5)
                q = CANNED_CX_QUESTIONS[self._cx_question_count % len(CANNED_CX_QUESTIONS)]
                self._cx_question_count += 1
                await ws.send(json.dumps({
                    "action": "cx_question",
                    "question": q,
                    "turn_number": i + 1,
                }))
                # Wait for AI answer before next question
                await asyncio.sleep(2)

            await asyncio.sleep(1)
            await ws.send(json.dumps({
                "action": "end_cx",
                "speech_type": speech_type,
            }))
        else:
            # We're answering — the AI will ask questions, we respond via cx_answer
            # Wait a bit then check for queued questions
            pass  # Answers are handled by cx_question events

    async def _handle_cx_question(
        self, data: Dict[str, Any], ws: websockets.WebSocketClientProtocol
    ) -> None:
        """Answer an AI-asked CX question."""
        answer = CANNED_CX_ANSWERS[self._cx_answer_count % len(CANNED_CX_ANSWERS)]
        self._cx_answer_count += 1

        await asyncio.sleep(0.5)
        await ws.send(json.dumps({
            "action": "cx_answer",
            "answer": answer,
            "question_ref": data.get("question"),
        }))

        # After answering 3 questions, end CX from human side
        if self._cx_answer_count % 3 == 0:
            # Determine current CX speech type from recent turn signals
            cx_speech = self._get_current_cx_speech()
            if cx_speech:
                logger.info("AutoDebater ending CX after %d answers: %s", self._cx_answer_count, cx_speech)
                await asyncio.sleep(1)
                await ws.send(json.dumps({
                    "action": "end_cx",
                    "speech_type": cx_speech,
                }))

    def _get_current_cx_speech(self) -> str | None:
        """Find the most recent CX speech type from turn signals."""
        for e in reversed(self.events):
            if e.get("type") == "turn_signal" and e.get("is_cx"):
                return e.get("speech_type")
        return None

    def _get_completed_speeches(self) -> List[str]:
        """Extract completed speech types from events."""
        return [
            e["speech_type"]
            for e in self.events
            if e.get("type") == "speech_text"
        ]
