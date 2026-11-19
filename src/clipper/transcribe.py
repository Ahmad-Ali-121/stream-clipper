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

from . import progress
from .config import Config, load_env
from .tools import ToolError, find_tool, run

log = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_USD_PER_HOUR = {"whisper-large-v3-turbo": 0.04, "whisper-large-v3": 0.111}

_model: Any = None
_model_key: tuple | None = None
_model_lock = __import__("threading").Lock()  # drafts are made in parallel; the model must be loaded once, not once per draft


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


def load_model(cfg: Config, name: str | None = None):
    """The speech model, loaded once per program. ``name`` defaults to transcribe.model."""
    global _model, _model_key
    t = cfg.transcribe
    name = name or t.model
    from . import hw

    hw.add_nvidia_dirs()
    device, compute_type = hw.whisper_device(cfg)
    if device == "cuda" and t.device == "auto":
        device, compute_type = hw.confirm_cuda(cfg, t.model, compute_type, str(cfg.data_dir / "models"))
    key = (name, device, compute_type)
    with _model_lock:
        return _load_locked(cfg, name, device, compute_type, key)


def _load_locked(cfg: Config, name: str, device: str, compute_type: str, key: tuple):
    global _model, _model_key
    from . import hw

    if _model is None or _model_key != key:
        from faster_whisper import WhisperModel  # imported late: it is slow to import and not needed by other stages

        models = cfg.data_dir / "models"
        models.mkdir(parents=True, exist_ok=True)
        log.info("loading whisper model %s (%s, %s); the first run downloads it", name, device, compute_type)
        ok, why = hw.trial_load(name, device, compute_type, str(models))
        if not ok:
            raise ToolError(
                f"the speech-to-text model could not be loaded ({device}, {compute_type}). What the trial said:\n{why}\n"
                f"If nothing above explains it, the model file may be damaged: delete the folders starting with 'models--' in {models} "
                "and try again (it downloads again)."
            )
        _model = WhisperModel(name, device=device, compute_type=compute_type, download_root=str(models))
        _model_key = key
    return _model


def _local(path: Path, cfg: Config) -> list[dict]:
    t = cfg.transcribe
    segments, _info = load_model(cfg).transcribe(
        _decode_audio(path, cfg),
        language=speech_language(cfg),
        beam_size=5,
        vad_filter=t.vad_filter,
        vad_parameters=dict(min_silence_duration_ms=500) if t.vad_filter else None,
        word_timestamps=True,
        condition_on_previous_text=False,  # short clips: avoids repeated-phrase loops
    )
    words: list[dict] = []
    for seg in segments:
        if _info.duration:
            progress.sub(seg.end / _info.duration)
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


def speech_language(cfg: Config) -> str | None:
    """The language handed to the speech model; None lets it detect.

    Urdu speech that is wanted in Latin letters is heard as Hindi on purpose. Spoken Urdu and Hindi are the same
    language, but Hindi letters spell out every vowel and Urdu letters leave most of them out, so Hindi letters
    turn into readable Latin letters ("bhai", "nahin") and Urdu letters would turn into "bhy", "nhyn".
    """
    lang = cfg.transcribe.language
    if lang == "auto":
        return None
    return "hi" if lang == "ur" and cfg.captions.script == "roman" else lang


_DEV_CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "sh",
    "स": "s", "ह": "h", "क़": "q", "ख़": "kh", "ग़": "gh", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f", "य़": "y",
}
_DEV_NUKTA = {"क": "q", "ख": "kh", "ग": "gh", "ज": "z", "ड": "r", "ढ": "rh", "फ": "f"}
_DEV_VOWELS = {"अ": "a", "आ": "a", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऋ": "ri", "ऑ": "o"}
_DEV_SIGNS = {"ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ृ": "ri", "ॉ": "o", "ॅ": "e"}
_DEV_OTHER = {"ं": "n", "ँ": "n", "ः": "h", "।": ".", "॥": ".", "ॐ": "om"}
_DEV_DIGITS = {chr(0x0966 + i): str(i) for i in range(10)}


def roman_fallback(word: str) -> str:
    """A Hindi-letter word in Latin letters by fixed rules, for when Claude cannot be asked.

    Rough but readable: "भाई" -> "bhai", "नहीं" -> "nahin", "क्या" -> "kya". It cannot know that "गेम" is the
    English word "game" (it gives "gem"); Claude does that part better.
    """
    out: list[str] = []
    chars = list(word)
    i = 0
    while i < len(chars):
        ch = chars[i]
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if ch in _DEV_CONSONANTS:
            base = _DEV_CONSONANTS[ch]
            if nxt == "़":  # a dot under the letter changes its sound (ज़ = z, फ़ = f)
                base = _DEV_NUKTA.get(ch, base)
                i += 1
                nxt = chars[i + 1] if i + 1 < len(chars) else ""
            out.append(base)
            if nxt == "्":  # joined to the next consonant: no vowel between them
                i += 1
            elif nxt in _DEV_SIGNS:
                out.append(_DEV_SIGNS[nxt])
                i += 1
            elif nxt and (nxt in _DEV_CONSONANTS or nxt in _DEV_OTHER):
                out.append("a")  # the unwritten "a" inside a word; at the end of a word it is silent
        elif ch in _DEV_VOWELS:
            out.append(_DEV_VOWELS[ch])
        elif ch in _DEV_SIGNS:
            out.append(_DEV_SIGNS[ch])
        elif ch in _DEV_OTHER:
            out.append(_DEV_OTHER[ch])
        elif ch in _DEV_DIGITS:
            out.append(_DEV_DIGITS[ch])
        elif ch not in ("़", "्", "\u200d", "\u200c"):
            out.append(ch)
        i += 1
    return "".join(out)


def caption_script(words: list[dict], cfg: Config) -> list[dict]:
    """Urdu or Hindi words in Latin letters when captions.script is "roman"; the heard spelling is kept as "native".

    Claude does it when it can be reached (it also restores English words). Without it, fixed rules are used,
    so captions are never left in letters the viewer was not meant to see.
    """
    from .captions import script_font

    if cfg.captions.script != "roman" or not script_font(w["word"] for w in words):
        return words
    from . import llm

    try:
        roman = llm.romanize([w["word"] for w in words], cfg)
    except llm.LlmUnavailable as e:
        log.warning("captions were put into Latin letters by fixed rules, which is rougher than Claude's version: %s", e)
        roman = [roman_fallback(w["word"]) for w in words]
    return [{**w, "native": w["word"], "word": r or w["word"]} for w, r in zip(words, roman)]


def transcribe_file(path: Path, cfg: Config) -> list[dict]:
    backend = cfg.transcribe.backend
    if backend == "local":
        return caption_script(_local(path, cfg), cfg)
    if backend == "groq":
        return caption_script(_groq(path, cfg), cfg)
    raise ToolError(f"transcribe.backend must be 'local' or 'groq', got {backend!r}")
