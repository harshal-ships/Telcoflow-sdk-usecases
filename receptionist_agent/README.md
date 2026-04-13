# AI Receptionist with Database Lookup

A production voice agent that greets callers by name, surfaces their open support tickets, and either resolves queries via AI or routes to a human department. Built on **Telcoflow SDK** (telephony) + **Gemini Live** (native audio + function calling).

Reference: [Database Lookup Use Case](https://b3networks.docs.buildwithfern.com/use-cases/database-lookup)

## Architecture

```
┌─────────────┐     INCOMING_CALL     ┌──────────────────────┐
│  PSTN /SIP  │ ──────────────────▶   │   Telcoflow SDK      │
│   Caller    │ ◀── audio stream ──   │   (WebSocket bridge)  │
└─────────────┘                       └──────────┬───────────┘
                                                 │
                                      call.answer()
                                                 │
                                      ┌──────────▼───────────┐
                                      │   SQLite Lookup      │
                                      │   (aiosqlite)        │
                                      │                      │
                                      │  Known? → name +     │
                                      │          tickets     │
                                      │  New?   → create     │
                                      │          lead        │
                                      └──────────┬───────────┘
                                                 │
                                      context injected into
                                      system instruction
                                                 │
                                      ┌──────────▼───────────┐
                                      │   Gemini Live        │
                                      │   (native audio)     │
                                      │                      │
                                      │  3 concurrent tasks:  │
                                      │   • stream_to_gemini │
                                      │   • receive_from_    │
                                      │     gemini           │
                                      │   • lifecycle_       │
                                      │     watcher          │
                                      └──────────┬───────────┘
                                                 │
                              ┌───────────────────┼───────────────────┐
                              │                                       │
                   route_to_department                            end_call
                              │                                       │
                   call.connect() + call.close()           call.disconnect()
                              │                                       │
                   CONNECTED → DISCONNECTED              ANSWERED → DISCONNECTED
                   (human handoff)                       (AI resolved)
```

## State Flows

```
Human route:   PENDING → ANSWERED → CONNECTED → DISCONNECTED
AI handled:    PENDING → ANSWERED → DISCONNECTED
```

## Project Structure

```
receptionist_agent/
├── agent.py          # Entrypoint — Telcoflow + Gemini Live bridge + lifecycle
├── database.py       # aiosqlite DB: customers, tickets, leads + seed data
├── requirements.txt
├── .env.example
└── README.md
```

## Setup

```bash
cd receptionist_agent

python -m venv .venv && source .venv/bin/activate

pip install -r requirements.txt --extra-index-url https://test.pypi.org/simple/

cp .env.example .env
# Fill in: GEMINI_API_KEY, WSS_API_KEY, WSS_CONNECTOR_UUID
```

## Run

```bash
python agent.py
```

Output:

```
2026-04-10 10:00:00  receptionist_agent          INFO   ReceptionistDB ready at receptionist.db
2026-04-10 10:00:00  receptionist_agent          INFO   ReceptionistAgent [XanhSM] is live — waiting for calls …
```

## Sample Call Flow

**Known caller (Alice Nguyen, +14155550101):**

```
Agent:  "Hi Alice! Thanks for calling XanhSM. I see you have two open tickets —
         one about a login page error and another about CSV exports. Is that what
         you're calling about?"
Alice:  "Yes, the login issue is really urgent."
Agent:  "I understand. Let me connect you with our support team right away."
         → route_to_department(department="support", reason="urgent login issue")
         → call.connect() → call.close()
```

**Unknown caller (+15551234567):**

```
Agent:  "Thank you for calling XanhSM! I don't think we've spoken before.
         How can I help you today?"
Caller: "I'd like to learn about your enterprise plans."
Agent:  "Our enterprise plan includes … [handles query via AI] …
         Is there anything else I can help with?"
Caller: "No, that's all. Thanks!"
Agent:  "You're welcome! Have a great day. Goodbye!"
         → end_call()
         → call.disconnect()
```

## Seed Data

The database is auto-seeded on first run with demo data:

| Customer | Phone | Company | Open Tickets |
|----------|-------|---------|--------------|
| Alice Nguyen | +14155550101 | Acme Corp | 2 (login error, CSV export) |
| Bob Martinez | +14155550102 | Globex Inc | 1 (billing discrepancy) |
| Carol Zhang | +14155550103 | Initech | 0 (feature request was closed) |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | *required* | Google Gemini API key |
| `WSS_API_KEY` | *required* | Telcoflow API key |
| `WSS_CONNECTOR_UUID` | *required* | Telcoflow connector UUID |
| `BUSINESS_NAME` | `XanhSM` | Company name used in greetings |
| `DB_PATH` | `receptionist.db` | SQLite database file path |
| `GEMINI_MODEL` | `gemini-2.5-flash-native-audio-preview-12-2025` | Gemini Live model |
| `SAMPLE_RATE` | `24000` | Audio sample rate (Hz) |
