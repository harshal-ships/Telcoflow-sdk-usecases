"""
Appointment Booking & Confirmation — Production Voice AI Agent
===============================================================
Bridges Telcoflow (telephony) ↔ Gemini Live (native audio) with:
  • Persistent SQLite storage for customers & appointments
  • Real Twilio SMS confirmations
  • Fallback transfer to human scheduling team
  • Structured logging throughout
  • Sandbox and production (mTLS) Telcoflow modes

State flows:
  Happy path:    PENDING → ANSWERED → DISCONNECTED
  Human fallback: PENDING → ANSWERED → CONNECTED → DISCONNECTED
"""

from __future__ import annotations

import asyncio
import logging
import signal

from google import genai
from google.genai import types
from telcoflow_sdk import TelcoflowClient, TelcoflowClientConfig, ActiveCall
import telcoflow_sdk.events as events

from config import AppConfig
from database import Database
from sms_service import SMSService
from tools import TOOL_DECLARATIONS, ToolDispatcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-28s  %(levelname)-5s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("appointment_agent")

# ---------------------------------------------------------------------------
# System instruction template (personalised per call)
# ---------------------------------------------------------------------------

SYSTEM_INSTRUCTION_TEMPLATE = """
You are **AppointmentBot**, the AI receptionist for **{business_name}**.
You are warm, efficient, and professional. Keep responses short — this is a phone call.

## Available services
{services_list}

## Business hours
{open_hour}:00 to {close_hour}:00, slots every {slot_minutes} minutes.

## Caller context
{caller_context}

## WORKFLOW — follow this order strictly:

### Step 1 – Greeting
Greet the caller by name if known. If they have upcoming appointments, mention the
next one and ask if they'd like to reschedule or book a new one.
If the caller is new, warmly welcome them and ask for their name.

### Step 2 – Gather details
Collect through natural conversation:
  1. **Service type** — which service they need.
  2. **Preferred date** — use YYYY-MM-DD format internally.
  3. **Preferred time** — or "any available".

### Step 3 – Check availability
Call `check_availability` with the date and service.
  • If slots exist, suggest the closest match to the caller's preference.
  • If none exist, offer to check another date OR call `connect_to_scheduling_team`
    if the caller wants human help.

### Step 4 – Confirm & book
Read back the chosen date, time, and service. Only after explicit confirmation,
call `book_appointment`.

### Step 5 – SMS confirmation
After `book_appointment` succeeds, call `send_sms_confirmation`.
Tell the caller: "You're all set! A confirmation SMS has been sent to your phone."

### Step 6 – Wrap up
Ask if there's anything else. If not, say goodbye and call `end_call`.

## CONSTRAINTS
- NEVER invent time slots — always call `check_availability` first.
- NEVER book without the caller's explicit verbal confirmation.
- If the caller asks about unrelated topics, politely redirect to appointment booking.
- If a booking fails (slot taken), apologise and offer to check availability again.
""".strip()


def _build_system_instruction(cfg: AppConfig, caller_context: str) -> str:
    services_list = "\n".join(f"  - {s}" for s in cfg.business.services)
    return SYSTEM_INSTRUCTION_TEMPLATE.format(
        business_name=cfg.business.name,
        services_list=services_list,
        open_hour=cfg.business.open_hour,
        close_hour=cfg.business.close_hour,
        slot_minutes=cfg.business.slot_duration_minutes,
        caller_context=caller_context,
    )


# ---------------------------------------------------------------------------
# Per-call session handler
# ---------------------------------------------------------------------------


async def handle_call(
    call: ActiveCall,
    cfg: AppConfig,
    db: Database,
    dispatcher: ToolDispatcher,
    gemini: genai.Client,
) -> None:
    """Run the full appointment conversation for a single inbound call."""

    call_id = getattr(call, "call_id", "unknown")
    caller_phone = getattr(call, "caller_number", None) or "unknown"
    log = logger.getChild(call_id[:12])

    @call.on(events.CALL_TERMINATED)
    def on_terminated():
        log.info("CALL_TERMINATED event received")

    await call.answer()
    log.info("Call answered from %s", caller_phone)

    # -- Build caller context before starting the LLM session ---------------
    customer = await db.get_customer_by_phone(caller_phone)
    if customer:
        upcoming = await db.get_upcoming_appointments(customer.id)
        if upcoming:
            appt = upcoming[0]
            caller_context = (
                f"Returning customer: {customer.name} (phone: {customer.phone}).\n"
                f"Next appointment: {appt.service} on {appt.date} at {appt.time} "
                f"(ref: {appt.id})."
            )
        else:
            caller_context = (
                f"Returning customer: {customer.name} (phone: {customer.phone}).\n"
                "No upcoming appointments."
            )
    else:
        caller_context = (
            f"New caller (phone: {caller_phone}). "
            "Not in the system yet — ask for their name."
        )

    system_instruction = _build_system_instruction(cfg, caller_context)

    live_config = {
        "response_modalities": ["AUDIO"],
        "system_instruction": system_instruction,
        "tools": TOOL_DECLARATIONS,
    }

    should_end = asyncio.Event()
    should_connect = asyncio.Event()

    async with gemini.aio.live.connect(
        model=cfg.gemini.model, config=live_config
    ) as session:

        async def stream_to_gemini():
            async for chunk in call.audio_stream():
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
                        args = fc.args if fc.args else {}
                        log.info("Tool call: %s(%s)", fc.name, args)
                        result = await dispatcher.dispatch(fc.name, args)

                        fn_responses.append(
                            types.FunctionResponse(
                                id=fc.id,
                                name=fc.name,
                                response=result,
                            )
                        )

                        if fc.name == "end_call":
                            should_end.set()
                        elif fc.name == "connect_to_scheduling_team":
                            should_connect.set()

                    await session.send_tool_response(function_responses=fn_responses)

        async def lifecycle_watcher():
            done = asyncio.Event()

            async def _wait_end():
                await should_end.wait()
                done.set()

            async def _wait_connect():
                await should_connect.wait()
                done.set()

            t1 = asyncio.create_task(_wait_end())
            t2 = asyncio.create_task(_wait_connect())
            await done.wait()
            t1.cancel()
            t2.cancel()

            await asyncio.sleep(2)

            if should_connect.is_set():
                log.info("Transferring to scheduling team")
                try:
                    await call.connect()
                    await call.close()
                except Exception:
                    log.exception("Fallback connect failed — disconnecting")
                    await call.disconnect()
            else:
                log.info("Ending call normally")
                await call.disconnect()

        await asyncio.gather(
            stream_to_gemini(),
            receive_from_gemini(),
            lifecycle_watcher(),
        )

    log.info("Session closed for %s", caller_phone)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def main() -> None:
    cfg = AppConfig.from_env()

    # -- Initialise services ------------------------------------------------
    db = Database(cfg.db_path)
    await db.connect()

    sms = SMSService(cfg.twilio)
    dispatcher = ToolDispatcher(db=db, sms=sms, business=cfg.business)
    gemini = genai.Client(api_key=cfg.gemini.api_key)

    # -- Build Telcoflow client ---------------------------------------------
    if cfg.telcoflow.mode == "production":
        tf_config = TelcoflowClientConfig.production(
            cert_path=cfg.telcoflow.cert_path,
            key_path=cfg.telcoflow.key_path,
            sample_rate=cfg.telcoflow.sample_rate,
        )
    else:
        tf_config = TelcoflowClientConfig.sandbox(
            api_key=cfg.telcoflow.api_key,
            connector_uuid=cfg.telcoflow.connector_uuid,
            sample_rate=cfg.telcoflow.sample_rate,
        )

    async with TelcoflowClient(tf_config) as tf_client:

        @tf_client.on(events.INCOMING_CALL)
        async def on_incoming(call: ActiveCall):
            logger.info("Incoming call: %s", getattr(call, "call_id", "?"))
            try:
                await handle_call(call, cfg, db, dispatcher, gemini)
            except Exception:
                logger.exception("Unhandled error in call session")

        logger.info(
            "AppointmentBot [%s] is live — %s mode — waiting for calls …",
            cfg.business.name,
            cfg.telcoflow.mode,
        )

        # Graceful shutdown on SIGINT / SIGTERM
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

    await db.close()
    logger.info("Goodbye.")


if __name__ == "__main__":
    asyncio.run(main())
