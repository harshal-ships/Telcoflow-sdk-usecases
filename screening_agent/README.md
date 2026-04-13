# Candidate Screening Agent — Multi-phase Voice Interview

A production voice agent that conducts structured phone screening interviews using **Telcoflow SDK** (telephony) + **Gemini Live** (native audio + function calling). Inspired by the [LiveKit AgentTask/TaskGroup pattern](https://docs.livekit.io/agents/), rebuilt entirely on Telcoflow.

## Architecture Pattern: LiveKit → Telcoflow Translation

| LiveKit Concept | Telcoflow + Gemini Live Equivalent |
|---|---|
| `AgentTask` | Phase in the system instruction + dedicated tool declarations |
| `TaskGroup` | `PhaseTracker` class that monitors tool call completions |
| `@function_tool()` | Gemini Live function declarations + `send_tool_response()` |
| `session.generate_reply()` | Gemini natively generates audio responses |
| `AgentSession(stt=..., tts=...)` | Not needed — Gemini Live is native audio end-to-end |
| `session.shutdown()` | `call.disconnect()` via Telcoflow |
| `session.userdata` | `PhaseTracker` dataclass stores all collected data |
| `AgentServer / @rtc_session` | `TelcoflowClient / @client.on(INCOMING_CALL)` |

## Interview Phases

```
Phase 1: Introduction    → record_intro
Phase 2: Contact Info    → record_contact
Phase 3: Commute         → record_commute
Phase 4: Experience      → record_experience
Phase 5: Behavioral      → record_strengths + record_weaknesses + record_work_style
Phase 6: Wrap-up         → end_screening
```

Cross-cutting: `disqualify` can fire at any phase to terminate early.

## How It Works

1. **Candidate calls in** → Telcoflow delivers the call to your agent
2. **Gemini Live session opens** with the full system instruction covering all 5 phases and 9 tools
3. **Alex (the AI interviewer)** guides the conversation phase-by-phase, calling recording tools as data is collected
4. **PhaseTracker** monitors completions — each tool handler returns a `"next"` field telling Gemini which phase to move to
5. **Behavioral phase** has 3 sub-tools (strengths/weaknesses/work_style) that can be called in any order — the phase completes when all 3 are recorded
6. **After all phases**, Gemini thanks the candidate and calls `end_screening`
7. **Post-call pipeline**: Gemini text API evaluates the candidate based on all collected data → results written to CSV

## Project Structure

```
screening_agent/
├── agent.py          # Entrypoint — Telcoflow + Gemini Live bridge + lifecycle
├── config.py         # Env-based configuration
├── tools.py          # Tool declarations, PhaseTracker, ToolDispatcher
├── evaluation.py     # Post-call LLM evaluation + CSV output
├── requirements.txt
├── .env.example
└── README.md
```

## Setup

```bash
cd screening_agent

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
2026-04-09 10:00:00  screening_agent            INFO   ScreeningAgent [YOUR_COMPANY_NAME — Customer Service Representative] is live — waiting for calls …
```

## Sample Call Flow

```
Alex:       "Hi, this is Alex from YOUR_COMPANY_NAME. Thank you for taking the time
             to speak with me today. Could you start by introducing yourself?"
Candidate:  "Sure, I'm Sarah Chen. I've been in customer service for about 5 years..."
             → record_intro(name="Sarah Chen", intro_notes="5 years in CS...")

Alex:       "Great to meet you, Sarah. Could I get your email and a good phone number?"
Candidate:  "sarah.chen@email.com, and my number is 555-0199."
             → record_contact(email="sarah.chen@email.com", phone="555-0199")

Alex:       "This role requires coming to the office three days a week. Would that work?"
Candidate:  "Yes, I drive so that's no problem."
             → record_commute(can_commute=true, commute_method="driving")

Alex:       "Tell me about your work background..."
Candidate:  [describes experience]
             → record_experience(years=5, description="...")

Alex:       "What would you say are your biggest strengths?"
             → record_strengths / record_weaknesses / record_work_style (any order)

Alex:       "Thank you Sarah! We'll be in touch within 3 business days. Goodbye!"
             → end_screening → call.disconnect()

[Post-call]  → Gemini evaluates → CSV row written
```

## Output: `screening_results.csv`

| Column | Description |
|--------|-------------|
| `candidate_name` | Full name |
| `intro_notes` | Self-introduction summary |
| `email` | Email address |
| `phone` | Phone number |
| `can_commute` | Boolean |
| `commute_method` | driving / bus / subway / etc. |
| `years_of_experience` | Integer |
| `experience_description` | Work history summary |
| `strengths` | Professional strengths |
| `weaknesses` | Areas for growth |
| `work_style` | independent / team_player / hybrid |
| `evaluation` | AI-generated assessment with verdict |
| `disqualification_reason` | Only if disqualified |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | *required* | Google API key |
| `WSS_API_KEY` | *required* | Telcoflow API key |
| `WSS_CONNECTOR_UUID` | *required* | Telcoflow connector UUID |
| `GEMINI_LIVE_MODEL` | `gemini-2.5-flash-native-audio-preview-12-2025` | Model for voice |
| `GEMINI_TEXT_MODEL` | `gemini-2.5-flash` | Model for post-call evaluation |
| `SCREENING_POSITION` | Customer Service Representative | Job title |
| `SCREENING_COMPANY` | YOUR_COMPANY_NAME | Company name |
| `RESULTS_CSV` | `screening_results.csv` | Output CSV path |
