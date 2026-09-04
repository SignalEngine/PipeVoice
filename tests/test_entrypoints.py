"""launch.py and wisprlite/__main__.py must dispatch the same flags.

PyInstaller freezes launch.py. `python -m wisprlite` runs __main__.py. A flag
added to only one of them works in development and silently does the WRONG thing
in the shipped exe - it falls through to `else`, launching the tray app, which
never exits.

That is exactly what happened with --winrt-selftest: five CI builds failed and
were read as "WinRT does not bundle" when the exe had simply never run the
self-test at all.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
LAUNCH = ROOT / "launch.py"
DUNDER = ROOT / "wisprlite" / "__main__.py"

FLAG = re.compile(r'"(--[a-z0-9-]+)" in sys\.argv')


def _flags(path: pathlib.Path) -> set:
    return set(FLAG.findall(path.read_text(encoding="utf-8")))


def test_both_entry_points_handle_the_same_flags():
    launch, dunder = _flags(LAUNCH), _flags(DUNDER)
    assert launch == dunder, (
        "the two entry points disagree.\n"
        f"  only in launch.py:     {sorted(launch - dunder)}\n"
        f"  only in __main__.py:   {sorted(dunder - launch)}\n"
        "A flag missing from launch.py silently launches the tray app in the "
        "shipped exe instead of doing what was asked."
    )


def test_the_frozen_entry_point_is_the_one_ci_builds():
    """If the workflow ever switches entry script, this test's premise dies."""
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    assert "launch.py" in workflow, \
        "CI no longer builds launch.py - update this test's assumption"


def test_winrt_selftest_is_reachable_from_the_frozen_entry_point():
    """The specific regression: the gate could never have run."""
    assert "--winrt-selftest" in _flags(LAUNCH), \
        "the shipped exe cannot run its own WinRT self-test"
