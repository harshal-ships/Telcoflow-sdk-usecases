"""
AI Receptionist with Database Lookup
=====================================
A Telcoflow + Gemini Live voice agent that greets callers by name (if known),
surfaces their open support tickets, and either handles the query via AI or
routes to a human department.

State flows:
  Human route:   PENDING → ANSWERED → CONNECTED → DISCONNECTED
  AI handled:    PENDING → ANSWERED → DISCONNECTED
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

from database import ReceptionistDB

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-28s  %(levelname)-5s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("receptionist_agent")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
WSS_API_KEY = os.environ["WSS_API_KEY"]
WSS_CONNECTOR_UUID = os.environ["WSS_CONNECTOR_UUID"]
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "XanhSM")
DB_PATH = os.getenv("DB_PATH", "receptionist.db")
GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025"
)
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "24000"))

# ---------------------------------------------------------------------------
# System instruction template
# ---------------------------------------------------------------------------

SYSTEM_INSTRUCTION = """
You are the AI receptionist for **{business_name}**.

{caller_context}

## BEHAVIOUR
- If you know the caller's name, greet them by name immediately.
- If they have open tickets, mention them briefly ("I see you have an open ticket
  about …") and ask if that's what they're calling about.
- If the caller is unknown, greet them warmly and ask how you can help.
- Keep responses concise and professional — this is a phone call.

## TOOLS
You have two tools:

1. **route_to_department** — Use this when the caller needs to speak with a human
   (billing, support, sales, etc.). Specify the department. After calling this tool,
   say something like "Let me connect you now" and wait.
2. **end_call** — Use this when you have fully handled the caller's request via AI
   and they are ready to hang up. Thank them and say goodbye before calling this.

## RULES
- NEVER make up information. If you don't know, offer to route to a human.
- If the caller is frustrated, empathise briefly and offer to connect them.
- Always call exactly one of the two tools to end the conversation.
""".strip()

# ---------------------------------------------------------------------------
# Gemini Live tool declarations
# ---------------------------------------------------------------------------

ROUTE_TO_DEPARTMENT = {
    "name": "route_to_department",
    "description": (
        "Route the caller to a human department. Use when the caller needs "
        "human assistance that the AI cannot provide."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "department": {
                "type": "STRING",
                "description": "Target department: support, billing, sales, or general.",
            },
            "reason": {
                "type": "STRING",
                "description": "Brief reason for the transfer.",
            },
        },
        "required": ["department", "reason"],
    },
}

END_CALL = {
    "name": "end_call",
    "description": (
        "End the call after the AI has fully resolved the caller's query. "
        "Call this after saying goodbye."
    ),
    "parameters": {"type": "OBJECT", "properties": {}},
}

TOOL_DECLARATIONS = [{"function_declarations": [ROUTE_TO_DEPARTMENT, END_CALL]}]

# ---------------------------------------------------------------------------
# Caller context builder
# ---------------------------------------------------------------------------


async def build_caller_context(db: ReceptionistDB, phone: str) -> tuple[str, bool]:
    """Look up caller in the DB and build a context string for the system prompt.

    Returns (context_string, is_known_caller).
    """
    customer = await db.get_customer_by_phone(phone)

    if not customer:
        await db.create_lead(phone)
        return (
            "The caller is NOT in our database. This is a new caller — "
            "no name or history available. Greet them warmly and ask for their name.",
            False,
        )

    lines = [
        f"Caller: **{customer.name}** ({customer.email})",
        f"Company: {customer.company}",
    ]

    tickets = await db.get_open_tickets(customer.id)
    if tickets:
        lines.append(f"Open tickets ({len(tickets)}):")
        for t in tickets:
            lines.append(f"  • #{t.id}: {t.subject}")
    else:
        lines.append("No open tickets.")

    return "\n".join(lines), True


# ---------------------------------------------------------------------------
# Per-call handler
# ---------------------------------------------------------------------------


async def handle_call(
    call: ActiveCall,
    gemini: genai.Client,
    db: ReceptionistDB,
) -> None:
    call_id = getattr(call, "call_id", "unknown")
    caller_phone = getattr(call, "caller_number", None) or "unknown"
    log = logger.getChild(call_id[:12])

    @call.on(events.CALL_TERMINATED)
    def on_terminated():
        log.info("CALL_TERMINATED")

    await call.answer()
    log.info("Call answered from %s", caller_phone)

    caller_context, is_known = await build_caller_context(db, caller_phone)
    log.info("Caller %s: %s", "known" if is_known else "new lead", caller_phone)

    system_instruction = SYSTEM_INSTRUCTION.format(
        business_name=BUSINESS_NAME,
        caller_context=caller_context,
    )

    live_config = {
        "response_modalities": ["AUDIO"],
        "system_instruction": system_instruction,
        "tools": TOOL_DECLARATIONS,
    }

    should_route = asyncio.Event()
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

                        if fc.name == "route_to_department":
                            dept = args.get("department", "general")
                            reason = args.get("reason", "")
                            log.info("Routing to %s — %s", dept, reason)
                            fn_responses.append(
                                types.FunctionResponse(
                                    id=fc.id,
                                    name=fc.name,
                                    response={
                                        "status": "connecting",
                                        "department": dept,
                                    },
                                )
                            )
                            should_route.set()

                        elif fc.name == "end_call":
                            fn_responses.append(
                                types.FunctionResponse(
                                    id=fc.id,
                                    name=fc.name,
                                    response={"status": "ending"},
                                )
                            )
                            should_end.set()

                        else:
                            fn_responses.append(
                                types.FunctionResponse(
                                    id=fc.id,
                                    name=fc.name,
                                    response={"error": f"Unknown tool: {fc.name}"},
                                )
                            )

                    await session.send_tool_response(function_responses=fn_responses)

        # --- Lifecycle watcher ---------------------------------------------
        async def lifecycle_watcher():
            route_task = asyncio.create_task(should_route.wait())
            end_task = asyncio.create_task(should_end.wait())
            done, pending = await asyncio.wait(
                [route_task, end_task], return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()

            await asyncio.sleep(2)

            if should_route.is_set():
                log.info("Connecting caller to human agent")
                await call.connect()
                await call.close()
            else:
                log.info("AI handled — disconnecting")
                await call.disconnect()

        await asyncio.gather(
            stream_to_gemini(),
            receive_from_gemini(),
            lifecycle_watcher(),
        )

    log.info("Receptionist session complete for %s", caller_phone)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def main() -> None:
    db = ReceptionistDB(DB_PATH)
    await db.connect()

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
            logger.info("[%s] Incoming call", log_id)
            try:
                await handle_call(call, gemini, db)
            except Exception:
                logger.exception("[%s] Receptionist session failed", log_id)

        logger.info(
            "ReceptionistAgent [%s] is live — waiting for calls …",
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

    await db.close()
    logger.info("Goodbye.")


if __name__ == "__main__":
    asyncio.run(main())
