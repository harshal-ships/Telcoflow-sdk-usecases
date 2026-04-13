# Smart IVR Replacement — Natural Voice Menu Navigator

Replaces traditional IVR phone trees ("press 1 for sales...") with a single natural voice conversation powered by **Telcoflow SDK** + **Gemini Live**. The caller just says what they need — the AI handles it or routes to the right department.

It demonstrates how Gemini Live's function calling can replace an entire IVR system with zero DTMF menus.

## Why This Exists

Traditional IVR systems are:
- Frustrating (nested menus, wrong options, "press 0 for operator")
- Slow (30-60 seconds navigating menus before reaching anyone)
- Expensive to maintain (every menu change requires reconfiguration)

This agent replaces all of that with: **"Hi, welcome to YOUR_COMPANY_NAME! How can I help you today?"**

## What It Can Do

### AI-Handled (no human needed)

| Intent | Tool | What happens |
|--------|------|--------------|
| Account status | `check_account_status` | Looks up name, plan, balance by phone |
| Order tracking | `check_orders` / `check_order_by_id` | Reports order status and delivery dates |
| File a complaint | `log_complaint` | Records complaint with category, gives reference # |
| Schedule callback | `schedule_callback` | Books a callback at the caller's preferred time |

### Human-Routed (transferred via `call.connect()`)

| Intent | Department | Example trigger |
|--------|-----------|-----------------|
| Buy / upgrade | Sales | "I want to upgrade my plan" |
| Technical problem | Tech Support | "My router isn't working" |
| Billing dispute | Billing | "I was charged twice" |

### Call Analytics

Every call is logged to `call_logs` table with: caller number, detected intent, outcome (ai_handled / routed), and duration. This gives you IVR analytics without any third-party tool.

## Architecture

```
Caller ←PCM→ Telcoflow ←PCM→ agent.py ←PCM→ Gemini Live
                                 │
                                 ├── check_account_status  → DB lookup
                                 ├── check_orders          → DB lookup
                                 ├── check_order_by_id     → DB lookup
                                 ├── log_complaint         → DB write
                                 ├── schedule_callback     → DB write
                                 ├── route_to_department   → call.connect() + call.close()
                                 └── end_call              → call.disconnect() + log
```

## State Flows

```
AI-handled:   PENDING → ANSWERED → DISCONNECTED
Human-routed: PENDING → ANSWERED → CONNECTED → DISCONNECTED
```

## Project Structure

```
smart_ivr/
├── agent.py          # Entrypoint — Telcoflow + Gemini Live bridge, 7 tools
├── database.py       # aiosqlite — customers, orders, complaints, callbacks, call logs
├── requirements.txt
├── .env.example
└── README.md
```

## Setup

```bash
cd smart_ivr
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt --extra-index-url https://test.pypi.org/simple/
cp .env.example .env
# Fill in: GEMINI_API_KEY, WSS_API_KEY, WSS_CONNECTOR_UUID
```

## Run

```bash
python agent.py
```

```
2026-04-09 10:00:00  smart_ivr                 INFO   Smart IVR [YOUR_COMPANY_NAME] is live — no menus, just talk — waiting for calls …
```

## Sample Conversations

### Account check (AI-handled)
```
AI:     "Hi, welcome to YOUR_COMPANY_NAME! How can I help you today?"
Caller: "What's my account balance?"
AI:     → check_account_status(phone="+6591234567")
AI:     "Hi Alice! You're on the premium plan with a balance of $142.50."
Caller: "That's all, thanks."
AI:     → end_call(summary="Account balance inquiry")
```

### Order tracking (AI-handled)
```
AI:     "Hi, welcome to YOUR_COMPANY_NAME! How can I help?"
Caller: "Where's my order?"
AI:     → check_orders(phone="+6591234567")
AI:     "You have two orders: your Wireless Router Pro X has shipped and
         should arrive April 11th. Your USB-C Hub is still being processed,
         expected April 14th."
```

### Complaint + callback (AI-handled, multi-intent)
```
Caller: "My delivery was damaged."
AI:     → log_complaint(category="delivery", description="Package arrived damaged")
AI:     "I've filed complaint CMP-a3f2b1c8. Our team will review within 24 hours.
         Would you like a callback about this?"
Caller: "Yes, tomorrow afternoon."
AI:     → schedule_callback(preferred_time="tomorrow afternoon", reason="damaged delivery follow-up")
```

### Sales routing (human-routed)
```
Caller: "I want to upgrade my plan."
AI:     "Let me connect you with our sales team."
AI:     → route_to_department(department="sales", context="wants plan upgrade")
        → call.connect() + call.close()
```

## Seed Data

The database ships with 3 customers and 4 orders:

| Customer | Phone | Plan | Orders |
|----------|-------|------|--------|
| Alice Nguyen | +6591234567 | Premium | Router (shipped), USB Hub (processing) |
| Bob Tan | +6598765432 | Basic | Earbuds (delivered) |
| Carol Lee | +6587654321 | Business | Standing Desk (shipped) |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | *required* | Google API key |
| `WSS_API_KEY` | *required* | Telcoflow API key |
| `WSS_CONNECTOR_UUID` | *required* | Telcoflow connector UUID |
| `BUSINESS_NAME` | `YOUR_COMPANY_NAME` | Company name used in greetings |
| `DB_PATH` | `ivr.db` | SQLite database path |

## vs Traditional IVR

| | Traditional IVR | Smart IVR |
|---|---|---|
| **Interface** | DTMF keypad menus | Natural voice |
| **Time to resolution** | 30-60s menu navigation | Immediate |
| **Multi-intent** | Start over for each | Handles sequentially |
| **Maintenance** | Reconfigure menu trees | Update system prompt |
| **Analytics** | Call volume per menu item | Intent + outcome + duration per call |
| **Self-service** | Limited to pre-built flows | Account, orders, complaints, callbacks |
