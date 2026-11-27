"""Errors in plain words: every fault a person sees says what happened and what to do, with the original kept."""

import re
from pathlib import Path

import clipper
from clipper import plain
from clipper.review import page_html

REPORTED = ("03:13:10 draft failed: joining the live buffer failed: exit code 3199971767: [in#0 @ 0000025194d61e40] trun track id unknown, "
            "no tfhd was found | [in#0 @ 0000025194d61e40] error reading header | [in#0 @ 0000025194d04740] Error opening input: Invalid data found "
            r"when processing input | Error opening input file C:\clipper-data\work\twitch_live322403305817\clips\4h35m30s\source.part.ts.")


def test_the_reported_error_is_explained():
    got = plain.explain(REPORTED)
    assert got["known"] and "newer format" in got["what"] and "Update the program" in got["do"] and got["step"] == "joining the live buffer"
    logged = "03:13:40 ERROR clipper.watch: ishowspeed: " + REPORTED[len("03:13:10 draft failed: "):]
    assert plain.explain(logged)["what"] == got["what"]


def test_common_faults_have_their_own_words():
    cases = {
        "metadata download failed: exit code 1: ERROR: [youtube] abc: Sign in to confirm you're not a bot": "refused to hand over",
        "render of clip 0h01m00s failed: exit code 1: av_interleaved_write_frame(): No space left on device": "disk is full",
        "PermissionError: [WinError 32] The process cannot access the file because it is being used by another process: 'final.mp4'": "another program has it open",
        "Twitch chat download failed: exit code 1: urlopen error [Errno 11001] getaddrinfo failed": "internet connection dropped",
        "ChatGPT (Codex) making a thumbnail failed: exit code 1: usage limit reached": "limit of your plan",
        "Kick range download failed: timed out after 900s": "took far longer",
        "ERROR: [twitch:vod] 123: HTTP Error 429: Too Many Requests": "too many requests",
        "render of clip x failed: exit code 1: [h264_nvenc @ 0000] OpenEncodeSessionEx failed: out of memory (10)": "graphics card",
        "this moment is no longer in the live buffer (it only keeps the last few minutes)": "too long ago",
        "ffmpeg was not found on PATH or under D:\\data\\tools. Run setup.ps1 to see what is missing.": "not installed",
    }
    for text, words in cases.items():
        got = plain.explain(text)
        assert got["known"] and words in got["what"], (text, got["what"])
        assert got["do"].strip()


def test_an_unknown_fault_still_gets_plain_words_and_a_next_step():
    got = plain.explain("still of clip 0h01m00s failed: exit code 1: [swscaler @ 00000216] deprecated pixel format used")
    assert not got["known"] and got["step"] == "still of clip 0h01m00s" and "Technical details" in got["do"]
    assert plain.technical("x failed: exit code 1: boom") and plain.technical("Traceback (most recent call last)")
    # what the program wrote itself for a person is not treated as technical
    for own in ("approve this clip first; only approved clips are made into videos", "describe the picture you want first, or use the automatic button",
                "videos are already being made; wait for that to end, or press Stop"):
        assert not plain.technical(own), own


def test_the_table_works_the_same_in_the_browser():
    for pattern, what, do in plain.RULES:
        re.compile(pattern, re.I)
        assert "(?" not in pattern and what.endswith(".") and do.endswith("."), pattern  # nothing a browser reads differently
    page = clipper.page_source()
    for piece in ("const PLAIN=__PLAIN__", "function plainOf(", "Technical details", "plainHtml(c.error", "bad.map(l=>LOG_LEAD+l)", "plainOf(msg,true)"):
        assert piece in page, piece
    made = page_html("Test", True)
    assert "__PLAIN__" not in made and "trun track id unknown" in made
