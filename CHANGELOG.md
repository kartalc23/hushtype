# Changelog

## 0.1.0 (2026-11-26)

First public release.

- Hold-to-talk dictation with faster-whisper, GPU with automatic CPU fallback
- Hotkeys via evdev (any combo) or the xdg-desktop-portal GlobalShortcuts API
- Pastes through a uinput virtual keyboard, with wtype / ydotool / xdotool fallbacks
- GTK 4 layer-shell overlay and tray icon
- TOML config, vocabulary, style prompt and replacement rules
- `transcribe`, `mics`, `toggle`, `doctor`, `init`, `install-desktop` commands
