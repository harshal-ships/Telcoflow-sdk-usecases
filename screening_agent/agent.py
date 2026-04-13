"""
Candidate Screening Agent — Multi-phase Voice Interview
========================================================
A Telcoflow + Gemini Live agent that conducts a structured phone screening
interview, collecting data across 5 phases via function calling, then
evaluating the candidate post-call.

Phases:
  1. Introduction   → record_intro
  2. Contact Info   → record_contact
  3. Commute        → record_commute
  4. Experience     → record_experience
  5. Behavioral     → record_strengths / record_weaknesses / record_work_style

Cross-cutting:
  • disqualify      → early termination
  • end_screening   → normal termination
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
from tools import TOOL_DECLARATIONS, PhaseTracker, ToolDispatcher
from evaluation import evaluate_candidate, write_results_csv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-28s  %(levelname)-5s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("screening_agent")

# ---------------------------------------------------------------------------
# System instruction (equivalent to LiveKit Agent + AgentTask instructions)
# ---------------------------------------------------------------------------

SYSTEM_INSTRUCTION = """
You are **Alex**, a professional phone screener for **{company}**.
You are conducting a structured screening interview for the position of **{position}**.
You are warm, concise, and conversational — never robotic. Keep responses short for a phone call.

## INTERVIEW STRUCTURE — follow these phases in order:

### Phase 1: Introduction
- Introduce yourself: "Hi, this is Alex from {company}. Thank you for taking the time to speak with me today."
- Ask the candidate to introduce themselves.
- Once they do, call `record_intro` with their name and a summary of their intro.

### Phase 2: Contact Information
- Ask for their email address and a good phone number to reach them.
- Confirm both, then call `record_contact`.

### Phase 3: Commute Flexibility
- The role requires coming into the office three days a week.
- Ask if they can commute and what transportation method they'd use.
- Call `record_commute` with the results.

### Phase 4: Work Experience
- Ask about their professional background: how many years of experience they have
  and a brief overview of their roles and companies.
- Call `record_experience` once you have a clear picture.

### Phase 5: Behavioral Assessment
Gather these three things through natural conversation (not as a checklist):
  a) Their professional strengths → call `record_strengths`
  b) Their weaknesses or areas for growth → call `record_weaknesses`
  c) Their preferred work style (independent, team player, or hybrid) → call `record_work_style`
You may collect these in any order. Each has its own tool — call each one as you collect it.

### Phase 6: Wrap-up
After ALL phases are complete (you'll know because each tool returns a "next" field —
when it says "complete", you're done):
- Thank the candidate for their time.
- Let them know they'll hear back within 3 business days.
- Call `end_screening` to terminate the call.

## DISQUALIFICATION
If at any point the candidate:
- Refuses to cooperate or answer questions
- Provides clearly inappropriate or offensive responses
- Reveals they fundamentally cannot meet the role requirements

Then call `disqualify` with a reason, inform the candidate respectfully that
the interview is ending, and call `end_screening`.

## RULES
- Move through phases sequentially. Do NOT skip ahead.
- Call each recording tool exactly once with confirmed data.
- Use a natural conversational tone. Avoid listing questions with bullet points.
- Do NOT fabricate answers. Only record what the candidate actually says.
- If the candidate goes off-topic, gently redirect.
""".strip()


def _build_system_instruction(cfg: AppConfig) -> str:
    return SYSTEM_INSTRUCTION.format(
        company=cfg.screening.company,
        position=cfg.screening.position,
    )


# ---------------------------------------------------------------------------
# Per-call screening session
# ---------------------------------------------------------------------------


async def handle_screening_call(
    call: ActiveCall,
    cfg: AppConfig,
    gemini: genai.Client,
) -> None:
    call_id = getattr(call, "call_id", "unknown")
    caller_phone = getattr(call, "caller_number", None) or "unknown"
    log = logger.getChild(call_id[:12])

    @call.on(events.CALL_TERMINATED)
    def on_terminated():
        log.info("CALL_TERMINATED")

    await call.answer()
    log.info("Screening call answered from %s", caller_phone)

    tracker = PhaseTracker()
    dispatcher = ToolDispatcher(tracker)

    system_instruction = _build_system_instruction(cfg)
    live_config = {
        "response_modalities": ["AUDIO"],
        "system_instruction": system_instruction,
        "tools": TOOL_DECLARATIONS,
    }

    should_end = asyncio.Event()

    async with gemini.aio.live.connect(
        model=cfg.gemini.live_model, config=live_config
    ) as session:

        # --- Caller audio → Gemini ----------------------------------------
        async def stream_to_gemini():
            async for chunk in call.audio_stream():
                await session.send_realtime_input(
                    audio=types.Blob(
                        data=chunk,
                        mime_type=f"audio/pcm;rate={cfg.gemini.sample_rate}",
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
                        result = dispatcher.dispatch(fc.name, args)

                        fn_responses.append(
                            types.FunctionResponse(
                                id=fc.id,
                                name=fc.name,
                                response=result,
                            )
                        )

                        if fc.name == "end_screening":
                            should_end.set()
                        elif fc.name == "disqualify":
                            should_end.set()

                    await session.send_tool_response(function_responses=fn_responses)

        # --- Lifecycle watcher ---------------------------------------------
        async def end_watcher():
            await should_end.wait()
            await asyncio.sleep(3)
            await call.disconnect()

        await asyncio.gather(
            stream_to_gemini(),
            receive_from_gemini(),
            end_watcher(),
        )

    # -- Post-call: evaluate + write results --------------------------------
    log.info("Call ended — running post-call evaluation")

    if not tracker.contact.phone:
        tracker.contact.phone = caller_phone

    evaluation = await evaluate_candidate(
        gemini_client=gemini,
        text_model=cfg.gemini.text_model,
        tracker=tracker,
        company=cfg.screening.company,
        position=cfg.screening.position,
    )
    log.info("Evaluation:\n%s", evaluation)

    await write_results_csv(
        csv_path=cfg.screening.results_csv,
        tracker=tracker,
        evaluation=evaluation,
    )

    log.info(
        "Screening complete for %s (%s)",
        tracker.intro.name or "Unknown",
        "DISQUALIFIED" if tracker.disqualification else "COMPLETED",
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def main() -> None:
    cfg = AppConfig.from_env()
    gemini = genai.Client(api_key=cfg.gemini.api_key)

    tf_config = TelcoflowClientConfig.sandbox(
        api_key=cfg.telcoflow.api_key,
        connector_uuid=cfg.telcoflow.connector_uuid,
        sample_rate=cfg.telcoflow.sample_rate,
    )

    async with TelcoflowClient(tf_config) as tf_client:

        @tf_client.on(events.INCOMING_CALL)
        async def on_incoming(call: ActiveCall):
            log_id = getattr(call, "call_id", "?")
            logger.info("[%s] Incoming screening call", log_id)
            try:
                await handle_screening_call(call, cfg, gemini)
            except Exception:
                logger.exception("[%s] Screening session failed", log_id)

        logger.info(
            "ScreeningAgent [%s — %s] is live — waiting for calls …",
            cfg.screening.company,
            cfg.screening.position,
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
            logger.info("Shutdown signal received")
            run_task.cancel()

    logger.info("Goodbye.")


if __name__ == "__main__":
    asyncio.run(main())
