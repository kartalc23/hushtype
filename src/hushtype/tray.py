"""System tray icon. The main process runs this as a SEPARATE process.

Why a separate process: pystray uses GTK3 + AppIndicator on Linux while the
overlay uses GTK4, and both cannot be loaded into one process (gi refuses
with "Gtk is already loaded as 4.0"). They talk over pipes:

    stdin  <- "recording" / "ready"    change the icon
    stdin  <- EOF                      exit
    stdout -> "quit"                   the user clicked Quit
"""

from __future__ import annotations

import os
import sys
import threading

os.environ.setdefault("PYSTRAY_BACKEND", "appindicator")

from PIL import Image, ImageDraw  # noqa: E402

READY_COLOR = (90, 95, 105, 255)
RECORDING_COLOR = (232, 80, 58, 255)


def icon(recording: bool = False, size: int = 64) -> Image.Image:
    """A microphone in a circle, drawn 4x larger and downscaled for smooth edges."""
    s = size * 4 / 64  # all coordinates below are on a 64-unit grid
    img = Image.new("RGBA", (size * 4, size * 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    d.ellipse((6 * s, 6 * s, 58 * s, 58 * s), fill=RECORDING_COLOR if recording else READY_COLOR)
    d.rounded_rectangle((26 * s, 16 * s, 38 * s, 38 * s), radius=6 * s, fill=white)
    d.rectangle((31 * s, 38 * s, 33 * s, 46 * s), fill=white)
    d.rectangle((24 * s, 46 * s, 40 * s, 49 * s), fill=white)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    import pystray

    hint = sys.argv[1] if len(sys.argv) > 1 else ""

    def quit_clicked(tray_icon=None, item=None):
        try:
            sys.stdout.write("quit\n")
            sys.stdout.flush()
        except Exception:
            pass
        tray_icon.stop()

    def settings_clicked(tray_icon=None, item=None):
        import subprocess

        subprocess.Popen([sys.executable, "-m", "hushtype", "settings"], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    items = [pystray.MenuItem("hushtype" + (f" ({hint})" if hint else ""), None, enabled=False),
             pystray.MenuItem("Settings…", settings_clicked),
             pystray.MenuItem("Quit", quit_clicked)]
    tray_icon = pystray.Icon("hushtype", icon(False), "hushtype: ready", pystray.Menu(*items))

    def listen():
        for line in sys.stdin:
            line = line.strip()
            if line == "recording":
                tray_icon.icon = icon(True)
                tray_icon.title = "hushtype: recording"
            elif line == "ready":
                tray_icon.icon = icon(False)
                tray_icon.title = "hushtype: ready"
        tray_icon.stop()  # the main process exited

    threading.Thread(target=listen, daemon=True).start()
    tray_icon.run()


if __name__ == "__main__":
    main()
