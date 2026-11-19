"""Speech to text with word-level timestamps, for short clip sources only.

Two backends behind one config switch (transcribe.backend):
  local  faster-whisper on this machine. Free. CPU by default; set device: cuda on an NVIDIA machine.
  groq   Groq's hosted Whisper. Paid per audio hour; needs GROQ_API_KEY in .env. Usage is logged.

Returns a list of {"start", "end", "word"} with times relative to the file.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from .config import Config, load_env
from .tools import ToolError, find_tool, run

log = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_USD_PER_HOUR = {"whisper-large-v3-turbo": 0.04, "whisper-large-v3": 0.111}

_model: Any = None
_model_key: tuple | None = None


def _decode_audio(path: Path, cfg: Config):
    """Decode to 16 kHz mono float32 with our own FFmpeg.

    faster-whisper's built-in decoder goes through PyAV, which broke on this
    setup ("open() got an unexpected keyword argument 'metadata_errors'").
    Using the FFmpeg the rest of the pipeline already depends on avoids that.
    """
    import subprocess

    import numpy as np

    proc = subprocess.run(
        [find_tool("ffmpeg", cfg.data_dir), "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=600,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise ToolError(f"could not decode audio from {path.name}: {proc.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def _local(path: Path, cfg: Config) -> list[dict]:
    global _model, _model_key
    t = cfg.transcribe
    from . import hw

    hw.add_nvidia_dirs()
    device, compute_type = hw.whisper_device(cfg)
    if device == "cuda" and t.device == "auto":
        device, compute_type = hw.confirm_cuda(cfg, t.model, compute_type, str(cfg.data_dir / "models"))
    key = (t.model, device, compute_type)
    if _model is None or _model_key != key:
        from faster_whisper import WhisperModel  # imported late: it is slow to import and not needed by other stages

        models = cfg.data_dir / "models"
        models.mkdir(parents=True, exist_ok=True)
        log.info("loading whisper model %s (%s, %s); the first run downloads it", t.model, device, compute_type)
        ok, why = hw.trial_load(t.model, device, compute_type, str(models))
        if not ok:
            raise ToolError(
                f"the speech-to-text model could not be loaded ({device}, {compute_type}). What the trial said:\n{why}\n"
                f"If nothing above explains it, the model file may be damaged: delete the folders starting with 'models--' in {models} "
                "and try again (it downloads again, about 1.6 GB)."
            )
        _model = WhisperModel(t.model, device=device, compute_type=compute_type, download_root=str(models))
        _model_key = key
    segments, _info = _model.transcribe(
        _decode_audio(path, cfg),
        language=t.language,
        beam_size=5,
        vad_filter=t.vad_filter,
        vad_parameters=dict(min_silence_duration_ms=500) if t.vad_filter else None,
        word_timestamps=True,
        condition_on_previous_text=False,  # short clips: avoids repeated-phrase loops
    )
    words: list[dict] = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text and w.probability >= t.min_word_prob:
                words.append({"start": round(w.start, 3), "end": round(w.end, 3), "word": text, "p": round(w.probability, 3)})
    return words


def _groq(path: Path, cfg: Config) -> list[dict]:
    import requests

    load_env()
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ToolError("transcribe.backend is 'groq' but GROQ_API_KEY is not set in .env")
    # Send compact mono audio, not the video: smaller upload on a slow connection.
    audio = path.with_name("audio.groq.flac")
    run(
        [find_tool("ffmpeg", cfg.data_dir), "-y", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(audio)],
        timeout_s=300,
        what="audio extraction",
    )
    try:
        with audio.open("rb") as fh:
            resp = requests.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (audio.name, fh, "audio/flac")},
                data={
                    "model": cfg.transcribe.groq_model,
                    "language": cfg.transcribe.language,
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "word",
                },
                timeout=300,
            )
    finally:
        audio.unlink(missing_ok=True)
    if resp.status_code != 200:
        raise ToolError(f"Groq transcription failed ({resp.status_code}): {resp.text[:300]}")
    data = resp.json()
    seconds = float(data.get("duration") or 0.0)
    billed = max(seconds, 10.0)  # Groq bills a 10-second minimum per request
    cost = billed / 3600.0 * GROQ_USD_PER_HOUR.get(cfg.transcribe.groq_model, 0.111)
    _log_usage(cfg, {"service": "groq", "model": cfg.transcribe.groq_model, "audio_s": round(seconds, 1), "est_usd": round(cost, 5), "file": str(path)})
    return [
        {"start": round(float(w["start"]), 3), "end": round(float(w["end"]), 3), "word": str(w["word"]).strip()}
        for w in data.get("words") or []
        if str(w.get("word", "")).strip()
    ]


def _log_usage(cfg: Config, entry: dict) -> None:
    """Append one line per paid API call, so the cost of each stream is visible."""
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), **entry}
    path = cfg.data_dir / "usage.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    log.info("paid API use: %s", entry)


def transcribe_file(path: Path, cfg: Config) -> list[dict]:
    backend = cfg.transcribe.backend
    if backend == "local":
        return _local(path, cfg)
    if backend == "groq":
        return _groq(path, cfg)
    raise ToolError(f"transcribe.backend must be 'local' or 'groq', got {backend!r}")
