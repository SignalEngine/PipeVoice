"""The installer must report the version it actually contains.

`installer/Pipevoice.iss` hardcoded `#define AppVersion "2.25.0"` and CI compiled
it with no /D override, so every installer from 2.25.0 onward told Add/Remove
Programs it was 2.25.0 whatever it really was. That is also exactly what winget
reads to decide an upgrade is available, so `winget upgrade` could never have
worked.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
ISS = ROOT / "installer" / "Pipevoice.iss"
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"


def test_the_installer_version_is_not_a_hardcoded_release_number():
    """A literal like 2.25.0 in the .iss is the bug. Only a dev fallback is allowed."""
    text = ISS.read_text(encoding="utf-8")
    literals = re.findall(r'#define\s+AppVersion\s+"([^"]+)"', text)
    for value in literals:
        assert not re.fullmatch(r"\d+\.\d+\.\d+", value), (
            f"AppVersion is pinned to the release number {value!r}; it must come "
            "from the source via ISCC /DAppVersion, with only a non-release fallback"
        )


def test_ci_passes_the_version_into_the_installer_compiler():
    """Without /DAppVersion the .iss fallback ships, and every build claims 0.0.0-dev."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "/DAppVersion=" in text, \
        "CI compiles the installer without passing a version"
    assert "wisprlite/__init__.py" in text, \
        "the version must be read from the source of truth, not retyped in CI"


def test_add_remove_programs_shows_the_real_version():
    """winget compares against the Add/Remove Programs version. Without
    VersionInfoVersion it can read stale or missing metadata."""
    text = ISS.read_text(encoding="utf-8")
    assert "VersionInfoVersion={#AppVersion}" in text, \
        "the installer does not stamp the version Add/Remove Programs reports"


def test_the_iss_still_defines_a_fallback_so_a_local_build_works():
    """A developer running ISCC by hand must not hit an undefined-symbol error."""
    text = ISS.read_text(encoding="utf-8")
    assert "#ifndef AppVersion" in text and "#define AppVersion" in text, \
        "a local build with no /D would fail to compile"
