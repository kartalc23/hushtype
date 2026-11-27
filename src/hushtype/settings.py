"""Settings window (GTK 4 + libadwaita): ``hushtype settings``.

Edits config.toml and the three text files, then applies the change to the
running instance: SIGHUP for things it can reload live (language, vocabulary,
style, replacements, microphone, sounds, paste behaviour), a restart for the
rest (model, device, hotkeys, overlay, tray).
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from copy import deepcopy

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from . import __version__, text, tone  # noqa: E402
from . import config as C  # noqa: E402

APP_ID = "io.github.kartalc23.hushtype.Settings"
REPO_URL = "https://github.com/kartalc23/hushtype"

# Whisper's languages (code -> English name). Filtered at runtime against the
# codes faster-whisper actually accepts.
LANGUAGES = {
    "en": "English", "zh": "Chinese", "de": "German", "es": "Spanish", "ru": "Russian",
    "ko": "Korean", "fr": "French", "ja": "Japanese", "pt": "Portuguese", "tr": "Turkish",
    "pl": "Polish", "ca": "Catalan", "nl": "Dutch", "ar": "Arabic", "sv": "Swedish",
    "it": "Italian", "id": "Indonesian", "hi": "Hindi", "fi": "Finnish", "vi": "Vietnamese",
    "he": "Hebrew", "uk": "Ukrainian", "el": "Greek", "ms": "Malay", "cs": "Czech",
    "ro": "Romanian", "da": "Danish", "hu": "Hungarian", "ta": "Tamil", "no": "Norwegian",
    "th": "Thai", "ur": "Urdu", "hr": "Croatian", "bg": "Bulgarian", "lt": "Lithuanian",
    "la": "Latin", "mi": "Maori", "ml": "Malayalam", "cy": "Welsh", "sk": "Slovak",
    "te": "Telugu", "fa": "Persian", "lv": "Latvian", "bn": "Bengali", "sr": "Serbian",
    "az": "Azerbaijani", "sl": "Slovenian", "kn": "Kannada", "et": "Estonian", "mk": "Macedonian",
    "br": "Breton", "eu": "Basque", "is": "Icelandic", "hy": "Armenian", "ne": "Nepali",
    "mn": "Mongolian", "bs": "Bosnian", "kk": "Kazakh", "sq": "Albanian", "sw": "Swahili",
    "gl": "Galician", "mr": "Marathi", "pa": "Punjabi", "si": "Sinhala", "km": "Khmer",
    "sn": "Shona", "yo": "Yoruba", "so": "Somali", "af": "Afrikaans", "oc": "Occitan",
    "ka": "Georgian", "be": "Belarusian", "tg": "Tajik", "sd": "Sindhi", "gu": "Gujarati",
    "am": "Amharic", "yi": "Yiddish", "lo": "Lao", "uz": "Uzbek", "fo": "Faroese",
    "ht": "Haitian Creole", "ps": "Pashto", "tk": "Turkmen", "nn": "Nynorsk", "mt": "Maltese",
    "sa": "Sanskrit", "lb": "Luxembourgish", "my": "Myanmar", "bo": "Tibetan", "tl": "Tagalog",
    "mg": "Malagasy", "as": "Assamese", "tt": "Tatar", "haw": "Hawaiian", "ln": "Lingala",
    "ha": "Hausa", "ba": "Bashkir", "jw": "Javanese", "su": "Sundanese", "yue": "Cantonese",
}

MODELS = [
    ("large-v3-turbo", "Large v3 Turbo", "Best choice with a GPU · ~1.6 GB"),
    ("large-v3", "Large v3", "Most accurate, slower · ~3 GB"),
    ("distil-large-v3", "Distil Large v3", "English only, fast · ~1.5 GB"),
    ("medium", "Medium", "Good on fast CPUs · ~1.5 GB"),
    ("small", "Small", "Light, for CPU-only machines · ~0.5 GB"),
    ("base", "Base", "Very light, lower accuracy · ~150 MB"),
    ("tiny", "Tiny", "Fastest, lowest accuracy · ~75 MB"),
]

DEVICES = [("auto", "Automatic"), ("cuda", "GPU (CUDA)"), ("cpu", "CPU")]

CASE_ROWS = [("unchanged", "Unchanged"), ("capitalize", "Capitalize sentence starts"),
             ("lowercase", "Lowercase sentence starts")]
PERIOD_ROWS = [("unchanged", "Unchanged"), ("add", "Always add"), ("remove", "Always remove"),
               ("remove_short", "Remove on short messages")]
LAYOUT_ROWS = [("none", "None"), ("email", "Email: greeting, paragraphs, closing, signature")]
CATEGORIES = [
    ("personal", "Personal chat", "WhatsApp, Telegram, Signal, Discord"),
    ("work", "Work chat", "Slack, Teams, Zoom, Mattermost, Element"),
    ("email", "Email", "Thunderbird, Evolution, Gmail or Outlook in a browser"),
    ("other", "Everything else", "Editors, terminals, documents"),
]

MODIFIER_KEYS = {
    "Control_L": "ctrl", "Control_R": "ctrl", "Shift_L": "shift", "Shift_R": "shift",
    "Alt_L": "alt", "Alt_R": "alt", "Meta_L": "super", "Meta_R": "super",
    "Super_L": "super", "Super_R": "super",
}
MODIFIER_ORDER = ["ctrl", "shift", "alt", "super"]


def _languages() -> list[tuple[str, str]]:
    try:
        from faster_whisper.tokenizer import _LANGUAGE_CODES as codes
    except Exception:
        codes = tuple(LANGUAGES)
    langs = sorted(((c, LANGUAGES[c]) for c in codes if c in LANGUAGES), key=lambda x: x[1])
    return [("auto", "Detect automatically")] + langs


def _token_counter(cfg: C.Config) -> tuple[text.TokenCounter, bool]:
    """Whisper's tokenizer for exact counts, or an estimate if the model isn't downloaded yet."""
    try:
        from faster_whisper.utils import download_model

        from .app import token_counter

        path = download_model(cfg.model.name, local_files_only=True, cache_dir=str(C.MODELS_DIR))
        counter = token_counter(path=path)
        return counter, counter is not text.estimate_tokens
    except Exception:
        return text.estimate_tokens, False


def _microphones() -> list[str]:
    try:
        import sounddevice as sd

        names = []
        for d in sd.query_devices():
            if d["max_input_channels"] > 0 and d["name"] not in names:
                names.append(d["name"])
        return names
    except Exception:
        return []


def _in_input_group() -> bool:
    try:
        return "input" in subprocess.run(["id", "-nG"], capture_output=True, text=True).stdout.split()
    except Exception:
        return False


def _describe(keys: list[str]) -> str:
    return " + ".join(k.capitalize() if len(k) > 1 else k.upper() for k in keys) or "None"


def _combo(rows: list[tuple[str, str]], title: str, current: str, subtitle: str = "") -> Adw.ComboRow:
    row = Adw.ComboRow(title=title, subtitle=subtitle)
    row.set_model(Gtk.StringList.new([label for _, label in rows]))
    keys = [k for k, _ in rows]
    row.set_selected(keys.index(current) if current in keys else 0)
    row.keys = keys
    return row


def _split_list(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def _icon_button(icon: str, tooltip: str, callback, *args) -> Gtk.Button:
    b = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    b.add_css_class("flat")
    b.connect("clicked", lambda *_: callback(*args))
    return b


# ---------------------------------------------------------------- hotkey capture


class ShortcutDialog(Adw.Dialog):
    """Records a key combination: hold the keys, then release them."""

    def __init__(self, title: str, on_done):
        super().__init__(title=title, content_width=420)
        self.on_done = on_done
        self.held: set[str] = set()
        self.combo: set[str] = set()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
        icon = Gtk.Image(icon_name="input-keyboard-symbolic", pixel_size=48)
        icon.add_css_class("dim-label")
        self.label = Gtk.Label(label="Press and hold the new combination…")
        self.label.add_css_class("title-3")
        hint = Gtk.Label(label="Release the keys to confirm. Esc cancels.", wrap=True)
        hint.add_css_class("dim-label")
        for w in (icon, self.label, hint):
            box.append(w)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(box)
        self.set_child(view)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._pressed)
        keys.connect("key-released", self._released)
        self.add_controller(keys)

    @staticmethod
    def _name(keyval: int) -> str | None:
        raw = Gdk.keyval_name(keyval) or ""
        if raw in MODIFIER_KEYS:
            return MODIFIER_KEYS[raw]
        name = (Gdk.keyval_name(Gdk.keyval_to_lower(keyval)) or "").lower()
        try:
            from .hotkeys import key_codes

            key_codes(name)
            return name
        except Exception:
            return None

    def _pressed(self, _ctrl, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape and not self.held:
            self.close()
            return True
        name = self._name(keyval)
        if name:
            self.held.add(name)
            self.combo |= self.held
            self.label.set_label(_describe(self._ordered(self.combo)))
        return True

    def _released(self, _ctrl, keyval, _code, _state):
        name = self._name(keyval)
        self.held.discard(name)
        if not self.held and self.combo:
            self.on_done(self._ordered(self.combo))
            self.close()

    @staticmethod
    def _ordered(keys: set[str]) -> list[str]:
        return [m for m in MODIFIER_ORDER if m in keys] + sorted(k for k in keys if k not in MODIFIER_ORDER)


# ---------------------------------------------------------------- window


class SettingsWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="hushtype settings", default_width=780, default_height=760)
        self.set_size_request(360, 400)
        try:
            self.cfg = C.load()
            load_error = None
        except Exception as e:
            self.cfg, load_error = C.Config(), str(e)
        self.saved_cfg = deepcopy(self.cfg)
        self.terms = text.read_terms(self.cfg.text_path("vocabulary"))
        self.style = text.read_style(self.cfg.text_path("style"))
        self.pairs = text.read_replacement_pairs(self.cfg.text_path("replacements"))
        self.saved_text = (list(self.terms), self.style, list(self.pairs))
        self.count, self.exact_tokens = _token_counter(self.cfg)

        self.toasts = Adw.ToastOverlay()
        self.stack = Adw.ViewStack()
        self.toasts.set_child(self.stack)

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        self.save_button = Gtk.Button(label="Save", sensitive=False)
        self.save_button.add_css_class("suggested-action")
        self.save_button.connect("clicked", lambda *_: self.save())
        header.pack_end(self.save_button)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self.toasts)
        switcher_bar = Adw.ViewSwitcherBar(stack=self.stack)
        view.add_bottom_bar(switcher_bar)
        self.set_content(view)

        # narrow windows: move the switcher to the bottom
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 560sp"))
        bp.add_setter(switcher_bar, "reveal", True)
        bp.add_setter(header, "show-title", False)
        self.add_breakpoint(bp)

        self.stack.add_titled_with_icon(self._general_page(), "general", "General",
                                        "preferences-system-symbolic")
        self.stack.add_titled_with_icon(self._vocabulary_page(), "vocabulary", "Vocabulary",
                                        "accessories-dictionary-symbolic")
        self.stack.add_titled_with_icon(self._replacements_page(), "replacements", "Replacements",
                                        "edit-find-replace-symbolic")
        self.stack.add_titled_with_icon(self._style_page(), "style", "Style",
                                        "document-edit-symbolic")
        self.stack.add_titled_with_icon(self._tone_page(), "tone", "Tone",
                                        "user-available-symbolic")

        self.stack.connect("notify::visible-child", self._page_changed)

        save = Gio.SimpleAction.new("save", None)
        save.connect("activate", lambda *_: self.save())
        self.add_action(save)
        app.set_accels_for_action("win.save", ["<Control>s"])
        self.connect("close-request", self._on_close)
        if load_error:
            self.toast(f"Config file has an error, showing defaults: {load_error}", 8)

    # ------------------------------------------------------------ helpers

    def _page_changed(self, *_):
        self.set_focus(None)
        page = self.stack.get_visible_child()
        if isinstance(page, Adw.PreferencesPage):
            page.scroll_to_top()

    def toast(self, message: str, timeout: int = 3, button: str | None = None, action=None) -> None:
        t = Adw.Toast(title=message, timeout=timeout)
        if button:
            t.set_button_label(button)
            t.connect("button-clicked", lambda *_: action())
        self.toasts.add_toast(t)

    def dirty(self) -> bool:
        return self.cfg != self.saved_cfg or (self.terms, self.style, self.pairs) != self.saved_text

    def changed(self, *_):
        self.save_button.set_sensitive(self.dirty())

    def _bind_combo(self, row: Adw.ComboRow, section: str, key: str) -> None:
        def update(*_):
            setattr(getattr(self.cfg, section), key, row.keys[row.get_selected()])
            self.changed()
        row.connect("notify::selected", update)

    def _bind_switch(self, row: Adw.SwitchRow, section: str, key: str) -> None:
        row.set_active(getattr(getattr(self.cfg, section), key))

        def update(*_):
            setattr(getattr(self.cfg, section), key, row.get_active())
            self.changed()
        row.connect("notify::active", update)

    def _bind_spin(self, row: Adw.SpinRow, section: str, key: str, cast=float) -> None:
        row.set_value(getattr(getattr(self.cfg, section), key))

        def update(*_):
            setattr(getattr(self.cfg, section), key, cast(round(row.get_value(), 3)))
            self.changed()
        row.connect("notify::value", update)

    # ------------------------------------------------------------ general

    def _general_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage()
        c = self.cfg

        rec = Adw.PreferencesGroup(title="Recognition")
        lang = _combo(_languages(), "Language", c.model.language,
                      "Pin your language for the best results on short phrases")
        lang.set_enable_search(True)
        self._bind_combo(lang, "model", "language")
        rec.add(lang)
        model = _combo([(k, t) for k, t, _ in MODELS], "Model", c.model.name)
        model.set_subtitle(dict((k, d) for k, _, d in MODELS).get(c.model.name, "Custom model"))
        if c.model.name not in model.keys:
            model.keys.insert(0, c.model.name)
            model.get_model().splice(0, 0, [c.model.name])
            model.set_selected(0)
        self._bind_combo(model, "model", "name")
        model.connect("notify::selected", lambda r, *_: r.set_subtitle(
            dict((k, d) for k, _, d in MODELS).get(r.keys[r.get_selected()], "Custom model")))
        rec.add(model)
        dev = _combo(DEVICES, "Device", c.model.device, "Automatic uses the GPU when CUDA works")
        self._bind_combo(dev, "model", "device")
        rec.add(dev)
        page.add(rec)

        evdev = _in_input_group()
        keys = Adw.PreferencesGroup(
            title="Shortcut",
            description=("Reading the keyboard directly (you are in the input group): any combination works, "
                         "including modifiers only." if evdev else
                         "Using the desktop portal: the shortcut needs a regular key. Join the input group "
                         "(see README) to use combinations like Ctrl + Super."))
        if evdev:
            self.hold_row = self._shortcut_row("Hold to dictate", "hold")
            self.quit_row = self._shortcut_row("Quit hushtype", "quit")
            keys.add(self.hold_row)
            keys.add(self.quit_row)
        else:
            for title, key in (("Hold to dictate", "portal_hold"), ("Quit hushtype", "portal_quit")):
                row = Adw.EntryRow(title=title, text=getattr(c.hotkey, key))
                row.connect("changed", lambda r, k=key: (setattr(self.cfg.hotkey, k, r.get_text()),
                                                        self.changed()))
                keys.add(row)
        page.add(keys)

        audio = Adw.PreferencesGroup(title="Microphone and sounds")
        mics = [("", "System default")] + [(n, n) for n in _microphones()]
        if c.audio.microphone and c.audio.microphone not in [k for k, _ in mics]:
            mics.append((c.audio.microphone, f"“{c.audio.microphone}” (name contains)"))
        mic = _combo(mics, "Microphone", c.audio.microphone)
        self._bind_combo(mic, "audio", "microphone")
        audio.add(mic)
        beeps = Adw.SwitchRow(title="Start and stop sounds")
        self._bind_switch(beeps, "audio", "beeps")
        audio.add(beeps)
        vol = Adw.ActionRow(title="Volume")
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 1.0, 0.02)
        scale.set_value(c.audio.beep_volume)
        scale.set_size_request(180, -1)
        scale.set_valign(Gtk.Align.CENTER)
        def volume(s):
            self.cfg.audio.beep_volume = round(s.get_value(), 2)
            self.changed()
        scale.connect("value-changed", volume)
        vol.add_suffix(scale)
        vol.add_suffix(_icon_button("media-playback-start-symbolic", "Play", self._test_beep))
        beeps.bind_property("active", vol, "sensitive", 2)  # SYNC_CREATE
        audio.add(vol)
        lead = Adw.SpinRow.new_with_range(0.0, 0.5, 0.02)
        lead.set_title("Silence before the start sound")
        lead.set_subtitle("Bluetooth headsets may swallow the first beep; try 0.16 s")
        lead.set_digits(2)
        self._bind_spin(lead, "audio", "beep_lead_silence")
        audio.add(lead)
        mind = Adw.SpinRow.new_with_range(0.1, 3.0, 0.1)
        mind.set_title("Ignore recordings shorter than")
        mind.set_subtitle("Seconds; filters accidental taps")
        mind.set_digits(1)
        self._bind_spin(mind, "audio", "min_duration")
        audio.add(mind)
        page.add(audio)

        out = Adw.PreferencesGroup(title="Behaviour")
        for title, sub, sec, key in (
            ("Type into the focused window", "Off: only copy the text to the clipboard", "output", "paste"),
            ("Restore the clipboard afterwards", "Put back what you had copied before", "output",
             "restore_clipboard"),
            ("Show the overlay", "The small animated pill at the bottom of the screen", "ui", "overlay"),
            ("Show the tray icon", None, "ui", "tray"),
        ):
            row = Adw.SwitchRow(title=title, subtitle=sub or "")
            self._bind_switch(row, sec, key)
            out.add(row)
        page.add(out)

        about = Adw.PreferencesGroup(title="About")
        info = Adw.ActionRow(title=f"hushtype {__version__}", subtitle="Private, offline dictation for Linux")
        info.add_suffix(_icon_button("folder-open-symbolic", "Open the config folder",
                                     lambda: Gio.AppInfo.launch_default_for_uri(C.CONFIG_DIR.as_uri(), None)))
        info.add_suffix(_icon_button("web-browser-symbolic", "Project page",
                                     lambda: Gio.AppInfo.launch_default_for_uri(REPO_URL, None)))
        about.add(info)
        page.add(about)
        return page

    def _shortcut_row(self, title: str, key: str) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title)
        label = Gtk.Label(label=_describe(getattr(self.cfg.hotkey, key)), valign=Gtk.Align.CENTER)
        label.add_css_class("dim-label")
        button = Gtk.Button(label="Change…", valign=Gtk.Align.CENTER)

        def done(keys):
            setattr(self.cfg.hotkey, key, keys)
            label.set_label(_describe(keys))
            self.changed()

        button.connect("clicked", lambda *_: ShortcutDialog(title, done).present(self))
        row.add_suffix(label)
        row.add_suffix(button)
        row.set_activatable_widget(button)
        return row

    def _test_beep(self) -> None:
        try:
            import numpy as np
            import sounddevice as sd

            from .app import BEEP_RATE, START_TONE, STOP_TONE, make_tone

            gap = np.zeros(int(BEEP_RATE * 0.12), dtype=np.float32)
            sd.play(np.concatenate([make_tone(START_TONE, self.cfg.audio), gap,
                                    make_tone(STOP_TONE, self.cfg.audio)]), BEEP_RATE)
        except Exception as e:
            self.toast(f"Could not play sound: {e}")

    # ------------------------------------------------------------ vocabulary

    def _vocabulary_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage()

        meter_group = Adw.PreferencesGroup(
            title="Vocabulary",
            description="Names and terms Whisper should spell correctly. Terms are used from the top until "
                        "the prompt budget is full, so keep the ones it gets wrong most often first.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6)
        self.meter = Gtk.LevelBar(min_value=0, max_value=1)
        self.meter.add_offset_value("full", 1.0)
        self.meter_label = Gtk.Label(xalign=0)
        self.meter_label.add_css_class("dim-label")
        box.append(self.meter)
        box.append(self.meter_label)
        meter_group.add(box)
        page.add(meter_group)

        add_group = Adw.PreferencesGroup()
        self.add_row = Adw.EntryRow(title="Add a term", show_apply_button=True)
        self.add_row.connect("apply", self._add_term)
        self.add_row.connect("entry-activated", self._add_term)
        add_group.add(self.add_row)
        page.add(add_group)

        list_group = Adw.PreferencesGroup()
        self.term_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.term_list.add_css_class("boxed-list")
        self.term_list.set_placeholder(self._placeholder("No terms yet"))
        list_group.add(self.term_list)
        page.add(list_group)

        adv = Adw.PreferencesGroup(title="Advanced")
        budget = Adw.SpinRow.new_with_range(64, 300, 8)
        budget.set_title("Prompt token budget")
        budget.set_subtitle("Shared by the style sample and vocabulary. Higher fits more terms but leaves "
                            "less room for long dictations. Default 224.")
        self._bind_spin(budget, "text", "prompt_token_budget", int)
        budget.connect("notify::value", lambda *_: self._refresh_terms())
        adv.add(budget)
        page.add(adv)

        self._rebuild_terms()
        return page

    def _placeholder(self, label: str) -> Gtk.Widget:
        w = Gtk.Label(label=label, margin_top=18, margin_bottom=18)
        w.add_css_class("dim-label")
        return w

    def _prompt(self) -> text.Prompt:
        return text.build_prompt(self.style, self.terms, self.count, self.cfg.text.prompt_token_budget)

    def _rebuild_terms(self) -> None:
        self.term_list.remove_all()
        self.term_rows = []
        for i, term in enumerate(self.terms):
            row = Adw.EntryRow(text=term)
            row.connect("changed", self._edit_term, i)
            handle = Gtk.Image(icon_name="list-drag-handle-symbolic", tooltip_text="Drag to reorder")
            handle.add_css_class("dim-label")
            row.add_prefix(handle)
            self._make_draggable(row, handle, i)
            row.add_suffix(_icon_button("go-up-symbolic", "Move up", self._move_term, i, -1))
            row.add_suffix(_icon_button("go-down-symbolic", "Move down", self._move_term, i, 1))
            row.add_suffix(_icon_button("user-trash-symbolic", "Remove", self._remove_term, i))
            self.term_list.append(row)
            self.term_rows.append(row)
        self._refresh_terms()

    def _make_draggable(self, row: Gtk.Widget, handle: Gtk.Widget, i: int) -> None:
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        src.connect("prepare", lambda *_: Gdk.ContentProvider.new_for_value(i))
        src.connect("drag-begin", lambda s, drag: s.set_icon(Gtk.WidgetPaintable.new(row), 0, 0))
        handle.add_controller(src)
        dst = Gtk.DropTarget.new(int, Gdk.DragAction.MOVE)
        dst.connect("drop", lambda _t, value, _x, _y: (self._move_term(value, i - value), True)[1])
        row.add_controller(dst)

    def _refresh_terms(self) -> None:
        p = self._prompt()
        for i, row in enumerate(self.term_rows):
            used = i < p.terms_used
            row.set_title(f"#{i + 1}" if used else f"#{i + 1} · over budget, not used")
            (row.remove_css_class if used else row.add_css_class)("dim-label")
        self.meter.set_value(min(1.0, p.tokens / max(1, p.budget)))
        approx = "" if self.exact_tokens else "≈"
        if self.terms:
            self.meter_label.set_label(
                f"{p.terms_used} of {len(self.terms)} terms fit · {approx}{p.tokens} of {p.budget} tokens "
                f"({p.style_tokens} used by the style sample)")
        else:
            self.meter_label.set_label(
                f"{approx}{p.style_tokens} of {p.budget} tokens used by the style sample")

    def _add_term(self, row) -> None:
        term = row.get_text().strip()
        if not term:
            return
        if term.lower() in (t.lower() for t in self.terms):
            self.toast(f"“{term}” is already in the list")
            return
        self.terms.insert(0, term)  # new terms are usually the ones that need help
        row.set_text("")
        self._rebuild_terms()
        self.changed()

    def _edit_term(self, row, i) -> None:
        self.terms[i] = row.get_text().strip()
        self._refresh_terms()
        self.changed()

    def _move_term(self, i: int, delta: int) -> None:
        j = max(0, min(len(self.terms) - 1, i + delta))
        if i != j:
            self.terms.insert(j, self.terms.pop(i))
            self._rebuild_terms()
            self.changed()

    def _remove_term(self, i: int) -> None:
        removed = self.terms.pop(i)
        self._rebuild_terms()
        self.changed()

        def undo():
            self.terms.insert(i, removed)
            self._rebuild_terms()
            self.changed()
        self.toast(f"Removed “{removed}”", button="Undo", action=undo)

    # ------------------------------------------------------------ replacements

    def _replacements_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title="Replacements",
            description="Fixes applied after transcription, for words the model keeps getting wrong. "
                        "Case-insensitive and whole words only.")
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add a rule", valign=Gtk.Align.CENTER)
        add.add_css_class("flat")
        add.connect("clicked", lambda *_: self._add_pair())
        group.set_header_suffix(add)
        self.pair_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.pair_list.add_css_class("boxed-list")
        self.pair_list.set_placeholder(self._placeholder("No rules yet"))
        group.add(self.pair_list)
        page.add(group)

        test = Adw.PreferencesGroup(title="Try it")
        self.try_row = Adw.EntryRow(title="Type a sentence")
        self.try_result = Gtk.Label(xalign=0, wrap=True, selectable=True, margin_top=8,
                                    margin_start=12, margin_end=12)
        self.try_row.connect("changed", lambda *_: self._try_rules())
        test.add(self.try_row)
        test.add(self.try_result)
        page.add(test)
        self._rebuild_pairs()
        return page

    def _rebuild_pairs(self, focus_first: bool = False) -> None:
        self.pair_list.remove_all()
        for i, (wrong, right) in enumerate(self.pairs):
            row = Gtk.ListBoxRow(activatable=False)
            box = Gtk.Box(spacing=8, margin_top=6, margin_bottom=6, margin_start=12, margin_end=6)
            w = Gtk.Entry(text=wrong, placeholder_text="heard as", hexpand=True)
            r = Gtk.Entry(text=right, placeholder_text="write as", hexpand=True)
            arrow = Gtk.Image(icon_name="go-next-symbolic")
            arrow.add_css_class("dim-label")
            w.connect("changed", self._edit_pair, i, 0)
            r.connect("changed", self._edit_pair, i, 1)
            for widget in (w, arrow, r, _icon_button("user-trash-symbolic", "Remove", self._remove_pair, i)):
                box.append(widget)
            row.set_child(box)
            self.pair_list.append(row)
            if focus_first and i == 0:
                GLib.idle_add(w.grab_focus)
        self._try_rules()

    def _add_pair(self) -> None:
        self.pairs.insert(0, ("", ""))
        self._rebuild_pairs(focus_first=True)
        self.changed()

    def _edit_pair(self, entry, i, side) -> None:
        pair = list(self.pairs[i])
        pair[side] = entry.get_text()
        self.pairs[i] = tuple(pair)
        self._try_rules()
        self.changed()

    def _remove_pair(self, i: int) -> None:
        self.pairs.pop(i)
        self._rebuild_pairs()
        self.changed()

    def _clean_pairs(self) -> list[tuple[str, str]]:
        return [(w.strip(), r.strip()) for w, r in self.pairs if w.strip()]

    def _try_rules(self) -> None:
        sample = self.try_row.get_text() if hasattr(self, "try_row") else ""
        if not sample:
            self.try_result.set_label("")
            return
        out = text.apply_replacements(sample, text.compile_replacements(self._clean_pairs()))
        self.try_result.set_label(f"→ {out}")

    # ------------------------------------------------------------ style

    def _style_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title="Your speaking style",
            description="A sentence or two that sounds like you, used as the model's starting context. "
                        "If you mix English terms into another language, show it here so the model "
                        "writes them in English instead of transliterating.")
        buf = Gtk.TextBuffer(text=self.style)
        tv = Gtk.TextView(buffer=buf, wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=12, bottom_margin=12,
                          left_margin=12, right_margin=12)
        tv.set_size_request(-1, 160)
        frame = Gtk.Frame(child=tv)
        self.style_label = Gtk.Label(xalign=0, margin_top=6)
        self.style_label.add_css_class("dim-label")

        def update(*_):
            self.style = " ".join(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).split())
            self._style_info()
            if hasattr(self, "term_rows"):
                self._refresh_terms()
            self.changed()

        buf.connect("changed", update)
        group.add(frame)
        group.add(self.style_label)
        page.add(group)
        self._style_info()
        return page

    def _style_info(self) -> None:
        p = self._prompt()
        approx = "" if self.exact_tokens else "≈"
        n = len(self.style.split())
        msg = f"{n} words · {approx}{p.style_tokens} tokens"
        if n and len(p.style.split()) < n:
            msg += f" · only the first {len(p.style.split())} words fit (half of the budget)"
        self.style_label.set_label(msg)

    # ------------------------------------------------------------ tone

    def _tone_page(self) -> Adw.PreferencesPage:
        self.tone_page = Adw.PreferencesPage()
        self.tone_groups: list[Adw.PreferencesGroup] = []
        self.tone_try_text = "Hi John, thanks for the update. New paragraph. I will send the files " \
                             "tomorrow. Best regards."
        self._fill_tone_page()
        return self.tone_page

    def _fill_tone_page(self) -> None:
        """(Re)build the page; called again when tones are added or removed."""
        for g in self.tone_groups:
            self.tone_page.remove(g)
        self.tone_groups = []
        c = self.cfg

        def add(group):
            self.tone_page.add(group)
            self.tone_groups.append(group)

        top = Adw.PreferencesGroup(
            title="Per-app tone",
            description="Shape the text for the app you dictate into: no final period in chats, an email "
                        "layout in your mail app. Your words are never rewritten. Works on KDE Plasma, "
                        "sway, Hyprland and X11.")
        enabled = Adw.SwitchRow(title="Match the tone to the app")
        self._bind_switch(enabled, "tone", "enabled")
        top.add(enabled)
        add(top)

        names = [(n, n.replace("_", " ").capitalize()) for n in c.tones]
        kinds = Adw.PreferencesGroup(title="Tone for each kind of app")
        for key, title, examples in CATEGORIES:
            row = _combo(names, title, getattr(c.tone, key), examples)
            self._bind_combo(row, "tone", key)
            row.connect("notify::selected", lambda *_: self._try_tone())
            enabled.bind_property("active", row, "sensitive", GObject.BindingFlags.SYNC_CREATE)
            kinds.add(row)
        add(kinds)

        test = Adw.PreferencesGroup(title="Try it")
        self.tone_try = Adw.EntryRow(title="Type what you would say", text=self.tone_try_text)
        self.tone_result = Gtk.Label(xalign=0, wrap=True, selectable=True, margin_top=8,
                                     margin_start=12, margin_end=12)
        self.tone_try.connect("changed", lambda *_: self._try_tone())
        test.add(self.tone_try)
        test.add(self.tone_result)
        add(test)

        tones = Adw.PreferencesGroup(
            title="Tones",
            description="What each tone does. Change the built-in ones or add your own.")
        new = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add a tone", valign=Gtk.Align.CENTER)
        new.add_css_class("flat")
        new.connect("clicked", lambda *_: self._add_tone())
        tones.set_header_suffix(new)
        for name in c.tones:
            tones.add(self._tone_editor(name))
        add(tones)

        rules = Adw.PreferencesGroup(
            title="Your app rules",
            description="Comma-separated text found in an app id or window title, checked before the "
                        "built-in lists. Run `hushtype app` to see what a window reports. Put an app "
                        "under Everything else to leave it alone.")
        for key, title, _ in CATEGORIES:
            row = Adw.EntryRow(title=title, text=", ".join(getattr(c.apps, key)))

            def update(r, key=key):
                setattr(self.cfg.apps, key, _split_list(r.get_text()))
                self.changed()
            row.connect("changed", update)
            rules.add(row)
        add(rules)
        self._try_tone()

    def _tone_summary(self, st: tone.ToneStyle) -> str:
        period = {"add": "adds the final period", "remove": "no final period",
                  "remove_short": f"no final period up to {st.short_sentences} sentences"}
        parts = [dict(CASE_ROWS)[st.case] if st.case != "unchanged" else "",
                 period.get(st.final_period, ""),
                 "email layout" if st.layout == "email" else "",
                 (f"{len(st.replacements)} replacement" + ("s" if len(st.replacements) > 1 else ""))
                 if st.replacements else ""]
        return " · ".join(p for p in parts if p) or "Leaves the text unchanged"

    def _tone_editor(self, name: str) -> Adw.ExpanderRow:
        st = self.cfg.tones[name]
        exp = Adw.ExpanderRow(title=name.replace("_", " ").capitalize(), subtitle=self._tone_summary(st))

        def touched():
            exp.set_subtitle(self._tone_summary(st))
            if hasattr(exp, "short_row"):
                exp.short_row.set_sensitive(st.final_period == "remove_short")
            self._try_tone()
            self.changed()

        def combo(rows, title, attr):
            row = _combo(rows, title, getattr(st, attr))

            def update(*_):
                setattr(st, attr, row.keys[row.get_selected()])
                touched()
            row.connect("notify::selected", update)
            exp.add_row(row)

        combo(CASE_ROWS, "Sentence case", "case")
        combo(PERIOD_ROWS, "Final period", "final_period")
        short = Adw.SpinRow.new_with_range(1, 50, 1)
        short.set_title("Short message: up to this many sentences")
        short.set_value(st.short_sentences)
        short.connect("notify::value", lambda r, *_: (setattr(st, "short_sentences", int(r.get_value())),
                                                      touched()))
        short.set_sensitive(st.final_period == "remove_short")
        exp.short_row = short
        exp.add_row(short)
        combo(LAYOUT_ROWS, "Layout", "layout")

        def entry(title, attr, as_list=True):
            value = getattr(st, attr)
            row = Adw.EntryRow(title=title, text=", ".join(value) if as_list else value.replace("\n", " | "))

            def update(r):
                t = r.get_text()
                lines = "\n".join(x.strip() for x in t.split("|")).strip()
                setattr(st, attr, _split_list(t) if as_list else lines)
                touched()
            row.connect("changed", update)
            exp.add_row(row)

        entry("Signature (email layout; | starts a new line)", "signature", as_list=False)
        entry("Extra greetings, comma-separated", "greetings")
        entry("Extra closings, comma-separated", "closings")
        entry("Replacements in this tone: going to => gonna, ...", "replacements")

        if name in tone.BUILTIN_TONES:
            button = Gtk.Button(label="Reset to built-in", halign=Gtk.Align.END, margin_top=6,
                                margin_bottom=6, margin_end=12)
            button.connect("clicked", lambda *_: self._reset_tone(name))
        else:
            button = Gtk.Button(label="Delete tone", halign=Gtk.Align.END, margin_top=6, margin_bottom=6,
                                margin_end=12)
            button.add_css_class("destructive-action")
            button.connect("clicked", lambda *_: self._delete_tone(name))
        exp.add_row(button)
        return exp

    def _add_tone(self) -> None:
        dlg = Adw.AlertDialog(heading="New tone", body="Lowercase letters, digits and _ only.")
        name_entry = Gtk.Entry(placeholder_text="e.g. client_chat", activates_default=True)
        dlg.set_extra_child(name_entry)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("add", "Add")
        dlg.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("add")

        def respond(_d, response):
            name = name_entry.get_text().strip().lower().replace(" ", "_")
            if response != "add" or not name:
                return
            if not C.TONE_NAME.match(name) or name in self.cfg.tones:
                self.toast(f"Cannot add '{name}': invalid or already used")
                return
            self.cfg.tones[name] = tone.ToneStyle()
            self._fill_tone_page()
            self.changed()
        dlg.connect("response", respond)
        dlg.present(self)

    def _reset_tone(self, name: str) -> None:
        self.cfg.tones[name] = deepcopy(tone.BUILTIN_TONES[name])
        self._fill_tone_page()
        self.changed()

    def _delete_tone(self, name: str) -> None:
        del self.cfg.tones[name]
        for key, _, _ in CATEGORIES:  # categories using it fall back to default
            if getattr(self.cfg.tone, key) == name:
                setattr(self.cfg.tone, key, "default")
        self._fill_tone_page()
        self.changed()

    def _try_tone(self) -> None:
        if not hasattr(self, "tone_result"):
            return
        self.tone_try_text = sample = self.tone_try.get_text()
        lang = self.cfg.model.language if self.cfg.model.language != "auto" else None
        blocks = []
        if sample.strip():
            for key, title, _ in CATEGORIES:
                out = tone.apply(sample, self.cfg.style_for(key), self.terms, lang)
                blocks.append(f"<b>{GLib.markup_escape_text(title)}</b>\n{GLib.markup_escape_text(out)}")
        self.tone_result.set_markup("\n\n".join(blocks))

    # ------------------------------------------------------------ save and apply

    def save(self) -> bool:
        if not self.dirty():
            return True
        try:
            C.save(self.cfg)
            terms = [t for t in self.terms if t]
            text.write_terms(self.cfg.text_path("vocabulary"), terms)
            text.write_style(self.cfg.text_path("style"), self.style)
            text.write_replacement_pairs(self.cfg.text_path("replacements"), self._clean_pairs())
        except Exception as e:
            self.toast(f"Could not save: {e}", 6)
            return False
        old = self.saved_cfg
        self.saved_cfg = deepcopy(self.cfg)
        self.terms = terms
        self.pairs = self._clean_pairs()
        self.saved_text = (list(self.terms), self.style, list(self.pairs))
        self.changed()
        self._apply(restart=(old.model.name, old.model.device, old.hotkey, old.ui)
                    != (self.cfg.model.name, self.cfg.model.device, self.cfg.hotkey, self.cfg.ui))
        return True

    def _apply(self, restart: bool) -> None:
        from .app import running_pid

        pid = running_pid()
        if pid is None:
            self.toast("Saved. hushtype is not running", button="Start", action=start_hushtype)
            return
        if not restart:
            os.kill(pid, signal.SIGHUP)
            self.toast("Saved and applied")
            return
        os.kill(pid, signal.SIGTERM)
        self.toast("Saved. Restarting hushtype…")
        deadline = time.time() + 10

        def wait():
            if running_pid() is None:
                start_hushtype()
                return False
            return time.time() < deadline
        GLib.timeout_add(200, wait)

    def _on_close(self, *_):
        if not self.dirty():
            return False
        dlg = Adw.AlertDialog(heading="Save changes?",
                              body="You have unsaved changes. They will be lost if you close now.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("discard", "Discard")
        dlg.add_response("save", "Save")
        dlg.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("save")

        def respond(_d, response):
            if response == "discard" or (response == "save" and self.save()):
                self.saved_cfg, self.saved_text = self.cfg, (self.terms, self.style, self.pairs)
                self.close()
        dlg.connect("response", respond)
        dlg.present(self)
        return True


def start_hushtype() -> None:
    exe = shutil.which("hushtype")
    cmd = [exe] if exe else [sys.executable, "-m", "hushtype"]
    subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- app


def _screenshot(win: SettingsWindow, spec: str) -> None:
    """HUSHTYPE_SETTINGS_SCREENSHOT=page:/path.png renders one page to PNG and exits (for docs)."""
    page, path = spec.split(":", 1)

    def render():
        win.stack.set_visible_child_name(page)
        GLib.timeout_add(600, save)
        return False

    def save():
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(win).snapshot(snap, win.get_width(), win.get_height())
        win.get_native().get_renderer().render_texture(snap.to_node(), None).save_to_png(path)
        win.get_application().quit()
        return False

    GLib.timeout_add(1000, render)


class SettingsApp(Adw.Application):
    def __init__(self):
        flags = Gio.ApplicationFlags.DEFAULT_FLAGS
        if os.environ.get("HUSHTYPE_SETTINGS_SCREENSHOT"):
            flags |= Gio.ApplicationFlags.NON_UNIQUE  # don't hand off to an open window
        super().__init__(application_id=APP_ID, flags=flags)

    def do_activate(self):
        win = self.get_active_window() or SettingsWindow(self)
        win.present()
        spec = os.environ.get("HUSHTYPE_SETTINGS_SCREENSHOT")
        if spec:
            _screenshot(win, spec)


def main() -> None:
    GLib.set_application_name("hushtype")
    sys.exit(SettingsApp().run([sys.argv[0]]))
