"""
After-hours Voicemail & Notification — Production Voice AI Agent
================================================================
Handles incoming calls with time-of-day routing:

  Business hours  → connect to callee directly (no AI, no answer)
  After hours     → Gemini Live voicemail: greet, record, transcribe, notify

State flows:
  Business hours:  PENDING → CONNECTED → DISCONNECTED
  After hours:     PENDING → ANSWERED  → DISCONNECTED
"""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types
from telcoflow_sdk import TelcoflowClient, TelcoflowClientConfig, ActiveCall
import telcoflow_sdk.events as events

from config import AppConfig
from database import VoicemailDB
from transcription import TranscriptionService
from notifications import SlackNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-28s  %(levelname)-5s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("voicemail_agent")

# ---------------------------------------------------------------------------
# Gemini system instruction for the voicemail bot
# ---------------------------------------------------------------------------

VOICEMAIL_SYSTEM_INSTRUCTION = """
You are the after-hours voicemail assistant for **{business_name}**.
The office is currently closed. Business hours are {open_hour}:00 to {close_hour}:00 ({timezone}).

## YOUR TASK
1. Greet the caller warmly and let them know the office is closed.
   Example: "Thank you for calling {business_name}. Our office is currently closed.
   Our business hours are {open_hour} AM to {close_hour_12} PM."

2. Ask them to leave a message:
   "Please leave your name, number, and a brief message, and we'll get back to you
   on the next business day."

3. Listen to their message patiently. Do NOT interrupt while they are speaking.

4. When the caller finishes their message (they stop speaking, say goodbye, or
   indicate they are done), respond with:
   "Thank you. Your message has been recorded and our team will be notified.
   We'll get back to you as soon as possible. Goodbye!"

5. Immediately after your goodbye, call the `end_voicemail` tool.

## CONSTRAINTS
- Keep your own responses very brief — you're a voicemail system, not a chatbot.
- If the caller asks a question you cannot answer, politely say the team will follow up.
- NEVER make promises about specific callback times.
- Call `end_voicemail` exactly once, as the very last action.
""".strip()

# Tool declaration for ending the voicemail
END_VOICEMAIL_DECLARATION = {
    "name": "end_voicemail",
    "description": (
        "Signals that the voicemail recording is complete and the call should end. "
        "Call this after confirming the message was recorded and saying goodbye."
    ),
    "parameters": {"type": "OBJECT", "properties": {}},
}

TOOLS = [{"function_declarations": [END_VOICEMAIL_DECLARATION]}]


def _build_system_instruction(cfg: AppConfig) -> str:
    close_12 = cfg.business.close_hour if cfg.business.close_hour <= 12 else cfg.business.close_hour - 12
    return VOICEMAIL_SYSTEM_INSTRUCTION.format(
        business_name=cfg.business.name,
        open_hour=cfg.business.open_hour,
        close_hour=cfg.business.close_hour,
        close_hour_12=close_12,
        timezone=cfg.business.timezone,
    )


def _is_business_hours(cfg: AppConfig) -> bool:
    try:
        tz = ZoneInfo(cfg.business.timezone)
    except KeyError:
        logger.warning("Unknown timezone %s, falling back to UTC", cfg.business.timezone)
        tz = ZoneInfo("UTC")
    now = datetime.now(tz)
    return cfg.business.open_hour <= now.hour < cfg.business.close_hour


# ---------------------------------------------------------------------------
# Business-hours handler (connect to callee, leave)
# ---------------------------------------------------------------------------


async def handle_business_hours(call: ActiveCall) -> None:
    call_id = getattr(call, "call_id", "?")
    logger.info("[%s] Business hours — connecting to callee", call_id)
    try:
        await call.connect()
        await call.close()
    except Exception:
        logger.exception("[%s] Failed to connect to callee", call_id)


# ---------------------------------------------------------------------------
# After-hours handler (Gemini Live voicemail session)
# ---------------------------------------------------------------------------


async def handle_after_hours(
    call: ActiveCall,
    cfg: AppConfig,
    gemini: genai.Client,
    voicemail_db: VoicemailDB,
    transcription: TranscriptionService,
    slack: SlackNotifier,
) -> None:
    call_id = getattr(call, "call_id", "unknown")
    caller_phone = getattr(call, "caller_number", None) or "unknown"
    log = logger.getChild(call_id[:12])

    @call.on(events.CALL_TERMINATED)
    def on_terminated():
        log.info("CALL_TERMINATED")

    await call.answer()
    log.info("After-hours call answered from %s", caller_phone)

    system_instruction = _build_system_instruction(cfg)
    live_config = {
        "response_modalities": ["AUDIO"],
        "system_instruction": system_instruction,
        "tools": TOOLS,
    }

    should_end = asyncio.Event()
    caller_audio_chunks: list[bytes] = []

    async with gemini.aio.live.connect(
        model=cfg.gemini.model, config=live_config
    ) as session:

        async def stream_to_gemini():
            async for chunk in call.audio_stream():
                caller_audio_chunks.append(chunk)
                await session.send_realtime_input(
                    audio=types.Blob(
                        data=chunk,
                        mime_type=f"audio/pcm;rate={cfg.gemini.sample_rate}",
                    )
                )

        async def receive_from_gemini():
            async for response in session.receive():
                if content := response.server_content:
                    if content.interrupted:
                        await call.clear_send_audio_buffer()
                    elif content.model_turn:
                        for part in content.model_turn.parts:
                            if part.inline_data:
                                await call.send_audio(part.inline_data.data)

                if response.tool_call:
                    fn_responses = []
                    for fc in response.tool_call.function_calls:
                        log.info("Tool call: %s", fc.name)
                        fn_responses.append(
                            types.FunctionResponse(
                                id=fc.id,
                                name=fc.name,
                                response={"status": "success"},
                            )
                        )
                        if fc.name == "end_voicemail":
                            should_end.set()

                    await session.send_tool_response(function_responses=fn_responses)

        async def end_watcher():
            await should_end.wait()
            await asyncio.sleep(2)
            await call.disconnect()

        await asyncio.gather(
            stream_to_gemini(),
            receive_from_gemini(),
            end_watcher(),
        )

    # -- Post-call processing (transcribe → save → notify) ------------------
    log.info("Call ended — processing voicemail (%d audio chunks)", len(caller_audio_chunks))

    if not caller_audio_chunks:
        log.info("No caller audio recorded — skipping post-processing")
        return

    full_pcm = b"".join(caller_audio_chunks)

    voicemail = await voicemail_db.save_voicemail(
        caller_number=caller_phone,
        pcm_audio=full_pcm,
    )

    transcript = await transcription.transcribe(full_pcm)
    if transcript:
        await voicemail_db.update_transcript(voicemail.id, transcript)
        voicemail.transcript = transcript

    await slack.send_voicemail_alert(
        caller_number=caller_phone,
        transcript=voicemail.transcript,
        duration_seconds=voicemail.duration_seconds,
        voicemail_id=voicemail.id,
        audio_path=voicemail.audio_path,
    )

    log.info(
        "Voicemail %s processed — %.1fs, transcript: %d chars",
        voicemail.id, voicemail.duration_seconds, len(voicemail.transcript),
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def main() -> None:
    cfg = AppConfig.from_env()

    voicemail_db = VoicemailDB(cfg.db_path, cfg.recordings_dir)
    await voicemail_db.connect()

    transcription = TranscriptionService(cfg.nova_sonic)
    slack = SlackNotifier(cfg.slack)
    gemini = genai.Client(api_key=cfg.gemini.api_key)

    tf_config = TelcoflowClientConfig.sandbox(
        api_key=cfg.telcoflow.api_key,
        connector_uuid=cfg.telcoflow.connector_uuid,
        sample_rate=cfg.telcoflow.sample_rate,
    )

    async with TelcoflowClient(tf_config) as tf_client:

        @tf_client.on(events.INCOMING_CALL)
        async def on_incoming(call: ActiveCall):
            call_id = getattr(call, "call_id", "?")

            if _is_business_hours(cfg):
                await handle_business_hours(call)
            else:
                try:
                    await handle_after_hours(
                        call, cfg, gemini, voicemail_db, transcription, slack
                    )
                except Exception:
                    logger.exception("[%s] After-hours session failed", call_id)

        logger.info(
            "VoicemailBot [%s] is live — hours %d:00–%d:00 %s — waiting for calls …",
            cfg.business.name,
            cfg.business.open_hour,
            cfg.business.close_hour,
            cfg.business.timezone,
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

    await voicemail_db.close()
    logger.info("Goodbye.")


if __name__ == "__main__":
    asyncio.run(main())
