"""Thumbnails: one cover picture per clip (cover.jpg, 1080x1920), for Shorts, Reels and TikTok. Optional.

Three ways to make one:

frame     free, no AI: the sharpest frame of the clip in its vertical layout, with the opening text in big letters.
chatgpt   ChatGPT draws it. Frames of the clip, its title and what is said are given to OpenAI's Codex command-line
          tool (``codex exec``), which the person signed in to with their own ChatGPT plan. It works out what the
          picture should be and makes it. No prompt has to be written.
prompt    the same, but the picture is described by the person in their own words.

Like Claude Code, Codex is only ever *run*: this program never reads its sign-in files. Frames and text of the clip are
sent to OpenAI when ChatGPT is used, and each use is written to usage.jsonl.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from pathlib import Path

from .config import Config
from .tools import ToolError, find_tool, run
from .work import done, read_json, update_clip

log = logging.getLogger(__name__)

MODES = {"off": "Off", "frame": "From the clip, with the opening text (free)", "chatgpt": "Drawn by ChatGPT (uses your ChatGPT plan)"}
COVER = "cover.jpg"
SETUP = ("Install it once: open a terminal, run  npm install -g @openai/codex  and then  codex login  "
         "and sign in with your ChatGPT account. A paid ChatGPT plan is needed for pictures")

_status: dict = {"at": 0.0, "value": None}


def codex_status(fresh: bool = False) -> dict:
    """Whether ChatGPT (through Codex) can be used on this computer: {"installed", "signed_in", "text"}. Asked at most once a minute."""
    if not fresh and _status["value"] is not None and time.time() - _status["at"] < 60:
        return _status["value"]
    exe = shutil.which("codex")
    if not exe:
        value = {"installed": False, "signed_in": False, "text": "Codex (ChatGPT's command-line tool) is not installed on this computer. " + SETUP}
    else:
        try:
            proc = run([exe, "login", "status"], timeout_s=30, what="asking Codex whether it is signed in")
            said = " ".join((proc.stdout + " " + proc.stderr).split())
            ok = "logged in" in said.lower() and "not logged in" not in said.lower()
            value = {"installed": True, "signed_in": ok,
                     "text": ("Ready: " + said) if ok else f"Codex is installed but not signed in ({said[:200]}). Open a terminal and run  codex login"}
        except ToolError as e:
            value = {"installed": True, "signed_in": False, "text": f"Codex is installed but did not answer ({str(e)[:200]}). Open a terminal and run  codex login"}
    _status.update(at=time.time(), value=value)
    return value


def cover_info(d: Path, state: dict) -> dict:
    """What the page shows about a clip's thumbnail."""
    f = d / COVER
    return {"made": f.exists(), "by": state.get("cover_by") or "", "at": state.get("cover_at") or "", "words": state.get("cover_words") or "",
            "error": state.get("cover_error") or "", "prompt": state.get("cover_prompt") or "", "mtime": int(f.stat().st_mtime * 1000) if f.exists() else 0}


# ---- the free one: a frame of the clip with the opening text ----

def _sharpness(path: Path) -> float:
    import cv2

    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    return float(cv2.Laplacian(img, cv2.CV_64F).var()) if img is not None else 0.0


def _ass_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[{}\\\r\n]", " ", text)).strip()


def cover_ass(words: str, font: str, accent: str = "&H0000D4FF", top: int = 190) -> str:
    """Subtitle-file text that puts ``words`` in big letters starting ``top`` pixels down; the last word is in the accent colour."""
    words = _ass_text(words).upper()
    size = 168 if len(words) <= 14 else (136 if len(words) <= 28 else (108 if len(words) <= 48 else 86))
    parts = words.rsplit(" ", 1)
    shown = (parts[0] + " {\\c" + accent + "&}" + parts[1]) if len(parts) == 2 else "{\\c" + accent + "&}" + words
    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: T,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,11,5,8,70,70,{top},1", "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 0,0:00:00.00,0:10:00.00,T,,0,0,0,,{shown}", ""])


def frame_cover(d: Path, cfg: Config) -> Path:
    """cover.jpg from the clip itself: its sharpest frame in the vertical layout, with the opening text on it. No AI."""
    from .render import cut_window, still_frame

    state = read_json(d / "clip.json")
    if not done(d / "source.mp4") or "source_width" not in state:
        raise ToolError("this clip has no finished draft yet, so there is no picture to make a thumbnail from")
    _offset, duration = cut_window(state)
    best, best_score = None, -1.0
    for n, part in enumerate((0.08, 0.2, 0.32, 0.45)):
        still = still_frame(d, cfg, at_s=max(0.2, duration * part), out_name=f"cover_try{n}.png")
        score = _sharpness(still)
        if score > best_score:
            best, best_score = still, score
    words = str(state.get("hook") or state.get("title") or "").strip()
    try:
        ffmpeg = find_tool("ffmpeg", cfg.data_dir)
        cmd = [ffmpeg, "-y", "-hide_banner", "-v", "error", "-i", best.name]
        if words:
            # With the face in one half and the video in the other, the words sit where the two meet, not across the face.
            split = str(state.get("layout") or cfg.render.layout) in ("split", "split_under")
            (d / "cover.ass").write_text(cover_ass(words, cfg.captions.font, top=560 if split else 190), encoding="utf-8")
            cmd += ["-vf", f"scale={cfg.render.width}:{cfg.render.height},eq=saturation=1.25:contrast=1.06,ass=cover.ass"]
        else:
            cmd += ["-vf", f"scale={cfg.render.width}:{cfg.render.height},eq=saturation=1.25:contrast=1.06"]
        run(cmd + ["-frames:v", "1", "-q:v", "2", "cover.part.jpg"], timeout_s=120, what=f"thumbnail of clip {d.name}", cwd=d)
        (d / "cover.part.jpg").replace(d / COVER)
    finally:
        for f in list(d.glob("cover_try*.png")) + [d / "cover.ass"]:
            f.unlink(missing_ok=True)
    update_clip(d / "clip.json", {"cover_by": "frame", "cover_at": time.strftime("%Y-%m-%d %H:%M:%S"), "cover_words": words}, drop=("cover_error", "cover_error_mode", "cover_prompt"))
    log.info("clip %s: thumbnail made from its own frame", d.name)
    return d / COVER


# ---- the one ChatGPT draws ----

RULES = """Rules for the picture, whatever else is asked:
- ONE image, vertical 9:16, saved in the current folder as cover.png, exactly 1080x1920 pixels.
- It is the thumbnail (cover) of a short vertical video for YouTube Shorts, TikTok and Instagram Reels: it must make someone stop scrolling. Bright, sharp, high contrast, one clear subject, a strong emotion or action.
- The attached pictures are frames from the real video, in order. Keep the real people, the game or place and what happens recognisable, so the thumbnail is honest about the video. Do not add people who are not in the frames.
- No logos, no watermarks, no app buttons, no borders, nothing that looks like a platform's own interface.
- Keep text and faces out of the bottom fifth and away from the right edge: apps cover those parts with their buttons.
Use the image generation skill to make it. When the file is saved, reply with one line only: the words you put on the picture (or "no text")."""


def auto_prompt(state: dict) -> str:
    """What ChatGPT is told when the person wrote nothing: the clip's own facts, and the job of choosing the picture itself."""
    said = " ".join(str(state.get("transcript") or "").split())[:700]
    facts = [f"Streamer or channel: {state.get('channel') or 'unknown'}",
             f"Title of the video: {state.get('title') or '(none)'}",
             f"Text shown at the start of the video: {state.get('hook') or '(none)'}",
             f"Why this moment was picked: {state.get('ai_pick') or state.get('ai_reason') or '(not recorded)'}",
             f"What is said in it: {said or '(no speech)'}"]
    return ("$imagegen Design and make the thumbnail for this short video. You decide what the picture shows: look at the frames, "
            "work out the single most eye-catching moment, and build the thumbnail around it.\n\n" + "\n".join(facts) + "\n\n"
            "Put 2 to 4 words of very large, bold text in the top third (white or yellow with a thick black outline), in the same language and "
            "alphabet as the title. The words must tease what happens, not repeat the whole title.\n\n" + RULES)


def own_prompt(text: str) -> str:
    """The person's own description of the picture, with the fixed rules that make the result usable."""
    return ("$imagegen Make a thumbnail picture exactly as described here by the person who made the video:\n\n" + text.strip() + "\n\n"
            "Where the description leaves something open, choose what makes the best thumbnail.\n\n" + RULES)


def _reference_frames(d: Path, cfg: Config, work: Path) -> list[str]:
    """Three frames of the clip as it was streamed, copied into the folder Codex works in."""
    from .render import cut_window

    state = read_json(d / "clip.json")
    offset, duration = cut_window(state)
    names = []
    for n, part in enumerate((0.15, 0.5, 0.85)):
        name = f"frame{n + 1}.jpg"
        run([find_tool("ffmpeg", cfg.data_dir), "-y", "-hide_banner", "-v", "error", "-ss", f"{offset + duration * part:.2f}", "-i", str(d / "source.mp4"),
             "-frames:v", "1", "-vf", "scale=1280:-2", "-q:v", "3", name], timeout_s=60, what=f"frame of clip {d.name}", cwd=work)
        if done(work / name):
            names.append(name)
    if not names:
        raise ToolError("no frame could be taken from this clip's video, so there is nothing to show ChatGPT")
    return names


def fit_cover(src: Path, out: Path, width: int = 1080, height: int = 1920) -> None:
    """Save any picture as a 9:16 JPEG of the right size: the middle is kept, the overhang cut off."""
    import cv2

    img = cv2.imread(str(src))
    if img is None:
        raise ToolError(f"the picture {src.name} could not be opened")
    h, w = img.shape[:2]
    if w * height > h * width:  # too wide
        keep = int(round(h * width / height))
        img = img[:, (w - keep) // 2: (w - keep) // 2 + keep]
    else:
        keep = int(round(w * height / width))
        img = img[(h - keep) // 2: (h - keep) // 2 + keep, :]
    img = cv2.resize(img, (width, height), interpolation=cv2.INTER_AREA if img.shape[1] > width else cv2.INTER_CUBIC)
    part = out.with_name(out.stem + ".part.jpg")
    if not cv2.imwrite(str(part), img, [cv2.IMWRITE_JPEG_QUALITY, 93]):
        raise ToolError(f"the thumbnail could not be written to {out}")
    part.replace(out)


def chatgpt_cover(d: Path, cfg: Config, prompt: str = "") -> Path:
    """cover.jpg drawn by ChatGPT through the Codex tool. ``prompt`` empty = it decides the picture itself from the clip."""
    exe = shutil.which("codex")
    status = codex_status()
    if not exe or not status["signed_in"]:
        status = codex_status(fresh=True)
        if not exe or not status["signed_in"]:
            raise ToolError("ChatGPT cannot be used for thumbnails yet: " + status["text"])
    state = read_json(d / "clip.json")
    if not done(d / "source.mp4"):
        raise ToolError("this clip has no downloaded video, so there are no frames to show ChatGPT")
    work = d / "cover_work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        frames = _reference_frames(d, cfg, work)
        text = own_prompt(prompt) if prompt.strip() else auto_prompt(state)
        cmd = [exe, "exec", "--skip-git-repo-check", "--ephemeral", "-s", "workspace-write"]
        for name in frames:
            cmd += ["-i", name]
        cmd += ["-o", "reply.txt", "-"]  # the request is given on the input, so no quoting can break it
        t0 = time.monotonic()
        try:
            proc = run(cmd, timeout_s=900, what="ChatGPT (Codex) making a thumbnail", cwd=work, input_text=text)
        except ToolError as e:
            raise ToolError(f"{e}. If it mentions signing in or a limit: run  codex login  in a terminal, or your ChatGPT plan's limit is reached") from e
        made = [f for f in work.iterdir() if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and f.name not in frames]
        if not made:
            said = " ".join((work / "reply.txt").read_text(encoding="utf-8", errors="replace").split())[:300] if (work / "reply.txt").exists() else ""
            raise ToolError("ChatGPT answered but saved no picture" + (f". It said: {said}" if said else "")
                            + ". Pictures need a paid ChatGPT plan with usage left; try again, or use the free thumbnail from the clip")
        picked = next((f for f in made if f.name == "cover.png"), max(made, key=lambda f: f.stat().st_size))
        fit_cover(picked, d / COVER, cfg.render.width, cfg.render.height)
        words = (work / "reply.txt").read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1][:120] if (work / "reply.txt").exists() else ""
        tokens = re.search(r"tokens used\s*([\d,]+)", (proc.stdout or "") + (proc.stderr or ""))
        entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "service": "codex (your ChatGPT plan, not billed per use)", "what": "thumbnail",
                 "clip": d.name, "tokens": int(tokens.group(1).replace(",", "")) if tokens else None, "seconds": round(time.monotonic() - t0)}
        usage = cfg.data_dir / "usage.jsonl"
        usage.parent.mkdir(parents=True, exist_ok=True)
        with usage.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        log.info("AI use through your ChatGPT plan: %s", entry)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    update_clip(d / "clip.json", {"cover_by": "prompt" if prompt.strip() else "chatgpt",
                                  "cover_at": time.strftime("%Y-%m-%d %H:%M:%S"), "cover_words": words, "cover_prompt": prompt.strip()[:2000]},
                drop=("cover_error", "cover_error_mode"))
    return d / COVER


def make_cover(d: Path, cfg: Config, how: str, prompt: str = "") -> Path:
    """Make a clip's thumbnail: ``how`` is "frame", "chatgpt" or "prompt" (ChatGPT with the person's own words)."""
    if how == "frame":
        return frame_cover(d, cfg)
    if how == "chatgpt":
        return chatgpt_cover(d, cfg)
    if how == "prompt":
        if not prompt.strip():
            raise ValueError("describe the picture you want first, or use the automatic button")
        if len(prompt) > 2000:
            raise ValueError("that description is very long; keep it under 2000 letters")
        return chatgpt_cover(d, cfg, prompt)
    raise ValueError("a thumbnail is made from the clip, by ChatGPT, or from your own description")


def export_cover(d: Path, video: Path) -> Path | None:
    """Put the clip's thumbnail beside its finished video in the ready folder, under the same name. None when there is none."""
    src = d / COVER
    if not src.exists() or not video.parent.is_dir():
        return None
    out = video.with_suffix(".jpg")
    tmp = out.with_name(out.name + ".part")
    shutil.copyfile(src, tmp)
    tmp.replace(out)
    return out
