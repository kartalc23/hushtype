"""Which window has focus: its app id and title, for per-app tone.

Linux has no portable API for this, so there is one backend per desktop:

* KDE Plasma (Wayland and X11): a tiny KWin script, loaded over DBus, reports
  every focus and title change back to us. It is unloaded on exit and lives
  only in KWin's memory; no KWin config file is touched.
* sway and Hyprland: their IPC (``swaymsg``, ``hyprctl``).
* Other X11 desktops: ``xprop`` on ``_NET_ACTIVE_WINDOW``.
* GNOME on Wayland: not possible without a shell extension, so no backend.

Titles are only kept in memory and never logged: they can contain private
things such as an email subject.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

BUS_NAME = "io.github.kartalc23.hushtype.Focus"
OBJECT_PATH = "/focus"
INTERFACE = "io.github.kartalc23.hushtype.Focus"
KWIN_PLUGIN = "hushtype-focus"

KWIN_SCRIPT_TEMPLATE = """\
// hushtype: report the focused window's app id and title. Unloaded when hushtype exits.
var current = null;
function send(w) {
    var id = w ? String(w.resourceClass || w.desktopFileName || "") : "";
    callDBus("@NAME@", "@PATH@", "@IFACE@", "Focus", id, w ? String(w.caption || "") : "");
}
function onCaption() { send(current); }
function activated(w) {
    if (current && current.captionChanged) {
        try { current.captionChanged.disconnect(onCaption); } catch (e) {}
    }
    current = w;
    if (w && w.captionChanged) w.captionChanged.connect(onCaption);
    send(w);
}
(workspace.windowActivated || workspace.clientActivated).connect(activated);
activated(workspace.activeWindow || workspace.activeClient);
"""


class Focus:
    name = "none"

    def current(self) -> tuple[str, str] | None:
        """(app id, title) of the focused window, or None if unknown."""
        return None

    def close(self) -> None:
        pass


class _Command(Focus):
    def _run(self, *cmd: str) -> str:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=1, check=True).stdout


class SwayFocus(_Command):
    name = "sway"

    def current(self):
        try:
            tree = json.loads(self._run("swaymsg", "-t", "get_tree", "-r"))
        except Exception:
            return None
        stack = [tree]
        while stack:
            node = stack.pop()
            if node.get("focused") and node.get("type") in ("con", "floating_con"):
                app = node.get("app_id") or (node.get("window_properties") or {}).get("class") or ""
                return app, node.get("name") or ""
            stack += node.get("nodes", []) + node.get("floating_nodes", [])
        return None


class HyprlandFocus(_Command):
    name = "hyprland"

    def current(self):
        try:
            w = json.loads(self._run("hyprctl", "activewindow", "-j"))
        except Exception:
            return None
        return (w.get("class") or "", w.get("title") or "") if w else None


class X11Focus(_Command):
    name = "x11"

    def current(self):
        try:
            active = self._run("xprop", "-root", "_NET_ACTIVE_WINDOW").split()[-1]
            if active in ("0x0", "0"):
                return None
            props = self._run("xprop", "-id", active, "WM_CLASS", "_NET_WM_NAME")
        except Exception:
            return None
        app = title = ""
        for line in props.splitlines():
            values = [v.strip().strip('"') for v in line.split("=", 1)[-1].split('",')]
            if line.startswith("WM_CLASS"):
                app = values[-1]  # the class, e.g. "Slack"; the first value is the instance
            elif line.startswith("_NET_WM_NAME"):
                title = line.split("=", 1)[-1].strip().strip('"')
        return app, title


class KWinFocus(Focus):
    """Owns a DBus name; a KWin script calls our Focus method on every change."""

    name = "kwin"

    def __init__(self, log=print, probe: bool = False):
        """``probe``: a short-lived check (``hushtype app``, ``doctor``) that must not
        clash with a running hushtype, so it uses its own bus name and script."""
        from jeepney import message_bus
        from jeepney.io.blocking import open_dbus_connection

        self.log = log
        self._latest: tuple[str, str] | None = None
        self._lock = threading.Lock()
        suffix = f".p{os.getpid()}" if probe else ""
        self._bus_name = BUS_NAME + suffix
        self._plugin = KWIN_PLUGIN + suffix.replace(".", "-")
        self._serve = open_dbus_connection(bus="SESSION")
        reply = self._serve.send_and_get_reply(message_bus.RequestName(self._bus_name, 4))  # 4 = do not queue
        if reply.body[0] not in (1, 4):  # primary owner / already owner
            raise RuntimeError("another hushtype owns the focus bus name")
        self._control = open_dbus_connection(bus="SESSION")
        threading.Thread(target=self._loop, daemon=True).start()
        fd, path = tempfile.mkstemp(prefix="hushtype-focus-", suffix=".js",
                                    dir=os.environ.get("XDG_RUNTIME_DIR") or None)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(KWIN_SCRIPT_TEMPLATE.replace("@NAME@", self._bus_name).replace("@PATH@", OBJECT_PATH)
                    .replace("@IFACE@", INTERFACE))
        self._script = Path(path)
        self._kwin("unloadScript", "s", (self._plugin,))  # left over from a crash
        sid = self._kwin("loadScript", "ss", (str(self._script), self._plugin))[0]
        if sid < 0:
            raise RuntimeError("KWin refused to load the focus script")
        self._run_script(sid)

    def _kwin(self, method, signature, body, path="/Scripting", iface="org.kde.kwin.Scripting"):
        from jeepney import DBusAddress, MessageType, new_method_call

        msg = new_method_call(DBusAddress(path, bus_name="org.kde.KWin", interface=iface),
                              method, signature, body)
        reply = self._control.send_and_get_reply(msg, timeout=3)
        if reply.header.message_type == MessageType.error:
            raise RuntimeError(f"KWin {method}: {reply.body[0] if reply.body else 'error'}")
        return reply.body

    def _run_script(self, sid: int) -> None:
        last = None
        for path in (f"/Scripting/Script{sid}", f"/{sid}"):  # Plasma 6, Plasma 5
            try:
                self._kwin("run", "", (), path=path, iface="org.kde.kwin.Script")
                return
            except Exception as e:
                last = e
        raise RuntimeError(f"cannot start the KWin focus script: {last}")

    def _loop(self) -> None:
        from jeepney import HeaderFields, MessageType, new_error, new_method_return

        while True:
            try:
                msg = self._serve.receive()
            except Exception:
                return
            h = msg.header
            if h.message_type != MessageType.method_call:
                continue
            if h.fields.get(HeaderFields.member) == "Focus" and len(msg.body) == 2:
                with self._lock:
                    self._latest = (str(msg.body[0]), str(msg.body[1]))
                reply = new_method_return(msg)
            else:
                reply = new_error(msg, "org.freedesktop.DBus.Error.UnknownMethod")
            try:
                self._serve.send(reply)
            except Exception:
                return

    def current(self):
        with self._lock:
            return self._latest

    def close(self) -> None:
        try:
            self._kwin("unloadScript", "s", (self._plugin,))
        except Exception:
            pass
        self._script.unlink(missing_ok=True)
        for conn in (self._control, self._serve):
            try:
                conn.close()
            except Exception:
                pass


def open_focus(log=print, probe: bool = False) -> Focus:
    """Pick the backend for this desktop; returns a no-op Focus if none works."""
    env = os.environ
    desktop = env.get("XDG_CURRENT_DESKTOP", "").lower()
    try:
        if "kde" in desktop:
            return KWinFocus(log, probe)
    except Exception as e:
        log(f"[tone] KWin focus tracking unavailable: {e}")
    if env.get("SWAYSOCK") and shutil.which("swaymsg"):
        return SwayFocus()
    if env.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        return HyprlandFocus()
    if env.get("XDG_SESSION_TYPE") == "x11" and shutil.which("xprop"):
        return X11Focus()
    return Focus()
