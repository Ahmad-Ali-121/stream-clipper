"""Errors in plain words.

The tools this program runs (FFmpeg, yt-dlp, the AI services) report faults in their own technical language.
A person who is not a programmer cannot tell from "trun track id unknown, no tfhd was found" what happened or
what to do. This table turns the faults that really occur into two sentences each: what happened, and what to do.

The technical text is never thrown away: the page shows the plain words first and keeps the original one click
away under "Technical details", and the terminal and the logs always carry the original.

The same table is used by the page (it is handed over inside the page, see ``review.page_html``), so the patterns
are written to mean the same in Python and in a browser: plain alternatives, no look-behind, no inline flags.
They are matched without regard to capital letters, first match wins, so the specific ones come first.
"""

from __future__ import annotations

import re

# (pattern, what happened, what to do)
RULES: list[tuple[str, str, str]] = [
    # ---- live streams ----
    (r"trun track id unknown|no tfhd was found",
     "This live stream sends its video in a newer format that this copy of the program could not put together, so the clip's video could not be saved.",
     "Update the program and restart it: the newest version reads this format. This one clip cannot be rescued; close the message or reject the clip. Moments found after the update will work."),
    (r"no longer in the live buffer",
     "This moment happened too long ago. For a stream that the site is not recording, the program only keeps the last few minutes of the live video, and this moment had already been dropped.",
     "Nothing can bring this clip back; reject it. Moments are normally cut within a minute or two, so this usually means the computer or the internet was very slow at that time."),
    (r"live buffer has not reached the end|live buffer is still empty",
     "The live stream has not played far enough yet to cut this moment.",
     "Nothing to do: it is tried again by itself in a moment."),
    (r"missing from the live feed",
     "A piece of this moment is missing: an advert break or a short internet drop cut a hole in the live video right there.",
     "Nothing can bring the missing piece back; reject this clip. The next moments are not affected."),
    (r"not currently live|is offline|channel is not live",
     "This channel is not live right now.",
     "Nothing to do. If you are watching it, the program notices by itself when it goes live."),
    # ---- the disk and files ----
    (r"no space left|not enough space|disk is full|errno 28|there is not enough space on the disk",
     "The disk is full, so the file could not be saved.",
     "Free some space on the drive that holds the data folder (delete finished videos you no longer need from the videos folder and empty the Recycle Bin), then press the button again."),
    (r"winerror 32|being used by another process|is in use right now|winerror 5\b|access is denied|permissionerror|permission denied",
     "A file could not be changed because another program has it open (a video player, a folder preview, or an antivirus scan).",
     "Close the video player or the folder window showing that file, wait a few seconds, and press the button again."),
    (r"no such file or directory|filenotfounderror|winerror 2\b|winerror 3\b|cannot find the (file|path)",
     "A file the program needed is not there any more. It was moved, renamed or deleted while the program was working.",
     "Press the button again so the file is made again. Do not move or rename files inside the data folder; take finished videos only from the videos folder."),
    (r"was not found on path|run setup\.ps1",
     "A helper program that this step needs is not installed on this computer.",
     "Open Setup check at the top of the left side: it says which one is missing and has a button to install it."),
    # ---- the internet and the video sites ----
    (r"(codex|chatgpt|claude|anthropic|openai)[\s\S]{0,300}(\b429\b|too many requests)",
     "The AI service refused: the limit of your plan is reached for now, or the service is overloaded.",
     "Wait until the limit resets (often a few hours) and try again, or choose another AI under Setup check. The clip itself is fine."),
    (r"http error 429|too many requests|rate.?limit(ed)? by (youtube|twitch|kick)",
     "The video site is refusing for a while because too many requests came from this internet connection in a short time.",
     "Wait 15 to 30 minutes, then try again. Watching fewer channels at once makes this rarer."),
    (r"local ai is installed but not answering|local ai did not give a usable answer|:11434|"
     r"ollama.{0,80}(refused|not running|not reachable|unreachable|did not answer|timed out)|could not reach ollama",
     "The free AI on this computer (Ollama) is not running or did not answer.",
     "Start Ollama from the Start menu, wait half a minute, then try again. Setup check shows whether it is running."),
    (r"private video|video unavailable|has been removed|no longer available|members-only|subscriber-only|subscribers only|http error 404|"
     r"(video|channel|account|user)\b.{0,40}does not exist|\[(twitch|youtube|kick)[^\]]{0,30}\].{0,80}does not exist",
     "The video is not available: it was deleted, made private, or is only for paying subscribers.",
     "Nothing can be done for this video. Check the link in your browser; if it plays there for everyone, try again later."),
    (r"sign in to confirm|not a bot|confirm your age|login required|only available for registered users|cookies-from-browser",
     "The video site refused to hand over the video: it wants a signed-in viewer, or it suspects an automatic program.",
     "Wait an hour and try again, or try another video. This comes from the site, not from your computer."),
    (r"http error 403|forbidden",
     "The video site refused the request.",
     "Wait a few minutes and try again. If it keeps happening for every video, update the program: the site may have changed something."),
    (r"getaddrinfo failed|name resolution|name or service not known|network is unreachable|connection refused|connection reset|connection aborted|connectionreseterror|"
     r"winerror 10060|winerror 10054|winerror 10061|urlopen error|read timed out|connection timed out|remote end closed|incomplete read|unable to download|"
     r"http error 50\d|bad gateway|service unavailable|\bssl|certificate verify failed|eof occurred|could not reach the claude api",
     "The internet connection dropped, or the site did not answer.",
     "Check that your internet works (open any website). Most steps are tried again by themselves; if this one was not, press the button again."),
    # ---- AI services ----
    (r"not signed in|not logged in|logged out|please run /login|please log in|run codex login",
     "The AI program on this computer is not signed in any more.",
     "Open Setup check: it shows which one is signed out and the one line to type to sign in again."),
    (r"usage limit reached|hit your usage limit|reached your usage limit|usage_limit|hit your limit|\blimit reached|limit will reset|"
     r"exceeded your current quota|quota exceeded|credit balance|"
     r"insufficient_quota|rate_limit_error|rate limit reached|overloaded",
     "The AI service refused: the limit of your plan is reached for now, or the service is overloaded.",
     "Wait until the limit resets (often a few hours) and try again, or choose another AI under Setup check. The clip itself is fine."),
    (r"invalid x-api-key|invalid api key|incorrect api key|http error 401|unauthorized|authentication_error|authentication failed",
     "The AI service did not accept the key or the sign-in.",
     "Open Setup check and press Check again under AI. If it stays red, the key in the .env file is wrong or has been cancelled."),
    (r"11434|ollama.{0,80}(refused|not running|not reachable|unreachable|did not answer|timed out)|could not reach ollama",
     "The free AI on this computer (Ollama) is not running or did not answer.",
     "Start Ollama from the Start menu, wait half a minute, then try again. Setup check shows whether it is running."),
    # ---- the computer itself ----
    (r"cuda|cublas|cudnn|nvenc|nvcuda|out of memory.*gpu|gpu.*out of memory",
     "The graphics card could not do this step: it ran out of its own memory, or its driver has a problem.",
     "Close games and other heavy programs and try again. If it keeps happening, update the graphics card driver."),
    (r"memoryerror|cannot allocate memory|bad allocation|out of memory",
     "The computer ran out of memory during this step.",
     "Close other programs (browser tabs, games) and try again."),
    # ---- broken video files ----
    (r"moov atom not found|invalid data found when processing input|error opening input|could not find codec parameters",
     "The video file for this step is damaged or was only half downloaded.",
     "Make the draft again (Fine-tune, then Save and make the draft again), so the video is fetched again. If it fails the same way, reject the clip."),
    # ---- waiting too long ----
    (r"timed out after",
     "This step took far longer than it should and was stopped, so the program would not hang forever.",
     "Check your internet and try again. If the computer was busy with something heavy, wait for that to end first."),
    # ---- damaged program files ----
    (r"\.json cannot be read \(|\.json is damaged",
     "One of the program's own small files is damaged, most likely because the computer was switched off while it was being written.",
     "Restart the program. If the message comes back, send the technical details to the person who set this up."),
]

RULES.append(
    (r"jsondecodeerror|expecting value: line|expecting property name|unterminated string starting",
     "A small file or an answer the program needed was incomplete or damaged.",
     "Try once more. If it keeps happening on the same clip, delete that clip and make it again; otherwise send the technical details to the person who set this up."))

UNKNOWN = ("This step did not work, and the program does not have a plain explanation for this particular fault.",
           "Try once more. If it fails the same way, open Technical details and send that text to the person who set this up (or paste it into the Ask chat).")

# The fixed words the program puts in front of a fault: the time, and which of its own jobs failed.
_FRAME = re.compile(r"^\s*(?:\d\d:\d\d:\d\d\s+)?(?:(?:ERROR|WARNING)\s+[\w.]+:\s*)?(?:(?:draft|finish|render) failed:\s*)?", re.I)
_STEP = re.compile(r"^(.{3,80}?) failed: ", re.I)


def explain(text: str) -> dict:
    """{"what", "do", "step", "known"} for one error text. ``step`` is the job that failed, when the text names it."""
    raw = str(text or "")
    body = _FRAME.sub("", raw, count=1)
    step = _STEP.match(body)
    for pattern, what, do in RULES:
        if re.search(pattern, raw, re.I):
            return {"what": what, "do": do, "step": step.group(1).strip() if step else "", "known": True}
    return {"what": UNKNOWN[0], "do": UNKNOWN[1], "step": step.group(1).strip() if step else "", "known": False}


def technical(text: str) -> bool:
    """Does this text need putting into plain words? Sentences the program wrote itself for a person do not."""
    raw = str(text or "")
    if any(re.search(pattern, raw, re.I) for pattern, _what, _do in RULES):
        return True
    return bool(re.search(r"exit code -?\d+|traceback|\w+error:|\w+exception|errno|\[\w+[#@ ]|0x[0-9a-f]{6,}| \| ", raw, re.I))


def for_page() -> dict:
    """What the page needs to do the same in the browser."""
    return {"rules": [list(rule) for rule in RULES], "unknown": list(UNKNOWN),
            "technical": r"exit code -?\d+|traceback|\w+error:|\w+exception|errno|\[\w+[#@ ]|0x[0-9a-f]{6,}| \| "}
