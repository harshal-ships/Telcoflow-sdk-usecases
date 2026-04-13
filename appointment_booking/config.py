"""
Centralised configuration loaded from environment variables.
All tunables live here so the rest of the codebase stays clean.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise EnvironmentError(f"Missing required env var: {name}")
    return val


@dataclass(frozen=True)
class GeminiConfig:
    api_key: str = ""
    model: str = "gemini-2.5-flash-native-audio-preview-12-2025"
    sample_rate: int = 24000

    @classmethod
    def from_env(cls) -> GeminiConfig:
        return cls(
            api_key=_require("GEMINI_API_KEY"),
            model=os.getenv("GEMINI_MODEL", cls.model),
            sample_rate=int(os.getenv("GEMINI_SAMPLE_RATE", str(cls.sample_rate))),
        )


@dataclass(frozen=True)
class TelcoflowConfig:
    api_key: str = ""
    connector_uuid: str = ""
    sample_rate: int = 24000
    mode: str = "sandbox"
    cert_path: str = ""
    key_path: str = ""

    @classmethod
    def from_env(cls) -> TelcoflowConfig:
        mode = os.getenv("TELCOFLOW_MODE", "sandbox").lower()
        if mode == "production":
            return cls(
                mode="production",
                cert_path=_require("TELCOFLOW_CERT_PATH"),
                key_path=_require("TELCOFLOW_KEY_PATH"),
                sample_rate=int(os.getenv("TELCOFLOW_SAMPLE_RATE", "24000")),
            )
        return cls(
            mode="sandbox",
            api_key=_require("WSS_API_KEY"),
            connector_uuid=_require("WSS_CONNECTOR_UUID"),
            sample_rate=int(os.getenv("TELCOFLOW_SAMPLE_RATE", "24000")),
        )


@dataclass(frozen=True)
class TwilioConfig:
    account_sid: str = ""
    auth_token: str = ""
    from_number: str = ""
    enabled: bool = False

    @classmethod
    def from_env(cls) -> TwilioConfig:
        sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        token = os.getenv("TWILIO_AUTH_TOKEN", "")
        from_num = os.getenv("TWILIO_FROM_NUMBER", "")
        enabled = all([sid, token, from_num])
        return cls(
            account_sid=sid,
            auth_token=token,
            from_number=from_num,
            enabled=enabled,
        )


@dataclass(frozen=True)
class BusinessConfig:
    """Configurable business rules for appointment scheduling."""
    name: str = "YOUR_COMPANY_NAME Clinic"
    open_hour: int = 9
    close_hour: int = 17
    slot_duration_minutes: int = 30
    services: tuple[str, ...] = (
        "General Checkup",
        "Dental Cleaning",
        "Eye Exam",
        "Physiotherapy",
        "Consultation",
    )

    @classmethod
    def from_env(cls) -> BusinessConfig:
        raw_services = os.getenv("BUSINESS_SERVICES", "")
        services = (
            tuple(s.strip() for s in raw_services.split(",") if s.strip())
            if raw_services
            else cls.services
        )
        return cls(
            name=os.getenv("BUSINESS_NAME", cls.name),
            open_hour=int(os.getenv("BUSINESS_OPEN_HOUR", str(cls.open_hour))),
            close_hour=int(os.getenv("BUSINESS_CLOSE_HOUR", str(cls.close_hour))),
            slot_duration_minutes=int(
                os.getenv("BUSINESS_SLOT_MINUTES", str(cls.slot_duration_minutes))
            ),
            services=services,
        )


@dataclass(frozen=True)
class AppConfig:
    gemini: GeminiConfig = field(default_factory=GeminiConfig)
    telcoflow: TelcoflowConfig = field(default_factory=TelcoflowConfig)
    twilio: TwilioConfig = field(default_factory=TwilioConfig)
    business: BusinessConfig = field(default_factory=BusinessConfig)
    db_path: str = "appointments.db"

    @classmethod
    def from_env(cls) -> AppConfig:
        return cls(
            gemini=GeminiConfig.from_env(),
            telcoflow=TelcoflowConfig.from_env(),
            twilio=TwilioConfig.from_env(),
            business=BusinessConfig.from_env(),
            db_path=os.getenv("DB_PATH", "appointments.db"),
        )
