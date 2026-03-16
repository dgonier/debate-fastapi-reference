# Architecture

## System Overview

```
┌─────────────┐      ┌──────────────────────┐      ┌──────────────────────┐
│  Your App   │      │   This Sidecar       │      │  LiveKit Cloud       │
│  (Flask,    │ WS   │   (FastAPI)          │ Data │  + AI Agent (Modal)  │
│   React,    │◄────►│                      │◄────►│                      │
│   etc.)     │      │  - Event buffering   │ Chan │  - Speech generation │
│             │ REST │  - Action dispatch   │      │  - Flow analysis     │
│             │◄────►│  - State queries     │      │  - Scoring & judging │
└─────────────┘      └──────────────────────┘      └──────────────────────┘
```

**Your app** never talks to LiveKit directly. It connects to the sidecar via WebSocket (for real-time events) and REST (for state queries). The sidecar manages the LiveKit room, data channel, and debate agent.

## Data Flow

### Creating a Debate

```
Client                          Sidecar                         LiveKit + Modal
------                          -------                         ---------------
POST /debates/managed ────────> DebateClient(livekit_creds)
  {topic, human_side}           Create LiveKit room ──────────> Room created
                                Dispatch agent ───────────────> Agent wakes up
                                Join room as data-only ───────> SDK Manager in room
                                Store session + handler
                         <───── {debate_id}
```

### Event Streaming

```
Agent generates speech ──────────────────> Data channel message
                                           │
                               DataOnlyParticipant receives
                                           │
                               ManagedDebateSession parses
                                           │
                               DebateEventHandler.on_speech_text()
                                           │
                               WebSocketDebateHandler._forward()
                                           │
                                ┌──────────┴──────────┐
                                │                     │
                         WebSocket connected?    Buffer event
                                │                     │
                         ws.send_json()         _buffer.append()
                                │                (flush on attach)
                         Client receives
```

### Action Dispatch

```
Client sends action ───> WS handler receives
  {action, ...}           │
                          Parse action field
                          │
                 ┌────────┴─────────────┐
                 │                      │
          submit_speech          cx_answer, end_cx, etc.
                 │                      │
          session.submit_speech()  session.submit_cx_answer()
                 │                      │
          Sends via data channel ──────> Agent processes
```

## File Layout

| File | What It Does | Key Classes/Functions |
|------|-------------|----------------------|
| `app/main.py` | App creation, lifespan, CORS, route registration | `app`, `lifespan()` |
| `app/config.py` | Loads `.env` via pydantic-settings | `Settings`, `settings` |
| `app/store.py` | Maps `debate_id` → `ManagedDebateSession` (in-memory) | `add()`, `get()`, `remove()`, `all_ids()` |
| `app/handler.py` | Bridges SDK events to WebSocket. Stores event history and belief tree for REST queries | `WebSocketDebateHandler` |
| `app/routes/debates.py` | REST endpoints: create debate, get status/belief-tree/events/transcripts | `create_managed()`, `get_status()`, `get_belief_tree()`, `get_events()`, `get_transcripts()` |
| `app/routes/ws.py` | WebSocket endpoint: attach handler, receive/dispatch actions | `debate_websocket()` |
| `app/auto_debater.py` | Mock human for testing: auto-submits speeches, answers CX, ends prep time | `AutoDebater` |

## Handler Storage

`WebSocketDebateHandler` stores three things beyond the WebSocket forwarding:

1. **`event_history`**: Every event received, with a `timestamp` field added. Used by `GET /events` for replay/observer catch-up.
2. **`belief_tree`**: The most recent belief tree from `on_belief_tree()`. Used by `GET /belief-tree`.
3. **`debate_config`**: Topic and human_side from `on_debate_initializing()`.

## Session Lifecycle

```
                POST /debates/managed
                        │
                    ┌───┴───┐
                    │Created│  Session in store, handler buffering events
                    └───┬───┘
                        │
                 WS /debates/{id}/ws
                        │
                    ┌───┴───┐
                    │Active │  Events stream to client, actions accepted
                    └───┬───┘
                        │
                  WS disconnects
                        │
                    ┌───┴────────┐
                    │Disconnected│  Handler detached, session still in store
                    └───┬────────┘
                        │
                  New WS connects (optional)
                        │
                    ┌───┴───┐
                    │Active │  Re-attached (buffered events from gap are lost)
                    └───┬───┘
                        │
                  App shutdown
                        │
                    ┌───┴──────┐
                    │Cleaned up│  session.disconnect(), client.close()
                    └──────────┘
```

## Extending

### Add persistence
Replace `app/store.py`. The `ManagedDebateSession` can't be serialized, but you can store `debate_id → room_name` and create a new session on reconnect.

### Add authentication
Use FastAPI `Depends()` on the endpoints. The WebSocket endpoint can check auth during the `ws.accept()` phase.

### Add multiple human debaters
Each `POST /debates/managed` creates an independent LiveKit room. Multiple debates can run concurrently.

### Add a database for event replay
The handler's `event_history` is in-memory. For production, pipe events to PostgreSQL/Redis alongside the WebSocket forwarding.
