"""Excitement in what is said: a moment where the streamer shouts "oh my god", "no way", "bhaag bhaag", "arre yaar".

Chat, loudness and motion say that something happened; the words say what kind of thing. This is a plain word
list, not a model: each exclamation is worth one to three points, the points inside a short window are added up,
and the total is squeezed to 0..10 like the other signals. It works on any timed words: YouTube's captions for a
whole stream (to find moments chat missed) and a clip's own captions (to score the clip).

English, Hindi and Urdu, in Latin letters and in their own letters, because captions arrive in either.
"""

from __future__ import annotations

import re
from typing import Sequence

# Phrases first (matched on the words joined together), then single words. Points: 3 = shock, 2 = hype or danger, 1 = mild.
_PHRASES: list[tuple[str, int]] = [
    (r"oh my (god|gosh|days)|what the (hell|heck|fuck|f)|no (fucking )?way|holy (shit|crap|moly|cow)|are you (kidding|serious)|i can'?t believe", 3),
    (r"let'?s (fucking )?go+|oh (no+|shit|fuck|damn|man|bhai)|look at (this|that)|watch (this|out)|get (out|down|in)|behind (you|me)|we did it|i'?m dead", 2),
    (r"ya+r kya|kya (baat|scene|kar|ho) |are+ (yaar|bhai|baap)|oh teri|hat teri|mar (gaya|gaye|gayi)|bach (gaya|gaye)|bhaag (bhaag|ja|jao)|nikal (ja|jao|lo)|pakad (le|lo)|ye kya", 2),
    (r"अरे (यार|भाई|बाप)|ओ तेरी|मर (गया|गए|गई)|बच (गया|गए)|क्या बात|ये क्या|भाग भाग|ओ माय गॉड|ओह माय गॉड|नो वे|लेट्स गो", 2),
    (r"ارے (یار|بھائی)|مر (گیا|گئے)|بچ (گیا|گئے)|کیا بات|یہ کیا|او مائی گاڈ", 2),
]
_WORDS: dict[str, int] = {
    # shock, disbelief
    "omg": 3, "wtf": 3, "bruh": 2, "what": 1, "whoa": 2, "woah": 2, "wow": 2, "damn": 2, "insane": 2, "crazy": 2, "unbelievable": 2, "impossible": 2,
    "nooo": 2, "noooo": 2, "yooo": 2, "sheesh": 2, "holy": 2, "wait": 1, "no": 1, "oh": 1, "bro": 1, "dude": 1, "yes": 1, "yeah": 1, "stop": 1, "help": 2,
    # danger, action
    "run": 2, "police": 2, "cops": 2, "shoot": 2, "shot": 1, "dead": 2, "died": 2, "killed": 2, "boom": 2, "crash": 2, "crashed": 2, "explosion": 2,
    "fire": 1, "chase": 2, "clutch": 2, "headshot": 2, "win": 1, "won": 2, "lost": 1, "clip": 1,
    # Hindi / Urdu in Latin letters
    "arre": 2, "arey": 2, "abe": 2, "oye": 2, "oyee": 2, "uff": 2, "yaar": 1, "bhaag": 2, "bhaago": 2, "bhago": 2, "bachao": 3, "pulis": 2,
    "maar": 1, "maaro": 2, "mara": 1, "goli": 2, "dhamaka": 2, "pagal": 2, "zabardast": 2, "kamaal": 2, "kamal": 2, "mast": 1, "khatarnak": 2,
    "gaya": 1, "gaye": 1, "nahi": 1, "nahin": 1, "ruk": 1, "ruko": 2, "jaldi": 2, "dekh": 1, "dekho": 1, "wah": 2, "waah": 2, "shabash": 2, "haha": 1,
    # Hindi letters
    "अरे": 2, "अबे": 2, "ओए": 2, "उफ": 2, "उफ़": 2, "यार": 1, "भाग": 2, "भागो": 2, "बचाओ": 3, "पुलिस": 2, "मार": 1, "मारो": 2, "गोली": 2, "धमाका": 2,
    "पागल": 2, "जबरदस्त": 2, "ज़बरदस्त": 2, "कमाल": 2, "खतरनाक": 2, "नहीं": 1, "रुको": 2, "जल्दी": 2, "देखो": 1, "वाह": 2, "शाबाश": 2, "ब्लास्ट": 2, "क्रैश": 2,
    # Urdu letters
    "ارے": 2, "ابے": 2, "اوئے": 2, "اف": 2, "یار": 1, "بھاگ": 2, "بھاگو": 2, "بچاؤ": 3, "پولیس": 2, "مارو": 2, "گولی": 2, "دھماکہ": 2, "پاگل": 2,
    "زبردست": 2, "کمال": 2, "خطرناک": 2, "نہیں": 1, "رکو": 2, "جلدی": 2, "دیکھو": 1, "واہ": 2, "شاباش": 2,
}
_PHRASE_RX = [(re.compile(f"(?:{p.strip()})(?= |$)", re.IGNORECASE), pts) for p, pts in _PHRASES]  # "what the f" is not "what the first"
_STRIP = ".,!?…\"'।۔:;()[]"
WINDOW_S = 15.0  # exclamations this close together count as one moment
FULL = 18.0  # points inside one window that count as the strongest signal (10)


def _clean(word: str) -> str:
    return str(word).strip().strip(_STRIP).lower()


def points(words: Sequence[dict]) -> list[tuple[float, float]]:
    """(second, points) for every exclamation in timed words ({"start", "word"})."""
    out: list[tuple[float, float]] = []
    texts = [_clean(w.get("word", "")) for w in words]
    used_until = -1
    for i, w in enumerate(words):
        if i <= used_until or not texts[i]:
            continue
        best, length = 0.0, 1
        for n in (4, 3, 2):
            if i + n <= len(words):
                joined = " ".join(texts[i:i + n]) + " "
                for rx, pts in _PHRASE_RX:
                    m = rx.match(joined)
                    if m and pts > best:
                        best, length = pts, max(1, len(m.group(0).split()))  # the words the phrase itself covers
        if not best:
            t = texts[i]
            best = _WORDS.get(t, 0)
            if best == 1:
                best = 0.5  # everyday words ("yaar", "no", "bro"): a little on their own, a lot when they pile up
            if not best and len(t) >= 4 and re.fullmatch(r"(.)\1{2,}|(no+|yo+|go+|a+h+|o+h+)", t):
                best = 1  # a drawn-out shout: "nooooo", "aaaah"
            # The same shout repeated is louder than once, but a word said on every line (a habit "bro") is not a moment.
        if best:
            out.append((float(w["start"]), float(best) + (1.0 if str(w.get("word", "")).rstrip().endswith("!") else 0.0)))
            used_until = i + length - 1
    return out


def strength(words: Sequence[dict], length_s: float) -> float:
    """How exclamation-heavy a clip is, 0..10, from its own captions (times counted from the clip's start)."""
    pts = points(words)
    if not pts or length_s <= 0:
        return 0.0
    best = max(sum(p for t2, p in pts if t <= t2 < t + WINDOW_S) for t, _ in pts)
    return round(min(10.0, 10.0 * best / FULL), 1)


def peaks(words: Sequence[dict], since_s: float = 0.0, gap_s: float = 90.0, floor: float = 5.0) -> list[tuple[float, float]]:
    """The moments of a whole stream where exclamations pile up: [(second, strength 0..10)], strongest kept, ``gap_s`` apart."""
    pts = [(t, p) for t, p in points(words) if t >= since_s]
    scored: list[tuple[float, float]] = []
    j = 0
    for i, (t, _) in enumerate(pts):
        while pts[j][0] < t - WINDOW_S / 2:
            j += 1
        total = 0.0
        k = j
        while k < len(pts) and pts[k][0] <= t + WINDOW_S / 2:
            total += pts[k][1]
            k += 1
        scored.append((t, min(10.0, 10.0 * total / FULL)))
    out: list[tuple[float, float]] = []
    for t, s in sorted(scored, key=lambda x: -x[1]):
        if s < floor:
            break
        if all(abs(t - o) >= gap_s for o, _ in out):
            out.append((t, round(s, 1)))
    return sorted(out)
