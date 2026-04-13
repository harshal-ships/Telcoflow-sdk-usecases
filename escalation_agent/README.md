# AI Agent with Human Escalation — Voice Support Agent

A production voice agent that handles customer support calls using **Gemini Live** (native audio + function calling) via **Telcoflow SDK** (telephony). The AI resolves issues when possible; when it can't — or the caller asks for a human — it saves conversation context to SQLite and hands the call off to a live agent.

Based on the [Telcoflow Human Escalation use case](https://b3networks.docs.buildwithfern.com/use-cases/human-escalation).

## State Flows

```
AI handled:  PENDING → ANSWERED → DISCONNECTED
Escalated:   PENDING → ANSWERED → CONNECTED → DISCONNECTED
```

## How It Works

1. **Caller phones in** → Telcoflow delivers the call to the agent
2. **`call.answer()`** → Gemini Live session opens with a support-oriented system instruction
3. **AI assists the caller** — answers questions, troubleshoots issues in natural voice conversation
4. **Two outcomes:**
   - **Resolved** → AI confirms resolution, says goodbye, calls `end_call` → `call.disconnect()`
   - **Escalated** → AI detects it can't help (or caller requests a human), calls `escalate_to_human` with a reason and conversation summary → context saved to DB → `call.connect()` + `call.close()` transfers to a live agent

The human agent receiving the transferred call can query the database for the conversation summary, so the caller doesn't have to repeat themselves.

## Project Structure

```
escalation_agent/
├── agent.py          # Entrypoint — Telcoflow + Gemini Live bridge + lifecycle
├── database.py       # aiosqlite persistence for call contexts
├── requirements.txt
├── .env.example
└── README.md
```

## Setup

```bash
cd escalation_agent

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
2026-04-10 10:00:00  escalation_agent           INFO   EscalationAgent [YOUR_COMPANY_NAME] is live — waiting for calls …
```

## Sample Call Flow

### AI-Resolved Call

```
AI:      "Thank you for calling YOUR_COMPANY_NAME. How can I help you today?"
Caller:  "What are your business hours?"
AI:      "Our office is open Monday through Friday, 9 AM to 5 PM Singapore time.
          Is there anything else I can help you with?"
Caller:  "No, that's all. Thanks!"
AI:      "You're welcome! Have a great day. Goodbye."
          → end_call → call.disconnect()
```

### Escalated Call

```
AI:      "Thank you for calling YOUR_COMPANY_NAME. How can I help you today?"
Caller:  "I need to dispute a charge on my last invoice."
AI:      "I understand. Could you tell me more about which charge you'd like to dispute?"
Caller:  "There's a $50 fee I wasn't expecting."
AI:      "I see. Billing disputes require account access that I don't have.
          I'm going to connect you with a team member who can help you further.
          I'll pass along our conversation so you won't need to repeat anything."
          → escalate_to_human(reason="Billing dispute requires account access",
              conversation_summary="Caller wants to dispute a $50 charge on their last invoice...")
          → context saved to DB → call.connect() → call.close()
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | *required* | Google API key with Gemini Live access |
| `WSS_API_KEY` | *required* | Telcoflow API key |
| `WSS_CONNECTOR_UUID` | *required* | Telcoflow connector UUID |
| `GEMINI_MODEL` | `gemini-2.5-flash-native-audio-preview-12-2025` | Gemini Live model |
| `BUSINESS_NAME` | `YOUR_COMPANY_NAME` | Company name used in the AI greeting |
| `DB_PATH` | `escalation.db` | SQLite database path |
| `SAMPLE_RATE` | `24000` | Audio sample rate (Hz) |

## Database Schema

### `call_contexts`

| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT | Unique context ID (e.g. `ctx-a1b2c3d4`) |
| `call_id` | TEXT | Telcoflow call ID |
| `caller_number` | TEXT | Caller's phone number |
| `summary_json` | TEXT | JSON-encoded conversation summary |
| `escalation_reason` | TEXT | Why the call was escalated (empty if AI-handled) |
| `status` | TEXT | `ai_handled` or `escalated` |
| `created_at` | TEXT | ISO timestamp |
