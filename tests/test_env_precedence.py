"""Where API keys come from, and which .env wins.

There are two .env files on a real install: %APPDATA%\\Pipevoice\\.env, which is
where Settings and the first-run prompt save, and {app}\\.env, which the
INSTALLER seeds from .env.example — and .env.example ships `DEEPGRAM_API_KEY=`
blank. python-dotenv's override=False counts a blank as "already set", so that
one empty line masked the real key and the app reported the key missing.
"""

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import config

KEYS = ("DEEPGRAM_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY")


def _isolate(monkeypatch, tmp_path, *, appdata: str | None, app: str | None,
             cwd_is_app: bool = True):
    """Point config at throwaway dirs and lay down the given .env files.

    cwd_is_app models production: every way Windows starts Pipevoice — the Start
    menu icon, the startup shortcut, the installer's own relaunch after a silent
    update — runs it with the working directory set to {app}. PyInstaller sets
    sys.frozen, which makes dotenv's find_dotenv() search from the cwd, so
    {app}\\.env is what a bare load_dotenv() picks up.

    sys.frozen is set here for the same reason. Unfrozen, find_dotenv() searches
    from the CALLING FILE instead, never looks at the cwd, and the whole defect
    disappears — a test that skips this passes on a shipped bug.
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    config._FROM_FILE.clear()
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)

    appdata_dir = tmp_path / "appdata"
    app_dir = tmp_path / "app"
    work_dir = tmp_path / "work"
    for d in (appdata_dir, app_dir, work_dir):
        d.mkdir(parents=True, exist_ok=True)

    if appdata is not None:
        (appdata_dir / ".env").write_text(appdata, encoding="utf-8")
    if app is not None:
        (app_dir / ".env").write_text(app, encoding="utf-8")

    monkeypatch.setattr(config, "config_dir", lambda: appdata_dir)
    monkeypatch.setattr(sys, "executable", str(app_dir / "Pipevoice.exe"))
    monkeypatch.chdir(app_dir if cwd_is_app else work_dir)


def test_a_blank_key_next_to_the_exe_does_not_mask_the_real_one(monkeypatch, tmp_path):
    """The exact shipped situation: installer stub blank, real key in APPDATA."""
    _isolate(
        monkeypatch, tmp_path,
        appdata="DEEPGRAM_API_KEY=realkey0123456789\n",
        app="OPENAI_API_KEY=\nDEEPGRAM_API_KEY=\n",   # verbatim from .env.example
    )
    config._load_env()
    assert config.deepgram_key() == "realkey0123456789"


def test_the_config_dir_wins_over_the_exe_dir_when_both_have_a_key(monkeypatch, tmp_path):
    """Settings writes to APPDATA, so APPDATA is the store of record."""
    _isolate(
        monkeypatch, tmp_path,
        appdata="DEEPGRAM_API_KEY=fromappdata12345\n",
        app="DEEPGRAM_API_KEY=stale_old_key_9999\n",
    )
    config._load_env()
    assert config.deepgram_key() == "fromappdata12345"


def test_a_key_only_next_to_the_exe_is_still_used(monkeypatch, tmp_path):
    """Users who pasted into {app}\\.env (the Start-menu shortcut) keep working."""
    _isolate(monkeypatch, tmp_path, appdata=None, app="DEEPGRAM_API_KEY=onlyhere12345678\n")
    config._load_env()
    assert config.deepgram_key() == "onlyhere12345678"


def test_a_real_environment_variable_beats_every_file(monkeypatch, tmp_path):
    """Someone who exports the key in their shell means it."""
    _isolate(monkeypatch, tmp_path, appdata="DEEPGRAM_API_KEY=fromfile12345678\n", app=None)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "fromenv123456789")
    config._load_env()
    assert config.deepgram_key() == "fromenv123456789"


def test_a_blank_environment_variable_does_not_block_the_file(monkeypatch, tmp_path):
    """An empty export is not a decision — it is the absence of one."""
    _isolate(monkeypatch, tmp_path, appdata="DEEPGRAM_API_KEY=fromfile12345678\n", app=None)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "   ")
    config._load_env()
    assert config.deepgram_key() == "fromfile12345678"


def test_saving_a_new_key_in_settings_replaces_the_old_one_without_a_restart(monkeypatch, tmp_path):
    """The whole point of the reload: the running app must adopt the new key."""
    _isolate(monkeypatch, tmp_path, appdata="DEEPGRAM_API_KEY=oldkey0123456789\n", app=None)
    config._load_env()
    assert config.deepgram_key() == "oldkey0123456789"

    (tmp_path / "appdata" / ".env").write_text("DEEPGRAM_API_KEY=newkey9876543210\n",
                                               encoding="utf-8")
    config.reload_keys()
    assert config.deepgram_key() == "newkey9876543210"


def test_a_blank_line_in_the_file_cannot_wipe_a_live_key(monkeypatch, tmp_path):
    """A reload used to overwrite a working key with "" — mid-session, no restart."""
    _isolate(monkeypatch, tmp_path, appdata=None, app="DEEPGRAM_API_KEY=working123456789\n")
    config._load_env()
    assert config.deepgram_key() == "working123456789"

    (tmp_path / "appdata" / ".env").write_text("DEEPGRAM_API_KEY=\n", encoding="utf-8")
    config.reload_keys()
    assert config.deepgram_key() == "working123456789"


def test_the_reload_sees_every_env_file_not_just_the_config_dir(monkeypatch, tmp_path):
    """It read one of the three locations, so a key added elsewhere stayed invisible."""
    _isolate(monkeypatch, tmp_path, appdata=None, app=None)
    config._load_env()
    assert config.deepgram_key() == ""

    (tmp_path / "app" / ".env").write_text("DEEPGRAM_API_KEY=addedlater123456\n",
                                           encoding="utf-8")
    config.reload_keys()
    assert config.deepgram_key() == "addedlater123456"


def test_the_shipped_env_example_really_does_carry_a_blank_deepgram_line():
    """If this ever stops being true the bug above changes shape — know about it."""
    example = pathlib.Path(__file__).resolve().parent.parent / ".env.example"
    lines = [ln.strip() for ln in example.read_text(encoding="utf-8").splitlines()]
    assert "DEEPGRAM_API_KEY=" in lines


def test_the_installer_no_longer_seeds_a_blank_env_beside_the_exe():
    """A stub .env next to the exe is what created the second, competing store."""
    iss = (pathlib.Path(__file__).resolve().parent.parent
           / "installer" / "Pipevoice.iss").read_text(encoding="utf-8")
    seeding = [ln for ln in iss.splitlines()
               if 'DestName: ".env"' in ln and not ln.lstrip().startswith(";")]
    assert seeding == [], f"installer still seeds a stub .env: {seeding}"


def test_a_stale_key_beside_the_exe_cannot_undo_a_key_just_saved_in_settings(
        monkeypatch, tmp_path):
    """Reviewer flagged this as a regression in reload_keys(). Pin the behaviour."""
    _isolate(
        monkeypatch, tmp_path,
        appdata="DEEPGRAM_API_KEY=oldkey0123456789\n",
        app="DEEPGRAM_API_KEY=stale_hand_typed_1\n",
    )
    config._load_env()

    (tmp_path / "appdata" / ".env").write_text("DEEPGRAM_API_KEY=justsaved1234567\n",
                                               encoding="utf-8")
    config.reload_keys()
    assert config.deepgram_key() == "justsaved1234567"


def test_a_reload_does_not_trample_a_key_the_user_exported_themselves(monkeypatch, tmp_path):
    """Files may replace what files supplied. A real export is the user's call."""
    _isolate(monkeypatch, tmp_path, appdata=None, app=None)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "fromenv123456789")
    config._load_env()

    (tmp_path / "appdata" / ".env").write_text("DEEPGRAM_API_KEY=fromfile12345678\n",
                                               encoding="utf-8")
    config.reload_keys()
    assert config.deepgram_key() == "fromenv123456789"


def test_the_installer_creates_the_config_dir_the_key_shortcut_points_into():
    """Notepad cannot save into a folder that does not exist yet."""
    iss = (pathlib.Path(__file__).resolve().parent.parent
           / "installer" / "Pipevoice.iss").read_text(encoding="utf-8")
    assert "[Dirs]" in iss
    assert 'Name: "{userappdata}\\{#AppName}"' in iss
