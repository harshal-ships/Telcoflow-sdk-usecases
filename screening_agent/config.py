"""
Centralised configuration loaded from environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

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
    live_model: str = "gemini-2.5-flash-native-audio-preview-12-2025"
    text_model: str = "gemini-2.5-flash"
    sample_rate: int = 24000

    @classmethod
    def from_env(cls) -> GeminiConfig:
        return cls(
            api_key=_require("GEMINI_API_KEY"),
            live_model=os.getenv("GEMINI_LIVE_MODEL", cls.live_model),
            text_model=os.getenv("GEMINI_TEXT_MODEL", cls.text_model),
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
class ScreeningConfig:
    position: str = "Customer Service Representative"
    company: str = "XanhSM"
    results_csv: str = "screening_results.csv"

    @classmethod
    def from_env(cls) -> ScreeningConfig:
        return cls(
            position=os.getenv("SCREENING_POSITION", cls.position),
            company=os.getenv("SCREENING_COMPANY", cls.company),
            results_csv=os.getenv("RESULTS_CSV", cls.results_csv),
        )


@dataclass(frozen=True)
class AppConfig:
    gemini: GeminiConfig = field(default_factory=GeminiConfig)
    telcoflow: TelcoflowConfig = field(default_factory=TelcoflowConfig)
    screening: ScreeningConfig = field(default_factory=ScreeningConfig)

    @classmethod
    def from_env(cls) -> AppConfig:
        return cls(
            gemini=GeminiConfig.from_env(),
            telcoflow=TelcoflowConfig.from_env(),
            screening=ScreeningConfig.from_env(),
        )
