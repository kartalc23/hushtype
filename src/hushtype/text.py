"""Vocabulary, style prompt and post-transcription replacements.

Three plain-text files in the config directory shape the output:

* ``vocabulary.txt``: names and terms Whisper should spell correctly
  (passed as ``hotwords``). One per line.
* ``style.txt``: a short sample of how you talk (passed as
  ``initial_prompt``). A sentence beats a word list: if you mix English terms
  into another language, show it here and the model stops "translating" them.
* ``replacements.txt``: ``wrong => right`` rules applied after transcription,
  for things prompting alone cannot fix.

Everything here is pure Python so it can be unit-tested without a model.
"""

from __future__ import annotations

import re
from pathlib import Path


def _content_lines(text: str) -> list[str]:
    return [s.strip() for s in text.splitlines() if s.strip() and not s.strip().startswith("#")]


def load_vocabulary(path: Path, limit: int, log=print) -> str:
    """Return the vocabulary as a single hotwords string ("" if none)."""
    if not path.exists():
        return ""
    terms = _content_lines(path.read_text(encoding="utf-8"))
    if not terms:
        return ""
    if len(terms) > limit:
        log(f"vocabulary: using the first {limit} of {len(terms)} terms "
            "(the prompt window is small; put the most misspelled ones on top)")
        terms = terms[:limit]
    else:
        log(f"vocabulary: {len(terms)} terms loaded")
    return ", ".join(terms) + "."


def load_style(path: Path, limit: int, log=print) -> str:
    """Return the style sample as an initial prompt ("" if none)."""
    if not path.exists():
        return ""
    words = " ".join(_content_lines(path.read_text(encoding="utf-8"))).split()
    if len(words) > limit:
        log(f"style: using the first {limit} of {len(words)} words")
        words = words[:limit]
    elif words:
        log(f"style: {len(words)}-word sample loaded")
    return " ".join(words)


def parse_replacements(text: str) -> list[tuple[re.Pattern, str]]:
    """Parse ``wrong => right`` lines into (pattern, replacement) pairs.

    Matching is case-insensitive and respects word boundaries, so "1910"
    also catches "1910's" but not "31910".
    """
    rules = []
    for line in _content_lines(text):
        if "=>" not in line:
            continue
        wrong, right = (p.strip() for p in line.split("=>", 1))
        if not wrong:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(wrong)}(?!\w)", re.IGNORECASE)
        rules.append((pattern, right))
    return rules


def load_replacements(path: Path, log=print) -> list[tuple[re.Pattern, str]]:
    if not path.exists():
        return []
    rules = parse_replacements(path.read_text(encoding="utf-8"))
    if rules:
        log(f"replacements: {len(rules)} rules loaded")
    return rules


def apply_replacements(text: str, rules) -> str:
    for pattern, right in rules:
        # A function replacement so backslashes in `right` are taken literally.
        text = pattern.sub(lambda _m, r=right: r, text)
    return text
