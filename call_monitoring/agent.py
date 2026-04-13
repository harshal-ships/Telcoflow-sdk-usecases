"""
Real-time Call Monitoring & Coaching Agent
==========================================
A Telcoflow + Gemini Live agent that silently monitors live calls between
a customer and a support agent, providing real-time coaching via whisper mode.

Uses 3-way conference audio modes:
  spy()     → AI listens silently (neither party hears AI)
  whisper() → AI coaches the callee/agent (caller doesn't hear)
  barge()   → AI speaks to both parties

State flow:
  PENDING → ANSWERED → CONNECTED (spy mode)
    ↕ whisper mode for coaching ↕
  → DISCONNECTED (AI leaves, parties stay connected)
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from dotenv import load_dotenv
from google import genai
from google.genai import types
from telcoflow_sdk import TelcoflowClient, TelcoflowClientConfig, ActiveCall
import telcoflow_sdk.events as events

from database import AnalyticsDB

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-28s  %(levelname)-5s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("call_monitoring")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise EnvironmentError(f"Missing required env var: {name}")
    return val


GEMINI_API_KEY = _require("GEMINI_API_KEY")
WSS_API_KEY = _require("WSS_API_KEY")
WSS_CONNECTOR_UUID = _require("WSS_CONNECTOR_UUID")
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "YOUR_COMPANY_NAME")
DB_PATH = os.getenv("DB_PATH", "call_monitoring.db")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025")
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "24000"))


# ---------------------------------------------------------------------------
# System instruction for the monitoring AI
# ---------------------------------------------------------------------------

SYSTEM_INSTRUCTION = """
You are a real-time call quality supervisor for **{business_name}**.
You are silently monitoring a live call between a customer and a support agent.

## YOUR ROLE
You listen to both sides of the conversation in real time. You do NOT speak to
the customer — you only coach the support agent when needed, via a private
whisper channel the customer cannot hear.

## ANALYSIS
Continuously analyze the conversation for:
1. **Sentiment** — is the customer positive, neutral, or negative?
2. **Key topics** — what subjects are being discussed?
3. **Coaching opportunities** — is the agent struggling, giving wrong info,
   or is the customer becoming frustrated?

## TOOLS — use these throughout the call:

### log_sentiment
Call this periodically (every 30–60 seconds, or when sentiment shifts) to
record the current sentiment and topics being discussed.

### coach_agent
Call this when you detect the support agent needs help. Provide a brief,
actionable suggestion. After calling this tool, speak your coaching message
aloud — it will be whispered privately to the agent only.

Examples of when to coach:
- Agent is giving incorrect information
- Customer is getting frustrated and agent isn't de-escalating
- Agent missed an upsell or resolution opportunity
- Agent is rambling or off-topic

Keep coaching messages SHORT (1–2 sentences). You are whispering in their ear
during a live call — be concise and direct.

### end_monitoring
Call this when the call is wrapping up (goodbyes exchanged, issue resolved).
This disconnects the AI supervisor — the customer and agent stay connected.

## CONSTRAINTS
- Do NOT speak unless you are coaching. You are in silent monitoring mode.
- When coaching, be brief and helpful — the agent hears you in real time.
- Never reveal to the customer that you exist.
""".strip()


# ---------------------------------------------------------------------------
# Tool declarations (Gemini Live function-calling schema)
# ---------------------------------------------------------------------------

COACH_AGENT = {
    "name": "coach_agent",
    "description": (
        "Privately coach the support agent with a brief suggestion. "
        "After calling this, speak your coaching message — it will be "
        "whispered to the agent only (the customer cannot hear it). "
        "Call this when the agent needs help, is giving wrong info, "
        "or the customer is frustrated."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "suggestion": {
                "type": "STRING",
                "description": "Brief coaching suggestion for the support agent.",
            },
        },
        "required": ["suggestion"],
    },
}

LOG_SENTIMENT = {
    "name": "log_sentiment",
    "description": (
        "Record the current call sentiment and discussion topics. "
        "Call this periodically (every 30–60 seconds) or whenever "
        "the sentiment noticeably shifts."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "sentiment": {
                "type": "STRING",
                "description": "Current sentiment: positive, neutral, or negative.",
            },
            "topics": {
                "type": "STRING",
                "description": "Comma-separated list of current discussion topics.",
            },
        },
        "required": ["sentiment", "topics"],
    },
}

END_MONITORING = {
    "name": "end_monitoring",
    "description": (
        "End the AI monitoring session. The customer and agent stay "
        "connected — only the AI supervisor disconnects. Call this when "
        "the call is wrapping up."
    ),
    "parameters": {"type": "OBJECT", "properties": {}},
}

TOOL_DECLARATIONS = [
    {"function_declarations": [COACH_AGENT, LOG_SENTIMENT, END_MONITORING]}
]


# ---------------------------------------------------------------------------
# Per-call monitoring session
# ---------------------------------------------------------------------------


async def handle_monitored_call(
    call: ActiveCall,
    gemini: genai.Client,
    analytics_db: AnalyticsDB,
) -> None:
    call_id = getattr(call, "call_id", "unknown")
    caller_phone = getattr(call, "caller_number", None) or "unknown"
    log = logger.getChild(call_id[:12])

    @call.on(events.CALL_TERMINATED)
    def on_terminated():
        log.info("CALL_TERMINATED")

    # Step 1: Answer the incoming call
    await call.answer()
    log.info("Call answered from %s", caller_phone)

    # Step 2: Connect to callee (human agent) — creates 3-way conference
    await call.connect()
    log.info("Connected to callee — 3-way conference established")

    # Step 3: Enter silent monitoring mode
    await call.spy()
    log.info("Entered spy mode — silently monitoring")

    # Step 4: Open Gemini Live session for analysis
    system_instruction = SYSTEM_INSTRUCTION.format(business_name=BUSINESS_NAME)
    live_config = {
        "response_modalities": ["AUDIO"],
        "system_instruction": system_instruction,
        "tools": TOOL_DECLARATIONS,
    }

    should_end = asyncio.Event()
    whisper_active = asyncio.Event()
    coaching_suggestions: list[str] = []

    async with gemini.aio.live.connect(
        model=GEMINI_MODEL, config=live_config
    ) as session:

        # --- Conference audio → Gemini (AI hears both parties) --------
        async def stream_to_gemini():
            async for chunk in call.audio_stream():
                await session.send_realtime_input(
                    audio=types.Blob(
                        data=chunk,
                        mime_type=f"audio/pcm;rate={SAMPLE_RATE}",
                    )
                )

        # --- Gemini → Call (coaching audio + tool calls) --------------
        async def receive_from_gemini():
            async for response in session.receive():
                if content := response.server_content:
                    if content.interrupted:
                        await call.clear_send_audio_buffer()
                    elif content.model_turn:
                        for part in content.model_turn.parts:
                            if part.inline_data:
                                await call.send_audio(part.inline_data.data)

                    if content.turn_complete and whisper_active.is_set():
                        whisper_active.clear()
                        await call.spy()
                        log.info("Coaching complete — back to spy mode")

                if response.tool_call:
                    fn_responses = []
                    for fc in response.tool_call.function_calls:
                        args = fc.args if fc.args else {}
                        log.info("Tool: %s(%s)", fc.name, args)

                        if fc.name == "coach_agent":
                            suggestion = args.get("suggestion", "")
                            coaching_suggestions.append(suggestion)
                            log.info("Coaching: %s", suggestion)

                            whisper_active.set()
                            await call.whisper()
                            log.info("Switched to whisper mode for coaching")

                            fn_responses.append(
                                types.FunctionResponse(
                                    id=fc.id,
                                    name=fc.name,
                                    response={
                                        "status": "whisper_active",
                                        "instruction": (
                                            "Whisper mode is active. Speak your coaching "
                                            "message now — the agent will hear you but the "
                                            "customer will not."
                                        ),
                                    },
                                )
                            )

                        elif fc.name == "log_sentiment":
                            sentiment = args.get("sentiment", "neutral")
                            topics = args.get("topics", "")
                            await analytics_db.log_analytics(
                                call_id=call_id,
                                sentiment=sentiment,
                                topics=topics,
                                caller_number=caller_phone,
                            )
                            fn_responses.append(
                                types.FunctionResponse(
                                    id=fc.id,
                                    name=fc.name,
                                    response={"status": "logged", "sentiment": sentiment},
                                )
                            )

                        elif fc.name == "end_monitoring":
                            fn_responses.append(
                                types.FunctionResponse(
                                    id=fc.id,
                                    name=fc.name,
                                    response={"status": "ending"},
                                )
                            )
                            should_end.set()

                        else:
                            log.warning("Unknown tool: %s", fc.name)
                            fn_responses.append(
                                types.FunctionResponse(
                                    id=fc.id,
                                    name=fc.name,
                                    response={"error": f"Unknown tool: {fc.name}"},
                                )
                            )

                    await session.send_tool_response(function_responses=fn_responses)

        # --- Lifecycle: end monitoring when signaled -------------------
        async def end_watcher():
            await should_end.wait()
            await asyncio.sleep(1)

            if coaching_suggestions:
                await analytics_db.log_analytics(
                    call_id=call_id,
                    sentiment="session_end",
                    topics="monitoring_complete",
                    caller_number=caller_phone,
                    coaching_given="; ".join(coaching_suggestions),
                )

            await call.close()
            log.info("AI supervisor disconnected — caller and agent remain connected")

        await asyncio.gather(
            stream_to_gemini(),
            receive_from_gemini(),
            end_watcher(),
        )

    analytics = await analytics_db.get_call_analytics(call_id)
    log.info(
        "Monitoring complete for call %s — %d analytics entries, %d coaching events",
        call_id, len(analytics), len(coaching_suggestions),
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def main() -> None:
    analytics_db = AnalyticsDB(DB_PATH)
    await analytics_db.connect()

    gemini = genai.Client(api_key=GEMINI_API_KEY)

    tf_config = TelcoflowClientConfig.sandbox(
        api_key=WSS_API_KEY,
        connector_uuid=WSS_CONNECTOR_UUID,
        sample_rate=SAMPLE_RATE,
    )

    async with TelcoflowClient(tf_config) as tf_client:

        @tf_client.on(events.INCOMING_CALL)
        async def on_incoming(call: ActiveCall):
            log_id = getattr(call, "call_id", "?")
            logger.info("[%s] Incoming call — starting monitoring", log_id)
            try:
                await handle_monitored_call(call, gemini, analytics_db)
            except Exception:
                logger.exception("[%s] Monitoring session failed", log_id)

        logger.info(
            "CallMonitor [%s] is live — waiting for calls …",
            BUSINESS_NAME,
        )

        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

        run_task = asyncio.create_task(tf_client.run_forever())
        stop_task = asyncio.create_task(stop.wait())
        await asyncio.wait(
            [run_task, stop_task], return_when=asyncio.FIRST_COMPLETED
        )

        if stop.is_set():
            logger.info("Shutdown signal received — cleaning up …")
            run_task.cancel()

    await analytics_db.close()
    logger.info("Goodbye.")


if __name__ == "__main__":
    asyncio.run(main())
