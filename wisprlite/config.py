"""Configuration: persisted user settings + secrets from the environment.

Non-secret settings live in %APPDATA%\\Pipevoice\\config.json so the tray menu
can change them at runtime. API keys are read from the environment / .env only
and are never written to disk.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .voices import STARTER_VOICES
from .meeting import DEFAULT_BOOKMARK_PHRASES

APP_NAME = "Pipevoice"


def config_dir() -> Path:
    base = os.getenv("APPDATA") or str(Path.home())
    d = Path(base) / APP_NAME
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


CONFIG_PATH = config_dir() / "config.json"


def _load_env() -> None:
    """Load API keys from .env — config dir first, then the exe's dir, then cwd.

    A key that is present but BLANK does not count as set. That one rule is the
    whole fix for "Deepgram API key is missing" on an install that plainly had
    one: the installer used to seed {app}\\.env from .env.example, which ships
    `DEEPGRAM_API_KEY=` empty, and python-dotenv's override=False treats "" as
    already-set. So the blank line won and the real key in %APPDATA% was never
    read. It only bit the SHIPPED build: PyInstaller sets sys.frozen, which is
    what makes dotenv search the working directory — and every Windows shortcut
    starts Pipevoice with its working directory set to {app}.

    Order is now explicit rather than a side effect of which call ran first.
    %APPDATA%\\Pipevoice\\.env is the store of record because that is where
    Settings and the first-run prompt write.
    """
    _apply_env_files(override=False)


def reload_keys() -> None:
    """Re-read the .env files after the user saves a key, and adopt the changes.

    Same precedence as startup, and the same blank rule — this used to be a bare
    load_dotenv(override=True) on the config dir alone, which read only one of
    the three locations and would happily overwrite a working key with an empty
    string if the file carried a blank line for it.
    """
    _apply_env_files(override=True)


def _env_paths() -> list:
    """The .env files we read, in precedence order. First non-blank value wins."""
    paths = [config_dir() / ".env"]
    try:
        paths.append(Path(sys.executable).resolve().parent / ".env")
    except Exception:
        pass
    try:
        from dotenv import find_dotenv

        found = find_dotenv(usecwd=True)
        if found:
            paths.append(Path(found))
    except Exception:
        pass
    return paths


# Keys this process took from a .env file. A reload may replace those; it must
# not touch a variable the user exported in their own environment, which outranks
# every file and is nobody's business but theirs.
_FROM_FILE: set = set()

# Which .env supplied each key, for the startup log. Diagnosing "the key is
# missing" took a theory and a repro because nothing recorded WHERE each key
# came from — with three candidate files, that is the one fact that settles it.
KEY_SOURCES: dict = {}


def _apply_env_files(*, override: bool) -> None:
    try:
        from dotenv import dotenv_values
    except Exception:
        return
    seen = set()
    for p in _env_paths():
        try:
            if not p.exists():
                continue
            for k, v in dotenv_values(p).items():
                if k in seen or not (v or "").strip():
                    continue
                seen.add(k)   # a later file must not undercut an earlier one
                current = os.environ.get(k, "").strip()
                if current and not (override and k in _FROM_FILE):
                    continue
                os.environ[k] = v
                _FROM_FILE.add(k)
                KEY_SOURCES[k] = str(p)
        except Exception:
            pass


def key_sources_summary() -> str:
    """One log line: where each API key came from. NEVER the key itself."""
    names = ("DEEPGRAM_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY",
             "OPENAI_API_KEY", "OPENROUTER_API_KEY")
    bits = []
    for name in names:
        if not os.getenv(name, "").strip():
            bits.append(f"{name}=MISSING")
        else:
            bits.append(f"{name}<-{KEY_SOURCES.get(name, 'environment')}")
    files = [str(p) for p in _env_paths() if p.exists()]
    return f"cwd={os.getcwd()} env files={files or 'none'} | " + " ".join(bits)


_load_env()


@dataclass
class Config:
    engine: str = "gemini"          # gemini | groq | deepgram | local (openai = legacy)
    mode: str = "ptt"               # ptt | toggle
    hotkey: str = "ctrl+\\"          # any key/combo, e.g. "ctrl+alt", "f9"
    clipboard_hotkey: str = "right ctrl+right shift"  # 2nd hotkey -> dictate to clipboard (no typing); safe to hold. "" = off
    meeting_hotkey: str = ""        # toggle meeting capture; "" = disabled
    bookmark_hotkey: str = ""       # mark the current moment while a meeting records
    bookmark_acoustic: bool = False # double-snap detector, opt-in
    bookmark_sensitivity: float = 0.5
    bookmark_phrases: str = DEFAULT_BOOKMARK_PHRASES
    meeting_max_minutes: int = 240  # safety cap for an unattended meeting capture
    meeting_retention_sessions: int = 20  # newest local meeting sessions to keep
    output_mode: str = "type"       # type | paste
    language: str = ""              # "" = auto-detect; else ISO code e.g. "en"
    device: str = ""                # mic index or name substring; "" = default
    gemini_model: str = "gemini-3.1-flash-lite"   # free tier; one key also powers AI polish
    groq_model: str = "whisper-large-v3-turbo"    # real Whisper, ~9x cheaper than OpenAI
    openai_model: str = "whisper-1"
    deepgram_model: str = "nova-3"
    local_model_size: str = "base.en"
    local_device: str = "auto"        # auto | cpu | cuda  (faster-whisper device)
    local_compute_type: str = "int8"  # int8 | int8_float16 | float16 | float32
    overlay: bool = True
    sounds: bool = False
    auto_update: bool = True         # check GitHub for a newer release on startup and install it
    min_seconds: float = 0.35       # ignore taps shorter than this
    ai_cleanup: bool = True         # polish transcript with an LLM
    cleanup_provider: str = "gemini"  # gemini(free) | openai | openrouter | ollama (all OpenAI-compatible)
    cleanup_model: str = ""           # blank = the provider's default model
    cleanup_style: str = "tidy"       # tidy | prompt | custom — how Flow mode polishes
    cleanup_instruction: str = ""     # the instruction used when cleanup_style == "custom"
    auto_enter: bool = False        # press Enter after typing (hands-free send)
    vocabulary: str = ""            # comma-separated terms to bias recognition
    starter_vocab: bool = True      # seed dev terms so they work on install
    starter_vocab_seeded: bool = False   # one-time marker; see apply_starter_vocab
    speech_notes: str = ""          # free text about the user's accent / speech, fed to AI cleanup
    replacements: dict = field(default_factory=dict)  # {wrong: right} post-fixes
    voices: list = field(default_factory=lambda: copy.deepcopy(STARTER_VOICES))
    voice_hotkeys: list = field(default_factory=list)   # [{"hotkey": "...", "voice": "name"}]
    voice_picker_hotkey: str = ""                        # "" = picker off
    key_prompt_skipped_for: str = ""  # engine the user dismissed the key prompt for (stops re-nagging)
    voice_commands: bool = True       # spoken commands: "new line", "scratch that", "send it"
    history_enabled: bool = True      # keep a local dictation history (history.jsonl)
    history_size: int = 50            # entries shown in the history viewer
    deepgram_finish_timeout: float = 6.0  # seconds to wait for Deepgram's final words
    paste_speed: str = "normal"       # fast | normal | slow — clipboard-paste timing
    last_version: str = ""            # app version last run, to detect a fresh update
    launches: int = 0                 # count of app starts (for the one-time star nudge)
    star_prompt_shown: bool = False   # one-time "star us on GitHub" nudge already shown
    profiles: list = field(default_factory=list)  # per-app behaviour overrides
    # Agent MCP server (listen + transcribe). Off by default; opt-in via the tray.
    mcp_enabled: bool = False
    mcp_port: int = 49518             # loopback control bridge (distinct from the 49517 lock)
    mcp_default_mode: str = "push_to_talk"  # push_to_talk | hands_free  (for `listen`)
    hands_free_silence_ms: int = 800  # trailing silence that ends a hands-free capture
    transcribe_model_size: str = ""   # blank = reuse local_model_size
    meetings_dir: str = ""            # where recordings are stored; blank = machine-local default
    update_channel: str = "stable"    # stable | beta — beta also sees prereleases
    pipefocus: bool = False           # live focus nudges during a meeting (Deepgram only)
    # Screen recorder — off until a hotkey is set, like meeting capture.
    screenrec_hotkey: str = ""        # drag a region, record it with narration
    screenrec_destination: str = ""   # scp target, e.g. root@host:/path/inbox-files/
    screenrec_keep_local: bool = True # keep the files after a successful send
    screenrec_dir: str = ""           # blank = %USERPROFILE%\\Videos\\PipeVoice
    screenrec_fps: int = 12           # pure-python capture; 10-15 is realistic at 1080p

    @property
    def meetings_keep(self) -> int:
        """Compatibility name used by the Meetings settings UI."""
        return self.meeting_retention_sessions

    @meetings_keep.setter
    def meetings_keep(self, value: int) -> None:
        self.meeting_retention_sessions = int(value)

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if hasattr(cfg, k):
                        current = getattr(cfg, k)
                        try:
                            if type(current) is int:
                                v = int(v)
                            elif type(current) is float:
                                v = float(v)
                        except (TypeError, ValueError):
                            continue
                        setattr(cfg, k, v)
            except Exception:
                pass
            # An existing install has never seen the starter vocabulary, and the
            # 1,760 of them are most of the users. Seed once here too.
            if cfg.apply_starter_vocab():
                cfg.save()
        else:
            # First run: seed a few settings from the environment, then persist.
            cfg.engine = os.getenv("WISPRLITE_ENGINE", cfg.engine)
            cfg.hotkey = os.getenv("WISPRLITE_HOTKEY", cfg.hotkey)
            cfg.mode = os.getenv("WISPRLITE_MODE", cfg.mode)
            cfg.language = os.getenv("WISPRLITE_LANG", cfg.language)
            cfg.device = os.getenv("WISPRLITE_DEVICE", cfg.device)
            cfg.openai_model = os.getenv("WISPRLITE_MODEL", cfg.openai_model)
            cfg.apply_starter_vocab()
            cfg.save()   # marker included, so it never runs twice
        return cfg

    def apply_starter_vocab(self) -> bool:
        """Seed the dev terms every engine mishears. True if anything changed.

        Both `vocabulary` and `replacements` have shipped EMPTY since they were
        added, so the feature only ever worked for someone who found Settings and
        typed a list by hand. Merged, never assigned: anything the user already
        set wins.

        Runs ONCE, tracked by `starter_vocab_seeded`. Re-running on every load
        would be idempotent against the file but wrong against the user - delete
        a term you do not want and it would reappear at the next launch, which is
        the app arguing with you.
        """
        if not self.starter_vocab or self.starter_vocab_seeded:
            return False
        try:
            from . import starter_vocab

            self.vocabulary, self.replacements = starter_vocab.merge_into(
                self.vocabulary, self.replacements)
            self.starter_vocab_seeded = True
            return True
        except Exception:
            # Never stop the app loading over a word list. But log it: a broken
            # starter_vocab retries on EVERY launch, and swallowed silently that
            # is a permanent no-op nobody can diagnose.
            logging.getLogger("wisprlite").warning(
                "starter vocabulary could not be applied", exc_info=True)
            return False

    def save(self) -> None:
        try:
            CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except Exception:
            pass


def save_api_key(env_name: str, value: str) -> None:
    """Persist an API key to %APPDATA%\\Pipevoice\\.env and the live process."""
    value = (value or "").strip()
    if not value:
        return
    os.environ[env_name] = value
    path = config_dir() / ".env"
    lines = []
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            lines = []
    out, replaced = [], False
    for ln in lines:
        if ln.strip().startswith(env_name + "="):
            out.append(f"{env_name}={value}")
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(f"{env_name}={value}")
    try:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    except Exception:
        pass


def asset_path(name: str) -> str | None:
    """Locate a bundled asset (works from source and PyInstaller onefile)."""
    candidates = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidates.append(Path(base) / "assets" / name)
    candidates.append(Path(__file__).resolve().parent.parent / "assets" / name)
    for c in candidates:
        try:
            if c.exists():
                return str(c)
        except Exception:
            pass
    return None


def openai_key() -> str:
    return os.getenv("OPENAI_API_KEY", "").strip()


def deepgram_key() -> str:
    return os.getenv("DEEPGRAM_API_KEY", "").strip()


def gemini_key() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip()


def groq_key() -> str:
    return os.getenv("GROQ_API_KEY", "").strip()


def openrouter_key() -> str:
    return os.getenv("OPENROUTER_API_KEY", "").strip()


def device_arg(cfg: Config):
    """Return a sounddevice-compatible device selector (int index, name, or None)."""
    d = (cfg.device or "").strip()
    if not d:
        return None
    try:
        return int(d)
    except ValueError:
        return d
