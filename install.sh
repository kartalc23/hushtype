#!/usr/bin/env bash
# hushtype installer: system packages, an isolated venv, the `hushtype` command
# and an app menu entry. Run from a clone of the repository:
#
#   ./install.sh               # install
#   ./install.sh --autostart   # install and start on login
#   ./install.sh --cpu         # skip the NVIDIA CUDA libraries
#
# Nothing is installed system-wide except the distro packages listed below.
set -euo pipefail

SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/hushtype"
VENV="$PREFIX/venv"
BIN="$HOME/.local/bin"

autostart=0
cpu=0
for arg in "$@"; do
    case "$arg" in
        --autostart) autostart=1 ;;
        --cpu) cpu=1 ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }

# --- system packages ---------------------------------------------------------
# GTK (overlay, tray) comes from the distro because PyGObject does not install
# cleanly from pip. The venv is created with --system-site-packages to see it.
if command -v pacman >/dev/null; then
    pkgs=(python python-gobject gtk4 gtk4-layer-shell libayatana-appindicator wl-clipboard portaudio cairo pkgconf)
    missing=()
    for p in "${pkgs[@]}"; do pacman -Qq "$p" >/dev/null 2>&1 || missing+=("$p"); done
    if ((${#missing[@]})); then say "installing: ${missing[*]}"; sudo pacman -S --needed "${missing[@]}"; fi
elif command -v apt-get >/dev/null; then
    pkgs=(python3-venv python3-gi gir1.2-gtk-4.0 gir1.2-ayatanaappindicator3-0.1 libgtk4-layer-shell0 gir1.2-gtk4layershell-1.0
          wl-clipboard libportaudio2 libcairo2-dev pkg-config python3-dev)
    say "installing (apt): ${pkgs[*]}"
    sudo apt-get install -y "${pkgs[@]}" || echo "some packages failed; the overlay may be unavailable (see README)"
elif command -v dnf >/dev/null; then
    pkgs=(python3-gobject gtk4 gtk4-layer-shell libayatana-appindicator-gtk3 wl-clipboard portaudio cairo-devel pkgconf python3-devel)
    say "installing (dnf): ${pkgs[*]}"
    sudo dnf install -y "${pkgs[@]}" || echo "some packages failed; the overlay may be unavailable (see README)"
else
    echo "unknown distro: install GTK4, gtk4-layer-shell, PyGObject, AppIndicator, wl-clipboard and PortAudio yourself"
fi

# --- venv ----------------------------------------------------------------------
extra=""
# Detect the card, not a working driver: right after a driver update nvidia-smi
# fails until reboot, and we still want the CUDA libraries installed.
has_nvidia() {
    [ -e /proc/driver/nvidia/version ] && return 0
    command -v lspci >/dev/null && lspci -d 10de: 2>/dev/null | grep -qiE 'vga|3d' && return 0
    grep -qs '^0x10de$' /sys/class/drm/card*/device/vendor
}
if ((cpu == 0)) && has_nvidia; then
    extra="[cuda]"
    say "NVIDIA GPU found, installing CUDA libraries into the venv (~1 GB)"
fi

mkdir -p "$PREFIX" "$BIN"
if command -v uv >/dev/null; then
    [ -x "$VENV/bin/python" ] || uv venv --python /usr/bin/python3 --system-site-packages "$VENV"
    VIRTUAL_ENV="$VENV" uv pip install --upgrade "$SRC$extra"
else
    [ -x "$VENV/bin/python" ] || /usr/bin/python3 -m venv --system-site-packages "$VENV"
    "$VENV/bin/pip" install --upgrade pip >/dev/null
    "$VENV/bin/pip" install --upgrade "$SRC$extra"
fi
ln -sf "$VENV/bin/hushtype" "$BIN/hushtype"
say "installed: $BIN/hushtype"

# --- config and desktop entry -------------------------------------------------
"$VENV/bin/hushtype" init
if ((autostart)); then "$VENV/bin/hushtype" install-desktop --autostart; else "$VENV/bin/hushtype" install-desktop; fi

case ":$PATH:" in *":$BIN:"*) ;; *) echo; echo "note: $BIN is not on your PATH; add it to use 'hushtype' directly";; esac

echo
"$VENV/bin/hushtype" doctor || true
echo
if ! id -nG | tr ' ' '\n' | grep -qx input; then
    echo "Optional: for hold-to-talk on Ctrl+Super (or any keys), run"
    echo "    sudo usermod -aG input \$USER"
    echo "and REBOOT (logging out is not enough). Without it, hushtype uses the"
    echo "desktop portal shortcut Ctrl+Super+Z."
fi
echo "Start it from your app menu or run: hushtype   (first start downloads the model, ~1.6 GB)"
