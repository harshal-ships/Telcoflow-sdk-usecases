# Appointment Booking & Confirmation — Production Voice AI Agent

A production-grade voice AI agent that handles appointment scheduling over real phone calls. Built on **Gemini Live** (native audio, function calling) and **Telcoflow SDK** (telephony).

Based on the [Telcoflow Appointment Booking use case](https://b3networks.docs.buildwithfern.com/use-cases/appointment-booking).

## What Makes This Production-Ready

| Concern | Implementation |
|---------|---------------|
| **Persistence** | SQLite via `aiosqlite` — customers and appointments survive restarts |
| **SMS** | Twilio integration — real confirmation texts to the caller's phone |
| **Logging** | Structured `logging` throughout — per-call child loggers for traceability |
| **Error handling** | Every tool call is wrapped; failures return structured errors to Gemini |
| **Graceful shutdown** | Catches SIGINT/SIGTERM, drains active calls, closes DB |
| **Configuration** | All tunables via env vars — business hours, services, DB path, Twilio creds |
| **Telcoflow modes** | Sandbox (API key) and production (mTLS) supported |
| **Race-condition guard** | Double-checks slot availability inside `book_appointment` before writing |

## Architecture

```
Caller ←PCM→ Telcoflow ←PCM→ appointment_agent.py ←PCM→ Gemini Live
                                      │
                          ┌───────────┼───────────────┐
                          │           │               │
                     database.py  sms_service.py   tools.py
                     (aiosqlite)    (Twilio)     (declarations
                                                  + dispatch)
```

## State Flows

```
Happy path:     PENDING → ANSWERED → DISCONNECTED
Human fallback: PENDING → ANSWERED → CONNECTED → DISCONNECTED
```

## Project Structure

```
appointment_booking/
├── appointment_agent.py   # Entrypoint — Telcoflow + Gemini Live bridge
├── config.py              # Centralised env-based configuration
├── database.py            # aiosqlite persistence (customers + appointments)
├── sms_service.py         # Twilio SMS (graceful fallback to logging)
├── tools.py               # Gemini tool declarations + async dispatch
├── requirements.txt
├── .env.example
└── README.md
```

## Setup

```bash
cd appointment_booking

python -m venv .venv && source .venv/bin/activate

pip install -r requirements.txt --extra-index-url https://test.pypi.org/simple/

cp .env.example .env
# Fill in at minimum: GEMINI_API_KEY, WSS_API_KEY, WSS_CONNECTOR_UUID
```

### Optional: Enable real SMS

1. Create a [Twilio account](https://www.twilio.com/try-twilio)
2. Uncomment and fill `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` in `.env`
3. The agent will send real SMS confirmations. Without Twilio creds, SMS content is logged to stdout instead.

### Optional: Production Telcoflow (mTLS)

```env
TELCOFLOW_MODE=production
TELCOFLOW_CERT_PATH=/etc/certs/client.pem
TELCOFLOW_KEY_PATH=/etc/certs/client.key
```

## Run

```bash
python appointment_agent.py
```

Output:

```
2026-04-09 10:00:00  appointment_agent         INFO   AppointmentBot [XanhSM Clinic] is live — sandbox mode — waiting for calls …
```

## Gemini Tools

| Tool | Trigger | Backend |
|------|---------|---------|
| `check_availability` | After gathering date + service | `database.py` — queries booked slots, computes open ones |
| `book_appointment` | After caller confirms a slot | `database.py` — creates appointment + customer if new |
| `send_sms_confirmation` | After booking succeeds | `sms_service.py` — sends via Twilio (or logs) |
| `connect_to_scheduling_team` | Caller wants a human | `call.connect()` → `call.close()` |
| `end_call` | Conversation done | `call.disconnect()` |

## Configuration Reference

All values are set via environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | *required* | Google API key with Gemini Live access |
| `GEMINI_MODEL` | `gemini-2.5-flash-native-audio-preview-12-2025` | Gemini model ID |
| `WSS_API_KEY` | *required (sandbox)* | Telcoflow API key |
| `WSS_CONNECTOR_UUID` | *required (sandbox)* | Telcoflow connector UUID |
| `TELCOFLOW_MODE` | `sandbox` | `sandbox` or `production` |
| `TWILIO_ACCOUNT_SID` | — | Twilio SID (optional) |
| `TWILIO_AUTH_TOKEN` | — | Twilio token (optional) |
| `TWILIO_FROM_NUMBER` | — | Twilio sender number (optional) |
| `BUSINESS_NAME` | `XanhSM Clinic` | Your business name |
| `BUSINESS_OPEN_HOUR` | `9` | Opening hour (24h) |
| `BUSINESS_CLOSE_HOUR` | `17` | Closing hour (24h) |
| `BUSINESS_SLOT_MINUTES` | `30` | Slot duration in minutes |
| `BUSINESS_SERVICES` | 5 defaults | Comma-separated service names |
| `DB_PATH` | `appointments.db` | SQLite database file path |

## Scaling Beyond SQLite

For production at scale, swap `database.py` to use PostgreSQL (`asyncpg`) or any async DB driver. The `Database` class interface stays the same — only the connection and query internals change.
