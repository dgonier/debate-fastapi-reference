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

### 1a. Create a Topic (Reusable)

Create a topic once — the belief tree builds in the background. Then run multiple debates against it without repeating the prep.

```http
POST /debates/topics
Content-Type: application/json

{ "topic": "The United States should adopt universal basic income" }
```

Response (instant):
```json
{ "topic_id": "f1a2b3c4d5e6", "status": "building", "has_belief_tree": false }
```

**Poll for readiness:**
```bash
# Poll until status is "ready"
curl http://localhost:8000/debates/topics/f1a2b3c4d5e6
# {"status": "building", "has_belief_tree": false}  ← still working
# {"status": "ready", "has_belief_tree": true}       ← tree available

# Get the tree
curl http://localhost:8000/debates/topics/f1a2b3c4d5e6/belief-tree
```

Status transitions: `building` → `ready` (tree available) or `failed`.

**You don't have to wait for `ready`** — starting a debate on a `building` topic works fine. The debate agent handles its own prep. But if the topic is `ready`, subsequent debates skip the ~30s prep phase.

Then use `topic_id` when creating debates:

```http
POST /debates/managed
{ "topic_id": "f1a2b3c4d5e6", "human_side": "aff" }
```

### 1b. Create a Debate

**Returns immediately** — setup happens in the background (~5-15s). You can pass `topic_id` (reusable) or `topic` (one-shot):

```http
POST /debates/managed
Content-Type: application/json

{
  "topic": "The United States should adopt universal basic income",
  "human_side": "aff",
  "coaching_enabled": true,
  "evidence_enabled": true
}
```

Response (instant):
```json
{ "debate_id": "a1b2c3d4e5f6", "status": "creating", "topic": "...", "message": "Debate is being created. Connect via WebSocket for events." }
```

### 2. Connect WebSocket (immediately)

```
ws://localhost:8000/debates/{debate_id}/ws
```

Connect right after the POST — don't wait. Events buffer during setup and flush when the agent is ready. You'll see `debate_initializing` → `debate_ready` once setup completes.

If you send actions before setup finishes, you'll get a recoverable error:
```json
{"type": "error", "message": "Debate is still being set up.", "code": "NOT_READY", "recoverable": true}
```

You can also poll status:
```bash
curl http://localhost:8000/debates/{id}/status
# {"status": "creating"} → {"status": "active"} → {"status": "complete"}
```

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

### Topics (create once, debate many times)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/debates/topics` | Create a reusable topic → `{topic_id}` |
| `GET` | `/debates/topics` | List all topics |
| `GET` | `/debates/topics/{id}` | Get topic details + debate count |
| `GET` | `/debates/topics/{id}/belief-tree` | Get cached belief tree for a topic |

### Debates

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/debates/managed` | Create debate (pass `topic` or `topic_id`) → `{debate_id}` |
| `POST` | `/debates/token-only` | Alternative: get LiveKit token for direct frontend connection |
| `GET` | `/debates/{id}/status` | Turn tracker: `current_speech`, `phase`, `is_human_turn`, `completed_speeches` |
| `GET` | `/debates/{id}/belief-tree` | Full belief tree for this debate |
| `GET` | `/debates/{id}/belief-tree/aff` | Affirmative beliefs only |
| `GET` | `/debates/{id}/belief-tree/neg` | Negative beliefs only |
| `GET` | `/debates/{id}/events` | Full event history (timestamped) |
| `GET` | `/debates/{id}/events?event_type=X` | Filter by event type |
| `GET` | `/debates/{id}/events?since=T` | Events after timestamp (catch-up) |
| `GET` | `/debates/{id}/transcripts` | All speech transcripts |
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

## Belief Tree Structure

The belief tree is the argument map generated during debate prep. Query it via `GET /debates/{id}/belief-tree` or `GET /debates/topics/{id}/belief-tree`.

```json
{
  "tree": {
    "topic": "The United States should adopt universal basic income",
    "generated_at": "2026-03-16T03:02:40Z",
    "beliefs": [
      {
        "id": "b1",
        "side": "aff",
        "label": "Contention 1: Poverty Reduction",
        "claim": "UBI directly reduces poverty rates",
        "arguments": [
          {
            "id": "a1",
            "claim": "UBI provides unconditional income floor",
            "warrant": "Guaranteed income eliminates extreme poverty by definition",
            "impact": "15-40% poverty reduction based on pilot programs",
            "label": "Income Floor Argument",
            "evidence": [
              {
                "tag": "Stanford Basic Income Lab 2023",
                "fulltext": "A randomized controlled trial of 1,000 participants...",
                "source": "Stanford University",
                "cite": "West et al., 2023",
                "source_url": "https://basicincome.stanford.edu/research"
              }
            ]
          }
        ]
      },
      {
        "id": "b2",
        "side": "neg",
        "label": "Contention 1: Fiscal Unsustainability",
        "claim": "UBI costs exceed available funding",
        "arguments": [...]
      }
    ]
  }
}
```

**Hierarchy:** Tree → Beliefs (by side) → Arguments → Evidence Cards

Filter by side to get only the arguments you need:
- `GET /debates/{id}/belief-tree/aff` — affirmative arguments only
- `GET /debates/{id}/belief-tree/neg` — negative arguments only

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
| `DEBATERHUB_VERBOSE` | No | — | Set to `1` to enable SDK `[EVENT]` / `[LOG]` tracing (see [Diagnosing a Stuck Session](#diagnosing-a-stuck-session)) |
| `DEBATERHUB_LOG_LEVEL` | No | — | `DEBUG \| INFO \| WARNING \| ERROR` — finer-grained SDK log level |

## Diagnosing a Stuck Session

If a debate sits at `debate_initializing` for a long time, the SDK's
verbose-logging mode surfaces every server event plus the last
progress message, so you can see exactly where prep stalled.

**Enable it** (see `.env.example`):

```bash
export DEBATERHUB_VERBOSE=1
```

Each inbound server event now emits **two** lines:

```
15:09:34 [EVENT] [+   9.2s] debate_initializing — [values] Generating values for: Resolved: social …
15:09:34 [LOG] recv type=debate_initializing {'topic': '…', 'format': 'ipda', …}

15:15:20 [EVENT] [+ 348.4s] debate_initializing — [clash] Detecting clashes: 1 AFF × 1 NEG args
15:16:34 [EVENT] [+ 421.5s] debate_initializing — [persist] Persisting tree to Neo4j + Weaviate
15:18:14 [EVENT] [+ 587.0s] debate_ready — Resolved: social media platforms should require …
```

- `[EVENT]` = scannable per-event trace (phase-by-phase timeline).
- `[LOG]` = raw frame dumps + framework chatter (parse errors,
  handler exceptions, connection events).

Grep one or the other out of a session log depending on what you need.

**Stall detection** (SDK-level). If you want your sidecar to react to
silence rather than wait 10 minutes, the SDK's `on_stall` callback
fires when no server event has arrived in a configurable window:

```python
async def on_stall(elapsed: float, silence: float, last_phase: str) -> None:
    log.warning(
        "Session stalled: silent for %.0fs, last phase %r",
        silence, last_phase,
    )

session = await client.create_managed_session(
    config=config,
    handler=handler,
    on_stall=on_stall,
    stall_after_seconds=120,   # fire if no event for 2 min
)
```

The SDK does NOT disconnect — your callback decides what to do (retry,
alert, abort, etc.). Re-arms on the next inbound event.

**Shrinking prep time.** Default belief-tree prep can run ~15 min for a
cold topic. For smoke tests or demos, pass `prep_config` to cut
breadth/depth — a minimal tree completes in ~5-7 min:

```python
config = DebateConfig(
    topic="…",
    human_side="aff",
    prep_config={
        "values_per_side": 1,
        "beliefs_per_value": 1,
        "research_per_belief": 1,
        "arguments_per_leaf": 1,
        "max_depth": 1,
    },
)
```

## Key Concepts

- **Sidecar pattern**: This server sits between your app and LiveKit. Your app never touches LiveKit directly — it just sends/receives JSON over a WebSocket.
- **Event-driven**: All debate state changes come as events. Your UI is purely reactive — listen for events, update display.
- **Stateless client**: The client doesn't need to track debate state. Call `GET /status` or `GET /events` at any time to reconstruct state.
- **Scale-to-zero agent**: The AI debate agent runs on Modal and scales to zero when idle. The warmup URL wakes it up (~10s cold start).

## Further Reading

- [Architecture deep dive](docs/architecture.md)
- [WebSocket protocol reference](docs/websocket-protocol.md)
- [debaterhub-sdk documentation](https://github.com/dgonier/debaterhub-sdk)
