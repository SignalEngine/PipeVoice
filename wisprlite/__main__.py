import sys

if "--settings" in sys.argv:
    from .settings import main
elif "--history" in sys.argv:
    from .history import main
elif "--meetings" in sys.argv:
    from .meetings_tab import main
elif "--about" in sys.argv:
    from .about import main
elif "--profiles" in sys.argv:
    from .profiles import main
elif "--voices" in sys.argv:
    from .voices_editor import main
elif "--transcribe" in sys.argv:
    from .transcribe_window import main
elif "--mcp" in sys.argv:
    from .mcp_shim import main
elif "--feedback" in sys.argv:
    from .feedback import main
else:
    from .app import main

if __name__ == "__main__":
    main()
