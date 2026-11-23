"""The options panel on the local review page: what to watch, how, and which versions to make.

Everything here runs on this machine only. Options are saved to
``config.local.yaml`` (the personal override file beside config.yaml), the
watcher is the same ``clipper watch`` command run as a child process, and
"finish" is the same ``clipper finish`` step. Nothing is posted anywhere.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import yaml

from .config import PROJECT_ROOT, Config, load_config
from .signals import MODE_TEXT, parse_mode

log = logging.getLogger(__name__)

LOCAL_FILE = "config.local.yaml"

# Caption looks offered on the page: name -> (label, the caption settings it sets). Fonts are ones every Windows has.
CAPTION_PRESETS = {
    "bold": ("Bold white, yellow highlight", dict(font="Arial Black", font_size=88, text_color="FFFFFF", active_color="FFE600", outline=6, uppercase=True, max_words=3)),
    "impact": ("Impact, green highlight", dict(font="Impact", font_size=100, text_color="FFFFFF", active_color="3CFF6B", outline=7, uppercase=True, max_words=2)),
    "yellow": ("Big yellow, white highlight", dict(font="Arial Black", font_size=98, text_color="FFE600", active_color="FFFFFF", outline=8, uppercase=True, max_words=2)),
    "clean": ("Clean sentence case, blue highlight", dict(font="Segoe UI Black", font_size=76, text_color="FFFFFF", active_color="5CC8FF", outline=4, uppercase=False, max_words=4)),
}


_SAVE_LOCK = threading.Lock()
LOOK_KEYS = ("font", "font_size", "text_color", "active_color", "outline", "uppercase", "max_words", "animation")  # what a caption profile holds


def clean_look(raw: dict) -> dict:
    """A caption look typed on the page, checked. Every problem is said in words a person can act on."""
    import re

    from .captions import ANIMATIONS

    if not isinstance(raw, dict):
        raise ValueError("the caption style must be a set of values")
    out: dict[str, Any] = {}
    font = str(raw.get("font") or "").strip()
    if not font or len(font) > 60 or not re.fullmatch(r"[\w .\-]+", font):
        raise ValueError("give the name of a font installed on this computer, for example Arial Black")
    out["font"] = font
    for key, lo, hi, what in (("font_size", 40, 160, "the letter size"), ("outline", 0, 14, "the outline"), ("max_words", 1, 6, "the words on screen")):
        try:
            value = int(raw.get(key))
        except (TypeError, ValueError):
            raise ValueError(f"{what} must be a number from {lo} to {hi}") from None
        if not lo <= value <= hi:
            raise ValueError(f"{what} must be from {lo} to {hi}")
        out[key] = value
    for key, what in (("text_color", "the text colour"), ("active_color", "the highlight colour")):
        value = str(raw.get(key) or "").lstrip("#").upper()
        if not re.fullmatch(r"[0-9A-F]{6}", value):
            raise ValueError(f"{what} must be a colour like FFE600")
        out[key] = value
    out["uppercase"] = bool(raw.get("uppercase"))
    out["animation"] = raw.get("animation") if raw.get("animation") in ANIMATIONS else "highlight"
    return out


def save_local(values: dict[str, dict[str, Any]], folder: Path | None = None) -> None:
    """Merge ``{section: {key: value}}`` into config.local.yaml, keeping whatever else is in it.

    The file sits beside the settings file in use, which is where ``load_config`` reads it back from
    (the project folder, unless ``CLIPPER_CONFIG`` points somewhere else)."""
    if folder is None:
        folder = Path(os.environ["CLIPPER_CONFIG"]).parent if os.environ.get("CLIPPER_CONFIG") else PROJECT_ROOT
    path = folder / LOCAL_FILE
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None) or {}
    for section, items in values.items():
        data.setdefault(section, {}).update(items)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    tmp.replace(path)


def clean_options(raw: dict) -> dict[str, dict[str, Any]]:
    """Validate options sent by the page and shape them as config sections. Unknown keys are an error."""
    out: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if key == "mode":
            out.setdefault("watch", {})["mode"] = parse_mode(str(value))
        elif key in ("ab_versions", "opening"):
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be on or off")
            out.setdefault("render", {})[key] = value
        elif key == "thumbnail":
            from .thumbs import MODES

            if value not in MODES:
                raise ValueError(f"thumbnails must be one of: {', '.join(MODES)}")
            out.setdefault("render", {})["thumbnail"] = value
        elif key == "thumbnail_face":
            if not isinstance(value, bool):
                raise ValueError("the streamer's face in thumbnails must be on or off")
            out.setdefault("render", {})[key] = value
        elif key == "thumbnail_avoid":
            text = " ".join(str(value).split())
            if len(text) > 500:
                raise ValueError("the list of things you do not want in thumbnails is very long; keep it under 500 letters")
            out.setdefault("render", {})[key] = text
        elif key == "speech_language":
            if value not in ("en", "ur", "hi", "auto"):
                raise ValueError("the stream language must be en, ur, hi or auto")
            out.setdefault("transcribe", {})["language"] = value
        elif key == "youtube_captions":
            if value not in ("auto", "off"):
                raise ValueError("YouTube's captions must be auto or off")
            out.setdefault("transcribe", {})["youtube_captions"] = value
        elif key == "caption_script":
            if value not in ("native", "roman"):
                raise ValueError("captions must be native or roman")
            out.setdefault("captions", {})["script"] = value
        elif key == "text_language":
            from .llm import TEXT_LANGUAGES

            if value not in TEXT_LANGUAGES:
                raise ValueError(f"the text language must be one of: {', '.join(TEXT_LANGUAGES)}")
            out.setdefault("llm", {})["text_language"] = value
        elif key == "campaign":
            value = str(value or "")
            if value and not (PROJECT_ROOT / "briefs" / f"{value}.yaml").is_file():
                raise ValueError(f"there is no campaign file briefs/{value}.yaml")
            out.setdefault("render", {})["campaign"] = value
        elif key == "caption_preset":
            if value not in CAPTION_PRESETS:
                raise ValueError(f"unknown caption style {value!r}")
            out.setdefault("captions", {}).update(preset=value, **CAPTION_PRESETS[value][1])
        elif key == "caption_animation":
            from .captions import ANIMATIONS

            if value not in ANIMATIONS:
                raise ValueError(f"the caption movement must be one of: {', '.join(ANIMATIONS)}")
            out.setdefault("captions", {})["animation"] = value
        elif key == "caption_look":
            name = str((value or {}).get("name") or "custom").strip()[:40] or "custom"
            out.setdefault("captions", {}).update(preset=name, **clean_look(value))
        elif key.startswith("brand_"):
            field = key[len("brand_"):]
            b = out.setdefault("brand", {})
            if field in ("logo", "music"):
                path = str(value or "").strip()
                if path and not Path(path).is_file():
                    raise ValueError(f"that {field} file is not there: {path}")
                b[field] = path
            elif field == "logo_corner":
                if value not in ("top-left", "top-right", "bottom-left", "bottom-right"):
                    raise ValueError("the logo corner must be top-left, top-right, bottom-left or bottom-right")
                b[field] = value
            elif field in ("logo_size", "logo_opacity", "music_volume", "outro_seconds", "music_rise_s", "music_peak"):
                lo, hi = {"logo_size": (0.05, 0.5), "logo_opacity": (0.1, 1.0), "music_volume": (0.0, 0.5), "outro_seconds": (2.0, 10.0),
                          "music_rise_s": (0.0, 20.0), "music_peak": (0.05, 0.8)}[field]
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    raise ValueError(f"{field.replace('_', ' ')} must be a number") from None
                b[field] = round(min(hi, max(lo, number)), 3)
            elif field == "outro_text":
                b[field] = " ".join(str(value or "").split())[:60]
            else:
                raise ValueError(f"unknown branding option: {key}")
        elif key == "llm_backend":
            from .llm import CHOICES

            if value not in CHOICES:
                raise ValueError(f"the AI connection must be one of: {', '.join(CHOICES)}")
            out.setdefault("llm", {})["backend"] = value
        elif key == "llm_prefer":
            from .llm import BACKENDS

            if value not in BACKENDS:
                raise ValueError(f"the preferred connection must be one of: {', '.join(BACKENDS)}")
            out.setdefault("llm", {})["prefer"] = value
        elif key == "llm_fallback":
            if not isinstance(value, bool):
                raise ValueError("llm_fallback must be on or off")
            out.setdefault("llm", {})["fallback"] = value
        elif key in ("llm_model", "ai_model"):
            from .llm import PRICES

            if value not in PRICES:
                raise ValueError(f"unknown model {value!r}; choose one of: {', '.join(PRICES)}")
            out.setdefault("llm" if key == "llm_model" else "ai", {})["model"] = value
        elif key == "moment_mix":
            from .scoring import MIXES

            if value not in MIXES:
                raise ValueError(f"the moment mix must be one of: {', '.join(MIXES)}")
            out.setdefault("moments", {})["mix"] = value
        elif key == "hook_seconds":
            try:
                seconds = float(value)
            except (TypeError, ValueError):
                raise ValueError("the opening text's time must be a number of seconds") from None
            if not 2 <= seconds <= 60:
                raise ValueError("the opening text can stay from 2 to 60 seconds")
            out.setdefault("render", {})["hook_seconds"] = seconds
        elif key in ("template", "card_bg", "card_color", "card_color2", "card_image", "post_name", "post_handle"):
            from .frames import CARD_BACKGROUNDS, TEMPLATES

            r = out.setdefault("render", {})
            if key == "template":
                if value not in TEMPLATES:
                    raise ValueError(f"the template must be one of: {', '.join(TEMPLATES)}")
                r[key] = value
            elif key == "card_bg":
                if value not in CARD_BACKGROUNDS:
                    raise ValueError(f"the card background must be one of: {', '.join(CARD_BACKGROUNDS)}")
                r[key] = value
            elif key in ("card_color", "card_color2"):
                colour = str(value or "").lstrip("#").upper()
                if len(colour) != 6 or any(ch not in "0123456789ABCDEF" for ch in colour):
                    raise ValueError("a colour must look like 151225")
                r[key] = colour
            elif key == "card_image":
                path = str(value or "").strip()
                if path and not Path(path).is_file():
                    raise ValueError(f"that image file is not there: {path}")
                r[key] = path
            else:
                r[key] = " ".join(str(value or "").split())[:40]
        elif key == "watch_autostart":
            if not isinstance(value, bool):
                raise ValueError("watch_autostart must be on or off")
            out.setdefault("watch", {})["autostart"] = value
        elif key == "channels":
            from .watch import channel_name

            names = [channel_name(c) for c in str(value).replace(",", " ").split()]
            out.setdefault("watch", {})["channels"] = names
        else:
            raise ValueError(f"unknown option: {key}")
    return out


def campaigns() -> list[dict]:
    """The campaign rule files in briefs/, each with a short plain summary of its rules; a broken file says why."""
    from .brief import load_brief

    out = []
    for f in sorted((PROJECT_ROOT / "briefs").glob("*.yaml")):
        try:
            b = load_brief(f)
            rules = [f"{b.min_duration_s:g} to {b.max_duration_s:g} s" if b.max_duration_s else (f"at least {b.min_duration_s:g} s" if b.min_duration_s else "")]
            rules += [f"needs {' '.join(b.required_tags)}"] if b.required_tags else []
            rules += ["on-screen ad label"] if b.require_disclosure_overlay else []
            rules += [f"{len(b.manual_checks)} thing(s) to check by hand"] if b.manual_checks else []
            out.append({"id": f.stem, "name": b.name, "rules": "; ".join(r for r in rules if r), "problem": ""})
        except Exception as e:
            out.append({"id": f.stem, "name": f.stem, "rules": "", "problem": str(e)})
    return out


class Control:
    """State behind the panel: the watcher process and the last finish run."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self.log_path = cfg.data_dir / "watch.log"
        self.finish_lines: list[str] = []
        self.finishing = False
        self.finish_progress = {"done": 0, "total": 0, "now": ""}
        self.finish_stopping = ""  # "after" or "now" once Stop was pressed while videos are being made
        self.finish_stop = threading.Event()  # set by "Stop now": ends the video tool in the middle
        self.finish_left = 0  # how many approved clips the last run left unmade because it was stopped
        self.watch_problem = ""  # why the watcher is not running although nobody stopped it
        self.run_proc: subprocess.Popen | None = None  # "analyse a finished stream", the same as `clipper run URL`
        self.run_log_path = cfg.data_dir / "run.log"
        self.run_problem = ""
        self.maint = {"name": "", "running": False, "lines": []}  # the one maintenance job: full check, repair or update
        self.health_started = False
        self.update_asked = 0.0  # when GitHub was last asked for a newer version
        # Jobs started from the page say how far they are in this file (see progress.py); the page shows it as a percentage.
        self.progress_path = cfg.data_dir / "progress.json"
        self.finish_progress_path = cfg.data_dir / "progress-finish.json"
        os.environ["CLIPPER_PROGRESS"] = str(self.progress_path)
        self.exit_code = 0  # 42 asks the launcher to start the program again (after an update)
        self.shutdown = None  # set by the server: stops it so the program can exit
        self.ai_checking = False
        self.ai_checked = False  # an automatic check was already started in this session
        self.watch_seen = False
        self.run_seen = False  # a run was started from this page, so its log is worth showing
        self._lock = threading.Lock()

    def reload(self) -> Config:
        self.cfg = load_config()
        return self.cfg

    # ---- options ----

    def status(self) -> dict:
        cfg = self.cfg
        self._notice_death()
        tail: list[str] = []
        if self.watch_seen and self.log_path.exists():  # a log left by an earlier session is not this session's news
            tail = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
        return {
            "mode": parse_mode(cfg.watch.mode), "modes": MODE_TEXT, "channels": " ".join(cfg.watch.channels),
            "watch_autostart": cfg.watch.autostart, "live": self._live(),
            "ab_versions": cfg.render.ab_versions, "opening": cfg.render.opening,
            "thumbnail": cfg.render.thumbnail, "thumbnail_face": cfg.render.thumbnail_face, "thumbnail_avoid": cfg.render.thumbnail_avoid, "thumbnails": __import__("clipper.thumbs", fromlist=["MODES"]).MODES, "chatgpt": self._chatgpt(),
            "campaign": cfg.render.campaign, "campaigns": campaigns(),
            "youtube_captions": cfg.transcribe.youtube_captions,
            "speech_language": cfg.transcribe.language, "caption_script": cfg.captions.script, "text_language": cfg.llm.text_language,
            "caption_preset": cfg.captions.preset,
            "caption_now": {k: getattr(cfg.captions, k) for k in LOOK_KEYS}, "caption_profiles": self.caption_profiles(),
            "caption_animations": __import__("clipper.captions", fromlist=["ANIMATIONS"]).ANIMATIONS,
            "moment_mix": cfg.moments.mix, "hook_seconds": cfg.render.hook_seconds,
            "frame": {**{k: getattr(cfg.render, k) for k in ("template", "card_bg", "card_color", "card_color2", "post_name", "post_handle")},
                      "card_image_name": Path(cfg.render.card_image).name if cfg.render.card_image else "",
                      "templates": __import__("clipper.frames", fromlist=["TEMPLATES"]).TEMPLATES,
                      "backgrounds": __import__("clipper.frames", fromlist=["CARD_BACKGROUNDS"]).CARD_BACKGROUNDS},
            "moment_mixes": list(__import__("clipper.scoring", fromlist=["MIXES"]).MIXES),
            "brand": {**{k: getattr(cfg.brand, k) for k in ("logo", "logo_corner", "logo_size", "logo_opacity", "outro_text", "outro_seconds", "music", "music_volume",
                                                           "music_rise_s", "music_peak")},
                      "logo_name": Path(cfg.brand.logo).name if cfg.brand.logo else "", "music_name": Path(cfg.brand.music).name if cfg.brand.music else ""},
            "caption_presets": {k: {"label": v[0], **v[1]} for k, v in CAPTION_PRESETS.items()},
            "finish_progress": self.finish_progress,
            "watching": self.watching(), "watch_log": tail, "watch_problem": self.watch_problem,
            "finishing": self.finishing, "finish_log": self.finish_lines[-200:], "finish_stopping": self.finish_stopping if self.finishing else "",
            "finish_stopped": bool(self.finish_stopping) and not self.finishing, "finish_left": self.finish_left,
            "running": self.running(), "run_log": self._run_tail(), "run_problem": self.run_problem,
            "run_progress": self._progress(self.progress_path, self.running()),
            "finish_percent": self._progress(self.finish_progress_path, self.finishing),
            "ai": self._ai_status(),
            "health": self._health_status(),
        }

    def _chatgpt(self) -> dict:
        """Whether ChatGPT (Codex) can draw thumbnails here. Asked in the background, so the page never waits for it."""
        from . import thumbs

        # Asked once when the page opens; after that only while ChatGPT thumbnails are switched on (a press on a clip asks again itself).
        if thumbs._status["value"] is None or (self.cfg.render.thumbnail == "chatgpt" and time.time() - thumbs._status["at"] > 60):
            if not getattr(self, "_chatgpt_asking", False):
                self._chatgpt_asking = True

                def ask() -> None:
                    try:
                        thumbs.codex_status(fresh=True)
                    except Exception as e:  # shown as "not ready" with the reason, never a crash of the status check
                        log.error("asking Codex for its sign-in state failed: %s", e, exc_info=True)
                        thumbs._status.update(at=time.time(), value={"installed": False, "signed_in": False, "text": f"Codex could not be asked: {e}"})
                    finally:
                        self._chatgpt_asking = False

                threading.Thread(target=ask, daemon=True, name="codex-status").start()
        return thumbs._status["value"] or {"installed": False, "signed_in": False, "text": "Checking…", "checking": True}

    def _ai_status(self) -> dict:
        from . import llm

        cfg = self.cfg
        checks = llm.read_checks(cfg)
        ways = {b: {"name": name, "missing": llm.static_problem(cfg, b), **checks[b]} for b, name in llm.BACKENDS.items()}
        order = llm.candidates(cfg) if not llm.why_unavailable(cfg) else []
        if not self.ai_checking and not self.ai_checked and any(not w["missing"] and not w["fresh"] for w in ways.values()):
            self.start_ai_check()  # nothing recent is known about a connection that could work: find out, once
        return {"backend": cfg.llm.backend, "backends": llm.CHOICES, "prefer": cfg.llm.prefer, "fallback": cfg.llm.fallback,
                "model": cfg.llm.model, "stream_model": cfg.ai.model, "models": list(llm.PRICES),
                "problem": llm.why_unavailable(cfg), "ways": ways, "using": order[0] if order else "", "note": checks["note"],
                "local": self._local_ai(),
                "checking": self.ai_checking}

    def _live(self) -> dict:
        """Who is live and how hot each chat is, as the watcher last wrote it; empty when nothing is being watched."""
        from .watch import LIVE_FILE
        from .work import read_json

        if not self.watching():
            return {}
        data = read_json(self.cfg.data_dir / LIVE_FILE, {})
        return data.get("channels", {}) if time.time() - float(data.get("at") or 0) < 90 else {}

    def autostart(self) -> None:
        """When the program opens: start watching the saved list by itself, if that was asked for."""
        cfg = self.cfg
        if cfg.watch.autostart and cfg.watch.channels and not self.watching():
            try:
                self.start_watch({})
                log.info("watching started by itself (%s): each creator is clipped whenever they go live", ", ".join(cfg.watch.channels))
            except Exception as e:  # shown on the page; the page itself must still open
                self.watch_problem = f"WATCHING COULD NOT START BY ITSELF: {e}"
                log.error("%s", self.watch_problem)

    def _local_ai(self) -> dict:
        from . import localai

        try:
            return localai.status(self.cfg)
        except Exception as e:  # never take the page down over an optional extra
            log.error("the free local AI could not be looked at: %s", e, exc_info=True)
            return {"supported": False, "why": str(e), "installed": False, "running": False, "has_model": False, "ready": False, "model": self.cfg.llm.local_model}

    def start_local_ai(self) -> dict:
        """Set up the free AI on this computer (Ollama and its model), if this computer can run it."""
        from . import localai

        ok, why = localai.supported(self.cfg)
        if not ok:
            raise ValueError(f"The free local AI is not set up on this computer: {why}. Nothing was downloaded.")

        def work(say) -> None:
            localai.setup(self.reload(), say, step=say.step)

        return self._maintain("Free local AI setup", work)

    def start_ai_check(self) -> dict:
        """Really try each connection that is set up, in the background."""
        from . import llm

        with self._lock:
            if self.ai_checking:
                return {"checking": True}
            self.ai_checking = self.ai_checked = True

        def work() -> None:
            try:
                cfg = self.reload()
                llm._remember(cfg, note="")
                for b in llm.BACKENDS:
                    if not llm.static_problem(cfg, b):
                        llm.check(cfg, b)
            except Exception:
                log.exception("the Claude connection check failed")
            finally:
                self.ai_checking = False

        threading.Thread(target=work, daemon=True, name="ai-check").start()
        return {"checking": True}

    # ---- is this computer ready? ----

    def _health_status(self) -> dict:
        from . import health
        from .work import read_json

        try:
            items = health.quick_checks(self.cfg)
        except Exception as e:  # the check list must never take the page down with it
            log.exception("the setup check failed")
            items = [{"id": "check", "name": "The setup check itself", "state": "bad", "detail": str(e), "fix": "Report this line."}]
        saved = read_json(self.cfg.data_dir / "health.json", {}) or {}
        stale = time.time() - float(saved.get("checked_at") or 0) > 24 * 3600
        if stale and not self.health_started and not self.maint["running"]:
            self.health_started = True
            self.start_full_check()  # once per session, when nothing recent is known
        # GitHub is asked at the start and then every half hour, so the Update button appears by itself soon after a
        # new version is published. It used to be asked once a day, with the full check.
        if time.time() - self.update_asked > 1800 and not self.maint["running"]:
            self.update_asked = time.time()

            def ask() -> None:
                from . import health as h

                try:
                    state = h.check_update(self.cfg)
                    if state.get("behind"):
                        log.info("a newer version is on GitHub: %s change(s) behind%s", state["behind"], f" ({state['why']})" if state.get("why") else "")
                except Exception as e:
                    log.error("GitHub could not be asked for a newer version: %s", e, exc_info=True)

            threading.Thread(target=ask, daemon=True).start()
        upd = saved.get("update") or {}
        return {"items": items, "summary": health.summary(items), "maint": {**self.maint, "lines": self.maint["lines"][-200:]},
                "can_update": bool(upd.get("can")), "launcher": bool(os.environ.get("CLIPPER_LAUNCHER"))}

    def _maintain(self, name: str, work) -> dict:
        with self._lock:
            if self.maint["running"]:
                raise ValueError(f"'{self.maint['name']}' is still running; wait for it to finish")
            self.maint = {"name": name, "running": True, "lines": [f"{time.strftime('%H:%M:%S')} {name} started"], "step": None}

        def say(line: str) -> None:
            self.maint["lines"].append(line)

        def step(index: int, count: int, text: str) -> None:  # step number (from 1) of how many, and what it is
            self.maint["step"] = [index, count, text]
            say(f"step {index} of {count}: {text}")

        say.step = step

        def run() -> None:
            try:
                work(say)
                self.maint["lines"].append(f"{time.strftime('%H:%M:%S')} {name} finished")
            except Exception as e:
                import traceback

                log.exception("%s failed", name)
                self.maint["lines"] += [f"FAILED: {name}: {type(e).__name__}: {e}"] + traceback.format_exc().splitlines()
            finally:
                self.maint["running"] = False

        threading.Thread(target=run, daemon=True, name=name).start()
        return self.status()

    @staticmethod
    def _run_logged(cmd: list[str], say, cwd: str) -> int:
        """Run a command, passing every line it prints to ``say``. Returns its exit code."""
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                text=True, errors="replace")
        for line in proc.stdout:
            if line.strip():
                say(line.rstrip())
        return proc.wait()

    def start_full_check(self) -> dict:
        from . import health

        def work(say) -> None:
            say.step(1, 2, "loading the speech model in a separate process (the slow part)")
            health.full_check(self.reload(), step=lambda text: say.step(2, 2, text))

        return self._maintain("Full check", work)

    def start_repair(self) -> dict:
        """Run the setup step again: it downloads whatever is missing and leaves the rest alone."""
        def work(say) -> None:
            if sys.platform != "win32":
                raise ValueError("Repair runs setup.ps1, which is for Windows")
            code = self._run_logged(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PROJECT_ROOT / "setup.ps1"),
                                     "-DataDir", str(self.cfg.data_dir), "-NoShortcut"], say, str(PROJECT_ROOT))
            if code != 0:
                raise ValueError(f"the setup step ended with exit code {code}; the lines above say why")

        return self._maintain("Repair", work)

    def start_update(self) -> dict:
        """Take the newest version from GitHub (fast-forward only), install what it needs, and restart if the launcher can."""
        from . import health

        if self.watching() or self.running() or self.finishing:
            raise ValueError("something is still running (watching, a job in step 1, or making videos). An update replaces the program's "
                             "files and restarts it, which would cut that work off. Stop it or wait, then update")

        def work(say) -> None:
            say.step(1, 4, "asking GitHub what is new")
            state = health.update_state(fetch=True)
            if not state["can"]:
                # Not a failure: there is simply nothing to do, or this copy must not be changed. Say so plainly.
                say("No update was made: " + (state["why"] or "this copy is already the newest version") + ".")
                health.full_check(self.reload())
                return
            say(f"{state['behind']} change(s) to take from GitHub")
            for n, cmd in enumerate((["git", "merge", "--ff-only", "origin/main"],
                                     ["uv", "sync", "--frozen"] + (["--extra", "gpu"] if (PROJECT_ROOT / ".venv/Lib/site-packages/nvidia/cublas").exists() else []))):
                say.step(n + 2, 4, ("taking the new version", "installing what it needs")[n])
                say("> " + " ".join(cmd))
                if (code := self._run_logged(cmd, say, str(PROJECT_ROOT))) != 0:
                    raise ValueError(f"'{' '.join(cmd)}' ended with exit code {code}; the lines above say why")
            say.step(4, 4, "checking that everything still works")
            health.full_check(self.reload())
            if os.environ.get("CLIPPER_LAUNCHER") and self.shutdown:
                say("updated. Restarting the program now; this page reconnects by itself.")
                self.exit_code = 42
                threading.Timer(1.5, self.shutdown).start()
            else:
                say("updated. Now close the program (Ctrl+C in its terminal) and start it again.")

        return self._maintain("Update", work)

    def restart(self) -> dict:
        """Start the program again (only possible when it was started with the launcher, which waits for this)."""
        if not (os.environ.get("CLIPPER_LAUNCHER") and self.shutdown):
            raise ValueError("this copy was started from a terminal, so it cannot restart itself. Press Ctrl+C there and start it again, "
                             "or start it with Stream-Clipper.cmd next time")
        if self.watching() or self.running() or self.finishing or self.maint["running"]:
            raise ValueError("something is still running (watching, a finished stream, making videos or a check). Stop it or wait, then restart")
        self.exit_code = 42
        threading.Timer(0.8, self.shutdown).start()
        return {"restarting": True}

    def _profiles_path(self) -> Path:
        return self.cfg.data_dir / "caption_profiles.json"

    def caption_profiles(self) -> dict:
        from .work import read_json

        return read_json(self._profiles_path(), {})

    def save(self, raw: dict) -> dict:
        from .work import write_json

        with _SAVE_LOCK:  # two options changed in quick succession must not overwrite each other's saved value
            return self._save(dict(raw), write_json)

    def _save(self, raw: dict, write_json) -> dict:
        # Caption profiles: a look saved under your own name, kept beside your clips (not in the settings file).
        keep, drop = raw.pop("caption_profile_save", None), raw.pop("caption_profile_delete", None)
        if keep is not None:
            name = " ".join(str(keep.get("name") or "").split())[:40]
            if not name:
                raise ValueError("give the profile a name")
            profiles = self.caption_profiles()
            profiles[name] = clean_look(keep)
            write_json(self._profiles_path(), profiles)
            raw["caption_look"] = {**profiles[name], "name": name}
        if drop is not None:
            profiles = self.caption_profiles()
            profiles.pop(str(drop), None)
            write_json(self._profiles_path(), profiles)
        if raw:
            save_local(clean_options(raw))
        self.reload()
        return self.status()

    # ---- watcher ----

    def watching(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _notice_death(self) -> None:
        """A watcher that ended without Stop being pressed is an error: say so in its log and on the page."""
        with self._lock:
            if self.proc is None or self.proc.poll() is None:
                return
            from .hw import describe_exit

            code = self.proc.returncode
            self.proc = None
            self.watch_problem = f"THE WATCHER STOPPED BY ITSELF at {time.strftime('%H:%M:%S')}: {describe_exit(code)}. The lines above it say why."
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(self.watch_problem + "\n")
            log.error(self.watch_problem)

    def start_watch(self, raw: dict) -> dict:
        opts = clean_options({k: raw[k] for k in ("mode", "channels") if k in raw})
        names = opts.get("watch", {}).get("channels") or list(self.cfg.watch.channels)
        if not names:
            raise ValueError("type at least one channel name")
        save_local(opts)
        cfg = self.reload()
        with self._lock:
            if self.watching():
                raise ValueError("already watching; stop it first")
            self.watch_problem, self.watch_seen = "", True
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            out = self.log_path.open("w", encoding="utf-8")
            out.write(f"{time.strftime('%H:%M:%S')} starting: {' '.join(names)} ({cfg.watch.mode})\n")
            out.flush()
            self.proc = subprocess.Popen(
                # -u: unbuffered, so every line reaches the log the moment it is printed.
                [sys.executable, "-u", "-X", "faulthandler", "-c", "from clipper.cli import main; raise SystemExit(main())", "watch", *names, "--mode", cfg.watch.mode],
                stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(PROJECT_ROOT),
            )
        return self.status()

    def clip_now(self, raw: dict) -> dict:
        """A person pressed "clip the last N seconds": pass it to the watcher, which cuts it from what it is recording."""
        from .watch import ask_clip_now

        if not self.watching():
            raise ValueError("nothing is being watched right now, so there is no live stream to clip. Press Start watching first")
        ask_clip_now(self.cfg.data_dir, float(raw.get("seconds") or 30))
        return self.status()

    def stop_watch(self) -> dict:
        with self._lock:
            if self.watching():
                if sys.platform == "win32":
                    # The watcher has children of its own (FFmpeg reading the live feed); end the whole tree.
                    subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True)
                else:
                    self.proc.terminate()
                try:
                    self.proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                self.proc = None  # stopped on purpose: not a death to report
                with self.log_path.open("a", encoding="utf-8") as fh:
                    fh.write(f"{time.strftime('%H:%M:%S')} stopped\n")
        return self.status()

    # ---- a finished stream ----

    def _new_job(self) -> None:
        """Forget the last job's percentage, so a new job never starts by showing the old one's."""
        try:
            self.progress_path.unlink(missing_ok=True)
        except OSError:  # the page is reading it this instant; the new job replaces it within half a second anyway
            pass

    def _progress(self, path: Path, active: bool) -> dict | None:
        from . import progress

        return progress.read(path) if active else None

    def running(self) -> bool:
        return self.run_proc is not None and self.run_proc.poll() is None

    def _run_tail(self) -> list[str]:
        if not self.run_seen or not self.run_log_path.exists():
            return []
        with self._lock:
            if self.run_proc is not None and self.run_proc.poll() is not None:
                code, self.run_proc = self.run_proc.returncode, None
                if code != 0:
                    from .hw import describe_exit

                    self.run_problem = f"ANALYSING THE FINISHED STREAM FAILED at {time.strftime('%H:%M:%S')}: {describe_exit(code)}. The lines above it say why."
                    with self.run_log_path.open("a", encoding="utf-8") as fh:
                        fh.write(self.run_problem + "\n")
                    log.error(self.run_problem)
        return self.run_log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]

    def start_run(self, raw: dict) -> dict:
        from .sources import parse_source

        source = parse_source(str(raw.get("url") or ""))  # a clear ValueError for anything that is not a video link
        top = int(raw.get("top") or 8)
        signals = parse_mode(str(raw.get("signals") or "chat"))
        if not 1 <= top <= 30:
            raise ValueError("the number of clips must be between 1 and 30")
        with self._lock:
            if self.running():
                raise ValueError("a finished stream is already being analysed; wait for it to end")
            self.run_problem, self.run_seen = "", True
            self.run_log_path.parent.mkdir(parents=True, exist_ok=True)
            out = self.run_log_path.open("w", encoding="utf-8")
            out.write(f"{time.strftime('%H:%M:%S')} analysing {source.url}: the best {top} moments, found by {signals}{' + AI reading the speech' if raw.get('ai') else ''}\n")
            out.flush()
            self._new_job()
            self.run_proc = subprocess.Popen(
                [sys.executable, "-u", "-X", "faulthandler", "-c", "from clipper.cli import main; raise SystemExit(main())",
                 "run", source.url, "--top", str(top), "--signals", signals] + (["--ai"] if raw.get("ai") else []),
                stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(PROJECT_ROOT),
            )
        return self.status()

    def _start_job(self, cmd: list[str], first_line: str) -> dict:
        with self._lock:
            if self.running():
                raise ValueError("another job is still running in this box; wait for it to end or press Stop")
            self.run_problem, self.run_seen = "", True
            self.run_log_path.parent.mkdir(parents=True, exist_ok=True)
            out = self.run_log_path.open("w", encoding="utf-8")
            out.write(f"{time.strftime('%H:%M:%S')} {first_line}\n")
            out.flush()
            self._new_job()
            self.run_proc = subprocess.Popen(
                [sys.executable, "-u", "-X", "faulthandler", "-c", "from clipper.cli import main; raise SystemExit(main())", *cmd],
                stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(PROJECT_ROOT),
            )
        return self.status()

    def start_range(self, raw: dict) -> dict:
        """Download one chosen part of a video for the cutting board (the same as ``clipper range``)."""
        from .pipeline import MIN_PART_S, parse_clock
        from .sources import parse_source

        source = parse_source(str(raw.get("source") or ""))
        start = parse_clock(raw.get("start") or 0)
        end = None if raw.get("to_end") else parse_clock(raw.get("end") or 0)
        if end is not None:
            if end <= start:
                raise ValueError("the end time must be after the start time")
            if end - start < MIN_PART_S:
                raise ValueError(f"this part is shorter than {MIN_PART_S:g} seconds, too short for a clip")
            if end - start > self.cfg.fetch.max_range_minutes * 60 + 1:
                raise ValueError(f"the cutting board takes at most {self.cfg.fetch.max_range_minutes:g} minutes at a time; choose a shorter part, or do it in two goes")
        cmd = ["range", source.url, "--start", f"{start:g}"] + (["--end", f"{end:g}"] if end is not None else [])
        return self._start_job(cmd, f"downloading the part you chose from {source.url}")

    def start_range_cut(self, raw: dict) -> dict:
        """Make clips from the pieces marked on the cutting board (the same as ``clipper cut --range``)."""
        import re

        from .pipeline import check_segments
        from .work import read_json

        m = re.fullmatch(r"([A-Za-z0-9_-]+)/([0-9a-z-]+)", str(raw.get("key") or ""))
        folder = self.cfg.data_dir / "work" / m.group(1) / "ranges" / m.group(2) if m else None
        info = read_json(folder / "range.json", {}) if folder else {}
        if not info.get("ready") or not (folder / "range.mp4").exists():
            raise ValueError("this downloaded part is not there any more; download it again")
        if not info.get("url") or not info.get("duration_s"):
            raise ValueError("this downloaded part's notes are damaged; delete it on the cutting board and download it again")
        pieces = check_segments(raw.get("pieces"), float(info["duration_s"]))  # refuse a bad piece now, with its reason
        cmd = ["cut", info["url"], "--range", m.group(2), "--pieces", ",".join(f"{a:g}-{b:g}" for a, b in pieces)]
        cmd += ["--no-part-label"] if raw.get("part_label") is False else []
        return self._start_job(cmd, f"making {len(pieces)} clip(s) from the cutting board")

    def start_cut(self, raw: dict) -> dict:
        """Make clips from a range the person chose (the same as ``clipper cut``), as the one finished-stream job."""
        from .pipeline import parse_clock, split_range
        from .sources import parse_source

        source = parse_source(str(raw.get("source") or ""))
        if raw.get("ranges"):
            # Several separate stretches picked from the transcript or from a chatbot's answers: each its own clip.
            from .pipeline import MAX_PART_S, MIN_PART_S

            pairs = []
            for item in raw["ranges"]:
                try:
                    a, b = float(item[0]), float(item[1])
                except (TypeError, ValueError, IndexError):
                    raise ValueError("each stretch needs a start and an end") from None
                if not MIN_PART_S - 0.05 <= b - a <= MAX_PART_S + 0.05:
                    raise ValueError(f"a stretch is {b - a:.0f} seconds long; a clip is {MIN_PART_S:g} to {MAX_PART_S:.0f} seconds")
                pairs.append(f"{a:g}-{b:g}")
            if len(pairs) > 30:
                raise ValueError("at most 30 clips at a time")
            return self._start_job(["cut", source.url, "--ranges", ",".join(pairs)], f"making {len(pairs)} clip(s) you picked from {source.url}")
        start = parse_clock(raw.get("start") or 0)
        end = None if raw.get("to_end") else parse_clock(raw.get("end") or 0)
        if end is not None and end <= start:
            raise ValueError("the end time must be after the start time")
        parts = int(raw.get("parts") or 1)
        part_s = float(raw.get("part_seconds")) if raw.get("part_seconds") else None
        if end is not None:
            split_range(start, end, parts, part_s)  # refuse an impossible split now, with its reason, not in the log later
        cmd = ["cut", source.url, "--start", f"{start:g}", "--parts", str(parts)]
        cmd += ["--end", f"{end:g}"] if end is not None else []
        cmd += ["--part-seconds", f"{part_s:g}"] if part_s else []
        cmd += ["--no-part-label"] if raw.get("part_label") is False else []
        with self._lock:
            if self.running():
                raise ValueError("another job is still running in this box; wait for it to end or press Stop")
            self.run_problem, self.run_seen = "", True
            self.run_log_path.parent.mkdir(parents=True, exist_ok=True)
            out = self.run_log_path.open("w", encoding="utf-8")
            out.write(f"{time.strftime('%H:%M:%S')} making your own clip(s) from {source.url}\n")
            out.flush()
            self._new_job()
            self.run_proc = subprocess.Popen(
                [sys.executable, "-u", "-X", "faulthandler", "-c", "from clipper.cli import main; raise SystemExit(main())", *cmd],
                stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(PROJECT_ROOT),
            )
        return self.status()

    def stop_run(self) -> dict:
        with self._lock:
            if self.running():
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(self.run_proc.pid), "/T", "/F"], capture_output=True)
                else:
                    self.run_proc.terminate()
                self.run_proc = None  # stopped on purpose: not a failure to report
                with self.run_log_path.open("a", encoding="utf-8") as fh:
                    fh.write(f"{time.strftime('%H:%M:%S')} stopped\n")
        return self.status()

    # ---- finish ----

    def start_finish(self, raw: dict | None = None) -> dict:
        """Make the videos of every approved clip, or of one (``only``: "work folder/clip id", from that clip's own button)."""
        only = None
        if (raw or {}).get("only"):
            m = re.fullmatch(r"([A-Za-z0-9_-]+)/([0-9a-z]+)", str(raw["only"]))
            if not m:
                raise ValueError("that is not a clip of this program")
            only = (m.group(1), m.group(2))
            from .work import read_json

            if read_json(self.cfg.data_dir / "work" / only[0] / "clips" / only[1] / "clip.json", {}).get("status") != "approved":
                raise ValueError("approve this clip first; only approved clips are made into videos")
        with self._lock:
            if self.finishing:
                raise ValueError("videos are already being made; wait for that to end, or press Stop")
            self.finishing = True
            self.finish_stopping = ""
            self.finish_left = 0
            self.finish_stop.clear()
            self.finish_lines = [f"{time.strftime('%H:%M:%S')} " + ("making the videos of one clip..." if only else "rendering approved clips...")]
        threading.Thread(target=self._finish, args=(only,), daemon=True, name="finish").start()
        return self.status()

    def stop_finish(self, raw: dict | None = None) -> dict:
        """Stop making videos: "after" lets the clip being made finish first, "now" ends it in the middle."""
        how = str((raw or {}).get("how") or "")
        if how not in ("after", "now"):
            raise ValueError("say how to stop: after this video, or now")
        with self._lock:
            if not self.finishing:
                raise ValueError("no videos are being made right now")
            if self.finish_stopping != how and self.finish_stopping != "now":
                self.finish_lines.append(f"{time.strftime('%H:%M:%S')} " + ("you pressed Stop: ending now, in the middle of this clip"
                                                                           if how == "now" else "you pressed Stop: this clip is finished first, then it ends"))
                self.finish_stopping = how
            if how == "now":
                self.finish_stop.set()
        return self.status()

    def open_ready(self) -> dict:
        """Show the folder with the finished videos (today's, when there is one) in the file manager."""
        root = self.cfg.data_dir / "ready"
        day = root / time.strftime("%Y-%m-%d")
        folder = day if day.is_dir() else root
        if not folder.is_dir():
            raise ValueError(f"no videos have been made yet, so the folder {root} does not exist")
        try:
            if sys.platform == "win32":
                os.startfile(str(folder))  # noqa: S606 - the program's own folder, on the user's own PC
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)], stdin=subprocess.DEVNULL)
        except OSError as e:
            raise ValueError(f"the folder could not be opened ({e}). It is here: {folder}") from e
        return {"folder": str(folder)}

    def _finish(self, only: tuple[str, str] | None = None) -> None:
        from . import tools
        from .pipeline import stage_finish

        try:
            problems: list[str] = []
            left: list[str] = []
            self.finish_progress = {"done": 0, "total": 0, "now": ""}
            tools.stop_on(self.finish_stop)  # "Stop now" ends the video tool this thread is waiting for

            from . import progress as report

            report.begin([("draft", "Making the videos", 1)], path=self.finish_progress_path)
            report.stage("draft")

            def progress(done: int, total: int, now: str) -> None:
                self.finish_progress = {"done": done, "total": total, "now": now}
                if done >= total:  # between clips the bar is moved by the work itself, step by step (see stage_finish)
                    report.finish()

            cfg = self.reload()
            brief = str(PROJECT_ROOT / "briefs" / f"{cfg.render.campaign}.yaml") if cfg.render.campaign else None
            results = stage_finish(cfg, brief_path=brief, problems=problems, progress=progress, only=only,
                                   stop=lambda: bool(self.finish_stopping), left=left)
            self.finish_lines += [f"READY {video}" if ok else f"NOT MADE to the campaign's rules: {video} (see its text file for what to fix)"
                                  for video, ok in results]
            self.finish_lines += [f"NOT MADE {p}" for p in problems]
            if self.finish_stopping == "after" and not left:
                self.finish_stopping = ""  # the clip it was to stop after was the last one: everything was made, nothing was cut short
            if self.finish_stopping:
                self.finish_left = len(left)
                self.finish_lines += [f"left for later (still approved): {name}" for name in left]
                self.finish_lines.append(f"{time.strftime('%H:%M:%S')} stopped by you: {len(results)} file(s) made in {self.cfg.data_dir / 'ready'}, "
                                         f"{len(left)} clip(s) left approved for next time")
            else:
                self.finish_lines.append(f"{time.strftime('%H:%M:%S')} done: {len(results)} file(s) in {self.cfg.data_dir / 'ready'}")
        except Exception as e:  # shown on the page instead of killing the server
            import traceback

            log.exception("finish failed")
            self.finish_lines += [f"FAILED: {type(e).__name__}: {e}"] + traceback.format_exc().splitlines()
        finally:
            tools.stop_on(None)
            self.finishing = False
