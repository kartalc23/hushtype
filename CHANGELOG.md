# Changelog

## 0.2.0 (2026-11-26)

- **Settings window** (`hushtype settings`, tray → Settings, app menu action): language, model,
  device, shortcut recorder, microphone, sounds, behaviour, vocabulary with drag-and-drop
  ordering, replacements with a live tester, and the style sample
- **Token-based prompt budget** instead of fixed limits (35 terms / 35 words): vocabulary now fills
  Whisper's prompt window by actual token count, usually fitting many more terms. The settings
  window shows a live meter and dims terms that don't fit
- Live reload on `SIGHUP`; the settings window restarts hushtype only when needed
- `hushtype toggle` and the settings window detect the running instance via its lock, so stale
  lock files no longer count
- Duplicate vocabulary terms are ignored
- Config: `[text] prompt_token_budget` replaces `max_vocabulary_terms` and `max_style_words`

## 0.1.0 (2026-11-26)

First public release.

- Hold-to-talk dictation with faster-whisper, GPU with automatic CPU fallback
- Hotkeys via evdev (any combo) or the xdg-desktop-portal GlobalShortcuts API
- Pastes through a uinput virtual keyboard, with wtype / ydotool / xdotool fallbacks
- GTK 4 layer-shell overlay and tray icon
- TOML config, vocabulary, style prompt and replacement rules
- `transcribe`, `mics`, `toggle`, `doctor`, `init`, `install-desktop` commands
