"""Per-app tone: sort the focused window into a category, then shape the text.

Categories: ``personal`` (chat with friends), ``work`` (team chat), ``email``
and ``other``. Each category is given a tone, and a tone is a small set of
rules (``ToneStyle``): sentence case, the final period, an email layout, a
signature and its own replacements. Five tones are built in (default, formal,
casual, very_casual, email); users can change them and add their own.

Everything runs offline and is deterministic; nothing rewrites your words.
Pure Python so it can be unit-tested without a desktop.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, field

CATEGORIES = ("personal", "work", "email", "other")
CASES = ("unchanged", "capitalize", "lowercase")
PERIODS = ("unchanged", "add", "remove", "remove_short")
LAYOUTS = ("none", "email")


@dataclass
class ToneStyle:
    # unchanged | capitalize (sentence starts) | lowercase (sentence starts,
    # keeping vocabulary names, acronyms and the English "I")
    case: str = "unchanged"
    # unchanged | add | remove | remove_short (only if <= short_sentences)
    final_period: str = "unchanged"
    short_sentences: int = 2
    # none | email: greeting and closing on their own lines, "new paragraph"
    # starts a paragraph, the signature goes at the end
    layout: str = "none"
    signature: str = ""
    # Extra greetings and closings to recognise, on top of the built-in ones
    greetings: list[str] = field(default_factory=list)
    closings: list[str] = field(default_factory=list)
    # "wrong => right" rules applied only in this tone
    replacements: list[str] = field(default_factory=list)


BUILTIN_TONES: dict[str, ToneStyle] = {
    "default": ToneStyle(),
    "formal": ToneStyle(case="capitalize", final_period="add"),
    "casual": ToneStyle(final_period="remove_short"),
    "very_casual": ToneStyle(case="lowercase", final_period="remove"),
    "email": ToneStyle(case="capitalize", final_period="add", layout="email"),
}


def builtin_tones() -> dict[str, ToneStyle]:
    return deepcopy(BUILTIN_TONES)


def validate_style(name: str, st: ToneStyle) -> None:
    for key, allowed in (("case", CASES), ("final_period", PERIODS), ("layout", LAYOUTS)):
        if getattr(st, key) not in allowed:
            raise ValueError(f"[tones.{name}] {key} must be one of: {', '.join(allowed)}")
    if not 1 <= st.short_sentences <= 50:
        raise ValueError(f"[tones.{name}] short_sentences must be between 1 and 50")


# Built-in rules, checked in this order: email, work, personal.
# App ids (Wayland app_id or X11 WM_CLASS) are split at ".", "-", "_" and spaces;
# a rule matches when its parts appear in that order ("teams-for-linux",
# "org.telegram.desktop"), so "element" does not catch "io.elementary.code".
# Title keywords are only used for browser windows, where the page title is all
# we can see (Linux has no portable way to read the URL).
APP_IDS = {
    "email": ["thunderbird", "betterbird", "evolution", "geary", "kmail", "kmail2", "mailspring",
              "claws-mail", "trojita", "sylpheed", "tutanota-desktop"],
    "work": ["slack", "teams-for-linux", "teams", "zoom", "mattermost", "rocket.chat", "zulip",
             "element", "riot", "webex", "skypeforlinux"],
    "personal": ["whatsapp", "zapzap", "wasistlos", "telegram", "telegramdesktop", "signal", "discord",
                 "vesktop", "webcord", "legcord", "armcord", "caprine", "beeper", "fractal", "nheko",
                 "dino", "gajim", "session-desktop", "threema"],
}
TITLE_KEYWORDS = {
    "email": ["gmail", "outlook", "proton mail", "fastmail", "yahoo mail", "tuta mail", "tutanota",
              "zoho mail", "superhuman", "inbox", "gelen kutusu"],
    "work": ["slack", "microsoft teams", "google chat", "mattermost", "zulip", "linkedin", "basecamp"],
    "personal": ["whatsapp", "telegram", "discord", "messenger", "instagram"],
}
BROWSERS = ["firefox", "librewolf", "floorp", "zen", "zen-browser", "waterfox", "chrome", "chromium",
            "brave", "brave-browser", "vivaldi", "opera", "microsoft-edge", "msedge", "epiphany",
            "falkon", "konqueror", "qutebrowser", "crx"]


def _parts(s: str) -> list[str]:
    return [p for p in re.split(r"[.\-_\s]+", s.lower()) if p]


def _id_matches(patterns: Iterable[str], app_id: str) -> bool:
    have = _parts(app_id)
    for pat in patterns:
        want = _parts(pat)
        n = len(want)
        if n and any(have[i:i + n] == want for i in range(len(have) - n + 1)):
            return True
    return False


def _title_matches(keywords: Iterable[str], title: str) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(k)}(?!\w)", title, re.IGNORECASE) for k in keywords)


# Webmail shows the signed-in address in the tab title ("Inbox - you@company.com -
# Company Mail"); Google Workspace titles don't say "Gmail" at all.
_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def is_browser(app_id: str) -> bool:
    return _id_matches(BROWSERS, app_id)


def _custom_matches(patterns: Iterable[str], *haystacks: str) -> bool:
    return any(p and any(p in h for h in haystacks) for p in (s.strip().lower() for s in patterns))


def classify(app_id: str, title: str = "", custom: dict[str, list[str]] | None = None) -> str:
    """Return the category for a window.

    ``custom`` maps a category to user patterns: case-insensitive text found in
    the app id or the window title. They always win over the built-in lists.
    """
    a, t = app_id.lower().strip(), title.strip()
    if not a and not t:
        return "other"
    for cat in CATEGORIES:  # user rules first; "other" lets them opt an app out
        if custom and _custom_matches(custom.get(cat, []), a, t.lower()):
            return cat
    browser = is_browser(a)
    for cat in ("email", "work", "personal"):
        if browser and (_title_matches(TITLE_KEYWORDS[cat], t) or (cat == "email" and _ADDRESS.search(t))):
            return cat
        if not browser and _id_matches(APP_IDS[cat], a):
            return cat
    return "other"


# ---------------------------------------------------------------- shaping

_SENTENCE_END = re.compile(r"[.!?…]+(?=\s|$)")
_WORD = re.compile(r"[^\W\d_][\w'’-]*", re.UNICODE)

# Built-in email phrases (English and Turkish), matched case-insensitively at
# the very start (greetings) or near the end (closings) of a dictation.
GREETINGS = ["hi", "hello", "hey", "dear", "good morning", "good afternoon", "good evening",
             "merhaba", "selam", "selamlar", "sayın", "değerli", "günaydın", "iyi günler"]
CLOSINGS = ["best regards", "kind regards", "warm regards", "regards", "best wishes", "all the best",
            "best", "many thanks", "thanks", "thank you", "cheers", "sincerely", "yours sincerely",
            "saygılarımla", "iyi çalışmalar", "teşekkürler", "teşekkür ederim", "sevgiler",
            "kolay gelsin", "görüşmek üzere", "iyi çalışmalar dilerim", "iyi günler dilerim",
            "iyi akşamlar dilerim", "saygılarımı sunarım", "hoşça kalın", "sağlıcakla kalın"]
_PARAGRAPH = ["new paragraph", "yeni paragraf"]


def _phrase_pattern(phrase: str) -> str:
    """Regex for a phrase, any case. Dotted and dotless i are treated alike, because
    Python's case folding gets Turkish İ/ı wrong ("İ".lower() is "i̇")."""
    out = []
    for ch in phrase.strip():
        if ch in "iıİI":
            out.append("[iıİI]")
        elif ch.isspace():
            out.append(r"\s+")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def _alternation(phrases: Iterable[str]) -> str:
    ordered = sorted({p.strip() for p in phrases if p.strip()}, key=len, reverse=True)
    return "|".join(_phrase_pattern(p) for p in ordered)


def count_sentences(text: str) -> int:
    parts = [p for p in _SENTENCE_END.split(text) if p.strip()]
    return max(1, len(parts)) if text.strip() else 0


def _lower_first(word: str, language: str | None) -> str:
    c = word[0]
    if c == "İ":
        low = "i"  # "İ".lower() is "i̇" (with a combining dot) in Python
    elif c == "I" and language in ("tr", "az"):
        low = "ı"
    else:
        low = c.lower()
    return low + word[1:]


def _upper_first(word: str, language: str | None) -> str:
    c = word[0]
    up = "İ" if c == "i" and language in ("tr", "az") else c.upper()
    return up + word[1:]


def _protected(word: str, names: set[str]) -> bool:
    bare = word.strip("'’-")
    if bare in ("I", "I'm", "I’m", "I've", "I’ve", "I'll", "I’ll", "I'd", "I’d"):
        return True
    if len(bare) > 1 and bare.isupper():  # acronyms: NASA, API
        return True
    return bare.lower() in names


def _sentence_case(text: str, mode: str, names: set[str], language: str | None) -> str:
    """Change the first letter of every sentence: "capitalize" or "lowercase"."""
    out, at_start, pos = [], True, 0
    for m in _WORD.finditer(text):
        between = text[pos:m.start()]
        if pos and (_SENTENCE_END.search(between + " ") or "\n" in between):
            at_start = True
        word = m.group()
        if at_start:
            if mode == "lowercase" and word[0].isupper() and not _protected(word, names):
                word = _lower_first(word, language)
            elif mode == "capitalize" and word[0].islower():
                word = _upper_first(word, language)
        out += [between, word]
        at_start, pos = False, m.end()
    out.append(text[pos:])
    return "".join(out)


def _final_period(text: str, mode: str, short: int) -> str:
    if mode == "add":
        return text + "." if text and text[-1].isalnum() else text
    if mode == "remove" or (mode == "remove_short" and count_sentences(text) <= short):
        # Only a plain period: "?" and "!" carry meaning, "..." is deliberate.
        if text.endswith(".") and not text.endswith(".."):
            return text[:-1].rstrip()
    return text


def _paragraphs(text: str) -> str:
    """Turn a spoken "new paragraph" into a blank line."""
    pat = re.compile(rf"[\s,.;:]*(?<!\w)(?:{_alternation(_PARAGRAPH)})(?!\w)[\s,.;:]*", re.IGNORECASE)
    return "\n\n".join(p.strip() for p in pat.split(text) if p.strip())


def _split_email(text: str, st: ToneStyle) -> tuple[str, str, str, str]:
    """Return (greeting, body, closing, name). Any part may be empty."""
    greeting = closing = name = ""
    words = _alternation(GREETINGS + st.greetings)
    # The greeting is the first clause (up to the first comma or sentence end) when
    # it opens with a greeting word and has at most six words ("Hi John," /
    # "Merhaba Ahmet Bey."), or ends with one and has at most four, as Turkish often
    # puts it ("Ahmet Bey merhaba.").
    m = re.match(r"([^,.!?\n]*)(?:[,.!?]\s*|$)", text)
    if m:
        first = m.group(1).strip()
        n = len(first.split())
        if (n <= 6 and re.match(rf"(?:{words})(?!\w)", first, re.IGNORECASE)) or \
                (n <= 4 and re.search(rf"(?<!\w)(?:{words})$", first, re.IGNORECASE)):
            greeting, text = first + ",", text[m.end():]
    # The closing must start a sentence (or follow a comma) and may only be
    # followed by a name of up to three capitalised words: "Best regards, Alex".
    closing_re = re.compile(
        rf"(?:^|(?<=[.!?,\n]))\s*({_alternation(CLOSINGS + st.closings)})(?!\w)[,.!]?\s*"
        rf"((?:[^\W\d_][\w'’-]*\s*){{0,3}}?)[.!]?\s*$", re.IGNORECASE)
    c = closing_re.search(text)
    if c and all(w[0].isupper() for w in c.group(2).split()):
        closing, name = c.group(1).strip(), c.group(2).strip()
        text = text[:c.start()].rstrip(" ,")
    return greeting, text.strip(), closing, name


def apply(text: str, style: ToneStyle, names: Iterable[str] = (), language: str | None = None,
          continuing: bool = False) -> str:
    """Shape transcribed text with a tone. ``names`` are vocabulary terms to keep capitalised.

    ``continuing``: this dictation goes into the same email as the previous one, so
    in the email layout it starts a new paragraph (unless it opens with a greeting).
    """
    from .text import apply_replacements, parse_replacements

    text = text.strip()
    if not text:
        return text
    if style.replacements:
        text = apply_replacements(text, parse_replacements("\n".join(style.replacements)))
    protected = {w.lower() for n in names for w in n.split()}

    def shape(part: str) -> str:
        if style.case != "unchanged":
            part = _sentence_case(part, style.case, protected, language)
        return _final_period(part, style.final_period, style.short_sentences)

    if style.layout != "email":
        return shape(text)

    greeting, body, closing, name = _split_email(text, style)
    body = "\n\n".join(shape(p) for p in _paragraphs(body).split("\n\n")) if body else ""
    if greeting:
        greeting = _upper_first(greeting, language)
    sign = style.signature.replace("\\n", "\n").strip() or name
    blocks = [b for b in (greeting, body) if b]
    if closing:
        blocks.append(_upper_first(closing, language) + (f",\n{sign}" if sign else ""))
    out = "\n\n".join(blocks)
    return "\n\n" + out if continuing and not greeting and out else out
