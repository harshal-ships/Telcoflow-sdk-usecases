"""
Interactive Notifications Agent — Voice Notification Delivery
=============================================================
A Telcoflow + Gemini Live agent that answers incoming calls, looks up the
caller in the database, reads their pending notifications one by one, and
lets them acknowledge or flag each for follow-up via voice.

State flow:  PENDING → ANSWERED → DISCONNECTED

Gemini Live handles native audio end-to-end (no separate TTS/STT).
Tool calls drive DB mutations:
  - acknowledge_notification(notification_id)
  - flag_for_followup(notification_id)
  - end_call
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

from database import NotificationDB

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-28s  %(levelname)-5s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("notification_agent")


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise EnvironmentError(f"Missing required env var: {name}")
    return val


GEMINI_API_KEY = lambda: _require("GEMINI_API_KEY")  # noqa: E731
WSS_API_KEY = lambda: _require("WSS_API_KEY")  # noqa: E731
WSS_CONNECTOR_UUID = lambda: _require("WSS_CONNECTOR_UUID")  # noqa: E731
BUSINESS_NAME = lambda: os.getenv("BUSINESS_NAME", "YOUR_COMPANY_NAME")  # noqa: E731
DB_PATH = lambda: os.getenv("DB_PATH", "notifications.db")  # noqa: E731
GEMINI_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"
SAMPLE_RATE = 24000


# ---------------------------------------------------------------------------
# System instruction template
# ---------------------------------------------------------------------------

SYSTEM_INSTRUCTION = """
You are a notification assistant for **{business_name}**.
A customer is calling in, and you need to deliver their pending notifications.

## CALLER CONTEXT
- **Name:** {customer_name}
- **Phone:** {customer_phone}

## PENDING NOTIFICATIONS
{notification_list}

## YOUR TASK
1. Greet the caller by name: "Hello {customer_name}, this is the notification assistant for {business_name}."
2. Tell them how many pending notifications they have.
3. Read each notification **one at a time**, clearly stating the notification ID and message.
4. After reading each notification, ask: "Would you like to acknowledge this notification, or should I flag it for a follow-up with our team?"
5. Based on the caller's response:
   - If they confirm / acknowledge → call `acknowledge_notification` with the notification_id
   - If they decline, are unsure, or want human follow-up → call `flag_for_followup` with the notification_id
6. After processing ALL notifications, summarize what was acknowledged vs. flagged.
7. Say goodbye: "That's all your notifications. Thank you and have a great day!"
8. Call `end_call` as the very last action.

## RULES
- Deliver notifications in the order listed above. Do NOT skip any.
- Wait for the caller's response before moving to the next notification.
- Keep your language concise and clear — this is a phone call.
- If the caller asks a question about a notification, answer briefly if you can, otherwise say a team member will follow up.
- Do NOT fabricate information not present in the notifications.
- Call each tool exactly once per notification. Call `end_call` exactly once at the end.
""".strip()


# ---------------------------------------------------------------------------
# Tool declarations (Gemini Live function-calling schema)
# ---------------------------------------------------------------------------

ACKNOWLEDGE_NOTIFICATION = {
    "name": "acknowledge_notification",
    "description": (
        "Mark a notification as acknowledged by the caller. "
        "Call this when the caller confirms they have received and understood the notification."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "notification_id": {
                "type": "INTEGER",
                "description": "The ID of the notification to acknowledge.",
            },
        },
        "required": ["notification_id"],
    },
}

FLAG_FOR_FOLLOWUP = {
    "name": "flag_for_followup",
    "description": (
        "Flag a notification for human follow-up. "
        "Call this when the caller is unsure, declines, or wants to speak with a team member about the notification."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "notification_id": {
                "type": "INTEGER",
                "description": "The ID of the notification to flag for follow-up.",
            },
        },
        "required": ["notification_id"],
    },
}

END_CALL = {
    "name": "end_call",
    "description": (
        "Terminate the call after all notifications have been delivered and processed. "
        "Call this as the very last action after saying goodbye."
    ),
    "parameters": {"type": "OBJECT", "properties": {}},
}

TOOL_DECLARATIONS = [
    {
        "function_declarations": [
            ACKNOWLEDGE_NOTIFICATION,
            FLAG_FOR_FOLLOWUP,
            END_CALL,
        ]
    }
]


# ---------------------------------------------------------------------------
# Build per-call system instruction with caller context
# ---------------------------------------------------------------------------


def _build_notification_list(notifications: list) -> str:
    if not notifications:
        return "(No pending notifications.)"
    lines = []
    for n in notifications:
        lines.append(f"- **ID {n.id}:** {n.message}")
    return "\n".join(lines)


def _build_system_instruction(
    business_name: str,
    customer_name: str,
    customer_phone: str,
    notifications: list,
) -> str:
    return SYSTEM_INSTRUCTION.format(
        business_name=business_name,
        customer_name=customer_name,
        customer_phone=customer_phone,
        notification_list=_build_notification_list(notifications),
    )


# ---------------------------------------------------------------------------
# Per-call notification session
# ---------------------------------------------------------------------------


async def handle_notification_call(
    call: ActiveCall,
    db: NotificationDB,
    gemini: genai.Client,
    business_name: str,
) -> None:
    call_id = getattr(call, "call_id", "unknown")
    caller_phone = getattr(call, "caller_number", None) or "unknown"
    log = logger.getChild(call_id[:12])

    @call.on(events.CALL_TERMINATED)
    def on_terminated():
        log.info("CALL_TERMINATED")

    await call.answer()
    log.info("Call answered from %s", caller_phone)

    # -- Look up caller in DB -----------------------------------------------
    customer = await db.get_customer_by_phone(caller_phone)
    if not customer:
        log.warning("Unknown caller %s — no customer record found", caller_phone)
        customer_name = "valued customer"
        notifications = []
    else:
        customer_name = customer.name
        notifications = await db.get_pending_notifications(customer.id)
        log.info(
            "Customer: %s (id=%d), %d pending notifications",
            customer.name, customer.id, len(notifications),
        )

    # -- Build Gemini Live config with caller context -----------------------
    system_instruction = _build_system_instruction(
        business_name=business_name,
        customer_name=customer_name,
        customer_phone=caller_phone,
        notifications=notifications,
    )

    live_config = {
        "response_modalities": ["AUDIO"],
        "system_instruction": system_instruction,
        "tools": TOOL_DECLARATIONS,
    }

    should_end = asyncio.Event()

    async with gemini.aio.live.connect(
        model=GEMINI_MODEL, config=live_config
    ) as session:

        # --- Caller audio → Gemini ----------------------------------------
        async def stream_to_gemini():
            async for chunk in call.audio_stream():
                await session.send_realtime_input(
                    audio=types.Blob(
                        data=chunk,
                        mime_type=f"audio/pcm;rate={SAMPLE_RATE}",
                    )
                )

        # --- Gemini → Caller (audio + tool calls) -------------------------
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
                        log.info("Tool: %s(%s)", fc.name, args)
                        result = await _dispatch_tool(db, fc.name, args, log)

                        fn_responses.append(
                            types.FunctionResponse(
                                id=fc.id,
                                name=fc.name,
                                response=result,
                            )
                        )

                        if fc.name == "end_call":
                            should_end.set()

                    await session.send_tool_response(function_responses=fn_responses)

        # --- Lifecycle watcher --------------------------------------------
        async def end_watcher():
            await should_end.wait()
            await asyncio.sleep(3)
            await call.disconnect()

        await asyncio.gather(
            stream_to_gemini(),
            receive_from_gemini(),
            end_watcher(),
        )

    log.info("Notification call complete for %s", customer_name)


# ---------------------------------------------------------------------------
# Tool dispatcher (routes Gemini tool calls → DB operations)
# ---------------------------------------------------------------------------


async def _dispatch_tool(
    db: NotificationDB,
    name: str,
    args: dict,
    log: logging.Logger,
) -> dict:
    if name == "acknowledge_notification":
        nid = int(args.get("notification_id", 0))
        ok = await db.mark_acknowledged(nid)
        if ok:
            return {"status": "acknowledged", "notification_id": nid}
        return {"status": "error", "message": f"Notification {nid} not found or already processed."}

    if name == "flag_for_followup":
        nid = int(args.get("notification_id", 0))
        ok = await db.flag_for_followup(nid)
        if ok:
            return {"status": "flagged_for_followup", "notification_id": nid}
        return {"status": "error", "message": f"Notification {nid} not found or already processed."}

    if name == "end_call":
        log.info("end_call invoked — will disconnect shortly")
        return {"status": "ending"}

    log.error("Unknown tool: %s", name)
    return {"error": f"Unknown tool: {name}"}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def main() -> None:
    api_key = GEMINI_API_KEY()
    wss_key = WSS_API_KEY()
    connector = WSS_CONNECTOR_UUID()
    business_name = BUSINESS_NAME()
    db_path = DB_PATH()

    db = NotificationDB(db_path)
    await db.connect()

    gemini = genai.Client(api_key=api_key)

    tf_config = TelcoflowClientConfig.sandbox(
        api_key=wss_key,
        connector_uuid=connector,
        sample_rate=SAMPLE_RATE,
    )

    async with TelcoflowClient(tf_config) as tf_client:

        @tf_client.on(events.INCOMING_CALL)
        async def on_incoming(call: ActiveCall):
            log_id = getattr(call, "call_id", "?")
            logger.info("[%s] Incoming notification call", log_id)
            try:
                await handle_notification_call(call, db, gemini, business_name)
            except Exception:
                logger.exception("[%s] Notification session failed", log_id)

        logger.info(
            "NotificationAgent [%s] is live — waiting for calls …",
            business_name,
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

    await db.close()
    logger.info("Goodbye.")


if __name__ == "__main__":
    asyncio.run(main())
