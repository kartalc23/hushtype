"""Command line entry point."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
from importlib import resources
from pathlib import Path

from . import __version__
from . import config as C

DESKTOP_FILE = Path.home() / ".local/share/applications/hushtype.desktop"
AUTOSTART_FILE = Path.home() / ".config/autostart/hushtype.desktop"
ICON_FILE = Path.home() / ".local/share/icons/hicolor/256x256/apps/hushtype.png"


# ---------------------------------------------------------------- runtime setup


def _enter_app_scope(argv: list[str]) -> None:
    """Re-run ourselves inside an ``app-hushtype-<pid>.scope`` systemd unit.

    The desktop portal identifies apps by the systemd scope they run in
    (``app-<app id>-<random>.scope``) and looks up ``<app id>.desktop``. Without
    an app id, GlobalShortcuts fails with "An app id is required". The evdev
    path does not care, so failure here is not fatal.
    """
    if os.environ.get("HUSHTYPE_SCOPED") or not shutil.which("systemd-run"):
        return
    os.environ["HUSHTYPE_SCOPED"] = "1"
    cmd = ["systemd-run", "--user", "--scope", "--quiet", "--collect",
           f"--unit=app-hushtype-{os.getpid()}", sys.executable, "-m", "hushtype", *argv]
    try:
        os.execvp(cmd[0], cmd)
    except OSError:
        pass


def _expose_pip_cuda() -> None:
    """Make pip-installed CUDA libraries (cuBLAS, cuDNN) findable.

    ctranslate2 dlopen()s them, and on Linux the library search path cannot be
    changed after the process starts. If the venv's ``nvidia/*/lib`` folders
    are not on LD_LIBRARY_PATH yet, add them and re-exec once (~50 ms).
    """
    libs = [str(p) for p in Path(sys.prefix).glob("lib/python3*/site-packages/nvidia/*/lib")]
    current = os.environ.get("LD_LIBRARY_PATH", "")
    missing = [p for p in libs if p not in current.split(os.pathsep)]
    if missing and not os.environ.get("HUSHTYPE_CUDA_REEXEC"):
        os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(missing + ([current] if current else []))
        os.environ["HUSHTYPE_CUDA_REEXEC"] = "1"
        os.execv(sys.executable, [sys.executable, "-m", "hushtype", *sys.argv[1:]])


def _load_config() -> C.Config:
    try:
        return C.load()
    except (C.ConfigError, ValueError) as e:
        sys.exit(f"hushtype: invalid config {C.CONFIG_FILE}: {e}")


# ---------------------------------------------------------------- commands


def cmd_run(args, argv) -> None:
    cfg = _load_config()
    _enter_app_scope(argv)
    if cfg.model.device != "cpu":
        _expose_pip_cuda()
    from .app import App, log

    try:
        App(cfg).run()
    except KeyboardInterrupt:
        log("exited (keyboard interrupt)")
    except Exception as e:
        log(f"[fatal] {type(e).__name__}: {e}")
        raise


def _decode_audio(path: str, rate: int):
    """Decode any audio file to mono float32 at `rate` Hz.

    Done here rather than with faster_whisper.decode_audio, which passes
    arguments that newer PyAV releases no longer accept.
    """
    import av
    import numpy as np

    chunks = []
    resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
    with av.open(path) as container:
        for frame in container.decode(audio=0):
            chunks.extend(f.to_ndarray() for f in resampler.resample(frame))
    chunks.extend(f.to_ndarray() for f in resampler.resample(None))
    if not chunks:
        sys.exit(f"hushtype: no audio in {path}")
    return np.concatenate(chunks, axis=1).flatten().astype(np.float32) / 32768.0


def cmd_transcribe(args, argv) -> None:
    cfg = _load_config()
    if args.language:
        cfg.model.language = args.language
    if cfg.model.device != "cpu":
        _expose_pip_cuda()
    import time

    from .app import SAMPLE_RATE, Transcriber

    audio = _decode_audio(args.file, SAMPLE_RATE)
    tr = Transcriber(cfg)
    t0 = time.time()
    result = tr.transcribe(audio)
    elapsed = time.time() - t0
    duration = len(audio) / SAMPLE_RATE
    print(f"[{duration:.1f} s audio in {elapsed:.1f} s, {duration / elapsed:.1f}x realtime]",
          file=sys.stderr)
    print(result)


def cmd_mics(args, argv) -> None:
    from .app import list_microphones

    list_microphones(print)


def cmd_toggle(args, argv) -> None:
    from .app import running_pid

    pid = running_pid()
    if pid is None:
        sys.exit("hushtype is not running")
    try:
        os.kill(pid, signal.SIGUSR1)
    except ProcessLookupError:
        sys.exit("hushtype is not running (stale lock file)")


def _copy_resource(name: str, dest: Path, force: bool) -> None:
    if dest.exists() and not force:
        print(f"  kept     {dest}")
        return
    dest.write_text(resources.files("hushtype.examples").joinpath(name).read_text(encoding="utf-8"),
                    encoding="utf-8")
    print(f"  wrote    {dest}")


def cmd_init(args, argv) -> None:
    C.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    print(f"config directory: {C.CONFIG_DIR}")
    for name in ("config.toml", "vocabulary.txt", "style.txt", "replacements.txt"):
        _copy_resource(name, C.CONFIG_DIR / name, args.force)


def _executable() -> str:
    exe = shutil.which("hushtype")
    if exe:
        return exe
    return f"{sys.executable} -m hushtype"


def cmd_install_desktop(args, argv) -> None:
    from .tray import icon

    ICON_FILE.parent.mkdir(parents=True, exist_ok=True)
    icon(False, 256).save(ICON_FILE)
    entry = resources.files("hushtype.examples").joinpath("hushtype.desktop").read_text(encoding="utf-8")
    entry = entry.replace("@EXEC@", _executable()).replace("@ICON@", str(ICON_FILE))
    DESKTOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    DESKTOP_FILE.write_text(entry, encoding="utf-8")
    print(f"wrote {DESKTOP_FILE}")
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(DESKTOP_FILE.parent)], check=False,
                       stderr=subprocess.DEVNULL)
    if args.autostart:
        AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART_FILE.write_text(entry, encoding="utf-8")
        print(f"wrote {AUTOSTART_FILE} (starts on login)")


def cmd_uninstall_desktop(args, argv) -> None:
    for f in (DESKTOP_FILE, AUTOSTART_FILE, ICON_FILE):
        if f.exists():
            f.unlink()
            print(f"removed {f}")


def cmd_doctor(args, argv) -> None:
    """Print what works and what is missing, without starting anything."""
    ok, bad = "  ok  ", "  --  "
    print(f"hushtype {__version__}, Python {sys.version.split()[0]}")
    print(f"config: {C.CONFIG_FILE} ({'found' if C.CONFIG_FILE.exists() else 'not found, defaults in use'})")
    session = os.environ.get("XDG_SESSION_TYPE", "?")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "?")
    print(f"session: {session} / {desktop}")

    groups = subprocess.run(["id", "-nG"], capture_output=True, text=True).stdout.split()
    print((ok if "input" in groups else bad) + "input group (hold-to-talk with any keys via evdev)")
    print((ok if os.access("/dev/uinput", os.W_OK) else bad) + "/dev/uinput writable (sending Ctrl+V)")
    tools = ["wl-copy", "wl-paste"] if session == "wayland" else ["xclip"]
    for t in tools:
        print((ok if shutil.which(t) else bad) + f"{t} (clipboard)")
    print((ok if shutil.which("systemd-run") else bad) + "systemd-run (app id for the desktop portal)")
    print((ok if DESKTOP_FILE.exists() else bad) + f"desktop entry ({DESKTOP_FILE})")
    for mod, why in (("gi", "overlay + tray"), ("cairo", "click-through overlay"),
                     ("evdev", "hotkeys"), ("sounddevice", "microphone")):
        try:
            __import__(mod)
            print(ok + f"python module {mod} ({why})")
        except Exception:
            print(bad + f"python module {mod} ({why})")
    cuda = list(Path(sys.prefix).glob("lib/python3*/site-packages/nvidia/cublas"))
    print((ok if cuda else bad) + "pip CUDA libraries (install the [cuda] extra for GPU)")


# ---------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hushtype",
        description="Private, offline hold-to-talk dictation for Linux.",
    )
    p.add_argument("--version", action="version", version=f"hushtype {__version__}")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("run", help="start dictation (default)")
    t = sub.add_parser("transcribe", help="transcribe an audio file and print the text")
    t.add_argument("file")
    t.add_argument("--language", help='override the language, e.g. "en"')
    sub.add_parser("mics", help="list microphones")
    sub.add_parser("toggle", help="start/stop recording in the running instance")
    i = sub.add_parser("init", help="write a commented config and example text files")
    i.add_argument("--force", action="store_true", help="overwrite existing files")
    d = sub.add_parser("install-desktop", help="add an app menu entry (required for the portal)")
    d.add_argument("--autostart", action="store_true", help="also start on login")
    sub.add_parser("uninstall-desktop", help="remove the app menu and autostart entries")
    sub.add_parser("doctor", help="check permissions and dependencies")
    return p


COMMANDS = {
    None: cmd_run, "run": cmd_run, "transcribe": cmd_transcribe, "mics": cmd_mics,
    "toggle": cmd_toggle, "init": cmd_init, "install-desktop": cmd_install_desktop,
    "uninstall-desktop": cmd_uninstall_desktop, "doctor": cmd_doctor,
}


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)
    COMMANDS[args.command](args, argv)
