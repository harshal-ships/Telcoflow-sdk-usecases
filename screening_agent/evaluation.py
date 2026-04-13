"""
Post-interview candidate evaluation using the Gemini text API.

After the voice call ends, we send all collected structured data to Gemini
for a written evaluation — no audio involved, just a standard generate call.
"""

from __future__ import annotations

import csv
import logging
import os
from pathlib import Path

from google import genai

from tools import PhaseTracker

logger = logging.getLogger(__name__)

EVALUATION_PROMPT = """
You are a hiring evaluator for {company}, assessing a candidate for the role of **{position}**.

Below is the structured data collected during a phone screening interview.
Evaluate whether this candidate is a good fit based on their profile, communication quality,
and the substance of their answers.

Be concise, firm, and professional. Give a clear RECOMMEND / DO NOT RECOMMEND / NEEDS REVIEW
verdict at the end.

## Candidate Data
{candidate_data}
""".strip()


async def evaluate_candidate(
    gemini_client: genai.Client,
    text_model: str,
    tracker: PhaseTracker,
    company: str,
    position: str,
) -> str:
    """Run a text-based evaluation of the candidate using their collected data."""

    data = tracker.to_dict()
    data_str = "\n".join(f"- **{k.replace('_', ' ').title()}**: {v}" for k, v in data.items() if v)

    if tracker.disqualification:
        data_str += f"\n- **DISQUALIFIED**: {tracker.disqualification.reason}"

    prompt = EVALUATION_PROMPT.format(
        company=company,
        position=position,
        candidate_data=data_str,
    )

    try:
        response = await gemini_client.aio.models.generate_content(
            model=text_model,
            contents=prompt,
        )
        evaluation = response.text.strip() if response.text else "Evaluation unavailable."
        logger.info("Evaluation complete (%d chars)", len(evaluation))
        return evaluation
    except Exception:
        logger.exception("Evaluation failed")
        return "Evaluation could not be generated."


async def write_results_csv(
    csv_path: str,
    tracker: PhaseTracker,
    evaluation: str,
) -> None:
    """Append the screening results as a single row to a CSV file."""

    data = tracker.to_dict()
    data["evaluation"] = evaluation

    file_exists = Path(csv_path).exists()

    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=data.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(data)

    logger.info("Results written to %s", csv_path)
