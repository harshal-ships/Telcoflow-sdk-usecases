"""
Persistent voicemail storage using aiosqlite.
Saves audio as WAV files on disk, metadata + transcript in SQLite.
"""

from __future__ import annotations

import io
import logging
import struct
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Voicemail:
    id: str
    caller_number: str
    audio_path: str
    transcript: str
    duration_seconds: float
    created_at: str


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS voicemails (
    id              TEXT PRIMARY KEY,
    caller_number   TEXT NOT NULL,
    audio_path      TEXT NOT NULL,
    transcript      TEXT DEFAULT '',
    duration_seconds REAL DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_voicemails_caller
    ON voicemails(caller_number);
CREATE INDEX IF NOT EXISTS idx_voicemails_created
    ON voicemails(created_at);
"""


# ---------------------------------------------------------------------------
# WAV helper
# ---------------------------------------------------------------------------

SAMPLE_RATE = 24000
BITS_PER_SAMPLE = 16
NUM_CHANNELS = 1


def pcm_to_wav(pcm_data: bytes) -> bytes:
    """Wrap raw PCM bytes in a WAV header (16-bit, 24 kHz, mono)."""
    byte_rate = SAMPLE_RATE * NUM_CHANNELS * (BITS_PER_SAMPLE // 8)
    block_align = NUM_CHANNELS * (BITS_PER_SAMPLE // 8)
    data_size = len(pcm_data)

    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<I", 16))
    buf.write(struct.pack("<H", 1))  # PCM format
    buf.write(struct.pack("<H", NUM_CHANNELS))
    buf.write(struct.pack("<I", SAMPLE_RATE))
    buf.write(struct.pack("<I", byte_rate))
    buf.write(struct.pack("<H", block_align))
    buf.write(struct.pack("<H", BITS_PER_SAMPLE))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(pcm_data)
    return buf.getvalue()


def pcm_duration_seconds(pcm_data: bytes) -> float:
    bytes_per_second = SAMPLE_RATE * NUM_CHANNELS * (BITS_PER_SAMPLE // 8)
    return len(pcm_data) / bytes_per_second if bytes_per_second else 0.0


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


class VoicemailDB:
    def __init__(self, db_path: str, recordings_dir: str) -> None:
        self._db_path = db_path
        self._recordings_dir = Path(recordings_dir)
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._recordings_dir.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()
        logger.info("VoicemailDB ready at %s (recordings: %s)", self._db_path, self._recordings_dir)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def save_voicemail(
        self,
        caller_number: str,
        pcm_audio: bytes,
        transcript: str = "",
    ) -> Voicemail:
        vid = f"vm-{uuid.uuid4().hex[:8]}"
        now = datetime.utcnow()
        filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{caller_number.replace('+', '')}_{vid}.wav"
        audio_path = self._recordings_dir / filename

        wav_data = pcm_to_wav(pcm_audio)
        audio_path.write_bytes(wav_data)

        duration = pcm_duration_seconds(pcm_audio)
        created = now.isoformat()

        await self._conn.execute(
            "INSERT INTO voicemails (id, caller_number, audio_path, transcript, duration_seconds, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (vid, caller_number, str(audio_path), transcript, duration, created),
        )
        await self._conn.commit()

        logger.info(
            "Saved voicemail %s from %s (%.1fs, %d bytes WAV)",
            vid, caller_number, duration, len(wav_data),
        )
        return Voicemail(
            id=vid,
            caller_number=caller_number,
            audio_path=str(audio_path),
            transcript=transcript,
            duration_seconds=duration,
            created_at=created,
        )

    async def update_transcript(self, voicemail_id: str, transcript: str) -> None:
        await self._conn.execute(
            "UPDATE voicemails SET transcript = ? WHERE id = ?",
            (transcript, voicemail_id),
        )
        await self._conn.commit()

    async def get_recent(self, limit: int = 20) -> list[Voicemail]:
        async with self._conn.execute(
            "SELECT * FROM voicemails ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
        return [Voicemail(**dict(r)) for r in rows]
