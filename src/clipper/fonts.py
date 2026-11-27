"""The caption fonts that come with the program (src/clipper/fonts/), so a good one is there on every PC.

They are open-licence fonts taken unchanged from github.com/google/fonts; each one's licence text sits beside it.
``fonts.json`` lists them. The subtitle renderer (libass, inside FFmpeg) is pointed at the folder with ``fontsdir``,
so a caption style can name one of them exactly like a font installed in Windows; fonts installed in Windows keep
working as before.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

FONT_DIR = Path(__file__).with_name("fonts")


@lru_cache(maxsize=1)
def bundled() -> tuple[dict, ...]:
    """The fonts that come with the program: {"label", "family", "file", "weight", "license", "kind"}. Empty when the folder is missing."""
    try:
        rows = json.loads((FONT_DIR / "fonts.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ()
    except (OSError, ValueError) as e:
        log.error("the list of fonts that come with the program could not be read (%s): only fonts installed in Windows can be used", e)
        return ()
    return tuple(r for r in rows if isinstance(r, dict) and r.get("family") and r.get("file") and (FONT_DIR / str(r["file"])).is_file())


def find(name: str) -> dict | None:
    """The bundled font a caption style names, by its family name or its label (any letter case)."""
    wanted = " ".join(str(name or "").split()).lower()
    for row in bundled():
        if wanted in (str(row["family"]).lower(), str(row.get("label") or "").lower()):
            return row
    return None


def ass_name(name: str) -> str:
    """The name to write into a subtitle style: a bundled font's exact family name, anything else as it was typed."""
    row = find(name)
    return str(row["family"]) if row else str(name)


def bold_flag(name: str) -> int:
    """The Bold value of a subtitle style for this font: -1 (bold) or 0.

    Asking for bold from a font that only has a regular weight makes the renderer thicken it artificially, which
    smears display faces such as Bebas Neue or Bangers. Their one weight is already the heavy one. Fonts that are not
    bundled keep bold, as before.
    """
    row = find(name)
    return 0 if row and int(row.get("weight") or 700) < 600 else -1


def ass_filter(script: str) -> str:
    """The FFmpeg filter that burns a subtitle file in, with the bundled fonts on offer.

    ``script`` is the .ass file's name relative to the folder FFmpeg runs in. The fonts folder is an absolute Windows
    path: inside a filter description its colon and backslashes must be written so that neither the filter-graph
    reader nor the filter's own option reader takes them for separators.
    """
    if not bundled():
        return f"ass={script}"
    folder = str(FONT_DIR.resolve()).replace("\\", "/").replace(":", "\\\\:").replace("'", "\\\\\\'").replace(",", "\\,").replace("[", "\\[").replace("]", "\\]").replace(";", "\\;")
    return f"ass={script}:fontsdir={folder}"
