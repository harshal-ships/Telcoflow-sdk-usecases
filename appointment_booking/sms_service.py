"""
SMS delivery via Twilio.

If Twilio credentials are not configured the service logs messages instead
of sending them, so the agent can still run end-to-end during development.
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial

from config import TwilioConfig

logger = logging.getLogger(__name__)


class SMSService:
    def __init__(self, cfg: TwilioConfig) -> None:
        self._cfg = cfg
        self._client = None

        if cfg.enabled:
            try:
                from twilio.rest import Client as TwilioClient
                self._client = TwilioClient(cfg.account_sid, cfg.auth_token)
                logger.info("Twilio SMS enabled (from: %s)", cfg.from_number)
            except ImportError:
                logger.warning(
                    "twilio package not installed — SMS will be logged only. "
                    "Run: pip install twilio"
                )
        else:
            logger.info(
                "Twilio credentials not configured — SMS will be logged only"
            )

    async def send_confirmation(
        self,
        to_number: str,
        booking_id: str,
        service: str,
        date: str,
        time: str,
        business_name: str = "XanhSM Clinic",
    ) -> dict:
        body = (
            f"Hi! Your {service} appointment at {business_name} is confirmed "
            f"for {date} at {time}. Booking ref: {booking_id}. "
            "Reply CANCEL to cancel."
        )

        if self._client:
            return await self._send_twilio(to_number, body)

        logger.info("[SMS-LOG] To: %s | %s", to_number, body)
        return {"status": "logged", "to": to_number, "body": body}

    async def _send_twilio(self, to_number: str, body: str) -> dict:
        loop = asyncio.get_running_loop()
        try:
            message = await loop.run_in_executor(
                None,
                partial(
                    self._client.messages.create,
                    to=to_number,
                    from_=self._cfg.from_number,
                    body=body,
                ),
            )
            logger.info("SMS sent to %s (sid: %s)", to_number, message.sid)
            return {
                "status": "sent",
                "sid": message.sid,
                "to": to_number,
            }
        except Exception:
            logger.exception("Failed to send SMS to %s", to_number)
            return {"status": "failed", "to": to_number, "error": "delivery_failed"}
