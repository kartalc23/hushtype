import json

from hushtype import focus


def fake(backend, outputs):
    """Make backend._run return canned command output, keyed by the command's first two words."""
    backend._run = lambda *cmd: outputs[" ".join(cmd[:2])]
    return backend


def test_sway_finds_focused_window():
    tree = {"type": "root", "nodes": [{"type": "output", "nodes": [{"type": "workspace", "nodes": [
        {"type": "con", "focused": False, "app_id": "firefox", "name": "Docs"},
        {"type": "con", "focused": True, "app_id": None, "name": "general - Slack",
         "window_properties": {"class": "Slack"}},
    ], "floating_nodes": []}]}]}
    f = fake(focus.SwayFocus(), {"swaymsg -t": json.dumps(tree)})
    assert f.current() == ("Slack", "general - Slack")


def test_sway_floating_and_nothing_focused():
    tree = {"type": "root", "nodes": [{"type": "workspace", "nodes": [], "floating_nodes": [
        {"type": "floating_con", "focused": True, "app_id": "org.telegram.desktop", "name": "Chat"}]}]}
    assert fake(focus.SwayFocus(), {"swaymsg -t": json.dumps(tree)}).current() == \
        ("org.telegram.desktop", "Chat")
    assert fake(focus.SwayFocus(), {"swaymsg -t": json.dumps({"nodes": []})}).current() is None


def test_hyprland():
    out = json.dumps({"class": "discord", "title": "#general"})
    assert fake(focus.HyprlandFocus(), {"hyprctl activewindow": out}).current() == ("discord", "#general")
    assert fake(focus.HyprlandFocus(), {"hyprctl activewindow": "{}"}).current() is None


def test_x11():
    outputs = {
        "xprop -root": "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x3a00007\n",
        "xprop -id": 'WM_CLASS(STRING) = "slack", "Slack"\n'
                     '_NET_WM_NAME(UTF8_STRING) = "general - Acme - Slack"\n',
    }
    assert fake(focus.X11Focus(), outputs).current() == ("Slack", "general - Acme - Slack")
    outputs["xprop -root"] = "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x0\n"
    assert fake(focus.X11Focus(), outputs).current() is None


def test_command_failure_is_none():
    def boom(*cmd):
        raise OSError("not found")
    for cls in (focus.SwayFocus, focus.HyprlandFocus, focus.X11Focus):
        b = cls()
        b._run = boom
        assert b.current() is None


def test_open_focus_without_a_known_desktop(monkeypatch):
    for var in ("XDG_CURRENT_DESKTOP", "SWAYSOCK", "HYPRLAND_INSTANCE_SIGNATURE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert focus.open_focus().name == "none"


def test_kwin_script_is_filled_in_per_instance():
    assert "@NAME@" in focus.KWIN_SCRIPT_TEMPLATE  # replaced with the instance's bus name at load
