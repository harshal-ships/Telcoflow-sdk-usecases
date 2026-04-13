# After-hours Voicemail & Notification — Production Voice AI Agent

A production voice agent that routes calls by time of day: during business hours it connects directly to staff; after hours it takes a voicemail using **Gemini Live** (native audio), transcribes it with **Amazon Nova 2 Sonic** (ASR), stores it in **SQLite**, and notifies the team via **Slack**.

Based on the [Telcoflow After-hours Voicemail use case](https://b3networks.docs.buildwithfern.com/use-cases/after-hours-voicemail).

## State Flows

```
Business hours:  PENDING → CONNECTED → DISCONNECTED   (straight to callee)
After hours:     PENDING → ANSWERED  → DISCONNECTED   (AI voicemail)
```

## How It Works

### Business Hours (e.g. 9:00–17:00)

Incoming call → `call.connect()` routes to the callee (staff phone / SIM) → `call.close()` and the agent leaves. The caller rings straight through with zero AI involvement.

### After Hours

1. **Gemini Live answers** — greets the caller, explains the office is closed, asks them to leave a message
2. **Recording** — all caller audio chunks are collected in memory while Gemini manages the conversation
3. **Gemini detects end of message** — when the caller finishes, it confirms and calls `end_voicemail`
4. **Call disconnects** — 2-second drain for final audio, then `call.disconnect()`
5. **Post-call pipeline** (runs after hangup):
   - Raw PCM → WAV file saved to `recordings/`
   - Audio downsampled 24 kHz → 16 kHz, then sent to Nova Sonic for ASR transcription
   - Transcript + metadata stored in SQLite
   - Slack webhook fires with caller number, duration, transcript, and file path

## Architecture

```
                        ┌─ business hours ─→ call.connect() → call.close()
                        │
Caller ←→ Telcoflow ←→ voicemail_agent.py
                        │
                        └─ after hours ─→ Gemini Live (voicemail conversation)
                                                │
                                     ┌──────────┼──────────┐
                                     │          │          │
                                database.py  transcription  notifications
                                (aiosqlite   .py            .py
                                 + WAV)     (Nova Sonic)    (Slack)
```

## Project Structure

```
after_hours_voicemail/
├── voicemail_agent.py     # Entrypoint — time-of-day routing + Gemini Live bridge
├── config.py              # All env-based configuration
├── database.py            # aiosqlite + WAV file persistence
├── transcription.py       # Amazon Nova 2 Sonic ASR (bidirectional stream)
├── notifications.py       # Slack incoming webhook
├── requirements.txt
├── .env.example
└── README.md
```

## Setup

```bash
cd after_hours_voicemail

python -m venv .venv && source .venv/bin/activate

pip install -r requirements.txt --extra-index-url https://test.pypi.org/simple/

cp .env.example .env
# Fill in at minimum: GEMINI_API_KEY, WSS_API_KEY, WSS_CONNECTOR_UUID
```

### Enable Nova Sonic transcription (recommended)

1. Ensure you have an AWS account with [Amazon Bedrock access to Nova Sonic](https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-getting-started.html) in `us-east-1`
2. Set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_DEFAULT_REGION` in `.env`
3. Voicemails will be transcribed automatically after each call via Nova Sonic ASR

Without AWS credentials, audio is still recorded and saved — just without a transcript.

### Enable Slack notifications (recommended)

1. Create an [Incoming Webhook](https://api.slack.com/messaging/webhooks) in your Slack workspace
2. Set `SLACK_WEBHOOK_URL` in `.env`
3. Your team gets notified in real-time when a voicemail arrives

Without Slack, notification content is logged to stdout.

## Run

```bash
python voicemail_agent.py
```

Output:

```
2026-04-09 18:00:00  voicemail_agent            INFO   VoicemailBot [XanhSM] is live — hours 9:00–17:00 Asia/Singapore — waiting for calls …
```

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | *required* | Google API key with Gemini Live access |
| `WSS_API_KEY` | *required* | Telcoflow API key |
| `WSS_CONNECTOR_UUID` | *required* | Telcoflow connector UUID |
| `AWS_ACCESS_KEY_ID` | — | AWS access key (optional, enables transcription) |
| `AWS_SECRET_ACCESS_KEY` | — | AWS secret key |
| `AWS_DEFAULT_REGION` | `us-east-1` | AWS region for Bedrock |
| `NOVA_SONIC_MODEL_ID` | `amazon.nova-2-sonic-v1:0` | Nova Sonic model ID |
| `SLACK_WEBHOOK_URL` | — | Slack incoming webhook URL (optional) |
| `SLACK_CHANNEL` | `#incoming-calls` | Slack channel override |
| `BUSINESS_NAME` | `XanhSM` | Your business name |
| `BUSINESS_TIMEZONE` | `Asia/Singapore` | IANA timezone for business hours |
| `BUSINESS_OPEN_HOUR` | `9` | Opening hour (24h) |
| `BUSINESS_CLOSE_HOUR` | `17` | Closing hour (24h) |
| `DB_PATH` | `voicemail.db` | SQLite database path |
| `RECORDINGS_DIR` | `recordings` | Directory for WAV files |

## Transcription Details

Amazon Nova 2 Sonic is a speech-to-speech model that provides automatic speech recognition (ASR) as part of its bidirectional streaming response. The transcription flow:

1. Recorded voicemail PCM audio (24 kHz) is **downsampled to 16 kHz** (Nova Sonic input format)
2. Audio is streamed to Nova Sonic via the `InvokeModelWithBidirectionalStream` Bedrock API
3. Nova Sonic emits `textOutput` events with `role: "USER"` containing the caller's ASR transcription
4. The final transcription (marked `generationStage: "FINAL"`) is collected and stored

## What Gets Stored

### SQLite (`voicemail.db`)

| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT | Unique voicemail ID |
| `caller_number` | TEXT | Caller's phone number |
| `audio_path` | TEXT | Path to the WAV recording |
| `transcript` | TEXT | Nova Sonic ASR transcription |
| `duration_seconds` | REAL | Recording length |
| `created_at` | TEXT | ISO timestamp |

### WAV Files (`recordings/`)

Files are named `YYYYMMDD_HHMMSS_{phone}_{id}.wav` — 16-bit, 24 kHz, mono PCM.
