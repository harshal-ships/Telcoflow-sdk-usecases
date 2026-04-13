"""
Tool declarations, handlers, and phase tracking for the screening agent.

Mirrors the LiveKit AgentTask / TaskGroup pattern but built on Gemini Live
function calling + a local PhaseTracker that monitors progress.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase results (structured data collected at each stage)
# ---------------------------------------------------------------------------


@dataclass
class IntroResult:
    name: str = ""
    intro_notes: str = ""


@dataclass
class ContactResult:
    email: str = ""
    phone: str = ""


@dataclass
class CommuteResult:
    can_commute: bool = False
    commute_method: str = ""


@dataclass
class ExperienceResult:
    years: int = 0
    description: str = ""


@dataclass
class BehavioralResult:
    strengths: str = ""
    weaknesses: str = ""
    work_style: str = ""


@dataclass
class DisqualificationResult:
    reason: str = ""


# ---------------------------------------------------------------------------
# Phase tracker
# ---------------------------------------------------------------------------


class Phase(str, Enum):
    INTRO = "intro"
    CONTACT = "contact"
    COMMUTE = "commute"
    EXPERIENCE = "experience"
    BEHAVIORAL = "behavioral"
    COMPLETE = "complete"
    DISQUALIFIED = "disqualified"


PHASE_ORDER = [
    Phase.INTRO,
    Phase.CONTACT,
    Phase.COMMUTE,
    Phase.EXPERIENCE,
    Phase.BEHAVIORAL,
]


@dataclass
class PhaseTracker:
    """
    Tracks which interview phases have been completed and stores their results.
    Equivalent to LiveKit's TaskGroup but driven by Gemini tool calls.
    """

    intro: IntroResult = field(default_factory=IntroResult)
    contact: ContactResult = field(default_factory=ContactResult)
    commute: CommuteResult = field(default_factory=CommuteResult)
    experience: ExperienceResult = field(default_factory=ExperienceResult)
    behavioral: BehavioralResult = field(default_factory=BehavioralResult)
    disqualification: DisqualificationResult | None = None
    _completed: set[Phase] = field(default_factory=set)

    def mark_complete(self, phase: Phase) -> None:
        self._completed.add(phase)
        logger.info("Phase %s completed (%d/%d)", phase.value, len(self._completed), len(PHASE_ORDER))

    @property
    def current_phase(self) -> Phase:
        if self.disqualification:
            return Phase.DISQUALIFIED
        for p in PHASE_ORDER:
            if p not in self._completed:
                return p
        return Phase.COMPLETE

    @property
    def all_complete(self) -> bool:
        return self.current_phase == Phase.COMPLETE

    def to_dict(self) -> dict[str, Any]:
        base = {
            "candidate_name": self.intro.name,
            "intro_notes": self.intro.intro_notes,
            "email": self.contact.email,
            "phone": self.contact.phone,
            "can_commute": self.commute.can_commute,
            "commute_method": self.commute.commute_method,
            "years_of_experience": self.experience.years,
            "experience_description": self.experience.description,
            "strengths": self.behavioral.strengths,
            "weaknesses": self.behavioral.weaknesses,
            "work_style": self.behavioral.work_style,
        }
        if self.disqualification:
            base["disqualification_reason"] = self.disqualification.reason
        return base


# ---------------------------------------------------------------------------
# Tool declarations (Gemini Live function-calling schema)
# ---------------------------------------------------------------------------

RECORD_INTRO = {
    "name": "record_intro",
    "description": (
        "Record the candidate's name and introduction notes after they introduce themselves. "
        "Call this once you have their full name and a brief summary of their self-introduction."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "name": {"type": "STRING", "description": "The candidate's full name."},
            "intro_notes": {"type": "STRING", "description": "Summary of the candidate's self-introduction."},
        },
        "required": ["name", "intro_notes"],
    },
}

RECORD_CONTACT = {
    "name": "record_contact",
    "description": "Record the candidate's email address and phone number.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "email": {"type": "STRING", "description": "The candidate's email address."},
            "phone": {"type": "STRING", "description": "The candidate's phone number."},
        },
        "required": ["email", "phone"],
    },
}

RECORD_COMMUTE = {
    "name": "record_commute",
    "description": (
        "Record whether the candidate can commute to the office and their transportation method."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "can_commute": {"type": "BOOLEAN", "description": "Whether the candidate can commute to the office."},
            "commute_method": {
                "type": "STRING",
                "description": "Transportation method: driving, bus, subway, cycling, walking, or none.",
            },
        },
        "required": ["can_commute", "commute_method"],
    },
}

RECORD_EXPERIENCE = {
    "name": "record_experience",
    "description": (
        "Record the candidate's years of experience and a description of their work history."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "years_of_experience": {"type": "INTEGER", "description": "Total years of relevant work experience."},
            "experience_description": {
                "type": "STRING",
                "description": "Summary of previous roles, companies, and responsibilities.",
            },
        },
        "required": ["years_of_experience", "experience_description"],
    },
}

RECORD_STRENGTHS = {
    "name": "record_strengths",
    "description": "Record a summary of the candidate's professional strengths.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "strengths": {"type": "STRING", "description": "Summary of the candidate's strengths."},
        },
        "required": ["strengths"],
    },
}

RECORD_WEAKNESSES = {
    "name": "record_weaknesses",
    "description": "Record a summary of the candidate's self-identified weaknesses or areas for growth.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "weaknesses": {"type": "STRING", "description": "Summary of the candidate's weaknesses."},
        },
        "required": ["weaknesses"],
    },
}

RECORD_WORK_STYLE = {
    "name": "record_work_style",
    "description": "Record the candidate's preferred work and communication style.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "work_style": {
                "type": "STRING",
                "description": "The candidate's work style: independent, team_player, or hybrid.",
            },
        },
        "required": ["work_style"],
    },
}

DISQUALIFY = {
    "name": "disqualify",
    "description": (
        "Disqualify the candidate if they refuse to cooperate, provide inappropriate answers, "
        "or clearly do not meet prerequisites. This terminates the interview."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "reason": {"type": "STRING", "description": "Justification for ending the interview."},
        },
        "required": ["reason"],
    },
}

END_SCREENING = {
    "name": "end_screening",
    "description": (
        "End the screening call after all phases are complete and the candidate has been thanked. "
        "Call this as the very last action."
    ),
    "parameters": {"type": "OBJECT", "properties": {}},
}

TOOL_DECLARATIONS = [
    {
        "function_declarations": [
            RECORD_INTRO,
            RECORD_CONTACT,
            RECORD_COMMUTE,
            RECORD_EXPERIENCE,
            RECORD_STRENGTHS,
            RECORD_WEAKNESSES,
            RECORD_WORK_STYLE,
            DISQUALIFY,
            END_SCREENING,
        ]
    }
]


# ---------------------------------------------------------------------------
# Tool dispatcher (routes calls → phase tracker)
# ---------------------------------------------------------------------------


class ToolDispatcher:
    """Routes Gemini tool calls to the PhaseTracker, returning structured results."""

    def __init__(self, tracker: PhaseTracker) -> None:
        self._tracker = tracker

    def dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, f"_handle_{name}", None)
        if not handler:
            logger.error("Unknown tool: %s", name)
            return {"error": f"Unknown tool: {name}"}
        try:
            return handler(args)
        except Exception:
            logger.exception("Tool %s failed", name)
            return {"error": f"Tool {name} failed"}

    def _handle_record_intro(self, args: dict) -> dict:
        self._tracker.intro = IntroResult(
            name=args.get("name", ""),
            intro_notes=args.get("intro_notes", ""),
        )
        self._tracker.mark_complete(Phase.INTRO)
        return {"status": "recorded", "phase": "intro", "next": self._tracker.current_phase.value}

    def _handle_record_contact(self, args: dict) -> dict:
        self._tracker.contact = ContactResult(
            email=args.get("email", ""),
            phone=args.get("phone", ""),
        )
        self._tracker.mark_complete(Phase.CONTACT)
        return {"status": "recorded", "phase": "contact", "next": self._tracker.current_phase.value}

    def _handle_record_commute(self, args: dict) -> dict:
        self._tracker.commute = CommuteResult(
            can_commute=bool(args.get("can_commute", False)),
            commute_method=args.get("commute_method", ""),
        )
        self._tracker.mark_complete(Phase.COMMUTE)
        return {"status": "recorded", "phase": "commute", "next": self._tracker.current_phase.value}

    def _handle_record_experience(self, args: dict) -> dict:
        self._tracker.experience = ExperienceResult(
            years=int(args.get("years_of_experience", 0)),
            description=args.get("experience_description", ""),
        )
        self._tracker.mark_complete(Phase.EXPERIENCE)
        return {"status": "recorded", "phase": "experience", "next": self._tracker.current_phase.value}

    def _handle_record_strengths(self, args: dict) -> dict:
        self._tracker.behavioral.strengths = args.get("strengths", "")
        self._maybe_complete_behavioral()
        return {"status": "recorded", "field": "strengths", "next": self._tracker.current_phase.value}

    def _handle_record_weaknesses(self, args: dict) -> dict:
        self._tracker.behavioral.weaknesses = args.get("weaknesses", "")
        self._maybe_complete_behavioral()
        return {"status": "recorded", "field": "weaknesses", "next": self._tracker.current_phase.value}

    def _handle_record_work_style(self, args: dict) -> dict:
        self._tracker.behavioral.work_style = args.get("work_style", "")
        self._maybe_complete_behavioral()
        return {"status": "recorded", "field": "work_style", "next": self._tracker.current_phase.value}

    def _maybe_complete_behavioral(self) -> None:
        b = self._tracker.behavioral
        if b.strengths and b.weaknesses and b.work_style:
            self._tracker.mark_complete(Phase.BEHAVIORAL)

    def _handle_disqualify(self, args: dict) -> dict:
        reason = args.get("reason", "Unspecified")
        self._tracker.disqualification = DisqualificationResult(reason=reason)
        logger.warning("Candidate disqualified: %s", reason)
        return {"status": "disqualified", "reason": reason}

    def _handle_end_screening(self, _args: dict) -> dict:
        logger.info("end_screening called")
        return {"status": "ending"}
