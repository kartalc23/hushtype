# Changelog

## Unreleased

- **Per-app tone** (off by default): hushtype sorts the window you dictate into as personal chat,
  work chat, email or everything else and shapes the text with that kind's tone
- Tones are customisable rule sets: sentence case, final period, an email layout, a signature,
  extra greetings and closings, and tone-only replacements. Built in: `default`, `formal`,
  `casual`, `very_casual`, `email`; change them or add your own under `[tones.NAME]` or in
  Settings → Tone
- **Email layout**: greeting and closing on their own lines, "new paragraph" / "yeni paragraf"
  starts a paragraph, signature under the closing (English and Turkish phrases built in).
  Dictating in several takes works too: later takes into the same window start a new paragraph
  (within 5 minutes). Turkish word order is recognised too ("Ahmet Bey merhaba",
  "İyi çalışmalar dilerim").
- Webmail is recognised by the address or "Inbox" in the tab title, which covers Google Workspace
- Focused window detection on KDE Plasma (temporary KWin script over D-Bus), sway, Hyprland and X11
- `hushtype app` shows a window's app id, category and tone; `hushtype doctor` reports whether
  focus detection works

## 0.2.1 (2026-11-27)

- **Arch package**: a PKGBUILD in `packaging/aur`, installed with `paru -Bi packaging/aur`;
  `python-ctranslate2-cuda` adds the GPU
- `hushtype doctor` asks CTranslate2 for CUDA GPUs instead of looking for pip's CUDA folders, so
  GPU support from distro packages is detected too
- `hushtype doctor` accepts a system-wide desktop entry (`/usr/share/applications`)

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
