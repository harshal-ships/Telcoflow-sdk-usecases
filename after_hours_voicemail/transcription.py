"""
Speech-to-text transcription via Amazon Nova 2 Sonic.

Opens a bidirectional stream to Bedrock, feeds the voicemail audio, and
collects the ASR transcription (USER-role textOutput events with
generationStage=FINAL).

Audio is downsampled from 24 kHz (Telcoflow) to 16 kHz (Nova Sonic input)
before sending.

Falls back to logging if AWS credentials are not configured.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import struct
import uuid

from config import NovaSonicConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Audio resampling  (24 kHz → 16 kHz, 16-bit mono PCM)
# ---------------------------------------------------------------------------

def _downsample_24k_to_16k(pcm_24k: bytes) -> bytes:
    """Resample 24 kHz 16-bit mono PCM down to 16 kHz using linear interpolation."""
    src = struct.unpack(f"<{len(pcm_24k) // 2}h", pcm_24k)
    src_len = len(src)
    if src_len < 2:
        return pcm_24k

    ratio = 24000 / 16000  # 1.5
    dst_len = int(src_len / ratio)
    dst = []
    for i in range(dst_len):
        pos = i * ratio
        idx = int(pos)
        frac = pos - idx
        if idx + 1 < src_len:
            sample = src[idx] + frac * (src[idx + 1] - src[idx])
        else:
            sample = src[idx]
        dst.append(max(-32768, min(32767, int(sample))))

    return struct.pack(f"<{len(dst)}h", *dst)


# ---------------------------------------------------------------------------
# Nova Sonic transcription service
# ---------------------------------------------------------------------------

AUDIO_CHUNK_SIZE = 1024  # bytes per chunk sent to Nova Sonic


class TranscriptionService:
    def __init__(self, cfg: NovaSonicConfig) -> None:
        self._cfg = cfg
        self._client_cls = None

        if cfg.enabled:
            try:
                from aws_sdk_bedrock_runtime.client import (
                    BedrockRuntimeClient,
                    InvokeModelWithBidirectionalStreamOperationInput,
                )
                from aws_sdk_bedrock_runtime.config import (
                    Config,
                    HTTPAuthSchemeResolver,
                    SigV4AuthScheme,
                )
                from smithy_aws_core.identity import EnvironmentCredentialsResolver

                self._bedrock_config = Config(
                    endpoint_uri=f"https://bedrock-runtime.{cfg.region}.amazonaws.com",
                    region=cfg.region,
                    aws_credentials_identity_resolver=EnvironmentCredentialsResolver(),
                    auth_scheme_resolver=HTTPAuthSchemeResolver(),
                    auth_schemes={"aws.auth#sigv4": SigV4AuthScheme(service="bedrock")},
                )
                self._client_cls = BedrockRuntimeClient
                logger.info(
                    "Nova Sonic transcription enabled (model: %s, region: %s)",
                    cfg.model_id, cfg.region,
                )
            except ImportError:
                logger.warning(
                    "aws-sdk-bedrock-runtime not installed — transcription disabled. "
                    "Run: pip install aws-sdk-bedrock-runtime"
                )
        else:
            logger.info("AWS credentials not set — Nova Sonic transcription disabled")

    async def transcribe(self, pcm_24k_audio: bytes) -> str:
        """
        Transcribe raw 24 kHz PCM audio using Nova Sonic ASR.
        Returns the assembled transcript string.
        """
        if not self._client_cls:
            logger.info("Transcription skipped (no Nova Sonic client)")
            return ""

        if len(pcm_24k_audio) < 4800:
            logger.info("Audio too short to transcribe (%d bytes)", len(pcm_24k_audio))
            return ""

        pcm_16k = _downsample_24k_to_16k(pcm_24k_audio)
        logger.info(
            "Downsampled %d bytes (24 kHz) → %d bytes (16 kHz)",
            len(pcm_24k_audio), len(pcm_16k),
        )

        try:
            return await self._run_nova_sonic(pcm_16k)
        except Exception:
            logger.exception("Nova Sonic transcription failed")
            return ""

    async def _run_nova_sonic(self, pcm_16k: bytes) -> str:
        from aws_sdk_bedrock_runtime.client import (
            BedrockRuntimeClient,
            InvokeModelWithBidirectionalStreamOperationInput,
        )
        from aws_sdk_bedrock_runtime.models import (
            InvokeModelWithBidirectionalStreamInputChunk,
            BidirectionalInputPayloadPart,
        )

        client = BedrockRuntimeClient(self._bedrock_config)
        stream = await client.invoke_model_with_bidirectional_stream(
            InvokeModelWithBidirectionalStreamOperationInput(model_id=self._cfg.model_id)
        )

        prompt_name = str(uuid.uuid4())
        content_name = str(uuid.uuid4())
        audio_content_name = str(uuid.uuid4())
        transcript_parts: list[str] = []
        is_user_role = False
        is_final = False

        async def send(event_json: str) -> None:
            chunk = InvokeModelWithBidirectionalStreamInputChunk(
                value=BidirectionalInputPayloadPart(bytes_=event_json.encode("utf-8"))
            )
            await stream.input_stream.send(chunk)

        # -- Session start ---------------------------------------------------
        await send(json.dumps({"event": {"sessionStart": {
            "inferenceConfiguration": {"maxTokens": 1024, "topP": 0.9, "temperature": 0.7},
        }}}))

        # -- Prompt start ----------------------------------------------------
        await send(json.dumps({"event": {"promptStart": {
            "promptName": prompt_name,
            "textOutputConfiguration": {"mediaType": "text/plain"},
            "audioOutputConfiguration": {
                "mediaType": "audio/lpcm",
                "sampleRateHertz": 24000,
                "sampleSizeBits": 16,
                "channelCount": 1,
                "voiceId": "matthew",
                "encoding": "base64",
                "audioType": "SPEECH",
            },
        }}}))

        # -- System prompt (tell it to just transcribe) ----------------------
        await send(json.dumps({"event": {"contentStart": {
            "promptName": prompt_name,
            "contentName": content_name,
            "type": "TEXT",
            "interactive": False,
            "role": "SYSTEM",
            "textInputConfiguration": {"mediaType": "text/plain"},
        }}}))

        system_msg = (
            "You are a transcription assistant. Listen to the audio and "
            "acknowledge that you received a voicemail message. "
            "Keep your response to one short sentence."
        )
        await send(json.dumps({"event": {"textInput": {
            "promptName": prompt_name,
            "contentName": content_name,
            "content": system_msg,
        }}}))

        await send(json.dumps({"event": {"contentEnd": {
            "promptName": prompt_name,
            "contentName": content_name,
        }}}))

        # -- Audio content start ---------------------------------------------
        await send(json.dumps({"event": {"contentStart": {
            "promptName": prompt_name,
            "contentName": audio_content_name,
            "type": "AUDIO",
            "interactive": False,
            "role": "USER",
            "audioInputConfiguration": {
                "mediaType": "audio/lpcm",
                "sampleRateHertz": self._cfg.input_sample_rate,
                "sampleSizeBits": 16,
                "channelCount": 1,
                "audioType": "SPEECH",
                "encoding": "base64",
            },
        }}}))

        # -- Send audio in chunks --------------------------------------------
        for offset in range(0, len(pcm_16k), AUDIO_CHUNK_SIZE):
            chunk_bytes = pcm_16k[offset : offset + AUDIO_CHUNK_SIZE]
            b64 = base64.b64encode(chunk_bytes).decode("utf-8")
            await send(json.dumps({"event": {"audioInput": {
                "promptName": prompt_name,
                "contentName": audio_content_name,
                "content": b64,
            }}}))
            await asyncio.sleep(0.001)

        # -- Audio content end -----------------------------------------------
        await send(json.dumps({"event": {"contentEnd": {
            "promptName": prompt_name,
            "contentName": audio_content_name,
        }}}))

        # -- Process responses (collect USER-role text = ASR transcript) ------
        try:
            while True:
                output = await asyncio.wait_for(stream.await_output(), timeout=30)
                result = await output[1].receive()

                if not (result.value and result.value.bytes_):
                    continue

                data = json.loads(result.value.bytes_.decode("utf-8"))
                event = data.get("event", {})

                if "contentStart" in event:
                    cs = event["contentStart"]
                    is_user_role = cs.get("role") == "USER"
                    additional = cs.get("additionalModelFields", "")
                    if additional:
                        try:
                            is_final = json.loads(additional).get("generationStage") == "FINAL"
                        except (json.JSONDecodeError, AttributeError):
                            is_final = False

                elif "textOutput" in event:
                    if is_user_role:
                        text = event["textOutput"].get("content", "")
                        if text:
                            transcript_parts.append(text)

                elif "completionEnd" in event:
                    break

        except asyncio.TimeoutError:
            logger.warning("Nova Sonic response timed out")
        except Exception:
            logger.exception("Error reading Nova Sonic response stream")

        # -- End session -----------------------------------------------------
        await send(json.dumps({"event": {"promptEnd": {"promptName": prompt_name}}}))
        await send(json.dumps({"event": {"sessionEnd": {}}}))
        await stream.input_stream.close()

        transcript = " ".join(transcript_parts).strip()
        logger.info("Nova Sonic transcription complete (%d chars)", len(transcript))
        return transcript
