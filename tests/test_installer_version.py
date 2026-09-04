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


def test_add_remove_programs_reads_appversion():
    """AppVersion IS the Add/Remove Programs DisplayVersion, and DisplayVersion is
    what winget compares to decide an upgrade exists. An earlier attempt at this
    added VersionInfoVersion instead - that sets the setup EXE's file-version
    resource, a different field, which Inno also lists as obsolete and which may
    require four components. Wrong field, and untestable from Linux."""
    text = ISS.read_text(encoding="utf-8")
    assert "AppVersion={#AppVersion}" in text, \
        "AppVersion is not wired to the define, so ARP would show a literal"
    assert "VersionInfoVersion" not in text, \
        "VersionInfoVersion is not the ARP field and risks a four-part compile error"


def test_the_iss_still_defines_a_fallback_so_a_local_build_works():
    """A developer running ISCC by hand must not hit an undefined-symbol error."""
    text = ISS.read_text(encoding="utf-8")
    assert "#ifndef AppVersion" in text and "#define AppVersion" in text, \
        "a local build with no /D would fail to compile"
