from __future__ import annotations

import re
import unicodedata

INVISIBLE_RE = re.compile(
    "[\u200b\u200c\u200d\ufeff\u2060\u180e\u00ad\u034f\u061c\u202a-\u202e]"
)
SPACE_RE = re.compile(r"\s+")

CONFUSABLES = str.maketrans(
    {
        "ё": "е",
        "a": "а",
        "e": "е",
        "o": "о",
        "p": "р",
        "c": "с",
        "x": "х",
        "y": "у",
        "k": "к",
        "m": "м",
        "h": "н",
        "t": "т",
        "b": "в",
    }
)


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = INVISIBLE_RE.sub("", text)
    text = text.lower().translate(CONFUSABLES)
    text = SPACE_RE.sub(" ", text)
    return text.strip()


def compact_text(text: str) -> str:
    normalized = normalize_text(text)
    return "".join(ch for ch in normalized if ch.isalnum())


def short_text(text: str, limit: int = 500) -> str:
    normalized = SPACE_RE.sub(" ", text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"
