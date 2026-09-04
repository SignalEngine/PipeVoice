"""Pipevoice application: wires hotkey -> record -> transcribe -> type, plus the
tray icon and the live overlay. State machine: idle -> recording -> transcribing.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

from . import autostart, config
from .audio import Recorder
from .hotkey import HotkeyManager
from .meeting import MeetingRecorder, meetings_dir
from .overlay import Overlay
from .tray import Tray
from .typer import apply_replacements, copy_clipboard, type_text

log = logging.getLogger("wisprlite")

try:
    import winsound  # Windows audio cues
except Exception:
    winsound = None


class ScreenrecUI:
    """The pill's phase, shared between the finish thread and the Tk thread.

    Kept out of App because both threads touch it every frame: one lock and one
    dict beats sprinkling attributes that are read mid-write.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = {"phase": "recording"}
        self._name_event = threading.Event()
        self._name = ""

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def update(self, **fields) -> None:
        with self._lock:
            self._state.update(fields)

    def clear(self) -> None:
        with self._lock:
            self._state = {"phase": "recording"}

    def expect_name(self, default: str) -> threading.Event:
        with self._lock:
            self._name = ""
            self._name_event.clear()
            self._state.update(phase="naming", name=default)
        return self._name_event

    def answer_name(self, typed: str) -> None:
        with self._lock:
            self._name = (typed or "").strip()
            # Phase only. The status line belongs to whoever is doing the work,
            # and setting one here flashed a string nobody owned for a frame.
            self._state.update(phase="working")
        self._name_event.set()

    def abandon_name(self) -> None:
        """Nobody answered. Drop the field so a very late Save cannot strand a
        name that the finished recording will never see."""
        with self._lock:
            self._name = ""
            self._state.update(phase="working")
        self._name_event.set()

    def take_name(self) -> str:
        with self._lock:
            name, self._name = self._name, ""
            return name


class App:
    def __init__(self) -> None:
        self.cfg = config.Config.load()
        self.paused = False
        self._meeting = MeetingRecorder(
            device=config.device_arg(self.cfg),
            max_minutes=self.cfg.meeting_max_minutes,
            retention_sessions=self.cfg.meeting_retention_sessions,
            bookmark_acoustic=self.cfg.bookmark_acoustic,
            bookmark_sensitivity=self.cfg.bookmark_sensitivity,
            on_auto_stop=self._on_meeting_auto_stop,
        )
        self._meeting_active = False
        self._meeting_degraded = False
        self._meeting_errors_reported = set()

        self.recorder = Recorder(device=config.device_arg(self.cfg))
        self.overlay = Overlay(
            level_provider=lambda: self.recorder.level,
            meeting_provider=self._meeting_overlay_state,
            on_meeting_click=self.toggle_meeting,
            screenrec_provider=self._screenrec_overlay_state,
            on_screenrec_action=self._screenrec_action,
            enabled=self.cfg.overlay,
        )
        self.tray = Tray(self)
        self.hotkeys = HotkeyManager(
            get_hotkey=lambda: self.cfg.hotkey,
            get_mode=lambda: self.cfg.mode,
            on_start=lambda: self._on_start(clipboard=False),
            on_stop=self._on_stop,
            is_paused=lambda: self.paused or self._meeting_active,
        )
        # second hotkey: same record flow, but the result goes to the clipboard
        self.clip_hotkeys = HotkeyManager(
            get_hotkey=lambda: self.cfg.clipboard_hotkey,
            get_mode=lambda: self.cfg.mode,
            on_start=lambda: self._on_start(clipboard=True),
            on_stop=self._on_stop,
            is_paused=lambda: self.paused or self._meeting_active,
        )
        self.meeting_hotkeys = HotkeyManager(
            get_hotkey=lambda: self.cfg.meeting_hotkey,
            get_mode=lambda: "toggle",
            on_start=self.toggle_meeting,
            on_stop=self.toggle_meeting,
            is_paused=lambda: self.paused and not self._meeting_active,
        )
        self.screenrec_hotkeys = HotkeyManager(
            get_hotkey=lambda: self.cfg.screenrec_hotkey,
            get_mode=lambda: "toggle",
            on_start=self.toggle_screen_recording,
            on_stop=self.toggle_screen_recording,
            is_paused=self._screen_recording_paused,
        )
        self.bookmark_hotkeys = HotkeyManager(
            get_hotkey=lambda: self.cfg.bookmark_hotkey,
            get_mode=lambda: "ptt",
            on_start=lambda: self._meeting.mark_bookmark("hotkey"),
            on_stop=lambda: None,
            is_paused=lambda: not self._meeting_active,
        )

        self._screenrec = None        # the live ScreenRecording, None when idle
        self._screenrec_selecting = False   # the region selector is open
        self._screenrec_finishing = None    # the thread muxing/sending, if any
        self._screenrec_agent = None        # Event an MCP caller is waiting on
        self._screenrec_agent_result = None
        self._screenrec_ui = ScreenrecUI()   # the pill's phase
        self._finished_recording = None      # what its Play/Copy act on
        self._armed_voice = None      # a Voice armed by the picker, consumed by the next utterance
        self._voice_mgrs = []         # dedicated voice HotkeyManagers
        self._picker_mgr = None       # the picker HotkeyManager
        self._picker_open = False
        self._build_voice_hotkeys()

        self._engines = {}            # per-engine cache (name -> engine), for per-app profiles
        self._session = None
        self._clipboard_only = False
        self._polish_error: str | None = None
        self._active = {}             # per-utterance profile overrides (never mutates cfg)
        self._fg_ctx = {}             # foreground window captured at hotkey-press time
        self._started_at = 0.0
        self._busy = threading.Lock()
        self._stop = threading.Event()
        self._pending_agent_listen = None   # set while a listen() awaits the user's hotkey
        self._bridge = None                  # agent_bridge.ControlListener when MCP is on

    # ---- engine -----------------------------------------------------------
    def _build_engine(self, name: str | None = None):
        name = name or self.cfg.engine
        vocab = (self.cfg.vocabulary or "").strip()
        lang = (self.cfg.language or "").split("-")[0] or None
        if name == "gemini":
            from .engines.gemini_engine import GeminiEngine

            if not config.gemini_key():
                raise RuntimeError("GEMINI_API_KEY is not set")
            return GeminiEngine(model=self.cfg.gemini_model, language=lang, prompt=vocab)
        if name == "groq":
            from .engines.groq_engine import GroqEngine

            if not config.groq_key():
                raise RuntimeError("GROQ_API_KEY is not set")
            return GroqEngine(model=self.cfg.groq_model, language=lang, prompt=vocab)
        if name == "openai":  # legacy: dropped from the UI, kept for existing configs
            from .engines.openai_engine import OpenAIEngine

            if not config.openai_key():
                raise RuntimeError("OPENAI_API_KEY is not set")
            return OpenAIEngine(model=self.cfg.openai_model,
                                language=(self.cfg.language or "").split("-")[0] or None, prompt=vocab)
        if name == "deepgram":
            from .engines.deepgram_engine import DeepgramEngine

            if not config.deepgram_key():
                raise RuntimeError("DEEPGRAM_API_KEY is not set")
            kw = [t.strip() for t in vocab.split(",") if t.strip()] or None
            return DeepgramEngine(
                api_key=config.deepgram_key(),
                model=self.cfg.deepgram_model,
                language=self.cfg.language or "en-US",
                keywords=kw,
                finish_timeout=self.cfg.deepgram_finish_timeout,
            )
        if name == "local":
            from .engines.local_engine import LocalEngine

            return LocalEngine(model_size=self.cfg.local_model_size,
                               language=(self.cfg.language or "").split("-")[0] or None, prompt=vocab,
                               device=self.cfg.local_device, compute_type=self.cfg.local_compute_type)
        raise RuntimeError(f"Unknown engine: {name}")

    def _get_engine(self, name: str | None = None):
        name = name or self.cfg.engine
        eng = self._engines.get(name)
        if eng is None:
            eng = self._build_engine(name)
            self._engines[name] = eng
        return eng

    def _eff(self, key):
        """Effective value for this utterance: a per-app profile override, else the setting."""
        return self._active.get(key, getattr(self.cfg, key))

    def _prewarm(self) -> None:
        def work():
            try:
                self._get_engine()
            except Exception as exc:
                print("engine warm-up:", exc, file=sys.stderr)

        threading.Thread(target=work, daemon=True).start()

    # ---- voice hotkeys + picker -------------------------------------------
    def _build_voice_hotkeys(self) -> None:
        # stop any existing ones first (called on init and on config reload)
        for m in self._voice_mgrs:
            try: m.stop()
            except Exception: pass
        if self._picker_mgr:
            try: self._picker_mgr.stop()
            except Exception: pass
        self._voice_mgrs = []
        self._picker_mgr = None
        for entry in (self.cfg.voice_hotkeys or []):
            hk = (entry.get("hotkey") or "").strip()
            vn = (entry.get("voice") or "").strip()
            if not hk or not vn:
                continue
            mgr = HotkeyManager(
                get_hotkey=(lambda h=hk: h),
                get_mode=lambda: self.cfg.mode,
                on_start=(lambda v=vn: self._on_start(voice=v)),
                on_stop=self._on_stop,
                is_paused=lambda: self.paused or self._meeting_active,
            )
            self._voice_mgrs.append(mgr)
        pk = (self.cfg.voice_picker_hotkey or "").strip()
        if pk:
            self._picker_mgr = HotkeyManager(
                get_hotkey=(lambda h=pk: h),
                get_mode=lambda: "ptt",             # fire on every press (toggle would skip every 2nd press)
                on_start=self._open_picker,
                on_stop=lambda: None,
                is_paused=lambda: self.paused or self._meeting_active,
            )

    def _start_voice_hotkeys(self) -> None:
        for m in self._voice_mgrs:
            m.start()
        if self._picker_mgr:
            self._picker_mgr.start()

    def _open_picker(self) -> None:
        if self._picker_open:
            return
        names = []
        try:
            from . import voices
            names = voices.names(self.cfg)
        except Exception:
            names = []
        if not names:
            return
        self._picker_open = True
        self.overlay.show_picker(names[:6], "Pick a voice (1-6, Esc)")
        threading.Thread(target=self._picker_loop, args=(names[:6],), daemon=True).start()

    def _picker_loop(self, names: list) -> None:
        import keyboard
        deadline = time.time() + 8.0     # auto-cancel after 8s
        chosen = None
        try:
            while time.time() < deadline and self._picker_open:
                if keyboard.is_pressed("esc"):
                    break
                hit = False
                for i, nm in enumerate(names, start=1):
                    if keyboard.is_pressed(str(i)):
                        chosen = nm; hit = True; break
                if hit:
                    break
                time.sleep(0.03)
        except Exception:
            pass
        self._picker_open = False
        if chosen:
            self._armed_voice = chosen
            self.overlay.set_state("done", f"Voice: {chosen} — talk now")
            # auto-disarm if not used within 12s
            def _disarm(name=chosen):
                if self._armed_voice == name:
                    self._armed_voice = None
            threading.Timer(12.0, _disarm).start()
        else:
            self.overlay.hide()

    # ---- hotkey callbacks (run on the hotkey thread) ----------------------
    def _on_start(self, clipboard: bool = False, voice: str | None = None) -> None:
        if not self._busy.acquire(blocking=False):
            return  # still finishing the previous utterance
        self._clipboard_only = clipboard
        # Capture the focused app NOW (before our overlay shows) and resolve a
        # per-app profile into per-utterance overrides. Never mutates self.cfg.
        voice = voice or self._armed_voice     # picker arms a one-shot voice
        self._armed_voice = None
        try:
            from . import foreground, profiles, voices
            self._fg_ctx = foreground.detect()
            if voice:
                self._active = voices.resolve(self.cfg, voice)   # explicit Voice overrides any profile
            else:
                self._active = profiles.resolve(self.cfg, self._fg_ctx)
        except Exception:
            self._fg_ctx, self._active = {}, {}
        try:
            engine = self._get_engine(self._active.get("engine"))
            self._session = engine.start_session(on_partial=self.overlay.set_text)
        except Exception as exc:
            self._session = None
            log.exception("could not start session (engine=%s)", self._active.get("engine") or self.cfg.engine)
            self._fail(str(exc))
            self._release()
            return
        self._beep(880, 70)
        self._set_icon("recording")
        self.overlay.show("listening", "")
        self.recorder.start(on_frame=self._session.feed if engine.streaming else None)
        self._started_at = time.time()

    def _on_stop(self) -> None:
        if self._session is None and not self._busy.locked():
            return
        self._beep(620, 60)
        audio = self.recorder.stop()
        duration = time.time() - self._started_at
        self._set_icon("transcribing")
        threading.Thread(target=self._finish, args=(audio, duration), daemon=True).start()

    def _finish(self, audio, duration) -> None:
        try:
            if duration < self.cfg.min_seconds or audio.size == 0:
                # Close the socket we opened on key-down. Without this a tap too
                # short to transcribe left a live Deepgram connection with no
                # audio on it, and ten seconds later the server killed it with a
                # 1011 - two ERROR lines per accidental tap, and a connection
                # held open for nothing. 59 of them in one log.
                if self._session is not None:
                    try:
                        self._session.cancel()
                    except Exception:
                        log.exception("cancel failed on short press")
                self.overlay.hide()
                self._set_icon("idle")
                return

            self.overlay.set_state("transcribing", "Transcribing…")
            try:
                text = self._session.finish(audio) if self._session else ""
            except Exception as exc:
                log.exception("transcription failed (engine=%s)", self.cfg.engine)
                text = self._fallback(audio, exc)

            text = (text or "").strip()

            # Agent MCP listen: route the transcript to the caller instead of typing.
            if self._pending_agent_listen is not None:
                import concurrent.futures
                pending = self._pending_agent_listen
                self._pending_agent_listen = None
                try:
                    answer = apply_replacements(self._polish(text), self.cfg.replacements) if text else ""
                except Exception:
                    log.exception("agent-listen polish failed")
                    answer = text  # fall back to the raw transcript so the caller still gets it
                try:
                    pending["future"].set_result({"status": "ok", "text": answer})
                except concurrent.futures.InvalidStateError:
                    pass  # caller already timed out / cancelled
                self.overlay.set_state("done", "↩ sent to agent" if text else "↩ (nothing heard)")
                self._set_icon("idle")
                return

            if not text:
                self.overlay.hide()
                self._set_icon("idle")
                return

            # Spoken commands (terminal actions) — run on the RAW transcript so
            # cleanup can't reword "scratch that" / "send it".
            from . import commands

            cmd = commands.pre(text, self.cfg.voice_commands)
            if cmd.discard:
                self.overlay.set_state("done", "✗ scratched")
                self._beep(440, 80)
                self._set_icon("idle")
                return
            text = cmd.text

            # AI cleanup ("Flow mode") — OpenAI / Gemini / OpenRouter / local Ollama.
            text = self._polish(text)

            # inline formatting commands ("new line") — after cleanup so newlines survive
            text = commands.inline(text, self.cfg.voice_commands)

            # user word-fixes, applied last so they always stick
            text = apply_replacements(text, self.cfg.replacements)

            press_enter = self._eff("auto_enter") or cmd.press_enter
            if not text and not press_enter:
                self.overlay.hide()
                self._set_icon("idle")
                return

            # Output: clipboard hotkey, a profile's clipboard output, or no text
            # target focused (desktop/shell) -> copy instead of typing into the void.
            from . import foreground

            out = self._eff("output_mode")
            to_clipboard = (self._clipboard_only or out == "clipboard"
                            or foreground.is_no_text_target(self._fg_ctx))
            if self._deliver(text, to_clipboard, out, press_enter) and self._polish_error:
                self.overlay.set_state(
                    "error", f"Raw text — polish failed: {self._polish_error}"[:110])
            self._beep(990, 60)
            self._set_icon("idle")
        finally:
            self._session = None
            self._active = {}
            self._fg_ctx = {}
            self._release()

    def _deliver(self, text: str, to_clipboard: bool, out: str, press_enter: bool) -> bool:
        """Save the words, then hand them over. In that order, deliberately.

        Returns True when delivery landed cleanly, so a caller can add a softer
        warning on top without stomping a real failure message.
        """
        # Record BEFORE delivering. Typing is the fragile half - a window
        # that refuses synthetic input, a keyboard backend that raises - and
        # this used to run AFTER it, inside a try that has only a finally.
        # So a failed paste took the transcript down with it: nothing in the
        # box AND nothing in the history, which is how you lose words you
        # have already said.
        if self.cfg.history_enabled and text:
            try:
                from . import history

                history.record(text, "clipboard" if to_clipboard else "typed")
            except Exception:
                log.exception("history record failed")

        if to_clipboard:
            if text:
                copy_clipboard(text)
            self.overlay.set_state("done", "Copied to clipboard" if text else "↵")
            return True
        else:
            try:
                type_text(text, out, press_enter=press_enter,
                          paste_speed=self.cfg.paste_speed)
                # "done" only once it IS done. Set before the attempt, a failure
                # showed the polished text as a success for a frame and then
                # flipped to an error - two contradictory answers to "did that
                # work?", in the order that reads as yes.
                self.overlay.set_state("done", text or "↵")
                return True
            except Exception as exc:
                # Never swallow the words. Put them somewhere usable and say
                # so, rather than showing the polished text on the overlay
                # and delivering nothing.
                log.exception("typing failed (mode=%s)", out)
                if text and copy_clipboard(text):
                    self.overlay.set_state(
                        "error", "Couldn't type there — copied instead, press Ctrl+V")
                else:
                    self._fail(f"couldn't type that: {exc}")
                return False

    def _fallback(self, audio, err) -> str:
        """If a cloud engine failed (e.g. offline), try local Whisper once."""
        # Use the engine actually active this utterance (a profile may override it).
        active_engine = self._active.get("engine") or self.cfg.engine
        if active_engine == "local":
            self._fail(str(err))
            return ""
        try:
            self.overlay.set_state("transcribing", "Cloud failed — using local…")
            # Through the CACHE, not a fresh LocalEngine. Building one loads the
            # whole Whisper model, and this path built a new one for every failed
            # utterance — so a spell of cloud trouble meant paying a full model
            # load on every single thing you said. That is the "it got much
            # slower" people report when their key or quota goes bad.
            local = self._get_engine("local")
            return local.start_session(on_partial=self.overlay.set_text).finish(audio)
        except Exception as exc:
            self._fail(f"{err} (local fallback: {exc})")
            return ""

    # ---- tray actions -----------------------------------------------------
    def set_engine(self, name: str) -> None:
        self.cfg.engine = name
        self.cfg.save()
        self.tray.update()
        self._prewarm()

    def set_mode(self, mode: str) -> None:
        self.cfg.mode = mode
        self.cfg.save()
        self.tray.update()

    def set_output(self, mode: str) -> None:
        self.cfg.output_mode = mode
        self.cfg.save()
        self.tray.update()

    def toggle_overlay(self) -> None:
        self.cfg.overlay = not self.cfg.overlay
        self.cfg.save()
        if self.cfg.overlay:
            self.overlay.enabled = True
            self.overlay.start()  # no-op if already running
        else:
            self.overlay.hide()
        self.tray.update()

    def toggle_sounds(self) -> None:
        self.cfg.sounds = not self.cfg.sounds
        self.cfg.save()
        self.tray.update()

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.tray.update()

    @property
    def meeting_active(self) -> bool:
        return self._meeting_active

    def _meeting_overlay_state(self) -> dict:
        return {
            "elapsed": self._meeting.elapsed,
            "levels": self._meeting.levels,
            "errors": self._meeting.fatal_errors,
            "bleed": self._meeting.bleed_suspected,
        }

    # ---- screen recording ------------------------------------------------

    def _screen_recording_paused(self) -> bool:
        """Paused means paused.

        Screen + microphone is the most invasive capture this app performs, so
        it must not be the one that ignores the switch. A recording that is
        already running can still be STOPPED while paused — otherwise pausing
        mid-recording would strand it with no way to finish the file.
        """
        return bool(self.paused) and self._screenrec is None


    def _screenrec_overlay_state(self) -> dict:
        """What the pill draws. Live numbers from the recorder, phase from here."""
        info = dict(self._screenrec_ui.snapshot())
        recording = self._screenrec
        if recording is not None:
            try:
                info["elapsed"] = recording.elapsed()
                info["paused"] = recording.paused
            except Exception:
                pass
        return info

    def _ask_name_in_pill(self, default: str) -> str:
        """Show the name field IN the pill and wait for Save or Skip.

        A separate dialog for one short field meant three windows for one
        action. Bounded: if the pill is switched off, or nobody ever answers,
        the recording keeps its timestamp rather than waiting for ever.
        """
        if not self.cfg.overlay:
            return ""
        answered = self._screenrec_ui.expect_name(default)
        if not answered.wait(timeout=180.0):
            self._screenrec_ui.abandon_name()
            return ""
        return self._screenrec_ui.take_name()

    def _screenrec_action(self, action: str, value: str = "") -> None:
        """A control on the pill. Runs on the overlay's worker thread."""
        if action in ("save", "skip"):
            self._screenrec_ui.answer_name(value if action == "save" else "")
            return
        if action in ("play", "copy", "open"):
            self._screenrec_done_action(action)
            return
        recording = self._screenrec
        if recording is None:
            # Stop clears _screenrec immediately and then muxes, transcribes and
            # uploads on a thread — seconds during which the pill is still up.
            # Without this guard a second press of Stop in that window falls
            # through to toggle_screen_recording's "nothing is recording" branch
            # and opens a region selector to start a NEW one.
            return
        if action == "stop":
            # Same path as the hotkey, so there is exactly one way to finish a
            # recording and both routes get the naming, transcript and send.
            self.toggle_screen_recording()
            return
        try:
            if action == "pause":
                recording.pause()
            elif action == "resume":
                recording.resume()
        except Exception as exc:
            self._fail(f"screen recording: {exc}")

    def _screenrec_done_action(self, action: str) -> None:
        """Play / Copy path / Open, from the finished-clip pill."""
        video = self._finished_recording
        if video is None:
            return
        try:
            if action == "play":
                from .screenrec_tab import _play

                _play(Path(video))
            elif action == "copy":
                copy_clipboard(str(video))
            elif action == "open":
                self.open_settings(tab="Recordings", select=Path(video).stem)
        except Exception as exc:
            log.info("screenrec: %s failed: %s", action, exc)
        if action in ("play", "open"):
            # The pill has done its job once you have gone somewhere else.
            self._screenrec_ui.clear()
            self.overlay.hide()

    def toggle_screen_recording(self) -> None:
        """Hotkey once: pick a region and record. Again: stop, transcribe, send."""
        if self._screenrec is not None:
            thread = threading.Thread(target=self._finish_screen_recording,
                                      name="screenrec-finish", daemon=True)
            self._screenrec_finishing = thread
            thread.start()
            return
        if self._screenrec_selecting:
            # The region selector is already open. A second press is the user
            # trying again, not asking for a second selector on top.
            return
        self._screenrec_selecting = True
        threading.Thread(target=self._begin_screen_recording,
                         name="screenrec-begin", daemon=True).start()

    def _begin_screen_recording(self) -> None:
        from . import screenrec

        try:
            # The overlay would otherwise sit on top of the very thing being
            # framed, and end up inside the recording.
            self.overlay.hide()
            region = screenrec.select_region()
            if not region:
                return
            out_dir = (self.cfg.screenrec_dir or "").strip()
            recording = screenrec.ScreenRecording(
                region,
                Path(out_dir) if out_dir else screenrec.default_output_dir(),
                fps=self.cfg.screenrec_fps,
                device=config.device_arg(self.cfg),
            )
            recording.start()
            self._screenrec = recording
            self._screenrec_ui.clear()
            self._finished_recording = None
            self.overlay.show_screenrec()
        except Exception as exc:
            self._screenrec = None
            self._fail(f"screen recording: {exc}")
        finally:
            self._screenrec_selecting = False

    def _finish_screen_recording(self, ask: bool = True) -> None:
        """Mux, transcribe and send. `ask=False` skips the naming dialog.

        Shutdown passes ask=False: a modal that waits for input is the one
        thing quitting must not do. Nobody is looking at a window that is in
        the middle of closing, so it would hang the quit until the dialog is
        found and dismissed. The recording keeps its timestamp name.
        """
        from . import screenrec

        recording, self._screenrec = self._screenrec, None
        if recording is None:
            return
        try:
            self._screenrec_ui.update(phase="working", status="Wrapping up the video\u2026")
            video = recording.stop()
            if video is None:
                self._fail("screen recording: " + "; ".join(recording.errors[:2]))
                return

            # Named AFTER the fact: the moment you want to hit record is the
            # wrong moment to be filling in a form. The timestamp always leads
            # so an inbox full of these still sorts.
            # An agent is blocked waiting on this, and the user recorded because
            # the agent asked — do not stop them for a filename.
            agent_call = self._screenrec_agent is not None
            typed = self._ask_name_in_pill(recording.stem) if (ask and not agent_call) else ""
            if typed:
                recording.rename(screenrec.stamped_stem(recording.stem, typed))
                video = recording.video_path
            files = [video]
            self._screenrec_ui.update(phase="working", status="Writing down what you said\u2026")
            transcript = self._transcribe_recording(recording)
            if transcript is not None:
                files.append(transcript)
            # Say so. The transcript is the thing that makes the clip cheap for
            # an agent to read, and silently shipping without it looks
            # identical to shipping with it.
            note = "" if transcript is not None else " (no transcript — check the mic)"

            # An agent asked for this one. It already gets the local path back,
            # so shipping the file to a remote host on the agent's say-so is a
            # second, larger act that nobody consented to. Hand back the path
            # and stop; the user can still send it themselves with the hotkey.
            waiter = self._screenrec_agent
            if waiter is not None:
                self._screenrec_agent = None
                self._screenrec_agent_result = {
                    "status": "ok",
                    "video_path": str(video),
                    "transcript_path": str(transcript) if transcript else "",
                    "transcript": self._read_text(transcript),
                    "uploaded": False,
                }
                self.overlay.show("done", f"✓ saved to {video.parent}{note}")
                waiter.set()
                return

            destination = (self.cfg.screenrec_destination or "").strip()
            if destination:
                self._screenrec_ui.update(phase="working", status="Sending it over\u2026")
                ok, message = screenrec.send(files, destination)
                if not ok:
                    # Never delete on failure, and never claim it arrived.
                    self._fail(f"kept locally — send failed: {message}")
                    return
                if not self.cfg.screenrec_keep_local:
                    for path in [*files, recording.audio_path]:
                        try:
                            Path(path).unlink()
                        except OSError:
                            pass
            # Deliberately NOT announcing the scp destination. It is a string
            # the user configured, it never changes, and leaving it on screen
            # was one more box to dismiss. Whether a clip was delivered belongs
            # in the Recordings tab, next to the clip.
            self._finished_recording = video
            # The path lands on the clipboard without being asked: it is what
            # gets pasted to an agent, and it costs nothing. OPENING the tab is
            # a button now, not something that steals focus from whatever you
            # were doing.
            try:
                copy_clipboard(str(video))
            except Exception as exc:
                log.info("screenrec: could not copy the path: %s", exc)
            self._screenrec_ui.update(
                phase="done",
                title="Recording saved" + (" and sent" if destination else ""),
                subtitle=(recording.stem + note).strip(),
            )
        except Exception as exc:
            self._fail(f"screen recording: {exc}")
        finally:
            # The pill is driven by a phase, so every early return above (no
            # frames, send failed, exception) would otherwise leave it showing a
            # sweeping progress bar or a name field for ever. Anything that did
            # not reach "done" ends as a dismissable error instead.
            if self._screenrec_ui.snapshot().get("phase") != "done":
                self._screenrec_ui.clear()
                self.overlay.hide()
            # Every early return would equally leave an agent blocked until its
            # own timeout with no idea why. Release it with the reason instead.
            waiter, self._screenrec_agent = self._screenrec_agent, None
            if waiter is not None:
                self._screenrec_agent_result = {
                    "status": "error",
                    "error": "; ".join(recording.errors[:2]) or "the recording produced no file",
                }
                waiter.set()

    def _handoff_recording(self, video, stem: str) -> None:
        """Clipboard, then the Recordings tab open on this clip.

        Deliberately AFTER the send: the path is only worth pasting once the
        file it names is finished. Failures here are cosmetic - the recording
        is already safe on disk - so neither step is allowed to raise.
        """
        try:
            copy_clipboard(str(video))
        except Exception as exc:
            log.info("screenrec: could not copy the path: %s", exc)
        try:
            self.open_settings(tab="Recordings", select=stem)
        except Exception as exc:
            log.info("screenrec: could not open the Recordings tab: %s", exc)

    @staticmethod
    def _read_text(path) -> str:
        try:
            return Path(path).read_text(encoding="utf-8") if path else ""
        except OSError:
            return ""

    def on_agent_record_screen(self, prompt: str = "", timeout: int = 300) -> dict:
        """An agent asks the user to SHOW it the problem. Returns clip + words.

        The user drags the region and presses the hotkey to stop, so nothing is
        captured without a deliberate act — an agent cannot silently start
        recording a screen and a microphone. Esc during selection returns
        `cancelled` and writes no file.
        """
        if self._screenrec is not None or self._screenrec_selecting:
            return {"status": "error", "error": "a screen recording is already in progress"}
        if self.paused:
            return {"status": "error", "error": "PipeVoice is paused"}
        hotkey = (self.cfg.screenrec_hotkey or "").strip()
        if not hotkey:
            # Without it the user has started something they cannot stop.
            return {"status": "error",
                    "error": "no screen recording hotkey is set — Settings > Screen recording"}

        self._screenrec_agent_result = None
        waiter = threading.Event()
        self._screenrec_agent = waiter
        try:
            self._screenrec_selecting = True
            if prompt:
                self._notify(prompt)
            self._begin_screen_recording()
            if self._screenrec is None:
                return {"status": "cancelled", "error": "no region was selected"}
            if not waiter.wait(timeout=max(5.0, float(timeout))):
                return {"status": "timeout",
                        "error": f"still recording after {timeout}s — press {hotkey} to stop"}
            return self._screenrec_agent_result or {
                "status": "error", "error": "the recording finished without a result"}
        finally:
            if self._screenrec_agent is waiter:
                self._screenrec_agent = None

    def _transcribe_recording(self, recording) -> Path | None:
        """Narration as text, so the agent reads it instead of decoding frames."""
        try:
            from .engines import transcribe as T

            if not recording.audio_path.exists():
                return None
            # transcribe_file returns a dict, not a string.
            result = T.transcribe_file(
                str(recording.audio_path),
                model_size=(self.cfg.transcribe_model_size or self.cfg.local_model_size),
                device=self.cfg.local_device,
                compute_type=self.cfg.local_compute_type,
            )
            text = str((result or {}).get("text") or "").strip()
            if not text:
                return None
            recording.transcript_path.write_text(text, encoding="utf-8")
            return recording.transcript_path
        except Exception as exc:
            log.info("screenrec: transcript failed: %s", exc)
            return None

    def toggle_meeting(self) -> None:
        if self._meeting_active:
            try:
                self._meeting.stop()
            finally:
                self._stop_pipefocus()
                self._meeting_active = False
                self._meeting_degraded = False
                if getattr(self, "overlay", None) is not None:
                    self.overlay.hide()
                self._set_icon("idle")
                self.tray.update()
            return
        if self.paused:
            return

        self._meeting_active = True
        self._meeting_degraded = False
        self._meeting_errors_reported.clear()
        self._start_pipefocus()
        try:
            self._meeting.start()
        except Exception as exc:
            # Focus was started first, so it must be released here or it keeps a
            # billable stream open for a meeting that never began.
            self._stop_pipefocus()
            self._meeting_active = False
            self._fail(f"meeting: {exc}")
            self.tray.update()
            return
        if getattr(self, "overlay", None) is not None:
            self.overlay.show_meeting()
        self._set_icon("meeting")
        self.tray.update()

    def _start_pipefocus(self) -> None:
        """Attach a live focus session, if the user asked for one.

        Deepgram ONLY: PipeFocus needs live streaming transcription and no other
        engine provides it. Rather than half-work elsewhere it simply does not
        run. Anything failing here must leave the RECORDING untouched — losing a
        meeting to a focus problem would be indefensible.
        """
        self._meeting.focus_session = None
        cfg = self.cfg
        if not getattr(cfg, "pipefocus", False) or cfg.engine != "deepgram":
            return
        try:
            from . import cleanup, focus
            from .engines.deepgram_engine import focus_stream

            def completion(messages):
                return cleanup.chat_completion(
                    messages, cfg.cleanup_provider, cfg.cleanup_model
                )

            session = focus.FocusSession(
                connect=lambda on_text: focus_stream(cfg, on_text),
                completion=completion,
                on_tip=self._show_focus_tip,
            )
            session.start()
            self._meeting.focus_session = session
        except Exception as exc:
            log.info("PipeFocus unavailable: %s", exc)
            self._meeting.focus_session = None

    def _stop_pipefocus(self) -> None:
        session = getattr(self._meeting, "focus_session", None)
        self._meeting.focus_session = None
        if session is not None:
            try:
                session.stop()
            except Exception:
                pass

    def _show_focus_tip(self, tip: str, because: str) -> None:
        overlay = getattr(self, "overlay", None)
        if overlay is not None and hasattr(overlay, "show_focus_tip"):
            overlay.show_focus_tip(tip, because)

    def _check_meeting_errors(self) -> None:
        if not self._meeting_active:
            return
        errors = self._meeting.errors
        fatal_errors = self._meeting.fatal_errors
        failures = []
        for label, error in errors.items():
            failure = (label, error)
            if error is not None and failure not in self._meeting_errors_reported:
                self._meeting_errors_reported.add(failure)
                failures.append(f"{label}: {error}")
        if any(error is not None for error in fatal_errors.values()):
            self._meeting_degraded = True
        if failures:
            self._fail("meeting capture: " + "; ".join(failures))
            timer = threading.Timer(2.2, self._restore_meeting_overlay)
            timer.daemon = True
            timer.start()

    def _restore_meeting_overlay(self) -> None:
        if self._meeting_active and getattr(self, "overlay", None) is not None:
            self.overlay.show_meeting()

    def _on_meeting_auto_stop(self, reason: str) -> None:
        if not self._meeting_active:
            return
        # The recorder can stop itself (max minutes, fatal capture error) without
        # going through toggle_meeting. Releasing the socket ONLY there left a
        # live, billable Deepgram stream open for the rest of the session.
        self._stop_pipefocus()
        self._meeting_active = False
        self._meeting_degraded = False
        if getattr(self, "overlay", None) is not None:
            self.overlay.hide()
        self._fail(f"meeting stopped: {reason}")
        self.tray.update()

    # ---- agent MCP -------------------------------------------------------
    def _polish(self, text: str) -> str:
        """Optional LLM cleanup ('Flow mode'); returns the input unchanged if off/unavailable."""
        self._polish_error = None
        if not (text and self._eff("ai_cleanup")):
            return text
        from . import cleanup
        if not cleanup.provider_ready(self.cfg.cleanup_provider):
            return text
        self.overlay.set_state("transcribing", "Polishing…")
        polished = cleanup.clean(text, self.cfg.cleanup_provider, self.cfg.cleanup_model,
                                 self.cfg.language, self.cfg.speech_notes,
                                 style=self._eff("cleanup_style"),
                                 custom_instruction=self._eff("cleanup_instruction"))
        if polished is None:
            # Flow mode was ON and the provider was reachable, yet nothing came
            # back - a dead model id, a revoked key, a quota wall. This used to
            # return the raw text with only a log line, so the words simply got
            # worse and nobody could see why. One log carried 235 of these.
            self._polish_error = cleanup.last_error() or "AI cleanup unavailable"
            log.warning("polish failed, delivering raw text: %s", self._polish_error)
        return polished or text

    def _agent_dispatch(self, req: dict) -> dict:
        op = req.get("op")
        if op == "listen":
            return self.on_agent_listen(req.get("prompt", ""), req.get("timeout", 45), req.get("mode", ""))
        if op == "transcribe":
            return self.on_agent_transcribe(req.get("path", ""), req.get("format", "json"),
                                            req.get("language", ""), req.get("model_size", ""))
        if op == "record_screen":
            return self.on_agent_record_screen(req.get("prompt", ""), req.get("timeout", 300))
        return {"status": "error", "error": f"unknown op: {op}"}

    def on_agent_transcribe(self, path="", fmt="json", language="", model_size="") -> dict:
        import os
        if not path or not os.path.exists(path):
            return {"status": "error", "error": f"file not found: {path}"}
        size = model_size or self.cfg.transcribe_model_size or self.cfg.local_model_size
        try:
            from .engines.transcribe import transcribe_file
            r = transcribe_file(path, language=(language or None), model_size=size)
        except Exception as exc:
            log.exception("agent transcribe failed")
            return {"status": "error", "error": str(exc)}
        out = {"status": "ok", "text": r["text"], "language": r["language"], "duration": r["duration"]}
        fmt = (fmt or "json").lower()
        if fmt in ("srt", "vtt"):
            from . import captions
            out["captions"] = captions.to_srt(r["segments"]) if fmt == "srt" else captions.to_vtt(r["segments"])
        else:
            out["segments"] = r["segments"]
        return out

    def on_agent_listen(self, prompt="", timeout=45, mode="") -> dict:
        import concurrent.futures
        mode = mode or self.cfg.mcp_default_mode
        if (
            self._meeting_active
            or self._busy.locked()
            or self._pending_agent_listen is not None
        ):
            return {"status": "busy", "text": ""}
        if mode == "hands_free":
            return self._agent_listen_hands_free(prompt, timeout)
        # push-to-talk: arm, cue the overlay, wait for the user's normal hotkey cycle.
        fut = concurrent.futures.Future()
        self._pending_agent_listen = {"future": fut}
        self.overlay.show("listening", prompt or "🎙 your agent is listening — hold your hotkey & speak")
        try:
            return fut.result(timeout=max(1, int(timeout)))
        except concurrent.futures.TimeoutError:
            fut.cancel()
            # If the user is mid-utterance when we time out, KEEP _pending_agent_listen
            # set so _finish() routes (and discards) that utterance instead of typing it
            # into the foreground app. If nothing is in flight, disarm so the next normal
            # dictation behaves normally.
            if not self._busy.locked():
                self._pending_agent_listen = None
                self.overlay.hide()
            return {"status": "timeout", "text": ""}

    def _agent_listen_hands_free(self, prompt="", timeout=45) -> dict:
        import time
        from .vad import SilenceEndpointer
        if self._meeting_active or not self._busy.acquire(blocking=False):
            return {"status": "busy", "text": ""}
        try:
            try:
                engine = self._get_engine()
                self._session = engine.start_session(on_partial=self.overlay.set_text)
            except Exception as exc:
                self._fail(str(exc))
                return {"status": "error", "text": "", "error": str(exc)}
            self.overlay.show("listening", prompt or "🎙 your agent is listening…")
            self._set_icon("recording")
            self.recorder.start(on_frame=self._session.feed if engine.streaming else None)
            endpointer = SilenceEndpointer(silence_ms=self.cfg.hands_free_silence_ms)
            deadline = time.time() + max(1, int(timeout))
            while time.time() < deadline:
                time.sleep(0.05)
                if endpointer.feed(self.recorder.level):
                    break
            audio = self.recorder.stop()
            self._set_icon("transcribing")
            if not endpointer.heard_speech:
                self.overlay.hide(); self._set_icon("idle")
                return {"status": "timeout", "text": ""}
            try:
                text = self._session.finish(audio) if self._session else ""
            except Exception as exc:
                text = self._fallback(audio, exc)
            text = apply_replacements(self._polish((text or "").strip()), self.cfg.replacements)
            self.overlay.set_state("done", "↩ sent to agent")
            self._beep(990, 60); self._set_icon("idle")
            return {"status": "ok", "text": text}
        finally:
            self._session = None
            self._release()

    def _mcp_add_command(self) -> str:
        if getattr(sys, "frozen", False):
            return f'claude mcp add pipevoice -- "{sys.executable}" --mcp'
        return "claude mcp add pipevoice -- python -m wisprlite --mcp"

    def start_mcp_bridge(self) -> None:
        if self._bridge is not None:
            return
        try:
            from .agent_bridge import ControlListener
            self._bridge = ControlListener(self.cfg.mcp_port, self._agent_dispatch)
            self._bridge.start()
            log.info("MCP control bridge on 127.0.0.1:%s", self.cfg.mcp_port)
        except Exception as exc:
            self._bridge = None
            log.warning("MCP bridge failed to start: %s", exc)
            self._fail(f"MCP bridge: {exc}")

    def stop_mcp_bridge(self) -> None:
        if self._bridge is not None:
            try:
                self._bridge.stop()
            except Exception:
                pass
            self._bridge = None

    def toggle_mcp(self) -> None:
        self.cfg.mcp_enabled = not self.cfg.mcp_enabled
        self.cfg.save()
        if self.cfg.mcp_enabled:
            self.start_mcp_bridge()
            self._notify("Agent MCP on. Register once:\n" + self._mcp_add_command())
        else:
            self.stop_mcp_bridge()
        self.tray.update()

    def open_settings(self, tab: str = "", select: str = "") -> None:
        import os
        import subprocess

        # The settings window is a separate process, so "open it on the
        # Recordings tab with this clip picked" has to travel as environment.
        env = dict(os.environ)
        if tab:
            env["PV_TAB"] = tab
        if select:
            env["PV_SELECT"] = select
        try:
            if getattr(sys, "frozen", False):
                subprocess.Popen([sys.executable, "--settings"], env=env)
            else:
                from .autostart import _pythonw

                parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                subprocess.Popen([_pythonw(), "-m", "wisprlite", "--settings"],
                                 cwd=parent, env=env)
        except Exception as exc:
            self._fail(f"settings: {exc}")

    def open_history(self) -> None:
        import os
        import subprocess

        try:
            if getattr(sys, "frozen", False):
                subprocess.Popen([sys.executable, "--history"])
            else:
                from .autostart import _pythonw

                parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                subprocess.Popen([_pythonw(), "-m", "wisprlite", "--history"], cwd=parent)
        except Exception as exc:
            self._fail(f"history: {exc}")

    def open_transcribe(self) -> None:
        import os
        import subprocess

        try:
            if getattr(sys, "frozen", False):
                subprocess.Popen([sys.executable, "--transcribe"])
            else:
                from .autostart import _pythonw

                parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                subprocess.Popen([_pythonw(), "-m", "wisprlite", "--transcribe"], cwd=parent)
        except Exception as exc:
            self._fail(f"transcribe: {exc}")

    def open_about(self) -> None:
        import os
        import subprocess

        try:
            if getattr(sys, "frozen", False):
                subprocess.Popen([sys.executable, "--about"])
            else:
                from .autostart import _pythonw

                parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                subprocess.Popen([_pythonw(), "-m", "wisprlite", "--about"], cwd=parent)
        except Exception as exc:
            self._fail(f"about: {exc}")

    def open_feedback(self) -> None:
        import os
        import subprocess

        try:
            if getattr(sys, "frozen", False):
                subprocess.Popen([sys.executable, "--feedback"])
            else:
                from .autostart import _pythonw

                parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                subprocess.Popen([_pythonw(), "-m", "wisprlite", "--feedback"], cwd=parent)
        except Exception as exc:
            self._fail(f"feedback: {exc}")

    def open_profiles(self) -> None:
        import os
        import subprocess

        try:
            if getattr(sys, "frozen", False):
                subprocess.Popen([sys.executable, "--profiles"])
            else:
                from .autostart import _pythonw

                parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                subprocess.Popen([_pythonw(), "-m", "wisprlite", "--profiles"], cwd=parent)
        except Exception as exc:
            self._fail(f"profiles: {exc}")

    def autostart_enabled(self) -> bool:
        return autostart.is_enabled()

    def toggle_autostart(self) -> None:
        try:
            if autostart.is_enabled():
                autostart.disable()
            else:
                autostart.enable()
        except Exception as exc:
            self._fail(f"autostart: {exc}")
        self.tray.update()

    def _notify(self, msg: str) -> None:
        try:
            if self.tray is not None and self.tray.icon is not None:
                self.tray.icon.notify(msg, "Pipevoice")
                return
        except Exception:
            pass
        print(msg)

    def check_for_updates(self, manual: bool = False) -> None:
        def work():
            try:
                from . import updater
                info = updater.check()
                if not info:
                    if manual:
                        self._notify("Pipevoice is up to date.")
                    return
                self._notify(f"Updating Pipevoice to {info['version']}…")
                if updater.download_and_run(info):
                    self.quit()
                elif manual:
                    self._notify("Update download failed — try again later.")
            except Exception as exc:
                log.warning("update flow failed: %s", exc)
        threading.Thread(target=work, daemon=True).start()

    def quit(self) -> None:
        self._stop.set()
        self.stop_mcp_bridge()
        self.hotkeys.stop()
        self.clip_hotkeys.stop()
        self.meeting_hotkeys.stop()
        self.screenrec_hotkeys.stop()
        from . import screenrec

        if self._screenrec is not None:
            # Quitting must not throw away what was already recorded: the
            # capture threads are daemons, so without this they die with no
            # mux step and leave no playable file at all.
            try:
                self._finish_screen_recording(ask=False)
            except Exception:
                pass
        finishing = self._screenrec_finishing
        if finishing is not None and finishing.is_alive():
            # Already muxing, transcribing or uploading. That work is on a
            # daemon thread, so returning here would kill it mid-file.
            # Must exceed the scp timeout in screenrec.send, or quitting
            # kills a slow upload that was still perfectly on track.
            finishing.join(timeout=screenrec.UPLOAD_TIMEOUT + 60.0)
        self.bookmark_hotkeys.stop()
        for m in self._voice_mgrs:
            m.stop()
        if self._picker_mgr:
            self._picker_mgr.stop()
        if self._meeting_active:
            try:
                self._meeting.stop()
            finally:
                self._stop_pipefocus()
                self._meeting_active = False
        self.overlay.stop()
        self.tray.stop()

    # ---- live config reload (settings window writes config.json) ----------
    def _watch_config(self) -> None:
        try:
            last = config.CONFIG_PATH.stat().st_mtime
        except Exception:
            last = None
        while not self._stop.is_set():
            time.sleep(1.0)
            if self._meeting_active:
                self._check_meeting_errors()
            try:
                mtime = config.CONFIG_PATH.stat().st_mtime
            except Exception:
                continue
            if last is None:
                last = mtime
                continue
            if mtime != last:
                last = mtime
                try:
                    self._reload_config()
                except Exception as exc:
                    print("config reload error:", exc, file=sys.stderr)

    def _reload_config(self) -> None:
        # Pick up any API key the settings window just saved to the .env file.
        try:
            config.reload_keys()
        except Exception:
            pass

        old, new = self.cfg, config.Config.load()
        self.cfg = new  # hotkey/mode/output read live via lambdas
        self._meeting.device = config.device_arg(new)
        # A changed "Save meetings to" was ignored until restart, so the next
        # recording silently landed in the old folder.
        self._meeting.base_dir = meetings_dir()
        self._meeting.max_minutes = new.meeting_max_minutes
        self._meeting.retention_sessions = max(
            1, new.meeting_retention_sessions
        )

        engine_keys = ("engine", "gemini_model", "groq_model", "openai_model",
                       "deepgram_model", "local_model_size", "local_device",
                       "local_compute_type", "language", "device", "vocabulary",
                       "deepgram_finish_timeout")
        if any(getattr(old, k) != getattr(new, k) for k in engine_keys):
            self._engines = {}
            self.recorder.device = config.device_arg(new)
            self._prewarm()
        elif not self._engines:
            # engine wasn't built yet (e.g. key was missing) — try now
            self._prewarm()

        if new.overlay and not self.overlay._started:
            self.overlay.enabled = True
            self.overlay.start()
        elif not new.overlay:
            self.overlay.hide()

        if (new.voice_hotkeys != old.voice_hotkeys
                or new.voice_picker_hotkey != old.voice_picker_hotkey):
            self._build_voice_hotkeys()
            self._start_voice_hotkeys()

        self.tray.update()

    # ---- helpers ----------------------------------------------------------
    def _release(self) -> None:
        try:
            self._busy.release()
        except RuntimeError:
            pass

    def _set_icon(self, state: str) -> None:
        if self._meeting_active and state != "error":
            state = "meeting_degraded" if self._meeting_degraded else "meeting"
        self.tray.set_state(state)

    def _fail(self, msg: str) -> None:
        log.error("Pipevoice error: %s", msg)
        self._beep(220, 200)
        self.overlay.set_state("error", msg[:80])
        self.tray.set_state("error")
        timer = threading.Timer(1.6, lambda: self._set_icon("idle"))
        timer.daemon = True
        timer.start()

    def _beep(self, freq: int, ms: int) -> None:
        if self.cfg.sounds and winsound is not None:
            try:
                winsound.Beep(freq, ms)
            except Exception:
                pass

    # ---- run --------------------------------------------------------------
    def _acquire_single_instance(self) -> bool:
        import socket

        self._lock_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self._lock_sock.bind(("127.0.0.1", 49517))
            self._lock_sock.listen(1)
            return True
        except OSError:
            return False

    def run(self) -> None:
        if not self._acquire_single_instance():
            print("Pipevoice is already running.", file=sys.stderr)
            return

        # Upgrade any legacy per-app profile overrides into named Voices on launch,
        # so a normal start (not just opening the profiles editor) migrates them.
        try:
            from . import voices
            if voices.migrate_profiles(self.cfg):
                self.cfg.save()
        except Exception:
            pass

        # First run: if a cloud engine has no key, ask for it in a dialog.
        from . import keyprompt

        keyprompt.ensure_api_key(self.cfg)

        # One-time, dismissible "star us on GitHub" nudge (counts launches, shows once).
        try:
            from . import star_prompt
            star_prompt.maybe_show(self.cfg)
        except Exception:
            pass

        self.overlay.start()
        self.tray.start()
        self.hotkeys.start()
        self.clip_hotkeys.start()
        self.meeting_hotkeys.start()
        self.screenrec_hotkeys.start()
        self.bookmark_hotkeys.start()
        self._start_voice_hotkeys()
        if self.cfg.mcp_enabled:
            self.start_mcp_bridge()
        self._prewarm()
        threading.Thread(target=self._watch_config, daemon=True).start()
        try:
            from . import updater
            updater.cleanup_old()
        except Exception:
            pass
        # If the version changed since last run, we were just updated -> tell the user.
        from . import __version__ as _ver
        if self.cfg.last_version and self.cfg.last_version != _ver:
            self._notify(f"Updated to Pipevoice {_ver}. Tray menu, About, to see what's new.")
        if self.cfg.last_version != _ver:
            self.cfg.last_version = _ver
            try:
                self.cfg.save()
            except Exception:
                pass
        if self.cfg.auto_update:
            self.check_for_updates(manual=False)

        print("Pipevoice running.")
        print(f"  Engine: {self.cfg.engine}   Mode: {self.cfg.mode}   Hotkey: [{self.cfg.hotkey}]")
        print(f"  Output: {self.cfg.output_mode}   Overlay: {self.cfg.overlay}")
        if not self.tray.ok:
            print("  (tray icon unavailable — install pystray + Pillow for the menu)")
        print("  Right-click the tray icon for settings.  Ctrl+C here to quit.")

        try:
            while not self._stop.is_set():
                time.sleep(0.2)
        except KeyboardInterrupt:
            self.quit()


def _setup_logging() -> None:
    try:
        logging.basicConfig(
            filename=str(config.config_dir() / "pipevoice.log"),
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
        )
    except Exception:
        pass


def main() -> None:
    _setup_logging()
    # Where each key came from. Without this, "DEEPGRAM_API_KEY is not set" on a
    # machine that has one is a mystery — three .env files can supply it and the
    # log never said which won. Names and paths only, never key values.
    try:
        logging.info("api keys: %s", config.key_sources_summary())
    except Exception:
        pass
    if not config.CONFIG_PATH.exists():
        # First run: a welcome/tutorial splash, then (if they continue) the
        # settings window pre-filled with the defaults. Start-at-login on too.
        try:
            from . import autostart, settings, welcome
            autostart.enable()
            config.Config.load()  # write config.json with defaults so we don't re-prompt
            # show_welcome() returns True unless the user explicitly pressed
            # "I'll set up later". Closing the window is not a choice to skip —
            # treating it as one dropped a brand-new user straight to the tray
            # with nothing on screen, which reads as "it minimised itself".
            if welcome.show_welcome():
                settings.main(first_run=True)
        except Exception:
            log.exception("first-run setup failed")
    log.info("Pipevoice starting (engine=%s)", config.Config.load().engine)
    try:
        App().run()
    except Exception:
        log.exception("fatal error")
        raise


if __name__ == "__main__":
    main()
