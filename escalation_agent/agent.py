"""
AI Agent with Human Escalation — Voice Support Agent
=====================================================
A Telcoflow + Gemini Live voice agent that handles customer support calls.
The AI resolves issues when possible; when it can't (or the caller requests
a human), it saves conversation context to the database and hands the call
off to a live agent via call.connect() + call.close().

State flows:
  AI handled:  PENDING → ANSWERED → DISCONNECTED
  Escalated:   PENDING → ANSWERED → CONNECTED → DISCONNECTED
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

from database import EscalationDB

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-28s  %(levelname)-5s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("escalation_agent")


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
DB_PATH = os.getenv("DB_PATH", "escalation.db")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025")
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "24000"))


# ---------------------------------------------------------------------------
# System instruction
# ---------------------------------------------------------------------------

SYSTEM_INSTRUCTION = """
You are a customer support agent for **{business_name}**.

## YOUR ROLE
- Help the caller with their questions and resolve their issues.
- Be warm, professional, and concise — this is a phone call, not a chat.
- Listen carefully and ask clarifying questions when needed.

## ESCALATION
If any of the following are true, call the `escalate_to_human` tool:
- You cannot answer the caller's question or resolve their issue.
- The caller is frustrated, upset, or explicitly asks for a human agent.
- The issue requires account access, billing changes, or actions you cannot perform.
- You have attempted to help but the caller is not satisfied.

When calling `escalate_to_human`, provide:
- `reason`: A brief explanation of why you're escalating.
- `conversation_summary`: A concise summary of the conversation so far,
  including the caller's issue, what you've discussed, and any relevant details.
  This summary will be passed to the human agent so the caller doesn't have to repeat themselves.

Before transferring, let the caller know:
"I'm going to connect you with a team member who can help you further.
I'll pass along our conversation so you won't need to repeat anything."

## RESOLUTION
If you fully resolve the caller's issue:
- Confirm the resolution with the caller.
- Ask if there's anything else you can help with.
- When they're done, say goodbye and call the `end_call` tool.

## RULES
- NEVER make up information. If you don't know, escalate.
- NEVER promise specific timelines or outcomes you can't guarantee.
- Keep responses short and natural for a phone conversation.
""".strip()


# ---------------------------------------------------------------------------
# Tool declarations
# ---------------------------------------------------------------------------

ESCALATE_TO_HUMAN = {
    "name": "escalate_to_human",
    "description": (
        "Transfer the call to a human agent. Call this when you cannot resolve "
        "the caller's issue, they are frustrated, or they request a human. "
        "Provide a reason and a summary of the conversation so far."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "reason": {
                "type": "STRING",
                "description": "Why the call is being escalated.",
            },
            "conversation_summary": {
                "type": "STRING",
                "description": (
                    "Summary of the conversation so far, including the caller's issue "
                    "and any relevant details discussed."
                ),
            },
        },
        "required": ["reason", "conversation_summary"],
    },
}

END_CALL = {
    "name": "end_call",
    "description": (
        "End the call after the caller's issue has been fully resolved. "
        "Call this as the very last action after saying goodbye."
    ),
    "parameters": {"type": "OBJECT", "properties": {}},
}

TOOLS = [{"function_declarations": [ESCALATE_TO_HUMAN, END_CALL]}]


# ---------------------------------------------------------------------------
# Per-call handler
# ---------------------------------------------------------------------------


async def handle_support_call(
    call: ActiveCall,
    gemini: genai.Client,
    db: EscalationDB,
) -> None:
    call_id = getattr(call, "call_id", "unknown")
    caller_phone = getattr(call, "caller_number", None) or "unknown"
    log = logger.getChild(call_id[:12])

    @call.on(events.CALL_TERMINATED)
    def on_terminated():
        log.info("CALL_TERMINATED")

    await call.answer()
    log.info("Support call answered from %s", caller_phone)

    system_instruction = SYSTEM_INSTRUCTION.format(business_name=BUSINESS_NAME)
    live_config = {
        "response_modalities": ["AUDIO"],
        "system_instruction": system_instruction,
        "tools": TOOLS,
    }

    should_escalate = asyncio.Event()
    should_end = asyncio.Event()
    escalation_data: dict = {}

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

                        if fc.name == "escalate_to_human":
                            escalation_data["reason"] = args.get("reason", "")
                            escalation_data["summary"] = args.get("conversation_summary", "")
                            fn_responses.append(
                                types.FunctionResponse(
                                    id=fc.id,
                                    name=fc.name,
                                    response={"status": "transferring"},
                                )
                            )
                            should_escalate.set()

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
            escalate_task = asyncio.create_task(should_escalate.wait())
            end_task = asyncio.create_task(should_end.wait())

            done, pending = await asyncio.wait(
                [escalate_task, end_task], return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()

            if should_escalate.is_set():
                log.info("Escalating to human — reason: %s", escalation_data.get("reason"))
                await asyncio.sleep(3)

                await db.save_call_context(
                    call_id=call_id,
                    caller_number=caller_phone,
                    summary={"conversation_summary": escalation_data.get("summary", "")},
                    escalation_reason=escalation_data.get("reason", ""),
                    status="escalated",
                )
                await call.connect()
                await call.close()

            elif should_end.is_set():
                log.info("Call resolved by AI — ending")
                await asyncio.sleep(2)

                await db.save_call_context(
                    call_id=call_id,
                    caller_number=caller_phone,
                    status="ai_handled",
                )
                await call.disconnect()

        await asyncio.gather(
            stream_to_gemini(),
            receive_from_gemini(),
            lifecycle_watcher(),
        )

    log.info("Session complete (call_id=%s, caller=%s)", call_id, caller_phone)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def main() -> None:
    db = EscalationDB(DB_PATH)
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
            logger.info("[%s] Incoming support call", log_id)
            try:
                await handle_support_call(call, gemini, db)
            except Exception:
                logger.exception("[%s] Support session failed", log_id)

        logger.info(
            "EscalationAgent [%s] is live — waiting for calls …",
            BUSINESS_NAME,
        )

        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

        run_task = asyncio.create_task(tf_client.run_forever())
        stop_task = asyncio.create_task(stop.wait())
        await asyncio.wait(
            [run_task, stop_task], return_when=asyncio.FIRST_COMPLETED,
        )

        if stop.is_set():
            logger.info("Shutdown signal received — cleaning up …")
            run_task.cancel()

    await db.close()
    logger.info("Goodbye.")


if __name__ == "__main__":
    asyncio.run(main())
