import tomllib
from importlib import resources

import pytest

from hushtype import config as C


def test_defaults():
    cfg = C.parse({})
    assert cfg.model.name == "large-v3-turbo"
    assert cfg.hotkey.hold == ["ctrl", "super"]


def test_overrides_and_int_to_float():
    cfg = C.parse({"model": {"language": "en"}, "audio": {"min_duration": 1}})
    assert cfg.model.language == "en"
    assert cfg.audio.min_duration == 1.0


@pytest.mark.parametrize("data", [
    {"nope": {}},
    {"model": {"nope": 1}},
    {"model": {"beam_size": "5"}},
    {"output": {"paste": 1}},
    {"model": {"device": "tpu"}},
    {"model": 3},
    {"__init__": {}},
])
def test_invalid(data):
    with pytest.raises(C.ConfigError):
        C.parse(data)


def test_shipped_example_config_is_valid():
    raw = resources.files("hushtype.examples").joinpath("config.toml").read_text()
    cfg = C.parse(tomllib.loads(raw))
    assert cfg == C.Config()  # the example documents the defaults exactly


def test_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("HUSHTYPE_PASTE", "0")
    monkeypatch.setenv("HUSHTYPE_LANGUAGE", "de")
    cfg = C.load(tmp_path / "missing.toml")
    assert cfg.output.paste is False
    assert cfg.model.language == "de"


def test_dumps_writes_only_changes_and_round_trips():
    cfg = C.Config()
    assert "[" not in C.dumps(cfg).split("\n", 3)[-1]  # nothing but the header
    cfg.model.language = 'tr"x'
    cfg.hotkey.hold = ["ctrl", "alt"]
    cfg.audio.beep_volume = 0.5
    cfg.output.paste = False
    back = C.parse(tomllib.loads(C.dumps(cfg)))
    assert back == cfg


def test_budget_bounds():
    with pytest.raises(C.ConfigError):
        C.parse({"text": {"prompt_token_budget": 999}})


def test_tone_and_apps_sections():
    cfg = C.parse({"tone": {"enabled": True, "personal": "very_casual"},
                   "apps": {"work": ["discord"]}})
    assert cfg.tone.enabled and cfg.tone.personal == "very_casual" and cfg.tone.email == "email"
    assert cfg.apps.rules()["work"] == ["discord"]
    with pytest.raises(C.ConfigError):
        C.parse({"tone": {"work": "loud"}})  # unknown tone name
    with pytest.raises(C.ConfigError):
        C.parse({"apps": {"work": [1, 2]}})


def test_tone_round_trip():
    cfg = C.parse({"tone": {"enabled": True}, "apps": {"personal": ["element"]}})
    again = C.parse(tomllib.loads(C.dumps(cfg)))
    assert again.tone.enabled and again.apps.personal == ["element"]


def test_tones_tables():
    cfg = C.parse({"tones": {"email": {"signature": "Alex\nAcme"},
                             "client": {"case": "capitalize", "replacements": ["gonna => going to"]}},
                   "tone": {"work": "client"}})
    assert cfg.tones["email"].layout == "email"            # built-in fields kept
    assert cfg.tones["email"].signature == "Alex\nAcme"
    assert cfg.style_for("work").case == "capitalize"
    assert cfg.style_for("email").final_period == "add"
    again = C.parse(tomllib.loads(C.dumps(cfg)))
    assert again == cfg
    assert "[tones.formal]" not in C.dumps(cfg)              # unchanged built-ins stay out


@pytest.mark.parametrize("bad", [
    {"tones": {"Bad Name": {}}},
    {"tones": {"x": {"case": "shout"}}},
    {"tones": {"x": {"nope": 1}}},
    {"tones": {"x": {"closings": [1]}}},
    {"tones": {"x": 3}},
])
def test_tones_errors(bad):
    with pytest.raises(C.ConfigError):
        C.parse(bad)


def test_custom_tone_without_changes_round_trips():
    cfg = C.parse({"tones": {"plain": {}}})
    assert "plain" in C.parse(tomllib.loads(C.dumps(cfg))).tones
