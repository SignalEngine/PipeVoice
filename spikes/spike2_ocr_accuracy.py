r"""Spike 2: is Windows.Media.Ocr good enough on the text this audience reads?

Windows.Media.Ocr is the Snipping Tool engine, tuned for documents and photos.
Read Aloud has to handle 9-11pt UI labels, dark mode, anti-aliasing and
high-contrast themes. If it garbles those, the feature disappoints exactly the
people it exists for, and shipping it anyway is worse than not shipping it.

    python spikes\spike2_ocr_accuracy.py shots\

Put one PNG per category in the folder, and beside each a .txt holding what it
ACTUALLY says (type it out; that is the ground truth). Suggested five:
    dark-ide.png       a dark-mode editor with small code text
    small-web.png      a web app at default zoom, 9-11pt labels
    low-contrast.png   a modal with grey-on-grey body text
    high-contrast.png  Windows high-contrast theme
    data-table.png     a dense table with tight rows

KILL CRITERION: >25% character error rate on ANY category.
Below that bar the answer is Tesseract (an extra install) or not shipping - not
shipping it anyway and hoping.
"""

import asyncio
import pathlib
import sys


def cer(truth: str, got: str) -> float:
    """Levenshtein distance over characters, normalised by the truth length."""
    truth = " ".join(truth.split())
    got = " ".join(got.split())
    if not truth:
        return 0.0 if not got else 1.0
    prev = list(range(len(got) + 1))
    for i, a in enumerate(truth, 1):
        cur = [i]
        for j, b in enumerate(got, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a != b)))
        prev = cur
    return prev[-1] / len(truth)


async def ocr_file(path: pathlib.Path) -> str:
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage import FileAccessMode, StorageFile

    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError("no OCR engine for this profile's languages")
    f = await StorageFile.get_file_from_path_async(str(path.resolve()))
    stream = await f.open_async(FileAccessMode.READ)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    result = await engine.recognize_async(bitmap)
    return result.text or ""


async def main(folder: pathlib.Path) -> int:
    shots = sorted(folder.glob("*.png"))
    if not shots:
        print(f"no .png files in {folder}")
        return 2

    worst = 0.0
    print(f"{'category':<22} {'CER':>7}  verdict")
    print("-" * 52)
    for shot in shots:
        truth_path = shot.with_suffix(".txt")
        if not truth_path.exists():
            print(f"{shot.stem:<22} {'-':>7}  SKIPPED - no {truth_path.name}")
            continue
        try:
            got = await ocr_file(shot)
        except Exception as exc:
            print(f"{shot.stem:<22} {'-':>7}  ERROR - {exc}")
            worst = 1.0
            continue
        rate = cer(truth_path.read_text(encoding="utf-8"), got)
        worst = max(worst, rate)
        verdict = "ok" if rate <= 0.25 else "OVER THE KILL LINE"
        print(f"{shot.stem:<22} {rate:>6.1%}  {verdict}")
        (shot.parent / f"{shot.stem}.ocr.txt").write_text(got, encoding="utf-8")

    print()
    print(f"worst category: {worst:.1%}")
    print("RESULT:", "PASS - build it" if worst <= 0.25 else
          "FAIL - Windows OCR is not good enough for this audience")
    return 0 if worst <= 0.25 else 1


if __name__ == "__main__":
    target = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "shots")
    raise SystemExit(asyncio.run(main(target)))
