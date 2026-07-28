"""Configuration: persisted user settings + secrets from the environment.

Non-secret settings live in %APPDATA%\\Pipevoice\\config.json so the tray menu
can change them at runtime. API keys are read from the environment / .env only
and are never written to disk.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .voices import STARTER_VOICES

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
    """Load .env from cwd, next to the executable, and the config dir."""
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    load_dotenv()  # cwd / parents
    candidates = [config_dir() / ".env"]
    try:
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / ".env")
    except Exception:
        pass
    for p in candidates:
        try:
            if p.exists():
                load_dotenv(p, override=False)
        except Exception:
            pass


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
        else:
            # First run: seed a few settings from the environment, then persist.
            cfg.engine = os.getenv("WISPRLITE_ENGINE", cfg.engine)
            cfg.hotkey = os.getenv("WISPRLITE_HOTKEY", cfg.hotkey)
            cfg.mode = os.getenv("WISPRLITE_MODE", cfg.mode)
            cfg.language = os.getenv("WISPRLITE_LANG", cfg.language)
            cfg.device = os.getenv("WISPRLITE_DEVICE", cfg.device)
            cfg.openai_model = os.getenv("WISPRLITE_MODEL", cfg.openai_model)
            cfg.save()
        return cfg

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
