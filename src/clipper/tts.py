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

# The voices of the free voice model that are worth offering, best first. The model's makers grade their own voices:
# Heart and Bella are their two best; the rest are the better ones of each kind. (voices-v1.0.bin holds 54 in all.)
KOKORO_VOICES = (
    ("af_heart", "Heart: American woman (rated best by the model's makers)"),
    ("af_bella", "Bella: American woman (rated second best)"),
    ("af_nicole", "Nicole: American woman"),
    ("af_sarah", "Sarah: American woman"),
    ("af_aoede", "Aoede: American woman"),
    ("af_kore", "Kore: American woman"),
    ("am_michael", "Michael: American man"),
    ("am_fenrir", "Fenrir: American man"),
    ("am_puck", "Puck: American man"),
    ("bf_emma", "Emma: British woman"),
    ("bm_george", "George: British man"),
    ("bm_fable", "Fable: British man"),
)
# ElevenLabs' speech models, best sounding first. The one used is the first of these the account really has.
ELEVEN_MODEL_ORDER = ("eleven_v3", "eleven_multilingual_v2", "eleven_turbo_v2_5", "eleven_flash_v2_5", "eleven_turbo_v2", "eleven_flash_v2")
ELEVEN_API = "https://api.elevenlabs.io"
# Offered when a key may not list the models itself: (id, what to call it).
ELEVEN_USUAL = (("eleven_multilingual_v2", "Multilingual v2 (the well-proven, natural one)"), ("eleven_v3", "v3 (the newest; not on every account)"),
                ("eleven_turbo_v2_5", "Turbo v2.5 (faster)"), ("eleven_flash_v2_5", "Flash v2.5 (fastest)"))
SAMPLE_LINE = "This is how the narrator of your explainer videos will sound."


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
    voice = cfg.voice.kokoro_voice
    # the British voices read with British sounds; every other voice of the list is American
    samples, rate = _kokoro.create(text, voice=voice, speed=cfg.voice.speed, lang="en-gb" if voice.startswith("b") else "en-us")
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


def elevenlabs_key() -> str:
    load_env()
    return (os.environ.get("ELEVENLABS_API_KEY") or "").strip()


def elevenlabs_check(cfg: Config) -> dict:
    """Ask ElevenLabs whether the key in .env works and what it offers. No speech is made, so it costs nothing.

    Returns {"ok", "why", "models": [{"id", "name"}], "best_model", "voices": [{"id", "name", "about"}], "left", "note"}.
    ``left`` is how many characters the plan still has, or None when the key may not ask that.

    A key can be made with only some permissions. One that may make speech but not list models or voices is a
    working key: it is not called refused, the usual models are offered instead, and the note says so.
    """
    import requests

    key = elevenlabs_key()
    if not key:
        return {"ok": False, "why": "No ElevenLabs key is set. Put a line ELEVENLABS_API_KEY=your-key in the file .env in the program's folder, "
                                    "then press Check ElevenLabs. The key is on elevenlabs.io under your profile, API keys."}
    head = {"xi-api-key": key}

    def ask(path: str):
        """(data, kind, why): kind is "ok", "limited" (the key is real but may not do this), "refused", or "failed"."""
        try:
            r = requests.get(ELEVEN_API + path, headers=head, timeout=30)
        except requests.RequestException as e:
            return None, "failed", f"ElevenLabs could not be reached ({type(e).__name__}). Check the internet connection and try again."
        if r.status_code == 200:
            try:
                return r.json(), "ok", ""
            except ValueError:
                return None, "failed", "ElevenLabs gave an answer that could not be read."
        try:
            detail = r.json().get("detail") or {}
        except ValueError:
            detail = {}
        status = str(detail.get("status") or "") if isinstance(detail, dict) else ""
        if r.status_code == 401 and status == "missing_permissions":
            return None, "limited", "the key is not allowed to do this"
        if r.status_code == 401:
            return None, "refused", "ElevenLabs refused the key in .env (it is wrong or was deleted). Make a new key on elevenlabs.io and put it in .env."
        return None, "failed", f"ElevenLabs answered with an error ({r.status_code}): {r.text[:160]}"

    models, m_kind, m_why = ask("/v1/models")
    voices, v_kind, v_why = ask("/v1/voices")
    plan, p_kind, _ = ask("/v1/user/subscription")
    kinds = (m_kind, v_kind, p_kind)
    if "refused" in kinds:
        return {"ok": False, "why": next(w for k, w in ((m_kind, m_why), (v_kind, v_why)) if k == "refused") if "refused" in (m_kind, v_kind)
                else "ElevenLabs refused the key in .env (it is wrong or was deleted). Make a new key on elevenlabs.io and put it in .env."}
    if "ok" not in kinds and "limited" not in kinds:
        return {"ok": False, "why": m_why or v_why}
    notes = []
    if m_kind == "ok":
        can = [{"id": str(m.get("model_id")), "name": str(m.get("name") or m.get("model_id"))} for m in models
               if isinstance(m, dict) and m.get("model_id") and m.get("can_do_text_to_speech", True)]
        ids = [m["id"] for m in can]
        best = next((m for m in ELEVEN_MODEL_ORDER if m in ids), ids[0] if ids else "")
    else:
        # the key may not list the models: offer the usual ones, the well-proven one first
        can = [{"id": i, "name": n} for i, n in ELEVEN_USUAL]
        best = ELEVEN_USUAL[0][0]
        notes.append("This key may not list ElevenLabs' models, so the usual ones are offered. To see the exact list, give the key the permission "
                     "'models read' on elevenlabs.io.")
    can.sort(key=lambda m: ELEVEN_MODEL_ORDER.index(m["id"]) if m["id"] in ELEVEN_MODEL_ORDER else 99)
    out = {"ok": True, "why": "", "models": can, "best_model": best, "voices": [], "left": None, "models_listed": m_kind == "ok"}
    if v_kind == "ok":
        for v in voices.get("voices", []) if isinstance(voices, dict) else []:
            labels = v.get("labels") or {}
            about = ", ".join(str(labels[k]) for k in ("gender", "accent", "age", "description", "use_case") if labels.get(k))
            out["voices"].append({"id": str(v.get("voice_id")), "name": str(v.get("name") or v.get("voice_id")), "about": about,
                                  "own": v.get("category") not in (None, "premade")})
        # the ready-made voices first (they work on every plan), each group by name
        out["voices"].sort(key=lambda v: (v["own"], v["name"].lower()))
    else:
        notes.append("This key may not list voices, so the voice already in the settings is used.")
    if isinstance(plan, dict) and plan.get("character_limit") is not None:
        out["left"] = max(0, int(plan.get("character_limit") or 0) - int(plan.get("character_count") or 0))
    if "ok" not in kinds:
        notes.append("Whether it can make speech is only known by trying: press Listen (it uses about 60 characters of your plan).")
    out["note"] = " ".join(notes)
    return out


def sample(cfg: Config) -> Path:
    """One short line spoken with the voice as it is set now, to listen to before making a video."""
    folder = cfg.data_dir / "tmp" / "voice-sample"
    for old in folder.glob("sample.*") if folder.exists() else ():
        old.unlink(missing_ok=True)
    return speak(SAMPLE_LINE, folder / "sample", cfg)

