# WebSocket Protocol

## Connection

```
WS /debates/{debate_id}/ws
```

Connect after creating a managed debate via `POST /debates/managed`. The server accepts the WebSocket and begins streaming events.

## Server → Client (Events)

All messages are JSON objects with a `type` field:

```json
{"type": "debate_initializing", "topic": "...", "human_side": "aff", "message": "...", "estimated_seconds": 30}
{"type": "debate_ready", "topic": "...", "human_side": "aff", "speech_order": [...], "speech_time_limits": {...}}
{"type": "turn_signal", "speech_type": "AC", "speaker": "human", "is_cx": false, "time_limit": 300, "speech_index": 0, "status": "active"}
{"type": "speech_text", "speech_type": "NC", "text": "...", "word_count": 450}
{"type": "speech_progress", "speech_type": "NC", "stage": "skeleton", "message": "Building argument structure..."}
{"type": "flow_update", "speech_type": "AC", "flow": {...}}
{"type": "coaching_hint", "for_speech": "1AR", "hints": [{...}]}
{"type": "speech_scored", "speech_type": "AC", "score": 0.75, "feedback": "...", "dimensions": [{...}]}
{"type": "cx_question", "question": "...", "turn_number": 1, "strategy": "..."}
{"type": "cx_answer", "answer": "...", "question_ref": "..."}
{"type": "evidence_result", "query": "...", "cards": [{...}], "total_results": 5}
{"type": "judging_started", "message": "...", "estimated_seconds": 30}
{"type": "judge_result", "winner": "aff", "aff_score": 0.82, "neg_score": 0.71, "margin": "clear", "decision": "...", "voting_issues": [...]}
{"type": "belief_tree", "tree": {...}}
{"type": "error", "message": "...", "code": "UNKNOWN", "recoverable": true}
{"type": "disconnect", "reason": "..."}
```

## Client → Server (Actions)

Send JSON with an `action` field:

### Submit a speech

```json
{
  "action": "submit_speech",
  "speech_type": "AC",
  "transcript": "I argue that artificial intelligence will benefit society...",
  "duration_seconds": 245.5,
  "word_count": 420
}
```

- `speech_type` (required): Which speech to submit (`"AC"`, `"1AR"`, `"2AR"`)
- `transcript` (required): Full speech text
- `duration_seconds` (optional, default 0): How long the speech took
- `word_count` (optional): Auto-calculated from transcript if omitted

### CX question

```json
{
  "action": "cx_question",
  "question": "Can you explain your second contention?",
  "turn_number": 1
}
```

### CX answer

```json
{
  "action": "cx_answer",
  "answer": "What I meant was that the economic data shows...",
  "question_ref": "optional-reference-to-question"
}
```

### End CX period

```json
{
  "action": "end_cx",
  "speech_type": "AC-CX"
}
```

### Skip CX period

```json
{
  "action": "skip_cx",
  "speech_type": "AC-CX"
}
```

### End prep time

```json
{
  "action": "end_prep_time"
}
```

### Request coaching

```json
{
  "action": "request_coaching",
  "for_speech": "1AR"
}
```

### Request evidence

```json
{
  "action": "request_evidence",
  "query": "economic impact of artificial intelligence",
  "limit": 5
}
```

## Error Handling

If an unknown action is sent:

```json
{"type": "error", "message": "Unknown action: invalid_action"}
```

If the debate session is not found when connecting:

```
WebSocket closed with code 4004, reason: "Debate abc123 not found"
```
