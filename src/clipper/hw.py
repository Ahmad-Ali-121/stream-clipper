"""Pick the fastest hardware this machine has, once, and remember it.

  render.encoder: auto   -> NVIDIA (h264_nvenc), else Intel Quick Sync (h264_qsv),
                            else AMD (h264_amf), else CPU (libx264). Each is tried
                            with a real test encode, because FFmpeg lists encoders
                            it was built with even when the hardware is absent.
  transcribe.device: auto -> "cuda" when an NVIDIA GPU and its CUDA libraries are
                            usable by the speech library, else "cpu".

The answer is cached in <data_dir>/hardware.json. Delete that file (or run
``clipper hardware --refresh``) after changing GPU or drivers.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from .config import Config
from .tools import find_tool
from .work import read_json, write_json

log = logging.getLogger(__name__)

ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf")


def _encoder_works(ffmpeg: str, name: str) -> bool:
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=1080x1920:r=30",
             "-frames:v", "5", "-c:v", name, "-f", "null", "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=60,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _cuda_usable() -> tuple[bool, int]:
    """(usable, VRAM in MB). Usable means the speech library can actually open a CUDA device."""
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() < 1:
            return False, 0
        ctranslate2.get_supported_compute_types("cuda")  # raises when the CUDA libraries are missing
    except Exception as e:
        log.info("no usable NVIDIA card for speech-to-text (%s)", e)
        return False, 0
    vram = 0
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20).stdout
        vram = int(out.strip().splitlines()[0])
    except Exception as e:
        log.warning("could not read the NVIDIA card's memory size (%s); assuming a small card", e)
    return True, vram


def detect(cfg: Config, refresh: bool = False) -> dict:
    path = cfg.data_dir / "hardware.json"
    cached = None if refresh else read_json(path)
    if cached:
        return cached
    ffmpeg = find_tool("ffmpeg", cfg.data_dir)
    encoder = next((e for e in ENCODERS if _encoder_works(ffmpeg, e)), "libx264")
    cuda, vram = _cuda_usable()
    info = {
        "encoder": encoder,
        "whisper_device": "cuda" if cuda else "cpu",
        # 4 GB cards cannot hold the large model in float16 alongside anything else; int8_float16 halves it.
        "whisper_compute_type": ("float16" if vram >= 6000 else "int8_float16") if cuda else "int8",
        "vram_mb": vram,
    }
    write_json(path, info)
    log.info("hardware: video encoder %s, speech on %s (%s)", info["encoder"], info["whisper_device"], info["whisper_compute_type"])
    return info


_CUDA_TRIAL = """
import sys
import numpy as np
from faster_whisper import WhisperModel
m = WhisperModel(sys.argv[1], device="cuda", compute_type=sys.argv[2], download_root=sys.argv[3])
segments, _info = m.transcribe(np.zeros(16000, dtype="float32"))
list(segments)
print("CUDA-OK")
"""


CRASH_CODES = {
    0xC0000005: "access violation: it read memory it should not, which usually means a damaged model file",
    0xC000001D: "illegal instruction: this processor lacks something the speech library needs",
    0xC0000135: "a required system library (DLL) is missing",
    0xC0000409: "the speech library aborted",
    0xC00000FD: "stack overflow",
}


def describe_exit(code: int) -> str:
    """A crash exit code in words. Windows reports crashes as large negative numbers."""
    unsigned = code & 0xFFFFFFFF
    if 0 <= code < 256:
        return f"it ended with an error (exit code {code})"
    return f"it crashed: exit code {code} (0x{unsigned:08X}, {CRASH_CODES.get(unsigned, 'killed, or a crash inside a native library')})"


def trial_load(model: str, device: str, compute_type: str, models_dir: str, env: dict[str, str] | None = None,
               library: str | None = None) -> tuple[bool, str]:
    """Load the speech model and transcribe a second of silence in a separate process: (worked, everything it said).

    A damaged model file or missing GPU libraries kill the process inside native code, with no Python error.
    Trying it in a child first means that death is seen and reported instead of taking the program with it.
    """
    python = [sys.executable]
    if library:  # the same trial with another version of the speech library, in a throwaway environment made by uv
        import shutil

        from .config import PROJECT_ROOT

        uv = shutil.which("uv")
        if not uv:
            return False, "uv was not found, so another library version cannot be tried"
        python = [uv, "run", "--quiet", "--project", str(PROJECT_ROOT), "--with", library, "python"]
    try:
        proc = subprocess.run(
            python + ["-X", "faulthandler", "-c", _CUDA_TRIAL.replace('"cuda"', "sys.argv[4]"), model, compute_type, models_dir, device],
            capture_output=True, text=True, errors="replace", stdin=subprocess.DEVNULL, timeout=900 if library else 3600,
            env={**os.environ, **(env or {})},
        )
    except Exception as e:
        return False, f"could not start the trial: {e}"
    if "CUDA-OK" in proc.stdout:
        return True, ""
    return False, (describe_exit(proc.returncode) + "\n" + proc.stderr.strip()[-1500:]).strip()


def machine_facts() -> list[str]:
    """Lines describing this computer and the speech library, for a problem report."""
    import platform

    facts = [f"system: {platform.platform()}", f"processor: {platform.processor()} ({os.cpu_count()} threads)", f"python: {sys.version.split()[0]}"]
    try:
        import ctypes

        class Mem(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in ("total", "free", "a", "b", "c", "d", "e")]

        m = Mem()
        m.length = ctypes.sizeof(Mem)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        facts.append(f"memory: {m.total / 2**30:.1f} GB installed, {m.free / 2**30:.1f} GB free")
    except Exception as e:
        facts.append(f"memory: could not be read ({e})")
    try:
        import ctranslate2
        import faster_whisper

        facts.append(f"speech library: ctranslate2 {ctranslate2.__version__}, faster-whisper {faster_whisper.__version__}")
        facts.append(f"processor number formats: {sorted(ctranslate2.get_supported_compute_types('cpu'))}")
        facts.append(f"NVIDIA cards the library sees: {ctranslate2.get_cuda_device_count()}")
    except Exception as e:
        facts.append(f"speech library: could not be read ({e})")
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                             capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20).stdout.strip()
        facts.append(f"nvidia-smi: {out or 'no answer'}")
    except Exception as e:
        facts.append(f"nvidia-smi: not available ({e})")
    return facts


# (label, model, device, compute type, extra environment) tried by ``clipper doctor``, each in its own process.
SPEECH_TRIALS = [
    ("tiny model, processor", "tiny", "cpu", "int8", {}),
    ("usual model, processor, int8 (the default)", None, "cpu", "int8", {}),
    ("usual model, processor, float32", None, "cpu", "float32", {}),
    ("usual model, processor, int8, one thread", None, "cpu", "int8", {"OMP_NUM_THREADS": "1"}),
    ("usual model, processor, int8, AVX2 only", None, "cpu", "int8", {"CT2_FORCE_CPU_ISA": "AVX2"}),
    ("usual model, processor, int8, plain instructions", None, "cpu", "int8", {"CT2_FORCE_CPU_ISA": "GENERIC"}),
    ("usual model, processor, int8, without Intel MKL", None, "cpu", "int8", {"CT2_USE_MKL": "0"}),
    ("usual model, NVIDIA card, int8_float16", None, "cuda", "int8_float16", {}),
    ("tiny model, processor, NVIDIA card hidden from the library", "tiny", "cpu", "int8", {"CUDA_VISIBLE_DEVICES": "-1"}),
]
# Older releases of the speech library, newest first, tried when the installed one fails.
OLDER_LIBRARIES = ["ctranslate2==4.8.5", "ctranslate2==4.8.2", "ctranslate2==4.7.2"]


def doctor(cfg: Config) -> bool:
    """Print this computer's facts and try loading the speech model several ways. True when the default way works."""
    models = cfg.data_dir / "models"
    models.mkdir(parents=True, exist_ok=True)
    for line in machine_facts():
        print(line)
    print(f"models folder: {models}")
    print("\nTrying speech-to-text several ways, each in its own process (a few minutes; nothing here can crash this window):")
    default_ok, seen = False, []

    def report(label: str, ok: bool, why: str, extra: str = "") -> None:
        print(f"\n[{'WORKS' if ok else 'FAILS'}] {label}{extra}", flush=True)
        if ok:
            return
        import re

        same = re.sub(r"0x[0-9a-fA-F]+", "0x", why)  # thread numbers differ on every run
        if same in seen:
            print("    exactly the same failure as the first one printed above with this text")
        else:
            seen.append(same)
            print("    " + why.replace("\n", "\n    "))

    for label, model, device, compute_type, env in SPEECH_TRIALS:
        ok, why = trial_load(model or cfg.transcribe.model, device, compute_type, str(models), env)
        report(label, ok, why, f"  {env}" if env else "")
        if label.endswith("(the default)"):
            default_ok = ok
    if not default_ok:
        print("\nThe installed speech library fails, so older releases are tried (each is downloaded once, about 20 MB):")
        for library in OLDER_LIBRARIES:
            ok, why = trial_load("tiny", "cpu", "int8", str(models), library=library)
            report(f"tiny model, processor, with {library}", ok, why)
    print("\nCopy everything above when reporting a problem.")
    return default_ok


def confirm_cuda(cfg: Config, model: str, compute_type: str, models_dir: str) -> tuple[str, str]:
    """(device, compute_type) after proving once that speech really runs on the NVIDIA card.

    Seeing the card is not enough: when NVIDIA's cuBLAS or cuDNN libraries are missing, loading the model
    kills the whole program without an error message. So the first load is tried in a separate process;
    if that dies, the answer is recorded and speech runs on the CPU from then on.
    """
    path = cfg.data_dir / "hardware.json"
    info = read_json(path, {}) or {}
    if info.get("cuda_checked"):
        return info.get("whisper_device", "cpu"), info.get("whisper_compute_type", "int8")
    log.info("checking that speech-to-text really runs on the NVIDIA card (once; the first run also downloads the model)")
    ok, why = trial_load(model, "cuda", compute_type, models_dir)
    info["cuda_checked"] = True
    if ok:
        log.info("speech-to-text runs on the NVIDIA card")
    else:
        info.update(whisper_device="cpu", whisper_compute_type="int8", cuda_problem=why)
        log.warning(
            "an NVIDIA card was found but speech-to-text cannot run on it. Using the processor instead, which is slower. "
            "The usual cause is missing NVIDIA libraries (cuBLAS and cuDNN); see GUIDE.md, 'When something goes wrong'. "
            "What the trial said:\n%s", why,
        )
    write_json(path, info)
    return info.get("whisper_device", "cpu"), info.get("whisper_compute_type", "int8")


def encoder(cfg: Config) -> str:
    return detect(cfg)["encoder"] if cfg.render.encoder == "auto" else cfg.render.encoder


def whisper_device(cfg: Config) -> tuple[str, str]:
    """(device, compute_type) for the speech model."""
    t = cfg.transcribe
    if t.device != "auto":
        return t.device, ("int8" if t.compute_type == "auto" else t.compute_type)
    info = detect(cfg)
    return info["whisper_device"], (info["whisper_compute_type"] if t.compute_type == "auto" else t.compute_type)


def encoder_args(name: str, crf: int, preset: str) -> list[str]:
    """FFmpeg video-encoder arguments giving similar quality on each encoder."""
    if name == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", str(crf + 1), "-b:v", "8M", "-maxrate", "12M", "-bufsize", "16M"]
    if name == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", str(crf + 2), "-maxrate", "12M", "-bufsize", "24M"]
    if name == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", str(crf + 2), "-qp_p", str(crf + 2)]
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-maxrate", "12M", "-bufsize", "24M"]
