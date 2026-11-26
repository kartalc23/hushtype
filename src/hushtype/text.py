"""Vocabulary, style prompt and post-transcription replacements.

Three plain-text files in the config directory shape the output:

* ``vocabulary.txt``: names and terms Whisper should spell correctly
  (passed as ``hotwords``). One per line, most important first.
* ``style.txt``: a short sample of how you talk (passed as
  ``initial_prompt``). A sentence beats a word list: if you mix English terms
  into another language, show it here and the model stops "translating" them.
* ``replacements.txt``: ``wrong => right`` rules applied after transcription,
  for things prompting alone cannot fix.

Both prompts share one token budget (see ``build_prompt``). Everything here is
pure Python so it can be unit-tested without a model.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

TokenCounter = Callable[[str], int]


def estimate_tokens(text: str) -> int:
    """Rough fallback when Whisper's tokenizer is not available (about 3 chars per token)."""
    return max(1, round(len(text) / 3)) if text else 0


# ---------------------------------------------------------------- reading


def _content_lines(text: str) -> list[str]:
    return [s.strip() for s in text.splitlines() if s.strip() and not s.strip().startswith("#")]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def read_terms(path: Path) -> list[str]:
    seen, terms = set(), []
    for t in _content_lines(_read(path)):
        if t.lower() not in seen:  # duplicates only waste prompt tokens
            seen.add(t.lower())
            terms.append(t)
    return terms


def read_style(path: Path) -> str:
    return " ".join(" ".join(_content_lines(_read(path))).split())


def parse_replacement_pairs(text: str) -> list[tuple[str, str]]:
    pairs = []
    for line in _content_lines(text):
        if "=>" not in line:
            continue
        wrong, right = (p.strip() for p in line.split("=>", 1))
        if wrong:
            pairs.append((wrong, right))
    return pairs


def read_replacement_pairs(path: Path) -> list[tuple[str, str]]:
    return parse_replacement_pairs(_read(path))


# ---------------------------------------------------------------- writing

VOCABULARY_HEADER = """\
# Names and terms Whisper should spell correctly, one per line.
# Terms are used in order until the prompt token budget is full, so put the
# ones it gets wrong most often at the top. Lines starting with # are ignored.
"""

STYLE_HEADER = """\
# A short sample of how you talk, used as the model's initial prompt.
# Write a natural sentence or two, not a word list.
"""

REPLACEMENTS_HEADER = """\
# Fixes applied after transcription.  Format:  wrong => right
# Case-insensitive, whole words only. Lines starting with # are ignored.
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def write_terms(path: Path, terms: list[str]) -> None:
    _write(path, VOCABULARY_HEADER + "\n" + "".join(f"{t.strip()}\n" for t in terms if t.strip()))


def write_style(path: Path, style: str) -> None:
    _write(path, STYLE_HEADER + "\n" + style.strip() + "\n")


def write_replacement_pairs(path: Path, pairs: list[tuple[str, str]]) -> None:
    body = "".join(f"{w.strip()} => {r.strip()}\n" for w, r in pairs if w.strip())
    _write(path, REPLACEMENTS_HEADER + "\n" + body)


# ---------------------------------------------------------------- prompt


@dataclass
class Prompt:
    style: str = ""  # initial_prompt
    hotwords: str = ""  # comma-separated vocabulary
    terms_used: int = 0
    terms_total: int = 0
    style_tokens: int = 0
    hotword_tokens: int = 0
    budget: int = 0
    dropped: list[str] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return self.style_tokens + self.hotword_tokens


def _fit_words(text: str, count: TokenCounter, limit: int) -> str:
    words = text.split()
    while words and count(" " + " ".join(words)) > limit:
        words.pop()
    return " ".join(words)


def build_prompt(style: str, terms: list[str], count: TokenCounter, budget: int) -> Prompt:
    """Fit the style sample and vocabulary into one token budget.

    The style sample goes first, capped at half the budget. Vocabulary terms
    then fill what is left, in file order, so the top of the list always wins.
    """
    p = Prompt(budget=budget, terms_total=len(terms))
    p.style = _fit_words(style, count, budget // 2)
    p.style_tokens = count(" " + p.style) if p.style else 0
    remaining = budget - p.style_tokens
    used: list[str] = []
    for i, term in enumerate(terms):
        candidate = ", ".join(used + [term]) + "."
        if count(" " + candidate) > remaining:
            p.dropped = terms[i:]
            break
        used.append(term)
    p.hotwords = ", ".join(used) + "." if used else ""
    p.hotword_tokens = count(" " + p.hotwords) if p.hotwords else 0
    p.terms_used = len(used)
    return p


def describe_prompt(p: Prompt) -> list[str]:
    lines = []
    if p.style:
        lines.append(f"style: {len(p.style.split())} words ({p.style_tokens} tokens)")
    if p.terms_total:
        lines.append(f"vocabulary: {p.terms_used} of {p.terms_total} terms "
                     f"({p.hotword_tokens} tokens; budget {p.tokens}/{p.budget})")
    if p.dropped:
        lines.append(f"            not used (over budget): {', '.join(p.dropped[:5])}"
                     + (" ..." if len(p.dropped) > 5 else ""))
    return lines


# ---------------------------------------------------------------- replacements


def compile_replacements(pairs: list[tuple[str, str]]) -> list[tuple[re.Pattern, str]]:
    """Case-insensitive, word-boundary aware: "1910" also catches "1910's" but not "31910"."""
    return [(re.compile(rf"(?<!\w){re.escape(w)}(?!\w)", re.IGNORECASE), r) for w, r in pairs]


def parse_replacements(text: str) -> list[tuple[re.Pattern, str]]:
    return compile_replacements(parse_replacement_pairs(text))


def apply_replacements(text: str, rules) -> str:
    for pattern, right in rules:
        # A function replacement so backslashes in `right` are taken literally.
        text = pattern.sub(lambda _m, r=right: r, text)
    return text
