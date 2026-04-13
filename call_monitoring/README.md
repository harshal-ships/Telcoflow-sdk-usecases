# Real-time Call Monitoring & Coaching Agent

A production voice agent that silently monitors live support calls and coaches agents in real time using **Telcoflow SDK** (telephony) + **Gemini Live** (native audio + function calling). Uses 3-way conference modes for transparent supervision.

Reference: [Call Monitoring and Coaching](https://b3networks.docs.buildwithfern.com/use-cases/call-monitoring-and-coaching)

## Conference Audio Modes

| Mode | Caller Hears AI? | Callee Hears AI? | AI Hears Both? |
|---|---|---|---|
| `barge()` | Yes | Yes | Yes |
| `whisper()` | No | Yes | Yes |
| `spy()` | No | No | Yes |

## How It Works

1. **Customer calls in** → Telcoflow delivers the call
2. **Agent answers** → `call.answer()` picks up
3. **Connect to callee** → `call.connect()` bridges to the human support agent (3-way conference)
4. **Spy mode** → `call.spy()` — AI enters silent monitoring, both parties are unaware
5. **Gemini Live analyzes** — continuously monitors sentiment, topics, and coaching opportunities
6. **Coaching detected** → AI calls `coach_agent`, switches to `call.whisper()`, speaks coaching to the agent only, then returns to `call.spy()`
7. **Sentiment logged** → periodic `log_sentiment` calls write to the analytics database
8. **Call wraps up** → `end_monitoring` disconnects the AI — customer and agent stay connected

## Project Structure

```
call_monitoring/
├── agent.py          # Entrypoint — Telcoflow + Gemini Live monitoring
├── database.py       # aiosqlite analytics storage
├── requirements.txt
├── .env.example
└── README.md
```

## Setup

```bash
cd call_monitoring

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
2026-04-10 10:00:00  call_monitoring              INFO   CallMonitor [YOUR_COMPANY_NAME] is live — waiting for calls …
```

## Sample Monitoring Flow

```
[Customer calls in]
  → call.answer() → call.connect() → call.spy()

Customer:  "I've been waiting two weeks for my refund and nobody has helped me!"
Agent:     "Let me look into that for you..."
  → log_sentiment(sentiment="negative", topics="refund,wait time,escalation")

AI detects agent struggling — switches to whisper:
  → coach_agent(suggestion="Acknowledge the delay, apologize, offer expedited refund")
  → call.whisper()
AI whispers: "Acknowledge the long wait, apologize sincerely, and offer to expedite the refund."
  → call.spy()  (back to silent monitoring)

Agent:     "I sincerely apologize for the two-week delay. Let me expedite that refund right now."
Customer:  "Thank you, that's all I needed."
  → log_sentiment(sentiment="positive", topics="refund,resolution")

[Call wrapping up]
  → end_monitoring → call.close()  (AI leaves, customer + agent stay connected)
```

## Database: `call_analytics`

| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT | Primary key (e.g. `ca-a1b2c3d4`) |
| `call_id` | TEXT | Telcoflow call identifier |
| `caller_number` | TEXT | Incoming phone number |
| `timestamp` | TEXT | ISO 8601 timestamp |
| `sentiment` | TEXT | positive / neutral / negative |
| `topics` | TEXT | Comma-separated discussion topics |
| `coaching_given` | TEXT | Coaching suggestions delivered |
| `created_at` | TEXT | Row creation timestamp |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | *required* | Google API key |
| `WSS_API_KEY` | *required* | Telcoflow API key |
| `WSS_CONNECTOR_UUID` | *required* | Telcoflow connector UUID |
| `BUSINESS_NAME` | YOUR_COMPANY_NAME | Company name |
| `DB_PATH` | `call_monitoring.db` | SQLite database path |
| `GEMINI_MODEL` | `gemini-2.5-flash-native-audio-preview-12-2025` | Gemini Live model |
| `SAMPLE_RATE` | 24000 | Audio sample rate (Hz) |
