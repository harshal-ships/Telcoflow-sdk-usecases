"""
Centralised configuration loaded from environment variables.
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

    @classmethod
    def from_env(cls) -> TelcoflowConfig:
        return cls(
            api_key=_require("WSS_API_KEY"),
            connector_uuid=_require("WSS_CONNECTOR_UUID"),
            sample_rate=int(os.getenv("TELCOFLOW_SAMPLE_RATE", "24000")),
        )


@dataclass(frozen=True)
class NovaSonicConfig:
    model_id: str = "amazon.nova-2-sonic-v1:0"
    region: str = "us-east-1"
    input_sample_rate: int = 16000
    enabled: bool = False

    @classmethod
    def from_env(cls) -> NovaSonicConfig:
        key = os.getenv("AWS_ACCESS_KEY_ID", "")
        return cls(
            model_id=os.getenv("NOVA_SONIC_MODEL_ID", cls.model_id),
            region=os.getenv("AWS_DEFAULT_REGION", os.getenv("AWS_REGION", cls.region)),
            input_sample_rate=int(os.getenv("NOVA_SONIC_INPUT_SAMPLE_RATE", str(cls.input_sample_rate))),
            enabled=bool(key),
        )


@dataclass(frozen=True)
class SlackConfig:
    webhook_url: str = ""
    channel: str = "#incoming-calls"
    enabled: bool = False

    @classmethod
    def from_env(cls) -> SlackConfig:
        url = os.getenv("SLACK_WEBHOOK_URL", "")
        return cls(
            webhook_url=url,
            channel=os.getenv("SLACK_CHANNEL", cls.channel),
            enabled=bool(url),
        )


@dataclass(frozen=True)
class BusinessHoursConfig:
    name: str = "XanhSM"
    timezone: str = "Asia/Singapore"
    open_hour: int = 9
    close_hour: int = 17

    @classmethod
    def from_env(cls) -> BusinessHoursConfig:
        return cls(
            name=os.getenv("BUSINESS_NAME", cls.name),
            timezone=os.getenv("BUSINESS_TIMEZONE", cls.timezone),
            open_hour=int(os.getenv("BUSINESS_OPEN_HOUR", str(cls.open_hour))),
            close_hour=int(os.getenv("BUSINESS_CLOSE_HOUR", str(cls.close_hour))),
        )


@dataclass(frozen=True)
class AppConfig:
    gemini: GeminiConfig = field(default_factory=GeminiConfig)
    telcoflow: TelcoflowConfig = field(default_factory=TelcoflowConfig)
    nova_sonic: NovaSonicConfig = field(default_factory=NovaSonicConfig)
    slack: SlackConfig = field(default_factory=SlackConfig)
    business: BusinessHoursConfig = field(default_factory=BusinessHoursConfig)
    db_path: str = "voicemail.db"
    recordings_dir: str = "recordings"

    @classmethod
    def from_env(cls) -> AppConfig:
        return cls(
            gemini=GeminiConfig.from_env(),
            telcoflow=TelcoflowConfig.from_env(),
            nova_sonic=NovaSonicConfig.from_env(),
            slack=SlackConfig.from_env(),
            business=BusinessHoursConfig.from_env(),
            db_path=os.getenv("DB_PATH", "voicemail.db"),
            recordings_dir=os.getenv("RECORDINGS_DIR", "recordings"),
        )
