"""Entry point for `python launch.py` and for PyInstaller builds.

Pass --settings to open the settings window instead of the app.

THIS is what PyInstaller freezes - not wisprlite/__main__.py, which exists for
`python -m wisprlite`. The two dispatch tables must list the same flags: a flag
added only to __main__.py works when run from source and silently falls through
to the tray app in the shipped exe, which then never exits. That cost five red
builds misread as a WinRT failure. tests/test_entrypoints.py pins them together.
"""

import sys

if "--settings" in sys.argv:
    from wisprlite.settings import main
elif "--history" in sys.argv:
    from wisprlite.history import main
elif "--meetings" in sys.argv:
    from wisprlite.meetings_tab import main
elif "--about" in sys.argv:
    from wisprlite.about import main
elif "--profiles" in sys.argv:
    from wisprlite.profiles import main
elif "--voices" in sys.argv:
    from wisprlite.voices_editor import main
elif "--transcribe" in sys.argv:
    from wisprlite.transcribe_window import main
elif "--mcp" in sys.argv:
    from wisprlite.mcp_shim import main
elif "--feedback" in sys.argv:
    from wisprlite.feedback import main
elif "--winrt-selftest" in sys.argv:
    from wisprlite.readaloud import main
else:
    from wisprlite.app import main

if __name__ == "__main__":
    main()
