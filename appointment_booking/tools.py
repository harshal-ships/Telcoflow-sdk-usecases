"""
Gemini Live function-calling declarations and their async handlers.

Each tool is:
  1. Declared as a dict in the Gemini schema format (TOOLS list).
  2. Backed by an async handler that talks to real services.
"""

from __future__ import annotations

import logging
from typing import Any

from config import BusinessConfig
from database import Database
from sms_service import SMSService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Declarations (passed to the Gemini Live session config)
# ---------------------------------------------------------------------------

CHECK_AVAILABILITY = {
    "name": "check_availability",
    "description": (
        "Queries the calendar for available appointment slots on a given date "
        "for a specific service. Returns a list of available time slots."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "date": {
                "type": "STRING",
                "description": "The date to check in YYYY-MM-DD format.",
            },
            "service_type": {
                "type": "STRING",
                "description": "The type of service requested.",
            },
        },
        "required": ["date", "service_type"],
    },
}

BOOK_APPOINTMENT = {
    "name": "book_appointment",
    "description": (
        "Creates a confirmed appointment booking. Call only after the caller "
        "has confirmed the date, time slot, and service."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "customer_name": {
                "type": "STRING",
                "description": "Full name of the customer.",
            },
            "phone_number": {
                "type": "STRING",
                "description": "Customer's phone number.",
            },
            "service_type": {
                "type": "STRING",
                "description": "The service being booked.",
            },
            "date": {
                "type": "STRING",
                "description": "Appointment date (YYYY-MM-DD).",
            },
            "time_slot": {
                "type": "STRING",
                "description": "Appointment time (HH:MM).",
            },
        },
        "required": [
            "customer_name",
            "phone_number",
            "service_type",
            "date",
            "time_slot",
        ],
    },
}

SEND_SMS_CONFIRMATION = {
    "name": "send_sms_confirmation",
    "description": (
        "Sends an SMS confirmation to the customer after a successful booking."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "phone_number": {
                "type": "STRING",
                "description": "Phone number to send the SMS to.",
            },
            "booking_id": {
                "type": "STRING",
                "description": "The booking ID returned by book_appointment.",
            },
            "service_type": {
                "type": "STRING",
                "description": "The booked service name.",
            },
            "date": {
                "type": "STRING",
                "description": "Appointment date.",
            },
            "time_slot": {
                "type": "STRING",
                "description": "Appointment time.",
            },
        },
        "required": ["phone_number", "booking_id", "service_type", "date", "time_slot"],
    },
}

CONNECT_TO_SCHEDULING_TEAM = {
    "name": "connect_to_scheduling_team",
    "description": (
        "Transfers the caller to the human scheduling team when the AI cannot "
        "fulfill the request (e.g. no available slots or customer requests it)."
    ),
    "parameters": {"type": "OBJECT", "properties": {}},
}

END_CALL = {
    "name": "end_call",
    "description": "Ends the phone call after the conversation is complete.",
    "parameters": {"type": "OBJECT", "properties": {}},
}

TOOL_DECLARATIONS = [
    {
        "function_declarations": [
            CHECK_AVAILABILITY,
            BOOK_APPOINTMENT,
            SEND_SMS_CONFIRMATION,
            CONNECT_TO_SCHEDULING_TEAM,
            END_CALL,
        ]
    }
]


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class ToolDispatcher:
    """Routes Gemini tool calls to real service implementations."""

    def __init__(
        self,
        db: Database,
        sms: SMSService,
        business: BusinessConfig,
    ) -> None:
        self._db = db
        self._sms = sms
        self._biz = business

    async def dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, f"_handle_{name}", None)
        if not handler:
            logger.error("Unknown tool called: %s", name)
            return {"error": f"Unknown tool: {name}"}
        try:
            return await handler(args)
        except Exception:
            logger.exception("Tool %s failed", name)
            return {"error": f"Internal error executing {name}"}

    # -- handlers -----------------------------------------------------------

    async def _handle_check_availability(self, args: dict) -> dict:
        date = args.get("date", "")
        service = args.get("service_type", "")
        logger.info("check_availability  date=%s  service=%s", date, service)

        booked = await self._db.get_booked_times(date)
        all_slots = [
            f"{h:02d}:{m:02d}"
            for h in range(self._biz.open_hour, self._biz.close_hour)
            for m in range(0, 60, self._biz.slot_duration_minutes)
        ]
        available = [t for t in all_slots if t not in booked]

        if not available:
            return {
                "available": False,
                "slots": [],
                "message": f"No slots available on {date}.",
            }
        return {
            "available": True,
            "slots": available[:10],
            "total_available": len(available),
            "message": f"{len(available)} slots available on {date}.",
        }

    async def _handle_book_appointment(self, args: dict) -> dict:
        phone = args.get("phone_number", "")
        name = args.get("customer_name", "Unknown")
        service = args.get("service_type", "")
        date = args.get("date", "")
        time_slot = args.get("time_slot", "")
        logger.info(
            "book_appointment  name=%s phone=%s service=%s date=%s time=%s",
            name, phone, service, date, time_slot,
        )

        booked = await self._db.get_booked_times(date)
        if time_slot in booked:
            return {
                "status": "failed",
                "reason": f"The {time_slot} slot on {date} was just taken. "
                          "Please check availability again.",
            }

        customer = await self._db.get_or_create_customer(phone=phone, name=name)
        appointment = await self._db.create_appointment(
            customer_id=customer.id,
            service=service,
            date=date,
            time_slot=time_slot,
        )
        return {
            "status": "success",
            "booking_id": appointment.id,
            "customer_name": customer.name,
            "service": appointment.service,
            "date": appointment.date,
            "time": appointment.time,
        }

    async def _handle_send_sms_confirmation(self, args: dict) -> dict:
        phone = args.get("phone_number", "")
        booking_id = args.get("booking_id", "")
        service = args.get("service_type", "")
        date = args.get("date", "")
        time_slot = args.get("time_slot", "")
        logger.info("send_sms_confirmation  phone=%s  booking=%s", phone, booking_id)

        result = await self._sms.send_confirmation(
            to_number=phone,
            booking_id=booking_id,
            service=service,
            date=date,
            time=time_slot,
            business_name=self._biz.name,
        )
        return result

    async def _handle_connect_to_scheduling_team(self, _args: dict) -> dict:
        logger.info("connect_to_scheduling_team — handing off to human agent")
        return {"status": "connecting", "message": "Transferring to scheduling team now."}

    async def _handle_end_call(self, _args: dict) -> dict:
        logger.info("end_call — conversation complete")
        return {"status": "success", "message": "Call ending."}
