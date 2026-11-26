# Contributing

Thanks for helping! hushtype is a small project, so the process is light.

## Reporting bugs

Please include the output of `hushtype doctor`, your distro, desktop
(KDE/GNOME/Hyprland/...), Wayland or X11, GPU, and the relevant part of
`~/.local/state/hushtype/hushtype.log`. The log never contains dictated text,
but check it before posting anyway.

## Development setup

```bash
git clone https://github.com/kartalc23/hushtype.git
cd hushtype
uv venv --system-site-packages .venv      # or: python -m venv --system-site-packages .venv
uv pip install -e ".[cuda,dev]"
.venv/bin/hushtype                         # run from source
```

`--system-site-packages` is needed for the distro's GTK bindings (overlay and tray).

## Checks

```bash
ruff check .
pytest
```

CI runs both on every pull request. The unit tests cover the pure-Python parts
(config, text processing); hotkeys, audio and the overlay need a real desktop,
so please describe how you tested those in your PR.

To regenerate the settings screenshots in `docs/assets`, use a demo config, never your own:

```bash
HUSHTYPE_CONFIG_DIR=/path/to/demo HUSHTYPE_SETTINGS_SCREENSHOT=general:docs/assets/settings-general.png hushtype settings
```

## Code layout

| Module | Responsibility |
|---|---|
| `cli.py` | Commands, systemd scope and CUDA library setup |
| `app.py` | Recording, transcription worker, pasting, lifecycle |
| `hotkeys.py` | evdev and portal hotkeys, virtual keyboard, clipboard |
| `overlay.py` | GTK 4 layer-shell status pill |
| `tray.py` | Tray icon (separate process: GTK 3 and 4 can't share one) |
| `settings.py` | Settings window (GTK 4 + libadwaita), applies changes via SIGHUP or restart |
| `config.py` / `text.py` | Config file (read and write), vocabulary, style, replacements, prompt budget |

## Guidelines

- Keep it offline. No telemetry, no network calls besides the one-time model download.
- Never write dictated text to disk or logs.
- Small focused PRs with a clear description beat large ones.
