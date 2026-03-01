# debate-fastapi-reference

Reference FastAPI app demonstrating [`debaterhub-sdk`](https://github.com/dgonier/debaterhub-sdk) in both Mode 1 (token-only) and Mode 2 (server-managed).

## Setup

```bash
git clone https://github.com/dgonier/debate-fastapi-reference.git
cd debate-fastapi-reference

pip install -e .

cp .env.example .env
# Edit .env with your LiveKit credentials

uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 for the test UI.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/debates/token-only` | Mode 1: returns LiveKit `ConnectionDetails` for frontend |
| `POST` | `/debates/managed` | Mode 2: creates server-managed session, returns `debate_id` |
| `GET` | `/debates/{id}/status` | Get turn tracker state for a managed session |
| `WS` | `/debates/{id}/ws` | WebSocket: receive events, send actions |
| `GET` | `/` | Test UI |

## Architecture

```
app/
├── main.py          # FastAPI app, lifespan, CORS, static mount
├── config.py        # pydantic-settings loading from .env
├── store.py         # In-memory dict: debate_id → ManagedDebateSession
├── handler.py       # WebSocketDebateHandler: bridges SDK events → WebSocket
└── routes/
    ├── debates.py   # POST /debates/token-only, POST /debates/managed, GET status
    └── ws.py        # WebSocket endpoint: event streaming + action dispatch
```

### Mode 1: Token-Only

```
Client → POST /debates/token-only → { server_url, room_name, participant_token }
Client → connects to LiveKit directly with token
```

### Mode 2: Server-Managed

```
Client → POST /debates/managed → { debate_id }
Client → WS /debates/{id}/ws
         ← receives events as JSON (turn_signal, speech_text, judge_result, ...)
         → sends actions as JSON (submit_speech, cx_question, end_prep_time, ...)
```

## WebSocket Actions

Send JSON with an `action` field:

```json
{"action": "submit_speech", "speech_type": "AC", "transcript": "My argument..."}
{"action": "cx_question", "question": "Can you clarify?", "turn_number": 1}
{"action": "cx_answer", "answer": "Yes, what I meant was...", "question_ref": "..."}
{"action": "end_cx", "speech_type": "AC-CX"}
{"action": "skip_cx", "speech_type": "AC-CX"}
{"action": "end_prep_time"}
{"action": "request_coaching", "for_speech": "1AR"}
{"action": "request_evidence", "query": "economic impact of AI", "limit": 5}
```

See [docs/websocket-protocol.md](docs/websocket-protocol.md) for the full protocol reference.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LIVEKIT_URL` | Yes | — | `wss://` LiveKit server URL |
| `LIVEKIT_API_KEY` | Yes | — | LiveKit API key |
| `LIVEKIT_API_SECRET` | Yes | — | LiveKit API secret |
| `DEBATE_AGENT_NAME` | No | `human-debate` | Deployed agent name |
| `WARMUP_URL` | No | — | Modal warmup endpoint |

## Documentation

- [Architecture](docs/architecture.md) — file layout, data flow, extension points
- [WebSocket Protocol](docs/websocket-protocol.md) — full event/action reference
- [SDK Documentation](https://github.com/dgonier/debaterhub-sdk) — SDK README, modes, events, state tracking
