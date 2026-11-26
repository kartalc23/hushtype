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
