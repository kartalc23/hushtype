#!/usr/bin/env bash
# Removes everything install.sh created. Distro packages are left alone.
#   ./uninstall.sh            # keep your config (~/.config/hushtype)
#   ./uninstall.sh --purge    # also delete config, models and logs
set -eu

PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/hushtype"
# Only processes running from our venv's Python (the app and its tray icon).
pkill -TERM -f "^$PREFIX/venv/bin/python" 2>/dev/null || true
[ -x "$PREFIX/venv/bin/hushtype" ] && "$PREFIX/venv/bin/hushtype" uninstall-desktop || true
rm -f "$HOME/.local/bin/hushtype"
rm -rf "$PREFIX"
echo "removed $PREFIX and the hushtype command"

if [ "${1:-}" = "--purge" ]; then
    rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/hushtype" \
           "${XDG_CACHE_HOME:-$HOME/.cache}/hushtype" \
           "${XDG_STATE_HOME:-$HOME/.local/state}/hushtype"
    echo "removed config, downloaded models and logs"
else
    echo "kept your config, models (~/.cache/hushtype) and logs; use --purge to delete them"
fi
