"""Entry point for `python launch.py` and for PyInstaller builds.

Pass --settings to open the settings window instead of the app.
"""

import sys

if "--settings" in sys.argv:
    from wisprlite.settings import main
elif "--history" in sys.argv:
    from wisprlite.history import main
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
else:
    from wisprlite.app import main

if __name__ == "__main__":
    main()
