import sys

from .theme import enable_dpi_awareness

# The app branch (overlay, region pickers), --mcp and --winrt-selftest stay DPI-unaware:
# their pixel maths must not change in this build.
if "--settings" in sys.argv:
    enable_dpi_awareness()
    from .settings import main
elif "--prep" in sys.argv:
    enable_dpi_awareness()
    from .prep_window import main as _prep_main

    def main() -> None:
        i = sys.argv.index("--prep")
        if i + 1 < len(sys.argv):
            _prep_main(sys.argv[i + 1])
elif "--history" in sys.argv:
    enable_dpi_awareness()
    from .history import main
elif "--meetings" in sys.argv:
    enable_dpi_awareness()
    from .meetings_tab import main
elif "--about" in sys.argv:
    enable_dpi_awareness()
    from .about import main
elif "--profiles" in sys.argv:
    enable_dpi_awareness()
    from .profiles import main
elif "--voices" in sys.argv:
    enable_dpi_awareness()
    from .voices_editor import main
elif "--transcribe" in sys.argv:
    enable_dpi_awareness()
    from .transcribe_window import main
elif "--mcp" in sys.argv:
    from .mcp_shim import main
elif "--feedback" in sys.argv:
    enable_dpi_awareness()
    from .feedback import main
elif "--winrt-selftest" in sys.argv:
    from .readaloud import main
else:
    from .app import main

if __name__ == "__main__":
    main()
