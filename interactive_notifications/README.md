# Interactive Notifications Agent — Voice Notification Delivery

A production voice agent that answers incoming calls, identifies the caller, reads their pending notifications one by one, and lets them acknowledge or flag each for human follow-up — all via natural voice conversation using **Telcoflow SDK** (telephony) + **Gemini Live** (native audio + function calling).

Based on the [Telcoflow Interactive Notifications use case](https://b3networks.docs.buildwithfern.com/use-cases/interactive-notifications).

## Architecture

```
Caller ←→ Telcoflow ←→ agent.py (Gemini Live session)
                            │
                        database.py
                        (aiosqlite)
                            │
               ┌────────────┼────────────┐
           customers    notifications    seed data
           (lookup)     (acknowledge/    (3 customers,
                         flag/query)     8 notifications)
```

On each incoming call:

1. `call.answer()` picks up the call
2. Caller is looked up by phone number in the `customers` table
3. Pending notifications are fetched from the `notifications` table
4. A Gemini Live session opens with the notification list injected into the system instruction
5. Gemini reads each notification, asks for confirmation, and calls tools to update the DB
6. Three concurrent coroutines bridge audio and handle lifecycle:
   - `stream_to_gemini` — caller audio → Gemini
   - `receive_from_gemini` — Gemini audio/tool calls → caller + DB
   - `end_watcher` — waits for `end_call` tool, then disconnects

## State Flow

```
PENDING → ANSWERED → DISCONNECTED
```

Notification status lifecycle:

```
pending → acknowledged    (caller confirms)
pending → follow_up       (caller declines / unsure)
```

## Project Structure

```
interactive_notifications/
├── agent.py          # Entrypoint — Telcoflow + Gemini Live bridge + lifecycle
├── database.py       # aiosqlite schema, seed data, query/mutation methods
├── requirements.txt
├── .env.example
└── README.md
```

## Setup

```bash
cd interactive_notifications

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
2026-04-10 10:00:00  notification_agent         INFO   NotificationDB ready at notifications.db
2026-04-10 10:00:00  notification_agent         INFO   NotificationAgent [YOUR_COMPANY_NAME] is live — waiting for calls …
```

## Sample Call Flow

```
Agent:   "Hello Alice, this is the notification assistant for YOUR_COMPANY_NAME.
          You have 3 pending notifications."

Agent:   "Notification 1: Your annual account review is due by April 30.
          Please schedule a meeting with your advisor.
          Would you like to acknowledge this, or should I flag it for follow-up?"

Alice:   "Yes, I've noted that. Acknowledge it."
          → acknowledge_notification(notification_id=1)

Agent:   "Notification 2: Payment of $245.00 for invoice #INV-2024-0389
          is overdue. Would you like to acknowledge or flag for follow-up?"

Alice:   "I need to talk to someone about that."
          → flag_for_followup(notification_id=2)

Agent:   "Notification 3: Your subscription plan will auto-renew on May 1.
          Would you like to acknowledge this?"

Alice:   "Got it, acknowledge."
          → acknowledge_notification(notification_id=3)

Agent:   "That's all your notifications. 2 acknowledged, 1 flagged for
          follow-up. Thank you and have a great day!"
          → end_call → call.disconnect()
```

## Seed Data

The database auto-seeds on first run with 3 customers and 8 pending notifications:

| Customer | Phone | Notifications |
|----------|-------|---------------|
| Alice Nguyen | +14155550101 | 3 (account review, overdue payment, subscription renewal) |
| Bob Martinez | +14155550102 | 2 (technician visit, support ticket update) |
| Carol Zhang | +14155550103 | 3 (order shipment, security alert, appointment reminder) |

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | *required* | Google API key with Gemini Live access |
| `WSS_API_KEY` | *required* | Telcoflow API key |
| `WSS_CONNECTOR_UUID` | *required* | Telcoflow connector UUID |
| `BUSINESS_NAME` | `YOUR_COMPANY_NAME` | Your business name (used in greetings) |
| `DB_PATH` | `notifications.db` | SQLite database file path |

## Tools

| Tool | Description |
|------|-------------|
| `acknowledge_notification(notification_id)` | Marks notification as acknowledged in DB |
| `flag_for_followup(notification_id)` | Flags notification for human follow-up in DB |
| `end_call` | Terminates the call after all notifications are delivered |
