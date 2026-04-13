"""
Team notification via Slack incoming webhook.

Uses a simple HTTP POST (aiohttp) — no heavy SDK needed.
Falls back to logging if the webhook URL is not configured.
"""

from __future__ import annotations

import logging

import aiohttp

from config import SlackConfig

logger = logging.getLogger(__name__)


class SlackNotifier:
    def __init__(self, cfg: SlackConfig) -> None:
        self._cfg = cfg
        if cfg.enabled:
            logger.info("Slack notifications enabled → %s", cfg.channel)
        else:
            logger.info("Slack webhook not configured — notifications will be logged only")

    async def send_voicemail_alert(
        self,
        caller_number: str,
        transcript: str,
        duration_seconds: float,
        voicemail_id: str,
        audio_path: str,
    ) -> bool:
        duration_str = f"{int(duration_seconds // 60)}m {int(duration_seconds % 60)}s"
        text = (
            f":phone: *New voicemail* from `{caller_number}`\n"
            f">*Duration:* {duration_str}  |  *Ref:* `{voicemail_id}`\n"
            f">*Audio:* `{audio_path}`\n"
            f">*Transcript:*\n>{transcript or '_No transcript available_'}"
        )

        if not self._cfg.enabled:
            logger.info("[SLACK-LOG] %s", text)
            return True

        payload = {"text": text}
        if self._cfg.channel:
            payload["channel"] = self._cfg.channel

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._cfg.webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        logger.info("Slack notification sent for %s", voicemail_id)
                        return True
                    body = await resp.text()
                    logger.error("Slack returned %d: %s", resp.status, body)
                    return False
        except Exception:
            logger.exception("Failed to send Slack notification")
            return False
