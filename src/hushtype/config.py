"""Configuration: defaults, the TOML config file and environment overrides.

The config lives at ``$XDG_CONFIG_HOME/hushtype/config.toml`` (usually
``~/.config/hushtype/config.toml``). Every key is optional; anything missing
falls back to the defaults below. ``hushtype init`` writes a commented copy.
"""

from __future__ import annotations

import os
import re
import tomllib
from copy import deepcopy
from dataclasses import dataclass, field, fields
from pathlib import Path

from .tone import BUILTIN_TONES, CATEGORIES, ToneStyle, builtin_tones, validate_style

APP = "hushtype"


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback) / APP


CONFIG_DIR = (
    Path(os.environ["HUSHTYPE_CONFIG_DIR"])
    if os.environ.get("HUSHTYPE_CONFIG_DIR")
    else _xdg("XDG_CONFIG_HOME", ".config")
)
CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache")
STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state")
MODELS_DIR = CACHE_DIR / "models"
LOG_FILE = STATE_DIR / "hushtype.log"
CONFIG_FILE = CONFIG_DIR / "config.toml"


@dataclass
class ModelConfig:
    name: str = "large-v3-turbo"
    # ISO 639-1 code ("en", "de", "tr", ...) or "auto". Auto-detection works,
    # but on very short clips it can guess wrong; pin your language if you
    # always dictate in one.
    language: str = "auto"
    device: str = "auto"  # auto | cuda | cpu
    beam_size: int = 5


@dataclass
class HotkeyConfig:
    # evdev path: modifier-only combos work, held down = recording.
    hold: list[str] = field(default_factory=lambda: ["ctrl", "super"])
    quit: list[str] = field(default_factory=lambda: ["ctrl", "super", "shift", "q"])
    # Desktop portal path (used when you are not in the `input` group). The
    # portal refuses modifier-only shortcuts, so a regular key is required.
    portal_hold: str = "CTRL+LOGO+z"
    portal_quit: str = "CTRL+SHIFT+LOGO+q"


@dataclass
class AudioConfig:
    microphone: str = ""  # empty = system default; a name fragment or a device index
    min_duration: float = 0.4  # seconds; shorter recordings are ignored
    silence_warning: float = 0.001  # RMS below this logs a "wrong mic?" warning
    beeps: bool = True
    beep_volume: float = 0.28
    beep_duration: float = 0.14
    # Bluetooth headsets put an idle audio stream to sleep and swallow the
    # first ~150 ms. If the start beep is often inaudible, try 0.16.
    beep_lead_silence: float = 0.0


@dataclass
class OutputConfig:
    paste: bool = True  # false = only copy to the clipboard, never send Ctrl+V
    restore_clipboard: bool = True


@dataclass
class UiConfig:
    overlay: bool = True
    tray: bool = True


@dataclass
class TextConfig:
    # Paths are relative to the config directory.
    vocabulary: str = "vocabulary.txt"
    style: str = "style.txt"
    replacements: str = "replacements.txt"
    # Tokens shared by the style sample and the vocabulary. Whisper's context is
    # 448 tokens; whatever the prompt takes is unavailable for the transcript,
    # and overflowing it fails with "maximum decoding length must be > 0".
    # The style sample is used first (up to half), vocabulary terms fill the
    # rest in file order. 224 mirrors how Whisper was trained.
    prompt_token_budget: int = 224


@dataclass
class ToneConfig:
    # Shape the text for the app you dictate into: the tone used for each kind
    # of app. Tones are defined under [tones.NAME]; see [apps] to sort apps.
    enabled: bool = False
    personal: str = "casual"  # WhatsApp, Telegram, Signal, Discord ...
    work: str = "default"  # Slack, Teams, Zoom, Mattermost ...
    email: str = "email"  # Thunderbird, Evolution, Gmail in a browser ...
    other: str = "default"  # everything else


@dataclass
class AppsConfig:
    # Your own rules, checked before the built-in lists: text found in the app
    # id or the window title (see `hushtype app`). "other" opts an app out.
    personal: list[str] = field(default_factory=list)
    work: list[str] = field(default_factory=list)
    email: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)

    def rules(self) -> dict[str, list[str]]:
        return {"personal": self.personal, "work": self.work, "email": self.email, "other": self.other}


@dataclass
class Config:
    model: ModelConfig = field(default_factory=ModelConfig)
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    text: TextConfig = field(default_factory=TextConfig)
    tone: ToneConfig = field(default_factory=ToneConfig)
    apps: AppsConfig = field(default_factory=AppsConfig)
    # Built-in tones plus the user's; [tones.NAME] tables change or add them.
    tones: dict[str, ToneStyle] = field(default_factory=builtin_tones)

    def style_for(self, category: str) -> ToneStyle:
        return self.tones.get(getattr(self.tone, category), ToneStyle())

    def text_path(self, name: str) -> Path:
        p = Path(getattr(self.text, name)).expanduser()
        return p if p.is_absolute() else CONFIG_DIR / p


class ConfigError(ValueError):
    pass


def _apply(section_obj, data: dict, section: str) -> None:
    known = {f.name: f for f in fields(section_obj)}
    for key, value in data.items():
        if key not in known:
            raise ConfigError(f"unknown key [{section}] {key}")
        current = getattr(section_obj, key)
        if isinstance(current, bool) and not isinstance(value, bool):
            raise ConfigError(f"[{section}] {key} must be true or false")
        if isinstance(current, float) and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if type(current) is not type(value):
            raise ConfigError(
                f"[{section}] {key} must be {type(current).__name__}, got {type(value).__name__}"
            )
        setattr(section_obj, key, value)


TONE_NAME = re.compile(r"^[a-z0-9_]{1,32}$")


def _parse_tones(cfg: Config, data) -> None:
    if not isinstance(data, dict):
        raise ConfigError("[tones] must contain tables like [tones.casual]")
    for name, values in data.items():
        if not TONE_NAME.match(name):
            raise ConfigError(f"tone name '{name}': use a-z, 0-9 and _ only")
        if not isinstance(values, dict):
            raise ConfigError(f"[tones.{name}] must be a table")
        style = deepcopy(BUILTIN_TONES.get(name, ToneStyle()))
        _apply(style, values, f"tones.{name}")
        for key in ("greetings", "closings", "replacements"):
            if not all(isinstance(x, str) for x in getattr(style, key)):
                raise ConfigError(f"[tones.{name}] {key} must be a list of strings")
        try:
            validate_style(name, style)
        except ValueError as e:
            raise ConfigError(str(e)) from None
        cfg.tones[name] = style


def parse(data: dict) -> Config:
    cfg = Config()
    data = dict(data)
    if "tones" in data:
        _parse_tones(cfg, data.pop("tones"))
    for section, values in data.items():
        if section not in {f.name for f in fields(cfg)}:
            raise ConfigError(f"unknown section [{section}]")
        if not isinstance(values, dict):
            raise ConfigError(f"[{section}] must be a table")
        _apply(getattr(cfg, section), values, section)
    if cfg.model.device not in ("auto", "cuda", "cpu"):
        raise ConfigError('[model] device must be "auto", "cuda" or "cpu"')
    for cat in CATEGORIES:
        if getattr(cfg.tone, cat) not in cfg.tones:
            raise ConfigError(f"[tone] {cat}: unknown tone '{getattr(cfg.tone, cat)}' "
                              f"(known: {', '.join(cfg.tones)})")
        if not all(isinstance(x, str) for x in getattr(cfg.apps, cat)):
            raise ConfigError(f"[apps] {cat} must be a list of strings")
    if not 32 <= cfg.text.prompt_token_budget <= 300:
        raise ConfigError("[text] prompt_token_budget must be between 32 and 300")
    return cfg


# ---------------------------------------------------------------- writing


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"cannot write {type(v).__name__} to TOML")


def dumps(cfg: Config) -> str:
    """Serialize only the values that differ from the defaults, so the file stays short."""
    default = Config()
    out = ["# hushtype configuration, written by `hushtype settings`.",
           "# Only values that differ from the defaults are listed. See the README for all options.", ""]
    for section in fields(cfg):
        if section.name == "tones":
            continue
        cur, base = getattr(cfg, section.name), getattr(default, section.name)
        lines = [f"{f.name} = {_toml_value(getattr(cur, f.name))}"
                 for f in fields(cur) if getattr(cur, f.name) != getattr(base, f.name)]
        if lines:
            out += [f"[{section.name}]", *lines, ""]
    for name, style in cfg.tones.items():
        base = BUILTIN_TONES.get(name, ToneStyle())
        lines = [f"{f.name} = {_toml_value(getattr(style, f.name))}"
                 for f in fields(style) if getattr(style, f.name) != getattr(base, f.name)]
        if lines or name not in BUILTIN_TONES:
            out += [f"[tones.{name}]", *lines, ""]
    return "\n".join(out)


def save(cfg: Config, path: Path | None = None) -> None:
    path = path or CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dumps(cfg), encoding="utf-8")
    tmp.replace(path)


def load(path: Path | None = None) -> Config:
    path = path or CONFIG_FILE
    if path.exists():
        with open(path, "rb") as f:
            cfg = parse(tomllib.load(f))
    else:
        cfg = Config()
    # Environment overrides, handy for one-off runs and debugging.
    if os.environ.get("HUSHTYPE_MICROPHONE") is not None:
        cfg.audio.microphone = os.environ["HUSHTYPE_MICROPHONE"]
    if os.environ.get("HUSHTYPE_PASTE") == "0":
        cfg.output.paste = False
    if os.environ.get("HUSHTYPE_LANGUAGE"):
        cfg.model.language = os.environ["HUSHTYPE_LANGUAGE"]
    return cfg
