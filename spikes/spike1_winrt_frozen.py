r"""Spike 1: can a PyInstaller-frozen exe actually activate WinRT?

The Read Aloud design rests entirely on two WinRT namespaces. PipeVoice ships as
a PyInstaller build, and this exact class of failure already shipped to a user
this week: --collect-all silently missed FastMCP's module-level imports and the
exe died with ModuleNotFoundError on a real machine.

Verifying the packages exist on PyPI is a VERSION check. This is the BUNDLING
check, and it only counts on a clean machine with no Python installed.

    pip install pyinstaller winrt-runtime winrt-Windows.Media.Ocr \
        winrt-Windows.Media.SpeechSynthesis winrt-Windows.Graphics.Imaging \
        winrt-Windows.Storage.Streams winrt-Windows.Foundation
    pyinstaller --onedir --collect-all winrt spikes\spike1_winrt_frozen.py
    # then copy dist\spike1_winrt_frozen\ into Windows Sandbox and run the exe

PASS = both namespaces activate and at least one voice is listed.
Anything else is a real cost that belongs in the estimate BEFORE building.
"""

import sys
import traceback


def main() -> int:
    print(f"python  : {sys.version.split()[0]}")
    print(f"frozen  : {getattr(sys, 'frozen', False)}")
    print(f"meipass : {getattr(sys, '_MEIPASS', '(not frozen)')}")
    ok = True

    try:
        from winrt.windows.media.ocr import OcrEngine

        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            print("OCR     : FAIL - no engine for this profile's languages")
            print("          (a real result: it means the language pack is missing,")
            print("           not that bundling failed - note which it is)")
            ok = False
        else:
            langs = [l.language_tag for l in OcrEngine.available_recognizer_languages]
            print(f"OCR     : PASS - engine created, {len(langs)} languages: {langs[:6]}")
    except Exception:
        print("OCR     : FAIL - could not import or activate")
        traceback.print_exc()
        ok = False

    try:
        from winrt.windows.media.speechsynthesis import SpeechSynthesizer

        voices = list(SpeechSynthesizer.all_voices)
        if not voices:
            print("TTS     : FAIL - activated but zero voices installed")
            ok = False
        else:
            print(f"TTS     : PASS - {len(voices)} voices")
            for v in voices[:6]:
                print(f"          - {v.display_name} ({v.language})")
    except Exception:
        print("TTS     : FAIL - could not import or activate")
        traceback.print_exc()
        ok = False

    print()
    print("RESULT  :", "PASS - build the feature" if ok else "FAIL - packaging work first")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
