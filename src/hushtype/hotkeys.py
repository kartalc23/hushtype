"""Global hotkeys, sending Ctrl+V, and clipboard access on Linux.

On Wayland no single library can both catch a global hotkey and type into
another window, so there are three independent paths here:

1. ``EvdevListener`` reads ``/dev/input/event*`` directly. It works under any
   compositor (KDE, GNOME, sway, Hyprland, X11) and sees key-down and key-up
   instantly, so true hold-to-talk with modifier-only combos is possible.
   Requires membership in the ``input`` group
   (``sudo usermod -aG input $USER``, then **reboot**: logging out is not
   enough because the systemd user manager keeps the old groups).

2. ``PortalListener`` uses the xdg-desktop-portal GlobalShortcuts API
   (KDE Plasma 6, GNOME 48+, Hyprland). No extra permissions; the desktop
   asks for confirmation once. It reports both Activated and Deactivated, so
   hold-to-talk works here too. Limitation: modifier-only shortcuts
   (Ctrl+Super) are rejected, a regular key such as Z is required.

3. ``KeySender`` creates a virtual keyboard via ``/dev/uinput`` and presses
   Ctrl+V. systemd-logind grants the active seat user access to
   ``/dev/uinput`` through an ACL on most distributions. Falls back to
   wtype / ydotool / xdotool; if none work the text stays on the clipboard.
"""

from __future__ import annotations

import os
import select
import shutil
import subprocess
import threading
import time

try:
    import evdev
    from evdev import ecodes as E
except ImportError:  # pragma: no cover
    evdev = None
    E = None

# key name -> evdev codes (any code in the group counts as the group being held)
KEY_GROUPS: dict[str, set] = {}
MODIFIERS: set = set()
if E is not None:
    KEY_GROUPS = {
        "ctrl": {E.KEY_LEFTCTRL, E.KEY_RIGHTCTRL},
        "shift": {E.KEY_LEFTSHIFT, E.KEY_RIGHTSHIFT},
        "alt": {E.KEY_LEFTALT, E.KEY_RIGHTALT},
        "super": {E.KEY_LEFTMETA, E.KEY_RIGHTMETA},
        "meta": {E.KEY_LEFTMETA, E.KEY_RIGHTMETA},
        "win": {E.KEY_LEFTMETA, E.KEY_RIGHTMETA},
    }
    MODIFIERS = KEY_GROUPS["ctrl"] | KEY_GROUPS["shift"] | KEY_GROUPS["alt"] | KEY_GROUPS["super"]

VIRTUAL_KEYBOARD_NAME = "hushtype virtual keyboard"


def key_codes(name: str) -> set:
    name = name.lower()
    if name in KEY_GROUPS:
        return KEY_GROUPS[name]
    code = getattr(E, f"KEY_{name.upper()}", None)
    if code is None:
        raise ValueError(f"unknown key: {name}")
    return {code}


def describe(keys) -> str:
    return "+".join(k.capitalize() for k in keys)


# ---------------------------------------------------------------- evdev


class EvdevListener:
    """Watches every keyboard; calls press() while the combo is held, release() when let go.

    Devices are not grabbed, so keys keep working normally. The device list is
    rescanned every 2 seconds to pick up keyboards that are plugged in later.
    """

    def __init__(self, hold, quit_keys, press, release, quit_cb, stopped, log=print):
        if evdev is None:
            raise RuntimeError("the evdev package is not installed")
        self.hold = [key_codes(k) for k in hold]
        self.quit_keys = [key_codes(k) for k in quit_keys]
        self.press, self.release, self.quit_cb = press, release, quit_cb
        self.stopped = stopped
        self.log = log
        self.down: set = set()
        self.devices: dict = {}  # path -> InputDevice
        self._lock = threading.Lock()
        self._scan(first=True)
        if not self.devices:
            raise PermissionError("cannot read /dev/input (is the user in the 'input' group?)")

    @staticmethod
    def _is_keyboard(dev) -> bool:
        if dev.name == VIRTUAL_KEYBOARD_NAME:
            return False  # don't listen to the keys we send ourselves
        keys = dev.capabilities().get(E.EV_KEY, [])
        return E.KEY_A in keys and E.KEY_LEFTCTRL in keys

    def _scan(self, first: bool = False) -> None:
        present = set(evdev.list_devices())
        for path in list(self.devices):
            if path not in present:
                self.devices.pop(path, None)
        for path in present - set(self.devices):
            try:
                dev = evdev.InputDevice(path)
            except OSError:
                continue
            if self._is_keyboard(dev):
                self.devices[path] = dev
                if not first:
                    self.log(f"[keys] new keyboard: {dev.name}")
            else:
                dev.close()

    def modifier_held(self) -> bool:
        with self._lock:
            return bool(self.down & MODIFIERS)

    def _combo(self, groups) -> bool:
        return all(self.down & g for g in groups)

    def run(self) -> None:
        recording = False
        last_scan = time.time()
        for d in self.devices.values():
            self.log(f"[keys] listening: {d.name}")
        while not self.stopped():
            if time.time() - last_scan > 2.0:
                self._scan()
                last_scan = time.time()
            if not self.devices:
                time.sleep(0.5)
                continue
            try:
                ready, _, _ = select.select(list(self.devices.values()), [], [], 0.3)
            except (OSError, ValueError):
                self._scan()
                continue
            changed = False
            for dev in ready:
                try:
                    events = list(dev.read())
                except OSError:
                    self.devices.pop(dev.path, None)
                    continue
                for ev in events:
                    if ev.type != E.EV_KEY or ev.value == 2:  # 2 = autorepeat
                        continue
                    with self._lock:
                        if ev.value == 1:
                            self.down.add(ev.code)
                        else:
                            self.down.discard(ev.code)
                    changed = True
            if not changed:
                continue
            with self._lock:
                held = self._combo(self.hold)
                quitting = self._combo(self.quit_keys)
            if quitting:
                self.quit_cb()
                return
            if held and not recording:
                recording = True
                self.press()
            elif not held and recording:
                recording = False
                self.release()


# ---------------------------------------------------------------- portal


class PortalListener:
    """Listens for shortcuts through xdg-desktop-portal GlobalShortcuts.

    Registers two shortcuts, "dictate" (hold) and "quit". The desktop shows a
    confirmation dialog the first time and remembers the choice as long as the
    app id stays the same (the launcher runs us in an ``app-hushtype-*.scope``
    systemd unit for exactly this reason).
    """

    BUS = "org.freedesktop.portal.Desktop"
    PATH = "/org/freedesktop/portal/desktop"
    IFACE = "org.freedesktop.portal.GlobalShortcuts"

    def __init__(self, trigger, quit_trigger, press, release, quit_cb, stopped, log=print):
        self.trigger, self.quit_trigger = trigger, quit_trigger
        self.press, self.release, self.quit_cb = press, release, quit_cb
        self.stopped = stopped
        self.log = log
        self.ready = threading.Event()
        self.error: Exception | None = None
        self.description = ""  # what the desktop actually bound, e.g. Meta+Ctrl+Z
        self.quit_description = ""

    def _request(self, conn, member, signature, body):
        """Call a portal method and wait for its Request.Response signal."""
        from jeepney import DBusAddress, MatchRule, message_bus, new_method_call

        token = f"ht{int(time.time() * 1000) % 10_000_000}"
        sender = conn.unique_name[1:].replace(".", "_")
        request_path = f"{self.PATH}/request/{sender}/{token}"
        rule = MatchRule(
            type="signal", interface="org.freedesktop.portal.Request",
            member="Response", path=request_path,
        )
        conn.send_and_get_reply(message_bus.AddMatch(rule))
        addr = DBusAddress(self.PATH, bus_name=self.BUS, interface=self.IFACE)
        body = list(body)
        body[-1] = dict(body[-1], handle_token=("s", token))
        with conn.filter(rule) as queue:
            conn.send_and_get_reply(new_method_call(addr, member, signature, tuple(body)))
            # a confirmation dialog may be open; wait until the user decides
            while not self.stopped():
                try:
                    msg = conn.recv_until_filtered(queue, timeout=1.0)
                    break
                except TimeoutError:
                    continue
            else:
                raise RuntimeError("stopped")
        code, result = msg.body
        if code != 0:
            raise RuntimeError(f"{member} was denied (code {code})")
        return result

    def run(self) -> None:
        try:
            import jeepney
            from jeepney import MatchRule, message_bus
            from jeepney.io.blocking import open_dbus_connection

            conn = open_dbus_connection(bus="SESSION")
            result = self._request(
                conn, "CreateSession", "a{sv}",
                [{"session_handle_token": ("s", "hushtype")}],
            )
            session = result["session_handle"][1]

            shortcuts = [
                ("dictate", {
                    "description": ("s", "hushtype: hold, speak, release"),
                    "preferred_trigger": ("s", self.trigger),
                }),
                ("quit", {
                    "description": ("s", "hushtype: quit"),
                    "preferred_trigger": ("s", self.quit_trigger),
                }),
            ]
            result = self._request(
                conn, "BindShortcuts", "oa(sa{sv})sa{sv}",
                [session, shortcuts, "", {}],
            )
            bound = {name: opts.get("trigger_description", ("s", "?"))[1]
                     for name, opts in result.get("shortcuts", ("", []))[1]}
            self.description = bound.get("dictate", "?")
            self.quit_description = bound.get("quit", "?")
            self.log(f"[keys] portal shortcuts: dictate = {self.description}, "
                     f"quit = {self.quit_description}")

            rule = MatchRule(type="signal", interface=self.IFACE, path=self.PATH)
            conn.send_and_get_reply(message_bus.AddMatch(rule))
            self.ready.set()
            with conn.filter(rule, bufsize=64) as queue:
                while not self.stopped():
                    try:
                        msg = conn.recv_until_filtered(queue, timeout=0.2)
                    except TimeoutError:
                        continue
                    member = msg.header.fields.get(jeepney.HeaderFields.member)
                    if member not in ("Activated", "Deactivated"):
                        continue
                    _, shortcut_id, _, _ = msg.body
                    pressed = member == "Activated"
                    if shortcut_id == "quit":
                        if pressed:
                            self.quit_cb()
                            return
                    elif shortcut_id == "dictate":
                        (self.press if pressed else self.release)()
        except Exception as e:
            self.error = e
            self.ready.set()


# ---------------------------------------------------------------- sending keys


class KeySender:
    """Presses Ctrl+V in the focused window. uinput first, then wtype/ydotool/xdotool."""

    def __init__(self, log=print):
        self.log = log
        self.ui = None
        self.tool: str | None = None
        if evdev is not None:
            try:
                self.ui = evdev.UInput(
                    {E.EV_KEY: [E.KEY_LEFTCTRL, E.KEY_LEFTSHIFT, E.KEY_V]},
                    name=VIRTUAL_KEYBOARD_NAME,
                )
                self.tool = "uinput"
                return
            except Exception as e:
                log(f"[keys] cannot open uinput ({type(e).__name__}: {e})")
        for tool in ("wtype", "ydotool", "xdotool"):
            if shutil.which(tool):
                self.tool = tool
                return
        log("[keys] WARNING: no way to send keys; text will be copied, paste with Ctrl+V")

    def _key(self, code: int, value: int) -> None:
        self.ui.write(E.EV_KEY, code, value)
        self.ui.syn()
        time.sleep(0.012)

    def ctrl_v(self) -> bool:
        try:
            if self.tool == "uinput":
                self._key(E.KEY_LEFTCTRL, 1)
                self._key(E.KEY_V, 1)
                self._key(E.KEY_V, 0)
                self._key(E.KEY_LEFTCTRL, 0)
                return True
            if self.tool == "wtype":
                subprocess.run(["wtype", "-M", "ctrl", "v", "-m", "ctrl"], check=True, timeout=3)
                return True
            if self.tool == "ydotool":
                subprocess.run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"], check=True, timeout=3)
                return True
            if self.tool == "xdotool":
                subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"], check=True, timeout=3)
                return True
        except Exception as e:
            self.log(f"[keys] could not send Ctrl+V ({self.tool}): {e}")
        return False

    def close(self) -> None:
        if self.ui is not None:
            try:
                self.ui.close()
            except Exception:
                pass


# ---------------------------------------------------------------- clipboard


def _wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def clipboard_read() -> str | None:
    """Return the clipboard text, or None if it is not text or cannot be read."""
    try:
        if _wayland() and shutil.which("wl-paste"):
            r = subprocess.run(["wl-paste", "-n", "-t", "text"], capture_output=True, timeout=2)
        elif shutil.which("xclip"):
            r = subprocess.run(["xclip", "-o", "-selection", "clipboard"], capture_output=True, timeout=2)
        elif shutil.which("xsel"):
            r = subprocess.run(["xsel", "-ob"], capture_output=True, timeout=2)
        else:
            return None
        if r.returncode != 0:
            return None
        return r.stdout.decode("utf-8", errors="replace")
    except Exception:
        return None


def clipboard_write(text: str) -> None:
    data = text.encode("utf-8")
    if _wayland() and shutil.which("wl-copy"):
        subprocess.run(["wl-copy"], input=data, check=True, timeout=2)
    elif shutil.which("xclip"):
        subprocess.run(["xclip", "-i", "-selection", "clipboard"], input=data, check=True, timeout=2)
    elif shutil.which("xsel"):
        subprocess.run(["xsel", "-ib"], input=data, check=True, timeout=2)
    else:
        raise RuntimeError("no clipboard tool found (install wl-clipboard, xclip or xsel)")
