# Architecture

## Overview

This reference app demonstrates both SDK modes through a FastAPI backend with WebSocket event forwarding.

```
┌─────────────────────────────────────────────────────────────┐
│                     FastAPI App                              │
│                                                              │
│  POST /debates/token-only    ──→  DebateClient.create_session()
│    └─ Returns ConnectionDetails      (Mode 1: token for frontend)
│                                                              │
│  POST /debates/managed       ──→  DebateClient.create_managed_session()
│    └─ Returns debate_id              (Mode 2: backend joins room)
│                                                              │
│  WS /debates/{id}/ws                                         │
│    ├─ Receives: SDK events ──→ JSON to client                │
│    └─ Sends: client actions ──→ session.submit_speech(), etc │
│                                                              │
│  GET /debates/{id}/status    ──→  session.tracker properties │
│                                                              │
│  GET /                       ──→  static/index.html          │
└─────────────────────────────────────────────────────────────┘
```

## File Layout

| File | Responsibility |
|------|----------------|
| `app/main.py` | FastAPI app creation, lifespan (cleanup on shutdown), CORS, static file mount, router includes |
| `app/config.py` | `pydantic-settings` model loading `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `DEBATE_AGENT_NAME`, `WARMUP_URL` from `.env` |
| `app/store.py` | In-memory dict mapping `debate_id → ManagedDebateSession`. No database — sessions lost on restart. |
| `app/handler.py` | `WebSocketDebateHandler(DebateEventHandler)` — bridges SDK events to a WebSocket client. Buffers events until a WebSocket attaches. |
| `app/routes/debates.py` | REST endpoints: create debate (Mode 1 + Mode 2), get status |
| `app/routes/ws.py` | WebSocket endpoint: attaches handler, receives actions, dispatches to session |
| `static/index.html` | Minimal test UI: form → create debate, WebSocket → event log, action buttons |

## Mode 2 Flow (Detail)

```
1. Client → POST /debates/managed {topic, human_side}
   └─ Server:
      a. Creates DebateClient with LiveKit creds
      b. Creates WebSocketDebateHandler (event buffer)
      c. Calls client.create_managed_session(config, handler)
         └─ SDK creates room, dispatches agent, joins as data-only participant
      d. Stores session + handler in store.py
      e. Returns {debate_id}

2. Client → WS /debates/{debate_id}/ws
   └─ Server:
      a. Looks up session in store
      b. Accepts WebSocket
      c. Attaches WebSocket to handler (flushes buffered events)
      d. Enters receive loop

3. SDK receives events from LiveKit agent
   └─ handler.on_<event>() → ws.send_json()
   └─ Client sees real-time events

4. Client sends action JSON
   └─ Server parses action field
   └─ Calls session.submit_speech() / session.submit_cx_question() / etc.
   └─ SDK sends to LiveKit agent via data channel
```

## Session Lifecycle

- **Created:** `POST /debates/managed` → session exists in store, handler buffering events
- **Active:** WebSocket connects → events stream to client, actions accepted
- **Disconnected:** WebSocket closes → handler detached, session still alive in store
- **Reconnectable:** A new WebSocket can connect to the same `debate_id` (buffered events may be lost)
- **Shutdown:** App shutdown triggers `session.disconnect()` + `client.close()` for all active sessions

## Extending This App

### Add persistence

Replace `app/store.py` with Redis, PostgreSQL, or any persistent store. The `ManagedDebateSession` object itself can't be serialized, but you can store the `debate_id → room_name` mapping and reconnect.

### Add authentication

Wrap the endpoints with FastAPI `Depends()` for JWT validation, API key checking, etc.

### Multi-user support

Each `POST /debates/managed` creates an independent session. Multiple users can run simultaneous debates since each gets a unique `debate_id` and LiveKit room.
