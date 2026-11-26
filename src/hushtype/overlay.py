"""The small animated status pill at the bottom of the screen (GTK4).

Uses GTK4 with the wlr-layer-shell protocol: the window sits on the
"overlay" layer, is anchored to the bottom edge, never takes focus and lets
clicks pass through. Works on KDE Plasma, sway, Hyprland and any compositor
with layer-shell. Elsewhere (X11, GNOME) it opens as a plain undecorated
window and the compositor decides where it goes.

Frames are drawn with Pillow and pushed to the screen as Gdk.MemoryTexture.

IMPORTANT: libgtk4-layer-shell must be loaded before libwayland-client. In
Python the only reliable way is to dlopen it with RTLD_GLOBAL before importing
gi, which is done below; otherwise is_supported() returns False.
"""

from __future__ import annotations

import ctypes
import math

from PIL import Image, ImageDraw, ImageFilter

try:
    ctypes.CDLL("libgtk4-layer-shell.so.0", mode=ctypes.RTLD_GLOBAL)
except OSError:
    pass

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402
except (ValueError, ImportError):
    LayerShell = None

try:
    import cairo  # pycairo; an empty input region makes the window click-through
except ImportError:
    cairo = None

HIDDEN, RECORDING, PROCESSING = 0, 1, 2

# ---------------------------------------------------------------- drawing

WIDTH, HEIGHT = 118, 36
SHADOW = 14  # room around the pill for the drop shadow
CANVAS_W, CANVAS_H = WIDTH + SHADOW * 2, HEIGHT + SHADOW * 2
BOTTOM_MARGIN = 36  # pixels above the panel / taskbar

BACKGROUND = (17, 18, 22)
BAR_COLOR = (247, 248, 250)
BAR_COUNT = 15
BAR_WIDTH = 2.6
BAR_SPACING = 5.4
MIN_BAR = 2.6

SUPERSAMPLE = 3  # draw large, then downscale: Pillow does not anti-alias shapes

_background_cache: dict = {}


def _background(scale: float) -> Image.Image:
    """Render the pill + shadow layer and cache it.

    The Gaussian blur of the shadow is the most expensive part of a frame. The
    background only changes with scale (while fading in/out), so it is cached.
    """
    key = round(scale, 3)
    if key in _background_cache:
        return _background_cache[key]

    k = SUPERSAMPLE
    w, h = WIDTH * k, HEIGHT * k
    cw, ch = CANVAS_W * k, CANVAS_H * k
    img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    iw, ih = w * scale, h * scale
    x1, y1 = (cw - iw) / 2, (ch - ih) / 2
    x2, y2 = x1 + iw, y1 + ih
    radius = ih / 2

    shadow = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (x1, y1 + 5 * k, x2, y2 + 5 * k), radius=radius, fill=(0, 0, 0, 120)
    )
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(9 * k)))

    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x1, y1, x2, y2), radius=radius, fill=BACKGROUND + (242,))
    d.rounded_rectangle(
        (x1 + k, y1 + k, x2 - k, y2 - k),
        radius=radius,
        outline=(255, 255, 255, 18),
        width=max(1, k // 2),
    )
    if len(_background_cache) > 40:
        _background_cache.clear()
    _background_cache[key] = img
    return img


def draw_frame(state: int, level: float, phase: float, visibility: float) -> Image.Image:
    """Render one animation frame (RGBA)."""
    k = SUPERSAMPLE
    cw, ch = CANVAS_W * k, CANVAS_H * k

    # grow slightly while appearing; a pure fade looks like it "pops" open
    scale = 0.94 + 0.06 * visibility
    ih = HEIGHT * k * scale

    img = _background(scale).copy()
    d = ImageDraw.Draw(img)
    mid_y = ch / 2
    bar_w = BAR_WIDTH * k * scale
    spacing = BAR_SPACING * k * scale
    span = (BAR_COUNT - 1) * spacing + bar_w
    x0 = (cw - span) / 2
    max_h = (ih - 13 * k * scale) / 2  # peak half-height
    min_h = MIN_BAR * k * scale

    for i in range(BAR_COUNT):
        # envelope fading symmetrically from the centre: reads as a waveform
        t = (i - (BAR_COUNT - 1) / 2) / ((BAR_COUNT - 1) / 2)  # -1..1
        envelope = math.cos(t * math.pi / 2) ** 1.6

        if state == RECORDING:
            wave = 0.55 + 0.45 * math.sin(phase * 7.0 + i * 0.85)
            bar_h = min_h + max_h * level * envelope * wave
            alpha = 236
        else:
            # A shimmer sliding left to right over flat dots. Height barely
            # changes so it can't be mistaken for "listening": it says "wait".
            centre = ((phase * 0.85) % 1.7) - 0.35
            dist = abs((i / (BAR_COUNT - 1)) - centre)
            p = max(0.0, 1.0 - dist * 3.0)
            bar_h = min_h * (1.0 + 0.35 * p)
            alpha = int(50 + 190 * p)

        bar_h = max(min_h, min(bar_h, max_h))
        x = x0 + i * spacing
        d.rounded_rectangle(
            (x, mid_y - bar_h, x + bar_w, mid_y + bar_h),
            radius=bar_w / 2,
            fill=BAR_COLOR + (alpha,),
        )

    img = img.resize((CANVAS_W, CANVAS_H), Image.LANCZOS)
    if visibility < 1.0:
        img.putalpha(img.getchannel("A").point(lambda v: int(v * visibility)))
    return img


def _texture(img: Image.Image) -> Gdk.Texture:
    w, h = img.size
    return Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(img.tobytes()), w * 4)


# ---------------------------------------------------------------- window


class Overlay:
    def __init__(self, get_state, get_level, stopped):
        self.get_state = get_state
        self.get_level = get_level
        self.stopped = stopped
        self.visibility = 0.0
        self.level = 0.0
        self.phase = 0.0
        self.shown = False
        self.win = None
        self.picture = None
        self.loop = None

    def _build(self) -> None:
        Gtk.init()
        self.win = Gtk.Window()
        self.win.set_title("hushtype")
        self.win.set_decorated(False)
        self.win.set_resizable(False)
        self.win.set_default_size(CANVAS_W, CANVAS_H)

        css = Gtk.CssProvider()
        css.load_from_string("window { background: transparent; }")
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        if LayerShell is not None and LayerShell.is_supported():
            LayerShell.init_for_window(self.win)
            LayerShell.set_namespace(self.win, "hushtype")
            LayerShell.set_layer(self.win, LayerShell.Layer.OVERLAY)
            LayerShell.set_anchor(self.win, LayerShell.Edge.BOTTOM, True)
            LayerShell.set_margin(self.win, LayerShell.Edge.BOTTOM, BOTTOM_MARGIN - SHADOW)
            LayerShell.set_keyboard_mode(self.win, LayerShell.KeyboardMode.NONE)
            LayerShell.set_exclusive_zone(self.win, 0)

        self.picture = Gtk.Picture()
        self.picture.set_can_shrink(False)
        self.picture.set_size_request(CANVAS_W, CANVAS_H)
        self.win.set_child(self.picture)

    def _click_through(self) -> None:
        if cairo is None:
            return
        try:
            surface = self.win.get_surface()
            if surface is not None:
                surface.set_input_region(cairo.Region())
        except Exception:
            pass

    def _show(self) -> None:
        self.win.set_visible(True)
        self.shown = True
        self._click_through()
        # GTK may reset the input region after the first draw
        GLib.timeout_add(60, lambda: (self._click_through(), False)[1])

    def _hide(self) -> None:
        self.win.set_visible(False)
        self.shown = False

    def _tick(self) -> bool:
        if self.stopped():
            self.loop.quit()
            return False
        state = self.get_state()
        target = 0.0 if state == HIDDEN else 1.0
        self.visibility += (target - self.visibility) * 0.22
        if self.visibility < 0.01 and target == 0.0:
            if self.shown:
                self._hide()
            return True
        if not self.shown:
            self._show()

        raw = min(1.0, self.get_level() * 16)
        rate = 0.30 if raw > self.level else 0.12  # fast attack, slow decay
        self.level += (raw - self.level) * rate
        self.phase += 1 / 60

        frame = draw_frame(
            state if state != HIDDEN else PROCESSING,
            self.level,
            self.phase,
            min(1.0, self.visibility),
        )
        self.picture.set_paintable(_texture(frame))
        return True

    def run(self) -> None:
        """Blocks on the main thread until stopped() returns True."""
        self._build()
        self.loop = GLib.MainLoop()
        GLib.timeout_add(16, self._tick)
        self.loop.run()
        try:
            self.win.destroy()
        except Exception:
            pass
