# Debate FastAPI Sidecar

FastAPI server that runs IPDA debates via [`debaterhub-sdk`](https://github.com/dgonier/debaterhub-sdk). Your app connects over WebSocket, submits human speeches, and receives AI speeches, scoring, coaching, and judge results in real time.

## 60-Second Setup

```bash
git clone https://github.com/dgonier/debate-fastapi-reference.git
cd debate-fastapi-reference
pip install -e ".[test]"
cp .env.example .env   # then fill in credentials
uvicorn app.main:app --port 8000
```

Your `.env` needs three things from LiveKit Cloud (or self-hosted):

```
LIVEKIT_URL=wss://your-server.livekit.cloud
LIVEKIT_API_KEY=your-key
LIVEKIT_API_SECRET=your-secret
DEBATE_AGENT_NAME=human-debate-agent
WARMUP_URL=https://your-modal-warmup-endpoint.modal.run   # optional but recommended
```

> **Important:** If using the Modal-deployed agent, the `WARMUP_URL` is required on first use to wake up the agent container. Without it, the agent won't respond to dispatches.

## How It Works

```
Your App                    This Sidecar                LiveKit + AI Agent (Modal)
--------                    ------------                --------------------------
POST /debates/managed ────> creates LiveKit room ─────> dispatches debate agent
                      <──── { debate_id }

WS /debates/{id}/ws ──────> streams events
                      <──── debate_initializing         agent joining room...
                      <──── debate_ready                agent ready, prep done
                      <──── turn_signal (AC, human)     your turn to speak

submit_speech ────────>     data channel ─────────────> agent processes speech
                      <──── speech_scored               score + feedback
                      <──── turn_signal (AC-CX)         CX period starts
                      <──── cx_question                 AI asks question
cx_answer ────────────>     data channel ─────────────> agent records answer

                 ... 7 speeches total ...

                      <──── judging_started             panel judge running
                      <──── judge_result                winner + scores
```

## Integration Guide

### 1. Create a Debate

```http
POST /debates/managed
Content-Type: application/json

{
  "topic": "The United States should adopt universal basic income",
  "human_side": "aff",          // "aff" or "neg"
  "coaching_enabled": true,     // get coaching hints before speeches
  "evidence_enabled": true      // get evidence cards during prep
}
```

Response:
```json
{ "debate_id": "a1b2c3d4e5f6", "message": "Session created. Connect via WebSocket at /debates/a1b2c3d4e5f6/ws" }
```

### 2. Connect WebSocket

```
ws://localhost:8000/debates/{debate_id}/ws
```

Events start flowing immediately (buffered events flush on connect). If the debate doesn't exist, the connection closes with code `4004`.

### 3. Handle Events (Server -> Client)

Every event is a JSON object with a `type` field. Here's what your UI should do for each:

| Event | Payload Keys | What To Do |
|-------|-------------|------------|
| `debate_initializing` | `topic`, `human_side`, `message`, `estimated_seconds` | Show loading state with topic |
| `belief_tree` | `tree` | Render the argument tree (aff/neg beliefs with evidence) |
| `debate_ready` | `topic`, `human_side`, `speech_order`, `speech_time_limits` | Enable UI, show the 7-speech order and time limits |
| `turn_signal` | `speech_type`, `speaker`, `is_cx`, `time_limit`, `speech_index`, `status` | **Core event.** Update whose turn it is. `status` is `"active"`, `"waiting"`, `"prep_time"`, or `"complete"` |
| `speech_text` | `speech_type`, `text`, `word_count` | Display AI-generated speech text |
| `speech_progress` | `speech_type`, `stage`, `message` | Show generation progress (stages: `tactic`, `skeleton`, `evidence`, `speech`) |
| `cx_question` | `question`, `turn_number`, `strategy` | Display question. If it's the AI asking, prompt user for an answer |
| `cx_answer` | `answer`, `question_ref` | Display the answer in the CX exchange |
| `coaching_hint` | `for_speech`, `hints` | Show coaching panel — hints are strategic advice for the upcoming speech |
| `flow_update` | `speech_type`, `flow` | Update argument flow visualization (tracks which arguments are standing/attacked/dropped) |
| `evidence_result` | `query`, `cards`, `total_results` | Display evidence cards with tag, fulltext, source |
| `speech_scored` | `speech_type`, `score`, `feedback`, `dimensions` | Show score (0-1) and dimensional breakdown |
| `judging_started` | `message`, `estimated_seconds` | Show "judging in progress" spinner |
| `judge_result` | `winner`, `aff_score`, `neg_score`, `margin`, `decision`, `voting_issues` | **Show the winner and final decision** |
| `error` | `message`, `code`, `recoverable` | Display error. If `recoverable` is false, the debate is over |

### 4. Send Actions (Client -> Server)

Send JSON objects with an `action` field. Here's every action you can send:

#### Submit a speech (when `turn_signal` says it's the human's turn)
```json
{
  "action": "submit_speech",
  "speech_type": "AC",
  "transcript": "The resolution should be affirmed because...",
  "duration_seconds": 120.0,
  "word_count": 250
}
```

#### Ask a CX question (when human is the questioner in CX)
```json
{"action": "cx_question", "question": "Can you clarify your warrant?", "turn_number": 1}
```

#### Answer a CX question (when human is being questioned)
```json
{"action": "cx_answer", "answer": "My warrant is based on...", "question_ref": "Can you clarify?"}
```

#### End cross-examination
```json
{"action": "end_cx", "speech_type": "AC-CX"}
```

#### End prep time (before 1AR, prep time is offered automatically)
```json
{"action": "end_prep_time"}
```

#### Request coaching hints (on-demand, for any upcoming speech)
```json
{"action": "request_coaching", "for_speech": "1AR"}
```

#### Request evidence search
```json
{"action": "request_evidence", "query": "economic impact of UBI", "limit": 5}
```

## IPDA Speech Order

The debate follows the IPDA (International Public Debate Association) format — 7 speeches in a fixed order:

| # | Type | Who Speaks | Time | Notes |
|---|------|-----------|------|-------|
| 1 | **AC** | Aff | 5 min | Affirmative Constructive — present your case |
| 2 | **AC-CX** | Neg asks, Aff answers | 3 min | Cross-examination of the AC |
| 3 | **NC** | Neg | 6 min | Negative Constructive — present counter-case |
| 4 | **NC-CX** | Aff asks, Neg answers | 3 min | Cross-examination of the NC |
| 5 | **1AR** | Aff | 5 min | First Aff Rebuttal (prep time offered before) |
| 6 | **NR** | Neg | 5 min | Negative Rebuttal |
| 7 | **2AR** | Aff | 3 min | Second Aff Rebuttal — final word |

If `human_side` is `"aff"`, the human delivers AC, 1AR, 2AR, asks in NC-CX, and answers in AC-CX.
If `human_side` is `"neg"`, the human delivers NC, NR, asks in AC-CX, and answers in NC-CX.

## REST Endpoints

All REST endpoints are available alongside the WebSocket for querying state at any time:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/debates/managed` | Create a new debate session → `{debate_id}` |
| `POST` | `/debates/token-only` | Alternative: get LiveKit token for direct frontend connection |
| `GET` | `/debates/{id}/status` | Current turn tracker: `current_speech`, `phase`, `is_human_turn`, `completed_speeches` |
| `GET` | `/debates/{id}/belief-tree` | Full belief tree with all arguments and evidence |
| `GET` | `/debates/{id}/belief-tree/aff` | Affirmative beliefs only |
| `GET` | `/debates/{id}/belief-tree/neg` | Negative beliefs only |
| `GET` | `/debates/{id}/events` | Full event history (all events, timestamped) |
| `GET` | `/debates/{id}/events?event_type=speech_text` | Filter events by type |
| `GET` | `/debates/{id}/events?since=1710000000.0` | Events after a timestamp (for catch-up) |
| `GET` | `/debates/{id}/transcripts` | All recorded speech transcripts |
| `WS` | `/debates/{id}/ws` | WebSocket for live events and actions |

### Observer / Late-Join Pattern

An observer can reconstruct full debate state without having been connected from the start:

```bash
# 1. Catch up on everything that happened
curl http://localhost:8000/debates/{id}/events

# 2. Get current state
curl http://localhost:8000/debates/{id}/status

# 3. Get the argument tree
curl http://localhost:8000/debates/{id}/belief-tree

# 4. Connect WebSocket for live events going forward
wscat -c ws://localhost:8000/debates/{id}/ws
```

## Testing

**Unit tests** (instant, no server needed):
```bash
pytest tests/test_endpoints.py -v
```

**End-to-end** (requires running server + LiveKit + deployed agent):
```bash
# Terminal 1: start server
uvicorn app.main:app --port 8000

# Terminal 2: run E2E (takes ~25 minutes for a full debate)
RUN_E2E=1 pytest tests/test_e2e.py -v -s --timeout=2400
```

The E2E test uses `AutoDebater` — a mock human that automatically submits speeches, answers CX questions, and validates the full 7-speech flow through to `judge_result`.

## Project Structure

```
debate-fastapi-reference/
├── app/
│   ├── main.py           # FastAPI app, lifespan, CORS, routes
│   ├── config.py         # pydantic-settings: loads .env
│   ├── store.py          # In-memory debate_id → session map
│   ├── handler.py        # WebSocketDebateHandler: SDK events → WS + stores history
│   ├── auto_debater.py   # Mock human WS client for E2E testing
│   └── routes/
│       ├── debates.py    # REST: create, status, belief-tree, events, transcripts
│       └── ws.py         # WebSocket: event streaming + action dispatch
├── tests/
│   ├── conftest.py       # Dummy env vars for test isolation
│   ├── test_endpoints.py # 14 unit tests for REST endpoints
│   └── test_e2e.py       # Full debate E2E test with AutoDebater
├── static/
│   └── index.html        # Built-in test UI
├── docs/
│   ├── architecture.md   # Data flow and extension points
│   └── websocket-protocol.md  # Full event/action reference
├── llms.txt              # Context file for AI coding assistants
├── pyproject.toml
├── .env.example
└── .gitignore
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LIVEKIT_URL` | Yes | — | `wss://` LiveKit server URL |
| `LIVEKIT_API_KEY` | Yes | — | LiveKit API key |
| `LIVEKIT_API_SECRET` | Yes | — | LiveKit API secret |
| `DEBATE_AGENT_NAME` | No | `human-debate-agent` | Name of the deployed debate agent |
| `WARMUP_URL` | No | — | Modal warmup endpoint (call before first debate to wake the agent) |

## Key Concepts

- **Sidecar pattern**: This server sits between your app and LiveKit. Your app never touches LiveKit directly — it just sends/receives JSON over a WebSocket.
- **Event-driven**: All debate state changes come as events. Your UI is purely reactive — listen for events, update display.
- **Stateless client**: The client doesn't need to track debate state. Call `GET /status` or `GET /events` at any time to reconstruct state.
- **Scale-to-zero agent**: The AI debate agent runs on Modal and scales to zero when idle. The warmup URL wakes it up (~10s cold start).

## Further Reading

- [Architecture deep dive](docs/architecture.md)
- [WebSocket protocol reference](docs/websocket-protocol.md)
- [debaterhub-sdk documentation](https://github.com/dgonier/debaterhub-sdk)
