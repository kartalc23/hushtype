"""The dictation engine: record while the hotkey is held, transcribe, paste.

Audio never leaves the machine. The model is loaded from the local cache and
the network is only used once, to download it on first run.
"""

from __future__ import annotations

import ctypes
import fcntl
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

from . import config as C
from . import hotkeys, text

SAMPLE_RATE = 16000
BEEP_RATE = 44100
MAX_LOG_BYTES = 512 * 1024

HIDDEN, RECORDING, PROCESSING = 0, 1, 2  # overlay states


# ---------------------------------------------------------------- logging


def _trim_log() -> None:
    try:
        if C.LOG_FILE.exists() and C.LOG_FILE.stat().st_size > MAX_LOG_BYTES:
            lines = C.LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            C.LOG_FILE.write_text("\n".join(lines[-500:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def log(message: str) -> None:
    """Print and append to the log file.

    Dictated TEXT is never passed here on purpose: it must not end up on disk.
    """
    try:
        print(message, flush=True)
    except Exception:
        pass
    try:
        C.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(C.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%H:%M:%S}] {message}\n")
    except Exception:
        pass


# ---------------------------------------------------------------- model


def _language(cfg: C.Config) -> str | None:
    lang = cfg.model.language.strip().lower()
    return None if lang in ("", "auto") else lang


def open_model(cfg: C.Config, device: str, compute_type: str) -> WhisperModel:
    """Open the model from local files first.

    A plain WhisperModel(...) call asks Hugging Face for updates on every
    start, which leaks your IP and model choice even though no audio is sent.
    We only go online when the model is genuinely missing (first run).
    """
    return WhisperModel(model_path(cfg.model.name), device=device, compute_type=compute_type)


def model_path(name: str) -> str:
    """Resolve the model to a local directory, downloading it only if missing.

    Kept separate from WhisperModel(...) so a CUDA error while loading is never
    mistaken for a missing model (which would trigger a pointless download).
    """
    from faster_whisper.utils import download_model

    if Path(name).is_dir():
        return name
    C.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        return download_model(name, local_files_only=True, cache_dir=str(C.MODELS_DIR))
    except Exception:
        log(f"model '{name}' not found locally, downloading it once...")
        return download_model(name, cache_dir=str(C.MODELS_DIR))


def token_counter(model: WhisperModel | None = None, path: str | None = None) -> text.TokenCounter:
    """Count tokens with Whisper's own tokenizer, or estimate if it is unavailable."""
    tok = getattr(model, "hf_tokenizer", None)
    if tok is None and path:
        try:
            from tokenizers import Tokenizer

            tok = Tokenizer.from_file(str(Path(path) / "tokenizer.json"))
        except Exception:
            tok = None
    if tok is None:
        return text.estimate_tokens
    return lambda s: len(tok.encode(s, add_special_tokens=False).ids)


class Transcriber:
    def __init__(self, cfg: C.Config):
        self.cfg = cfg
        self.model, self.gpu = self._load()
        self.reload_text(cfg)

    def reload_text(self, cfg: C.Config) -> None:
        """(Re)read vocabulary, style and replacements; called on start and on SIGHUP."""
        self.cfg = cfg
        p = text.build_prompt(
            text.read_style(cfg.text_path("style")),
            text.read_terms(cfg.text_path("vocabulary")),
            token_counter(self.model),
            cfg.text.prompt_token_budget,
        )
        for line in text.describe_prompt(p):
            log(line)
        self.prompt, self.hotwords = p.style, p.hotwords
        pairs = text.read_replacement_pairs(cfg.text_path("replacements"))
        self.rules = text.compile_replacements(pairs)
        if pairs:
            log(f"replacements: {len(pairs)} rules")

    def _load(self):
        """Return (model, on_gpu). The GPU is verified with a real computation."""
        log(f"loading model: {self.cfg.model.name}")
        if self.cfg.model.device in ("auto", "cuda"):
            try:
                m = open_model(self.cfg, "cuda", "int8_float16")
                # Loading is not proof: cuBLAS/cuDNN are only touched on the first
                # real decode. Force the encoder with a synthetic signal now so a
                # broken CUDA setup shows up at startup, not on your first dictation.
                probe = np.zeros(SAMPLE_RATE, dtype=np.float32)
                probe[::80] = 0.1
                self.prompt = self.hotwords = ""
                self._run(m, probe, vad=False)
                log("device: GPU (CUDA)")
                return m, True
            except Exception as e:
                if self.cfg.model.device == "cuda":
                    raise
                log(f"GPU unavailable ({type(e).__name__}: {e})")
                log("falling back to CPU, transcription will be slower")
        m = open_model(self.cfg, "cpu", "int8")
        log("device: CPU")
        return m, False

    def _run(self, model, audio, vad: bool = True) -> str:
        segments, _ = model.transcribe(
            audio,
            language=_language(self.cfg),
            beam_size=self.cfg.model.beam_size,
            vad_filter=vad,
            vad_parameters={"min_silence_duration_ms": 400},
            initial_prompt=self.prompt or None,
            hotwords=self.hotwords or None,
            # Feeding the model its own output on long recordings leads to
            # hallucinated loops; turn it off.
            condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segments).strip()

    def transcribe(self, audio) -> str:
        try:
            result = self._run(self.model, audio)
        except Exception as e:
            log(f"[error] transcription failed: {type(e).__name__}: {e}")
            # Only switch devices on genuine CUDA errors. Falling back to CPU on
            # any error would make every later dictation ~14x slower because of
            # one bad request.
            msg = f"{type(e).__name__} {e}".lower()
            cuda_error = any(k in msg for k in ("cuda", "cublas", "cudnn", "gpu", "device", "out of memory"))
            if not (self.gpu and cuda_error):
                raise
            log("[error] GPU path broken, switching to CPU")
            self.model, self.gpu = open_model(self.cfg, "cpu", "int8"), False
            result = self._run(self.model, audio)
        return text.apply_replacements(result, self.rules)


def release_memory() -> None:
    """Return freed pages to the OS.

    CUDA/cuDNN allocate large scratch buffers while decoding. They are freed
    afterwards, but glibc keeps them mapped and RSS stays high.
    """
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


# ---------------------------------------------------------------- devices


def list_microphones(out=log) -> None:
    out("available microphones:")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            out(f"  [{i}] {d['name']}")


def pick_microphone(choice: str):
    if not choice:
        return None
    if choice.isdigit():
        return int(choice)
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and choice.lower() in d["name"].lower():
            return i
    log(f"WARNING: no microphone matching '{choice}', using the default")
    return None


# ---------------------------------------------------------------- single instance


def runtime_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or C.STATE_DIR)


LOCK_FILE_NAME = "hushtype.lock"


def running_pid() -> int | None:
    """PID of the running instance, or None. Uses the lock itself, so stale files don't count."""
    path = runtime_dir() / LOCK_FILE_NAME
    try:
        with open(path) as f:
            try:
                fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except OSError:
                return int(f.read().strip() or 0) or None  # locked: an instance holds it
            fcntl.flock(f, fcntl.LOCK_UN)
            return None
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- beeps


START_TONE, STOP_TONE = 587.33, 392.00  # D5, G4


def make_tone(freq: float, audio: C.AudioConfig) -> np.ndarray:
    """A soft tone with a sin^2 envelope, so there is no click at the edges."""
    n = int(BEEP_RATE * audio.beep_duration)
    i = np.arange(n)
    env = np.sin(np.pi * i / n) ** 2
    tone = (audio.beep_volume * env * np.sin(2 * np.pi * freq * i / BEEP_RATE)).astype(np.float32)
    lead = np.zeros(int(BEEP_RATE * audio.beep_lead_silence), dtype=np.float32)
    return np.concatenate([lead, tone])


# ---------------------------------------------------------------- app


class App:
    def __init__(self, cfg: C.Config):
        self.cfg = cfg
        self.frames: list = []
        self.recording = False
        self.stream = None
        self.jobs: queue.Queue = queue.Queue()
        self.stop = threading.Event()
        self.tray: subprocess.Popen | None = None
        self.listener = None
        self.sender: hotkeys.KeySender | None = None
        self.transcriber: Transcriber | None = None
        self.overlay_state = HIDDEN
        self.level = 0.0
        self.paste_count = 0
        self.beeps: dict = {}
        self.mic = None
        self._lock_file = None

    # ------------------------------------------------------------ beeps

    def _tone(self, freq: float) -> np.ndarray:
        return make_tone(freq, self.cfg.audio)

    def _beep(self, start: bool) -> None:
        if not self.beeps:
            return
        try:
            sd.play(self.beeps["start" if start else "stop"], BEEP_RATE)
        except Exception as e:
            log(f"WARNING: could not play beep: {e}")

    # ------------------------------------------------------------ recording

    def _on_audio(self, indata, frames, when, status):
        if self.recording:
            self.frames.append(indata.copy())
            self.level = float(np.sqrt((indata**2).mean()))

    def start_recording(self) -> None:
        if self.recording:
            return
        try:
            self.stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                callback=self._on_audio, device=self.mic,
            )
            self.stream.start()
        except Exception as e:
            log(f"[error] cannot open microphone: {type(e).__name__}: {e}")
            self.stream = None
            return
        self.frames.clear()
        self.recording = True
        self.overlay_state = RECORDING
        self._tray_send("recording")
        self._beep(True)
        log("[rec] listening...")

    def stop_recording(self) -> None:
        if not self.recording:
            return
        self.recording = False
        self._tray_send("ready")
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        self._beep(False)
        if not self.frames:
            self.overlay_state = HIDDEN
            log("[rec] no audio received")
            return
        audio = np.concatenate(self.frames, axis=0).flatten()
        duration = len(audio) / SAMPLE_RATE
        if duration < self.cfg.audio.min_duration:
            self.overlay_state = HIDDEN
            log(f"[rec] too short ({duration:.1f} s), skipped")
            return
        rms = float(np.sqrt((audio**2).mean()))
        if rms < self.cfg.audio.silence_warning:
            log(f"[rec] WARNING: very low input level (RMS {rms:.5f}); wrong microphone?")
        self.overlay_state = PROCESSING
        log(f"[rec] {duration:.1f} s, transcribing...")
        self.jobs.put((audio, duration))

    def reload(self, *_):
        """SIGHUP handler: re-read the config and text files without reloading the model.

        Model, device, hotkeys and UI toggles need a restart; the settings app
        restarts us when those change.
        """
        threading.Thread(target=self._reload, daemon=True).start()

    def _reload(self) -> None:
        try:
            cfg = C.load()
        except Exception as e:
            log(f"[reload] config not reloaded: {e}")
            return
        log("[reload] config and text files reloaded")
        self.cfg = cfg
        self.mic = pick_microphone(cfg.audio.microphone)
        self.beeps = {}
        if cfg.audio.beeps:
            self.beeps = {"start": self._tone(START_TONE), "stop": self._tone(STOP_TONE)}
        if self.transcriber is not None:
            self.transcriber.reload_text(cfg)

    def toggle_recording(self, *_):
        """SIGUSR1 handler: press-to-start / press-to-stop for custom bindings."""
        if self.recording:
            self.stop_recording()
        else:
            self.start_recording()

    # ------------------------------------------------------------ output

    def _wait_for_modifiers(self, limit: float = 1.5) -> bool:
        """Wait until the user lets go of modifier keys.

        Ctrl can come up a moment after the other keys. Sending Ctrl+V while
        the user still holds Ctrl+Super makes the paste collide with it.
        """
        if not isinstance(self.listener, hotkeys.EvdevListener):
            time.sleep(0.2)  # the portal can't tell us key state; leave some slack
            return True
        t0 = time.time()
        while time.time() - t0 < limit:
            if not self.listener.modifier_held():
                return True
            time.sleep(0.02)
        return False

    def paste(self, result: str) -> None:
        """Put the text on the clipboard, press Ctrl+V, then restore the old clipboard."""
        previous = hotkeys.clipboard_read() if self.cfg.output.restore_clipboard else None
        try:
            hotkeys.clipboard_write(result)
        except Exception as e:
            log(f"[error] cannot write to the clipboard: {e}")
            return
        if not self.cfg.output.paste or self.sender is None or self.sender.tool is None:
            log("[paste] text is on the clipboard; paste with Ctrl+V")
            return
        if not self._wait_for_modifiers():
            log("[paste] WARNING: modifier keys still held, sending anyway")
        time.sleep(0.05)
        self.paste_count += 1
        log(f"[paste] sending Ctrl+V (#{self.paste_count} this session)")
        if not self.sender.ctrl_v():
            return  # leave the text on the clipboard so the user can paste it
        time.sleep(0.35)
        if previous is not None:
            try:
                hotkeys.clipboard_write(previous)
            except Exception:
                pass

    def _worker(self) -> None:
        while not self.stop.is_set():
            try:
                audio, duration = self.jobs.get(timeout=0.3)
            except queue.Empty:
                continue
            t0 = time.time()
            try:
                result = self.transcriber.transcribe(audio)
            except Exception as e:
                log(f"[error] {type(e).__name__}: {e}")
                self.overlay_state = HIDDEN
                continue
            elapsed = time.time() - t0
            self.overlay_state = HIDDEN
            if not result:
                log(f"[result] empty ({elapsed:.1f} s)")
                continue
            speed = duration / elapsed if elapsed else 0
            log(f"[result] {elapsed:.1f} s ({speed:.1f}x realtime) | {len(result.split())} words")
            if sys.stdout.isatty():
                print(f"         {result}", flush=True)  # terminal only, never the log
            self.paste(result)
            release_memory()

    # ------------------------------------------------------------ hotkeys

    def _start_hotkeys(self) -> tuple[str, str]:
        """evdev first, then the desktop portal, then SIGUSR1 only.

        Returns (dictation hint, quit hint) for whichever path won, so the
        messages never point the user at the wrong keys.
        """
        hk = self.cfg.hotkey
        try:
            self.listener = hotkeys.EvdevListener(
                hk.hold, hk.quit, self.start_recording, self.stop_recording,
                self.stop.set, self.stop.is_set, log)
            threading.Thread(target=self.listener.run, daemon=True).start()
            return "hold " + hotkeys.describe(hk.hold), hotkeys.describe(hk.quit)
        except Exception as e:
            log(f"[keys] evdev unavailable: {e}")
            log("       for hold-to-talk with any keys: sudo usermod -aG input $USER, then reboot")

        try:
            self.listener = hotkeys.PortalListener(
                hk.portal_hold, hk.portal_quit, self.start_recording, self.stop_recording,
                self.stop.set, self.stop.is_set, log)
            threading.Thread(target=self.listener.run, daemon=True).start()
            log("[keys] binding shortcuts through the desktop portal (a dialog may appear)")
            self.listener.ready.wait(timeout=120)
            if self.listener.error is not None:
                raise self.listener.error
            return f"hold {self.listener.description}", self.listener.quit_description
        except Exception as e:
            log(f"[keys] portal unavailable: {e}")
            self.listener = None

        log("[keys] no global hotkey; bind a custom shortcut to 'hushtype toggle'")
        return "run 'hushtype toggle'", "tray > Quit"

    # ------------------------------------------------------------ tray

    def _tray_send(self, msg: str) -> None:
        if self.tray is None or self.tray.poll() is not None:
            return
        try:
            self.tray.stdin.write(msg.encode() + b"\n")
            self.tray.stdin.flush()
        except Exception:
            pass

    def _start_tray(self, hint: str) -> None:
        if not self.cfg.ui.tray:
            return
        try:
            self.tray = subprocess.Popen(
                [sys.executable, "-m", "hushtype.tray", hint],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            log(f"WARNING: tray icon unavailable: {e}")
            return

        def listen():
            for line in self.tray.stdout:
                if line.strip() == b"quit":
                    log("[tray] quit selected")
                    self.stop.set()

        threading.Thread(target=listen, daemon=True).start()

    # ------------------------------------------------------------ lifecycle

    def _single_instance(self) -> bool:
        path = runtime_dir() / LOCK_FILE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._lock_file = open(path, "a+")
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        self._lock_file.seek(0)
        self._lock_file.truncate()
        self._lock_file.write(str(os.getpid()))
        self._lock_file.flush()
        return True

    def _wait(self) -> None:
        while not self.stop.is_set():
            time.sleep(0.2)

    def run(self) -> None:
        _trim_log()
        log("=" * 50)
        log("hushtype starting")
        if not self._single_instance():
            log("another instance is already running, exiting")
            return

        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop.set())
        signal.signal(signal.SIGUSR1, self.toggle_recording)
        signal.signal(signal.SIGHUP, self.reload)

        if self.cfg.audio.beeps:
            self.beeps = {"start": self._tone(START_TONE), "stop": self._tone(STOP_TONE)}
        self.mic = pick_microphone(self.cfg.audio.microphone)
        try:
            log(f"microphone: {sd.query_devices(self.mic, 'input')['name']}")
        except Exception as e:
            log(f"WARNING: cannot query the microphone: {e}")

        t0 = time.time()
        self.transcriber = Transcriber(self.cfg)
        log(f"ready ({time.time() - t0:.1f} s)")

        self.sender = hotkeys.KeySender(log)
        threading.Thread(target=self._worker, daemon=True).start()
        hint, quit_hint = self._start_hotkeys()
        log(f"{hint}, speak, release. Quit: {quit_hint} or tray > Quit")
        self._start_tray(hint)

        if self.cfg.ui.overlay:
            try:
                from .overlay import Overlay

                Overlay(lambda: self.overlay_state, lambda: self.level, self.stop.is_set).run()
            except Exception as e:
                log(f"WARNING: overlay unavailable ({type(e).__name__}: {e}), continuing without it")
                self._wait()
        else:
            self._wait()

        self.stop.set()
        if self.recording:
            self.stop_recording()
        if self.tray is not None:
            try:
                self.tray.stdin.close()
                self.tray.wait(timeout=2)
            except Exception:
                self.tray.kill()
        if self.sender is not None:
            self.sender.close()
        log("exited")
