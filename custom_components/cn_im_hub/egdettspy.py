"""Edge TTS helper for CN IM Hub."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile

from homeassistant.core import HomeAssistant

DEFAULT_EDGE_TTS_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_EDGE_TTS_RATE = "+0%"
DEFAULT_EDGE_TTS_VOLUME = "+0%"
DEFAULT_EDGE_TTS_PITCH = "+0Hz"


def is_edge_tts_available() -> bool:
    """Return whether the edge-tts package is importable."""

    return importlib.util.find_spec("edge_tts") is not None


async def async_generate_tts_mp3(
    hass: HomeAssistant,
    text: str,
    *,
    voice: str = DEFAULT_EDGE_TTS_VOICE,
    rate: str = DEFAULT_EDGE_TTS_RATE,
    volume: str = DEFAULT_EDGE_TTS_VOLUME,
    pitch: str = DEFAULT_EDGE_TTS_PITCH,
) -> bytes:
    """Generate MP3 bytes with edge-tts."""

    message = text.strip()
    if not message:
        raise ValueError("TTS text is empty")

    import edge_tts

    tmp_path = Path(
        tempfile.NamedTemporaryFile(prefix="cn_im_hub_tts_", suffix=".mp3", delete=False).name
    )
    try:
        communicate = edge_tts.Communicate(
            message,
            voice=voice or DEFAULT_EDGE_TTS_VOICE,
            rate=rate or DEFAULT_EDGE_TTS_RATE,
            volume=volume or DEFAULT_EDGE_TTS_VOLUME,
            pitch=pitch or DEFAULT_EDGE_TTS_PITCH,
        )
        await communicate.save(str(tmp_path))
        return await hass.async_add_executor_job(tmp_path.read_bytes)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
