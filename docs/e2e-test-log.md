# E2E Test Log — Full 7-Speech IPDA Debate

**Date:** 2026-03-16
**Duration:** 25 minutes 45 seconds (1545.96s)
**Result:** PASSED
**Topic:** "The United States should adopt universal basic income"
**Human side:** aff (AutoDebater)
**Winner:** neg

## Raw Log

```
============================= test session starts ==============================
platform linux -- Python 3.13.5, pytest-9.0.2, pluggy-1.5.0
rootdir: /home/dgonier/debaterhub/debate-fastapi-package-test
timeout: 2400.0s
collected 1 item

tests/test_e2e.py::TestFullDebateE2E::test_full_debate_completes

-------------------------------- live log call ---------------------------------
INFO     httpx         HTTP Request: POST http://localhost:8001/debates/managed "HTTP/1.1 200 OK"
INFO     auto_debater  AutoDebater connecting to ws://localhost:8001/debates/d9ff8591ec80/ws
INFO     auto_debater  AutoDebater received: debate_initializing
INFO     auto_debater  AutoDebater received: debate_ready
INFO     auto_debater  AutoDebater received: turn_signal
INFO     auto_debater  AutoDebater received: turn_signal
INFO     auto_debater  AutoDebater submitting speech: AC (473 chars)
INFO     auto_debater  AutoDebater received: turn_signal
INFO     auto_debater  AutoDebater received: cx_question
INFO     auto_debater  AutoDebater received: flow_update
INFO     auto_debater  AutoDebater received: cx_question
INFO     auto_debater  AutoDebater received: cx_answer
INFO     auto_debater  AutoDebater received: cx_question
INFO     auto_debater  AutoDebater ending CX after 3 answers: AC-CX
INFO     auto_debater  AutoDebater received: cx_answer
INFO     auto_debater  AutoDebater received: turn_signal
INFO     auto_debater  AutoDebater received: speech_progress
INFO     auto_debater  AutoDebater received: speech_progress
INFO     auto_debater  AutoDebater received: speech_progress
INFO     auto_debater  AutoDebater received: flow_update
INFO     auto_debater  AutoDebater received: speech_text
INFO     auto_debater  AutoDebater received: evidence_result
INFO     auto_debater  AutoDebater received: turn_signal
INFO     auto_debater  AutoDebater received: cx_question
INFO     auto_debater  AutoDebater received: cx_answer
INFO     auto_debater  AutoDebater received: flow_update
INFO     auto_debater  AutoDebater received: turn_signal
INFO     auto_debater  AutoDebater ending prep time for 1AR
INFO     auto_debater  AutoDebater received: turn_signal
INFO     auto_debater  AutoDebater submitting speech: 1AR (457 chars)
INFO     auto_debater  AutoDebater received: coaching_hint
INFO     auto_debater  AutoDebater received: flow_update
INFO     auto_debater  AutoDebater received: turn_signal
INFO     auto_debater  AutoDebater received: speech_progress
INFO     auto_debater  AutoDebater received: speech_progress
INFO     auto_debater  AutoDebater received: speech_progress
INFO     auto_debater  AutoDebater received: flow_update
INFO     auto_debater  AutoDebater received: speech_text
INFO     auto_debater  AutoDebater received: turn_signal
INFO     auto_debater  AutoDebater submitting speech: 2AR (355 chars)
INFO     auto_debater  AutoDebater received: coaching_hint
INFO     auto_debater  AutoDebater received: flow_update
INFO     auto_debater  AutoDebater received: judging_started
INFO     auto_debater  AutoDebater received: judge_result
PASSED

======================== 1 passed in 1545.96s (0:25:45) ========================
```

## Event Sequence

| Time | Event | Details |
|------|-------|---------|
| 0:00 | **Setup** | POST /debates/managed → debate_id `d9ff8591ec80` |
| 0:00 | `debate_initializing` | Agent joining room, preparing belief tree |
| 0:00 | `debate_ready` | Speech order and time limits sent |
| — | **AC (Human)** | — |
| 0:01 | `turn_signal` | AC / human / waiting |
| 0:01 | `turn_signal` | AC / human / active |
| 0:01 | AutoDebater submits | AC speech (473 chars, canned) |
| — | **AC-CX (AI asks)** | — |
| 0:02 | `turn_signal` | AC-CX / ai / active |
| 0:02 | `cx_question` | AI asks question #1 |
| 0:02 | `flow_update` | Flow analysis from AC |
| 0:02 | `cx_question` | AI asks question #2 |
| 0:02 | `cx_answer` | AutoDebater answers |
| 0:02 | `cx_question` | AI asks question #3 |
| 0:02 | AutoDebater `end_cx` | Ends CX after 3 answers |
| 0:02 | `cx_answer` | Final answer echoed |
| — | **NC (AI)** | — |
| ~2:00 | `turn_signal` | NC / ai / active |
| ~3:00 | `speech_progress` ×3 | tactic → skeleton → speech |
| ~5:00 | `flow_update` | Flow after NC |
| ~5:00 | `speech_text` | NC generated |
| ~5:00 | `evidence_result` | Evidence cards |
| — | **NC-CX (Human asks)** | — |
| ~10:00 | `turn_signal` | NC-CX / human / active |
| ~10:00 | `cx_question` | AutoDebater asks |
| ~10:00 | `cx_answer` | AI answers |
| ~10:00 | `flow_update` | Flow after NC-CX |
| — | **1AR Prep** | — |
| ~11:00 | `turn_signal` | 1AR / human / prep_time |
| ~11:00 | AutoDebater `end_prep_time` | |
| — | **1AR (Human)** | — |
| ~11:00 | `turn_signal` | 1AR / human / active |
| ~11:00 | AutoDebater submits | 1AR speech (457 chars) |
| ~11:00 | `coaching_hint` | 10 coaching hints for 1AR |
| ~12:00 | `flow_update` | Flow after 1AR |
| — | **NR (AI)** | — |
| ~12:00 | `turn_signal` | NR / ai / active |
| ~13:00 | `speech_progress` ×3 | tactic → skeleton → speech |
| ~18:00 | `flow_update` | Flow after NR |
| ~18:00 | `speech_text` | NR generated |
| — | **2AR (Human)** | — |
| ~23:00 | `turn_signal` | 2AR / human / active |
| ~23:00 | AutoDebater submits | 2AR speech (355 chars) |
| ~23:00 | `coaching_hint` | Coaching hints |
| ~23:00 | `flow_update` | Final flow |
| — | **Judging** | — |
| ~24:00 | `judging_started` | Panel judge evaluating |
| ~25:45 | `judge_result` | **Winner: neg** |

## Test Assertions Verified

- `result["success"]` is True
- `result["judge_result"]` is not None
- `result["judge_result"]["winner"]` is "aff" or "neg"
- AI speeches "NC" and "NR" present in `speech_text` events
