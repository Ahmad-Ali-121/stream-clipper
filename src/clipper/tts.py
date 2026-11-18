"""Turn a narration line into a voice recording.

voice.backend:
  kokoro      free, runs on this machine (Kokoro-82M through kokoro-onnx). No account, no upload.
  elevenlabs  paid, more natural. Needs ELEVENLABS_API_KEY in .env. Each call is logged with its character count.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from .config import Config, load_env
from .tools import ToolError

log = logging.getLogger(__name__)

_kokoro: Any = None


def _speak_kokoro(text: str, out: Path, cfg: Config) -> None:
    global _kokoro
    import soundfile
    from kokoro_onnx import Kokoro

    folder = cfg.data_dir / "models" / "kokoro"
    model, voices = folder / "kokoro-v1.0.int8.onnx", folder / "voices-v1.0.bin"
    if not model.exists() or not voices.exists():
        raise ToolError(f"voice model missing in {folder}. Run setup.ps1.")
    if _kokoro is None:
        _kokoro = Kokoro(str(model), str(voices))
    samples, rate = _kokoro.create(text, voice=cfg.voice.kokoro_voice, speed=cfg.voice.speed, lang="en-us")
    soundfile.write(str(out), samples, rate)


def _speak_elevenlabs(text: str, out: Path, cfg: Config) -> None:
    import requests

    load_env()
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise ToolError("voice.backend is 'elevenlabs' but ELEVENLABS_API_KEY is not set in .env")
    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{cfg.voice.elevenlabs_voice_id}",
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": key},
        json={"text": text, "model_id": cfg.voice.elevenlabs_model},
        timeout=120,
    )
    if resp.status_code != 200:
        raise ToolError(f"ElevenLabs failed ({resp.status_code}): {resp.text[:200]}")
    out.write_bytes(resp.content)
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "service": "elevenlabs", "model": cfg.voice.elevenlabs_model, "characters": len(text)}
    with (cfg.data_dir / "usage.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    log.info("paid API use: %s", entry)


def speak(text: str, out: Path, cfg: Config) -> Path:
    """Write ``text`` as speech to ``out`` (.wav for kokoro, .mp3 for elevenlabs). Returns the file written."""
    text = " ".join(text.split())
    if not text:
        raise ToolError("nothing to say: the narration line is empty")
    out.parent.mkdir(parents=True, exist_ok=True)
    if cfg.voice.backend == "kokoro":
        out = out.with_suffix(".wav")
        _speak_kokoro(text, out, cfg)
    elif cfg.voice.backend == "elevenlabs":
        out = out.with_suffix(".mp3")
        _speak_elevenlabs(text, out, cfg)
    else:
        raise ToolError(f"voice.backend must be 'kokoro' or 'elevenlabs', got {cfg.voice.backend!r}")
    if not out.exists() or out.stat().st_size == 0:
        raise ToolError("the voice engine produced no audio")
    return out
