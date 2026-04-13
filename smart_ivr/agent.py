"""
Smart IVR Replacement — Natural Voice Menu Navigator
=====================================================
Replaces traditional "press 1 for sales" IVR trees with a single natural
voice conversation powered by Telcoflow + Gemini Live.

The caller says what they need; the AI classifies intent via function calling
and either handles it directly or routes to the right human department.

Intents handled by AI:
  • Account status lookup
  • Order tracking
  • Complaint logging
  • Callback scheduling

Intents routed to human:
  • Sales inquiries
  • Technical support
  • Billing disputes

Every interaction is logged for analytics.

State flows:
  AI-handled:     PENDING → ANSWERED → DISCONNECTED
  Human-routed:   PENDING → ANSWERED → CONNECTED → DISCONNECTED
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types
from telcoflow_sdk import TelcoflowClient, TelcoflowClientConfig, ActiveCall
import telcoflow_sdk.events as events

from database import IVRDB

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-24s  %(levelname)-5s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("smart_ivr")

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025")
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "24000"))
COMPANY = os.getenv("BUSINESS_NAME", "YOUR_COMPANY_NAME")
DB_PATH = os.getenv("DB_PATH", "ivr.db")

# ---------------------------------------------------------------------------
# System instruction
# ---------------------------------------------------------------------------

SYSTEM_INSTRUCTION = """
You are the voice assistant for **{company}**. You replace a traditional phone menu.
When a caller reaches you, there is NO menu. Just a warm greeting and a question:
"Hi, welcome to {company}! How can I help you today?"

Your job is to understand what the caller needs and take the right action using your tools.

## WHAT YOU CAN DO DIRECTLY (no human needed):

1. **Account status** — Caller asks about their account, balance, or plan.
   → Call `check_account_status` with their phone number. Read back their name, plan, and balance.

2. **Order tracking** — Caller asks "where's my order" or similar.
   → Call `check_orders` with their phone number. Report the status of each order.
   → If they have a specific order ID, call `check_order_by_id`.

3. **File a complaint** — Caller wants to complain about service, product, billing, etc.
   → Gather the category and a brief description.
   → Call `log_complaint`. Confirm the reference number.

4. **Schedule a callback** — Caller can't wait or wants to be called back.
   → Ask for their preferred time and the reason.
   → Call `schedule_callback`. Confirm the scheduled time.

## WHAT REQUIRES A HUMAN (route the call):

5. **Sales inquiry** — Caller wants to buy something, upgrade plan, or learn about products.
   → Say "Let me connect you with our sales team."
   → Call `route_to_department` with department="sales".

6. **Technical support** — Caller has a technical problem you can't troubleshoot.
   → Say "Let me connect you with technical support."
   → Call `route_to_department` with department="technical_support".

7. **Billing dispute** — Caller disputes a charge or wants a refund.
   → Say "Let me connect you with our billing team."
   → Call `route_to_department` with department="billing".

## ENDING THE CALL:

8. After you've handled the request (or if the caller says they're done), ask if there's
   anything else. If not, say goodbye and call `end_call` with a summary of what was handled.

## RULES:
- Always greet warmly. Never read out a menu.
- If you're unsure which department, ask a clarifying question.
- If the caller needs multiple things, handle them one at a time.
- Keep responses conversational and brief — this is a phone call.
- If account lookup returns no customer, tell them you couldn't find their account
  and offer to connect them with support or schedule a callback.
""".strip().format(company=COMPANY)

# ---------------------------------------------------------------------------
# Tool declarations
# ---------------------------------------------------------------------------

TOOLS = [{"function_declarations": [
    {
        "name": "check_account_status",
        "description": "Look up a customer's account by phone number. Returns name, plan, and balance.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "phone_number": {"type": "STRING", "description": "The customer's phone number."},
            },
            "required": ["phone_number"],
        },
    },
    {
        "name": "check_orders",
        "description": "Retrieve all orders for a customer by their phone number.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "phone_number": {"type": "STRING", "description": "The customer's phone number."},
            },
            "required": ["phone_number"],
        },
    },
    {
        "name": "check_order_by_id",
        "description": "Look up a specific order by its order ID.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "order_id": {"type": "STRING", "description": "The order ID (e.g. ord-1001)."},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "log_complaint",
        "description": "File a complaint for the caller. Requires category and description.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "phone_number": {"type": "STRING", "description": "Caller's phone number."},
                "category": {
                    "type": "STRING",
                    "description": "Complaint category: service, product, billing, delivery, or other.",
                },
                "description": {"type": "STRING", "description": "Brief description of the complaint."},
            },
            "required": ["phone_number", "category", "description"],
        },
    },
    {
        "name": "schedule_callback",
        "description": "Schedule a callback for the caller at their preferred time.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "phone_number": {"type": "STRING", "description": "Phone number to call back."},
                "preferred_time": {"type": "STRING", "description": "When to call back (e.g. 'tomorrow at 2 PM')."},
                "reason": {"type": "STRING", "description": "Brief reason for the callback."},
            },
            "required": ["phone_number", "preferred_time", "reason"],
        },
    },
    {
        "name": "route_to_department",
        "description": (
            "Transfer the caller to a human department. Use when the request "
            "requires sales, technical support, or billing."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "department": {
                    "type": "STRING",
                    "description": "Target department: sales, technical_support, or billing.",
                },
                "context": {"type": "STRING", "description": "Brief summary of the caller's need for the human agent."},
            },
            "required": ["department", "context"],
        },
    },
    {
        "name": "end_call",
        "description": "End the call after the caller's requests have been handled.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "summary": {"type": "STRING", "description": "Brief summary of what was handled during the call."},
            },
            "required": ["summary"],
        },
    },
]}]

# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


async def dispatch_tool(name: str, args: dict, db: IVRDB, caller_phone: str) -> dict:
    try:
        if name == "check_account_status":
            return await _handle_account(args, db, caller_phone)
        elif name == "check_orders":
            return await _handle_orders(args, db, caller_phone)
        elif name == "check_order_by_id":
            return await _handle_order_by_id(args, db)
        elif name == "log_complaint":
            return await _handle_complaint(args, db, caller_phone)
        elif name == "schedule_callback":
            return await _handle_callback(args, db, caller_phone)
        elif name == "route_to_department":
            return _handle_route(args)
        elif name == "end_call":
            return _handle_end(args)
        else:
            return {"error": f"Unknown tool: {name}"}
    except Exception:
        logger.exception("Tool %s failed", name)
        return {"error": f"{name} failed"}


async def _handle_account(args: dict, db: IVRDB, caller_phone: str) -> dict:
    phone = args.get("phone_number", caller_phone)
    customer = await db.get_customer_by_phone(phone)
    if not customer:
        return {"found": False, "message": "No account found for this phone number."}
    return {
        "found": True,
        "name": customer.name,
        "plan": customer.plan,
        "balance": f"${customer.balance:.2f}",
        "email": customer.email,
    }


async def _handle_orders(args: dict, db: IVRDB, caller_phone: str) -> dict:
    phone = args.get("phone_number", caller_phone)
    customer = await db.get_customer_by_phone(phone)
    if not customer:
        return {"found": False, "orders": [], "message": "No account found."}
    orders = await db.get_orders(customer.id)
    if not orders:
        return {"found": True, "orders": [], "message": f"{customer.name} has no orders."}
    return {
        "found": True,
        "customer_name": customer.name,
        "orders": [
            {
                "id": o.id,
                "item": o.description,
                "status": o.status,
                "delivery": o.estimated_delivery,
            }
            for o in orders
        ],
    }


async def _handle_order_by_id(args: dict, db: IVRDB) -> dict:
    order_id = args.get("order_id", "")
    order = await db.get_order_by_id(order_id)
    if not order:
        return {"found": False, "message": f"No order found with ID {order_id}."}
    return {
        "found": True,
        "id": order.id,
        "item": order.description,
        "status": order.status,
        "delivery": order.estimated_delivery,
    }


async def _handle_complaint(args: dict, db: IVRDB, caller_phone: str) -> dict:
    phone = args.get("phone_number", caller_phone)
    customer = await db.get_customer_by_phone(phone)
    cust_id = customer.id if customer else ""
    complaint = await db.log_complaint(
        customer_id=cust_id,
        category=args.get("category", "other"),
        description=args.get("description", ""),
    )
    return {
        "status": "filed",
        "reference": complaint.id,
        "message": f"Complaint {complaint.id} has been filed. Our team will review it within 24 hours.",
    }


async def _handle_callback(args: dict, db: IVRDB, caller_phone: str) -> dict:
    phone = args.get("phone_number", caller_phone)
    customer = await db.get_customer_by_phone(phone)
    callback = await db.schedule_callback(
        phone=phone,
        preferred_time=args.get("preferred_time", ""),
        reason=args.get("reason", ""),
        customer_id=customer.id if customer else "",
    )
    return {
        "status": "scheduled",
        "reference": callback.id,
        "time": callback.preferred_time,
        "message": f"Callback scheduled for {callback.preferred_time}.",
    }


def _handle_route(args: dict) -> dict:
    dept = args.get("department", "support")
    context = args.get("context", "")
    logger.info("Routing to %s — context: %s", dept, context)
    return {"status": "routing", "department": dept, "context": context}


def _handle_end(args: dict) -> dict:
    summary = args.get("summary", "")
    logger.info("Call ended — summary: %s", summary)
    return {"status": "ending", "summary": summary}


# ---------------------------------------------------------------------------
# Per-call session
# ---------------------------------------------------------------------------


async def handle_ivr_call(call: ActiveCall, db: IVRDB, gemini: genai.Client) -> None:
    call_id = getattr(call, "call_id", "unknown")
    caller_phone = getattr(call, "caller_number", None) or "unknown"
    log = logger.getChild(call_id[:12])
    call_start = time.monotonic()
    resolved_intent = "unknown"

    @call.on(events.CALL_TERMINATED)
    def on_terminated():
        log.info("CALL_TERMINATED")

    await call.answer()
    log.info("IVR call from %s", caller_phone)

    live_config = {
        "response_modalities": ["AUDIO"],
        "system_instruction": SYSTEM_INSTRUCTION,
        "tools": TOOLS,
    }

    should_end = asyncio.Event()
    should_route = asyncio.Event()
    route_dept = ""

    async with gemini.aio.live.connect(model=MODEL, config=live_config) as session:

        async def stream_to_gemini():
            async for chunk in call.audio_stream():
                await session.send_realtime_input(
                    audio=types.Blob(
                        data=chunk, mime_type=f"audio/pcm;rate={SAMPLE_RATE}"
                    )
                )

        async def receive_from_gemini():
            nonlocal resolved_intent, route_dept

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

                        result = await dispatch_tool(fc.name, args, db, caller_phone)
                        fn_responses.append(
                            types.FunctionResponse(
                                id=fc.id, name=fc.name, response=result,
                            )
                        )

                        if fc.name == "end_call":
                            resolved_intent = args.get("summary", "handled")
                            should_end.set()
                        elif fc.name == "route_to_department":
                            resolved_intent = f"routed:{args.get('department', 'unknown')}"
                            route_dept = args.get("department", "")
                            should_route.set()
                        elif fc.name not in ("end_call", "route_to_department"):
                            resolved_intent = fc.name

                    await session.send_tool_response(function_responses=fn_responses)

        async def lifecycle_watcher():
            done = asyncio.Event()

            async def _wait_end():
                await should_end.wait()
                done.set()

            async def _wait_route():
                await should_route.wait()
                done.set()

            t1 = asyncio.create_task(_wait_end())
            t2 = asyncio.create_task(_wait_route())
            await done.wait()
            t1.cancel()
            t2.cancel()

            await asyncio.sleep(2)

            if should_route.is_set():
                log.info("Routing caller to %s", route_dept)
                try:
                    await call.connect()
                    await call.close()
                except Exception:
                    log.exception("Route failed — disconnecting")
                    await call.disconnect()
            else:
                log.info("Ending call")
                await call.disconnect()

        await asyncio.gather(
            stream_to_gemini(),
            receive_from_gemini(),
            lifecycle_watcher(),
        )

    duration = time.monotonic() - call_start
    outcome = "routed" if should_route.is_set() else "ai_handled"
    await db.log_call(
        caller_number=caller_phone,
        intent=resolved_intent,
        outcome=outcome,
        duration_seconds=round(duration, 1),
    )
    log.info("Call logged — intent=%s outcome=%s duration=%.1fs", resolved_intent, outcome, duration)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def main() -> None:
    db = IVRDB(DB_PATH)
    await db.connect()

    gemini = genai.Client(api_key=GEMINI_API_KEY)

    tf_config = TelcoflowClientConfig.sandbox(
        api_key=os.environ["WSS_API_KEY"],
        connector_uuid=os.environ["WSS_CONNECTOR_UUID"],
        sample_rate=SAMPLE_RATE,
    )

    async with TelcoflowClient(tf_config) as tf_client:

        @tf_client.on(events.INCOMING_CALL)
        async def on_incoming(call: ActiveCall):
            log_id = getattr(call, "call_id", "?")
            logger.info("[%s] Incoming call", log_id)
            try:
                await handle_ivr_call(call, db, gemini)
            except Exception:
                logger.exception("[%s] IVR session failed", log_id)

        logger.info("Smart IVR [%s] is live — no menus, just talk — waiting for calls …", COMPANY)

        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

        run_task = asyncio.create_task(tf_client.run_forever())
        stop_task = asyncio.create_task(stop.wait())
        await asyncio.wait([run_task, stop_task], return_when=asyncio.FIRST_COMPLETED)

        if stop.is_set():
            logger.info("Shutdown signal received")
            run_task.cancel()

    await db.close()
    logger.info("Goodbye.")


if __name__ == "__main__":
    asyncio.run(main())
