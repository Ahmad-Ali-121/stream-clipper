"""Load config.yaml (+ optional config.local.yaml) into typed settings.

Every tunable lives here so no module hard-codes paths or thresholds.
Unknown keys are an error: a typo in the config should fail loudly, not be ignored.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(ValueError):
    pass


@dataclass
class PathSettings:
    data_dir: str = "data"


@dataclass
class MomentSettings:
    bucket_s: float = 10.0
    window_s: float = 300.0
    mode: str = "symmetric"  # "symmetric" (VOD) or "trailing" (live)
    count_mode: str = "chatters"  # "chatters" or "messages"
    min_activity: int = 5
    min_score: float = 2.0
    min_neighbors: int = 6
    gap_s: float = 90.0
    top: int = 5
    before_s: float = 20.0
    after_s: float = 10.0
    hype_weight: float = 0.5
    audio_weight: float = 0.5
    hype_spikes: bool = True  # also score spikes in hype-word messages (for saturated chats)
    hype_spike_min_ratio: float = 0.15  # share of a bucket's messages that must be hype for that to count
    skip_start_s: float = 120.0  # ignore the greeting flood at the start of a stream
    # What kind of moments are wanted: balanced, action (loud, fast, shouted) or dialogue (story, humour, what is said).
    mix: str = "balanced"
    signals: str = "chat"  # finished streams: chat, audio (chat + sound), video (chat + picture) or all
    hype_words: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if self.mode not in ("symmetric", "trailing"):
            raise ConfigError(f"moments.mode must be 'symmetric' or 'trailing', got {self.mode!r}")
        if self.count_mode not in ("chatters", "messages"):
            raise ConfigError(f"moments.count_mode must be 'chatters' or 'messages', got {self.count_mode!r}")
        if self.bucket_s <= 0:
            raise ConfigError("moments.bucket_s must be greater than 0")
        if self.window_s < self.bucket_s:
            raise ConfigError("moments.window_s must be at least one bucket long")
        if self.top < 1:
            raise ConfigError("moments.top must be at least 1")
        if self.before_s < 0 or self.after_s <= 0:
            raise ConfigError("moments.before_s must be >= 0 and moments.after_s must be > 0")


@dataclass
class CaptionSettings:
    preset: str = "bold"  # the look chosen on the page; "custom" when the values below were set by hand
    script: str = "roman"  # for Urdu or Hindi speech: "roman" = Latin letters ("bhai ne kamaal kar diya"), "native" = Urdu or Devanagari letters
    play_res_x: int = 1080
    play_res_y: int = 1920
    font: str = "Arial Black"
    font_size: int = 88
    text_color: str = "FFFFFF"
    active_color: str = "FFE600"
    outline_color: str = "000000"
    outline: int = 6
    shadow: int = 2
    active_scale: int = 112
    margin_v: int = 520
    uppercase: bool = True
    max_words: int = 3
    max_chars: int = 18
    max_gap_s: float = 0.6
    min_word_s: float = 0.12
    # How the words move: highlight = the spoken word changes colour; pop = it also pops in; word = one big word at a
    # time; build = the line fills in word by word as it is spoken.
    animation: str = "highlight"


@dataclass
class BrandSettings:
    """Your own mark on every clip. All optional; empty means off."""

    logo: str = ""  # an image file (PNG with a clear background is best), shown for the whole clip
    logo_corner: str = "top-left"
    logo_size: float = 0.16  # its width as a share of the video's width
    logo_opacity: float = 0.9
    outro_text: str = ""  # a ribbon over the last seconds, e.g. "Follow for part 2"
    outro_seconds: float = 4.0
    music: str = ""  # a music file played quietly under the voice (use only music you have the right to use)
    music_volume: float = 0.12
    music_rise_s: float = 0.0  # over the last this many seconds the music grows louder (0 = it stays level)
    music_peak: float = 0.35  # how loud it gets at the very end, when it rises


@dataclass
class FetchSettings:
    pad_s: float = 5.0  # extra seconds fetched on each side of a moment; cuts can slide this far to reach a pause
    max_height: int = 1080
    retries: int = 5
    retry_wait_s: float = 10.0
    timeout_s: float = 1800.0
    # The longest part of a video the cutting board downloads in one go. It is for a part you chose, never a whole stream.
    max_range_minutes: float = 30.0


@dataclass
class TranscribeSettings:
    backend: str = "local"  # "local" (faster-whisper on this machine) or "groq" (paid API, needs GROQ_API_KEY)
    model: str = "large-v3-turbo"  # local model; "small" is about 10x faster on CPU but drops and mishears more
    # Voice-activity filtering skips "silence" before transcribing. On streams it wrongly
    # removed speech under game audio (measured: 34 of 55 words kept), so it is off.
    vad_filter: bool = False
    min_word_prob: float = 0.35  # drop words the model itself is unsure of (stray "You" in silence)
    compute_type: str = "auto"  # auto, or int8 / int8_float16 / float16
    device: str = "auto"  # auto = NVIDIA GPU when usable, else CPU; or force "cpu" / "cuda"
    language: str = "en"  # what is spoken on the stream: en, ur (Urdu), hi (Hindi), or auto (detect; can mislabel mixed speech)
    groq_model: str = "whisper-large-v3-turbo"
    # auto = for YouTube videos use YouTube's own captions when it has them (seconds, not minutes), else listen here.
    # off = always listen with the speech model.
    youtube_captions: str = "auto"


@dataclass
class RenderSettings:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    # crop = video only; split = face on top, video below; split_under = face below; blur = whole picture on a blurred
    # background; pip = full-height video with the face in a corner; circle = the same with a round face
    layout: str = "crop"
    crop_x: float = 0.5  # horizontal centre of the crop, 0 (left) to 1 (right)
    # Facecam rectangle in the source, as fractions of width/height: x, y, w, h. Used by the split layout.
    facecam: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.25, 0.25])
    # The part of the picture shown as "the video" (below the face in split, the whole clip in crop): x, y, w, h.
    # Empty = worked out automatically from crop_x. Set per clip with the boxes on the review page.
    game: list[float] = field(default_factory=list)
    split_top: float = 0.38  # share of the output height the facecam gets
    # A frame around the finished clip: none, card, xpost, xpost_dark, news or hookbox (see frames.py)
    template: str = "none"
    card_bg: str = "blur"  # what is behind the card: blur (a blurred copy of the video), color, gradient or image
    card_color: str = "151225"
    card_color2: str = "FF3D8B"  # the second colour of the gradient
    card_image: str = ""
    post_name: str = ""  # the name shown on the post templates (empty = the streamer's name)
    post_handle: str = ""  # and the @handle under it
    face_corner: str = "top-right"  # where the small face sits in the "pip" and "circle" layouts
    face_size: float = 0.36  # how wide that small face is, as a share of the video's width
    encoder: str = "auto"  # auto = NVIDIA, Intel or AMD hardware encoder when one works, else libx264 (CPU)
    preset: str = "veryfast"
    crf: int = 20
    audio_bitrate: str = "192k"
    loudness_lufs: float = -14.0
    hook_seconds: float = 3.0  # how long the hook text stays on screen
    campaign: str = ""  # name of a file in briefs/ (without .yaml): every clip is checked against that campaign's rules
    opening: bool = True  # eye-catching start: quick zoom-in, hook pops in, whoosh, progress bar
    ab_versions: bool = True  # finish makes two versions of each approved clip (A and B) with different hooks
    # A cover picture per clip, made with its videos: "off", "frame" (free: a frame of the clip with the opening text)
    # or "chatgpt" (drawn by ChatGPT through the Codex tool, on the person's own ChatGPT plan)
    thumbnail: str = "off"
    thumbnail_face: bool = True  # ChatGPT thumbnails: the streamer's own face, big and excited, when it is in the clip
    thumbnail_avoid: str = ""  # ChatGPT thumbnails: what the person never wants in the picture, in their own words
    hook_font_size: int = 76
    credit_font_size: int = 40
    disclosure_text: str = "Ad"
    disclosure_seconds: float = 4.0
    font_file: str = "C:/Windows/Fonts/arialbd.ttf"


@dataclass
class TrendSettings:
    enabled: bool = True
    seeds: list[str] = field(default_factory=lambda: ["gta6"])  # always researched, besides the streamer's own tag
    cache_hours: float = 6.0
    max_tags: int = 6  # hashtags per post


@dataclass
class LlmSettings:
    enabled: bool = True  # only takes effect when the chosen backend can be reached
    backend: str = "auto"  # "auto" = whichever works; "api" = Claude API key in .env, paid per use; "claude-code" = the Claude Code command on this PC, using your own Claude plan
    prefer: str = "claude-code"  # with "auto" and both available, the one tried first
    fallback: bool = True  # with "auto": if the first fails (a plan limit, a rejected key), use the other and say so
    model: str = "claude-opus-5-5"  # "claude-haiku-5-5" costs about 40 times less and is usually enough here
    effort: str = "low"
    text_language: str = "English"  # what the opening text, titles and description are written in: English, Roman Urdu, Hinglish, Urdu or Hindi
    # The free AI that runs on this computer (Ollama), used for clip text when Claude cannot be reached.
    # auto = use it if it is installed; off = never. It is only ever set up on a computer that can run it.
    local: str = "auto"
    local_model: str = "llama3.2:3b"
    # Turning Urdu or Hindi captions into Latin letters is a simple job done for every clip: the small model does it
    # in a fraction of the time and of your plan's usage. Set it to the same model as above to go back.
    roman_model: str = "claude-haiku-5-5"
    # The built-in help assistant. It reads the whole guide with every question, so a quick model keeps answers snappy.
    help_model: str = "claude-sonnet-5-5"
    look_at_frames: bool = True  # also show AI three small frames of each clip, so it can rate what is on screen, not only the words


@dataclass
class AiSettings:
    """AI that reads a whole finished stream's speech and picks moments from the words (run --ai)."""

    model: str = "claude-haiku-5-5"  # reads the whole stream's text: a few cents. "claude-opus-5-5" chooses better, about $0.30 a stream
    speech_model: str = "auto"  # auto = the usual model on an NVIDIA card, "small" on a processor (several times faster)
    max_hours: float = 0.0  # read only the first hours of a stream; 0 = all of it


@dataclass
class VoiceSettings:
    backend: str = "kokoro"  # "kokoro" = free, runs on this machine; "elevenlabs" = paid, needs ELEVENLABS_API_KEY
    kokoro_voice: str = "am_michael"
    speed: float = 1.05
    elevenlabs_voice_id: str = "JBFqnCBsd6RMkjVDRZzb"
    elevenlabs_model: str = "eleven_turbo_v2_5"


@dataclass
class WatchSettings:
    poll_s: float = 30.0  # how often live chat is re-scored
    min_score: float = 2.5  # live clips are made automatically, so the bar is a little higher
    vod_delay_s: float = 60.0  # wait this long after a moment so Twitch's VOD has it
    live_check_s: float = 300.0  # how often to confirm the stream is still live
    mode: str = "chat"  # chat, audio (chat + audio), video (chat + video) or all
    channels: list[str] = field(default_factory=list)  # channels the options panel starts watching
    autostart: bool = False  # start watching this list by itself when the program opens, and clip each one whenever it goes live
    audio_level: float = 5.0  # how unusual a loud moment must be to make a clip without chat (higher = fewer clips)
    video_level: float = 6.0  # the same for fast action on screen
    signal_gap_s: float = 300.0  # at most one audio-only or video-only clip in this many seconds
    feed_delay_s: float = 8.0  # how far the low-quality feed runs behind the broadcast
    buffer_when_no_vod: bool = True  # stream not being recorded by Twitch: keep a short rolling copy of the live feed to cut from
    buffer_minutes: float = 10.0  # how much of the live feed that copy holds
    draft_workers: int = 2  # how many drafts are made at the same time, so a busy stream does not queue


@dataclass
class Config:
    paths: PathSettings = field(default_factory=PathSettings)
    moments: MomentSettings = field(default_factory=MomentSettings)
    captions: CaptionSettings = field(default_factory=CaptionSettings)
    fetch: FetchSettings = field(default_factory=FetchSettings)
    transcribe: TranscribeSettings = field(default_factory=TranscribeSettings)
    render: RenderSettings = field(default_factory=RenderSettings)
    watch: WatchSettings = field(default_factory=WatchSettings)
    ai: AiSettings = field(default_factory=AiSettings)
    trends: TrendSettings = field(default_factory=TrendSettings)
    llm: LlmSettings = field(default_factory=LlmSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    brand: BrandSettings = field(default_factory=BrandSettings)
    # Per-streamer layout, keyed by lower-case channel name:
    #   channels: {lacy: {layout: split, facecam: [0.0, 0.0, 0.28, 0.28]}}
    channels: dict = field(default_factory=dict)

    @property
    def data_dir(self) -> Path:
        override = os.environ.get("CLIPPER_DATA_DIR")
        raw = Path(override or self.paths.data_dir)
        return raw if raw.is_absolute() else PROJECT_ROOT / raw


def _apply(target: Any, values: dict[str, Any], where: str) -> None:
    if not isinstance(values, dict):
        raise ConfigError(f"{where} must be a mapping, got {type(values).__name__}")
    known = {f.name for f in fields(target)}
    for key, value in values.items():
        if key not in known:
            raise ConfigError(f"unknown config key {where}.{key} (known: {', '.join(sorted(known))})")
        current = getattr(target, key)
        if is_dataclass(current):
            _apply(current, value, f"{where}.{key}")
        else:
            setattr(target, key, value)


def load_env(path: Path | None = None) -> None:
    """Read KEY=value lines from .env into the environment (existing variables win).

    API keys for optional paid add-ons live in .env, which is git-ignored.
    """
    env = path or PROJECT_ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config(path: str | Path | None = None) -> Config:
    """Load config.yaml, then config.local.yaml beside it if present.

    Order of precedence for the base file: explicit ``path``, the
    ``CLIPPER_CONFIG`` environment variable, then config.yaml in the project root.
    """
    base = Path(path or os.environ.get("CLIPPER_CONFIG") or PROJECT_ROOT / "config.yaml")
    cfg = Config()
    if path is not None and not base.exists():
        raise ConfigError(f"config file not found: {base}")
    for candidate in (base, base.with_name("config.local.yaml")):
        if candidate.exists():
            data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            _apply(cfg, data, candidate.name)
    cfg.moments.validate()
    return cfg
