# WebSocket Protocol Reference

## Connection

```
WS /debates/{debate_id}/ws
```

Connect after creating a debate via `POST /debates/managed`. If the debate ID doesn't exist, the connection closes immediately with code `4004`.

On connect, any events that arrived before your WebSocket connected are flushed (the handler buffers them).

## Events (Server -> Client)

Every event is a JSON object with a `type` field. Below is every event type with its full payload.

### debate_initializing

Sent when the agent joins the room and starts preparing.

```json
{
  "type": "debate_initializing",
  "topic": "The US should adopt universal basic income",
  "human_side": "aff",
  "message": "Preparing debate...",
  "estimated_seconds": 30
}
```

### debate_ready

All prep is done, the debate is about to begin.

```json
{
  "type": "debate_ready",
  "topic": "The US should adopt universal basic income",
  "human_side": "aff",
  "speech_order": ["AC", "AC-CX", "NC", "NC-CX", "1AR", "NR", "2AR"],
  "speech_time_limits": {"AC": 300, "AC-CX": 180, "NC": 360, "NC-CX": 180, "1AR": 300, "NR": 300, "2AR": 180}
}
```

### turn_signal

**The most important event.** Tells you whose turn it is and what they should do.

```json
{
  "type": "turn_signal",
  "speech_type": "AC",
  "speaker": "human",
  "is_cx": false,
  "time_limit": 300,
  "speech_index": 0,
  "status": "active"
}
```

`status` values:
- `"waiting"` — about to start (brief pause)
- `"active"` — this turn is live, submit speech or ask/answer CX
- `"prep_time"` — prep time before a rebuttal, send `end_prep_time` when ready
- `"complete"` — debate is over

`speaker` values:
- `"human"` — the human debater should act
- `"ai"` — the AI is generating/speaking (just wait)

### speech_text

AI-generated speech text. Only sent for AI speeches (NC, NR when human is aff).

```json
{
  "type": "speech_text",
  "speech_type": "NC",
  "text": "The resolution should be negated because...",
  "word_count": 650
}
```

### speech_progress

Progress updates during AI speech generation.

```json
{
  "type": "speech_progress",
  "speech_type": "NC",
  "stage": "skeleton",
  "message": "Building argument structure..."
}
```

Stages in order: `tactic` → `skeleton` → `evidence` → `speech`

### cx_question

A cross-examination question (from AI or echoed back from human).

```json
{
  "type": "cx_question",
  "question": "What specific evidence supports your second contention?",
  "turn_number": 2,
  "strategy": "probe_evidence"
}
```

### cx_answer

A cross-examination answer.

```json
{
  "type": "cx_answer",
  "answer": "The evidence comes from the 2023 Stanford study...",
  "question_ref": "What specific evidence..."
}
```

### coaching_hint

Strategic coaching for the human's upcoming speech.

```json
{
  "type": "coaching_hint",
  "for_speech": "1AR",
  "hints": [
    {"priority": "high", "category": "refutation", "text": "Address the opponent's cost argument first"},
    {"priority": "medium", "category": "extension", "text": "Extend your economic growth contention"}
  ]
}
```

### flow_update

Argument flow visualization — tracks which arguments are standing, attacked, dropped, etc.

```json
{
  "type": "flow_update",
  "speech_type": "1AR",
  "flow": {
    "arguments": [...],
    "clash_points": [...],
    "voting_issues": [...]
  }
}
```

### evidence_result

Response to a `request_evidence` action.

```json
{
  "type": "evidence_result",
  "query": "economic impact of UBI",
  "cards": [
    {"tag": "UBI reduces poverty", "fulltext": "...", "source": "Stanford 2023", "cite": "..."}
  ],
  "total_results": 12
}
```

### speech_scored

Per-speech score and feedback.

```json
{
  "type": "speech_scored",
  "speech_type": "AC",
  "score": 0.75,
  "feedback": "Strong thesis but evidence could be more specific",
  "dimensions": [
    {"name": "argumentation", "score": 0.8, "max_score": 1.0, "reasoning": "..."},
    {"name": "evidence", "score": 0.6, "max_score": 1.0, "reasoning": "..."}
  ]
}
```

### belief_tree

The argument tree for the debate topic.

```json
{
  "type": "belief_tree",
  "tree": {
    "topic": "UBI",
    "beliefs": [
      {"id": "b1", "side": "aff", "claim": "UBI reduces poverty", "arguments": [...]},
      {"id": "b2", "side": "neg", "claim": "UBI is too expensive", "arguments": [...]}
    ]
  }
}
```

### judging_started

The panel judge is evaluating the complete debate.

```json
{
  "type": "judging_started",
  "message": "Panel judge is evaluating the debate...",
  "estimated_seconds": 60
}
```

### judge_result

**The final event.** Who won and why.

```json
{
  "type": "judge_result",
  "winner": "aff",
  "aff_score": 0.82,
  "neg_score": 0.71,
  "margin": "clear",
  "decision": "The affirmative demonstrated a stronger...",
  "voting_issues": ["Economic impact", "Feasibility"]
}
```

### error

Something went wrong.

```json
{
  "type": "error",
  "message": "Speech generation failed",
  "code": "GENERATION_ERROR",
  "recoverable": true
}
```

If `recoverable` is `false`, the debate cannot continue.

### disconnect

The debate session has ended.

```json
{
  "type": "disconnect",
  "reason": "client requested disconnect"
}
```

## Actions (Client -> Server)

Send JSON with an `action` field. Unknown actions receive an error response.

| Action | Required Fields | Optional Fields | When to Send |
|--------|----------------|-----------------|-------------|
| `submit_speech` | `speech_type`, `transcript` | `duration_seconds`, `word_count` | When `turn_signal` has `speaker: "human"` and `is_cx: false` |
| `cx_question` | `question` | `turn_number` | During CX when human is the questioner |
| `cx_answer` | `answer` | `question_ref` | When `cx_question` event received and human is the answerer |
| `end_cx` | `speech_type` | — | To end a CX period early |
| `skip_cx` | `speech_type` | — | To skip CX entirely |
| `end_prep_time` | — | — | When `turn_signal` has `status: "prep_time"` |
| `request_coaching` | `for_speech` | — | Any time before/during a human speech |
| `request_evidence` | `query` | `limit` (default 5) | Any time during the debate |

## Error Handling

Unknown actions:
```json
{"type": "error", "message": "Unknown action: invalid_action"}
```

Debate not found on connect:
```
WebSocket closed with code 4004, reason: "Debate {id} not found"
```

## Typical Event Sequence (Human as Aff)

```
debate_initializing
belief_tree              (may arrive, depends on agent prep)
debate_ready
turn_signal              AC / human / active
  → submit_speech AC
turn_signal              AC-CX / ai / active
cx_question              AI asks
  → cx_answer
cx_question              AI asks again
  → cx_answer
  → end_cx
turn_signal              NC / ai / active
speech_progress          tactic...skeleton...evidence...speech
speech_text              NC generated
flow_update
turn_signal              NC-CX / human / active
  → cx_question
cx_answer                AI answers
  → cx_question
  → end_cx
turn_signal              1AR / human / prep_time
  → end_prep_time
turn_signal              1AR / human / active
coaching_hint
  → submit_speech 1AR
turn_signal              NR / ai / active
speech_progress
speech_text              NR generated
flow_update
turn_signal              2AR / human / active
  → submit_speech 2AR
judging_started
judge_result             winner!
```
