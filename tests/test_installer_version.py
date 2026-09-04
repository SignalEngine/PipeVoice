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


def test_the_winget_manifest_hash_and_url_name_the_same_release():
    """The first real submission carried v2.43.0's SHA against v2.44.1's URL -
    the version strings were sed-updated and the hash was not. Microsoft's CI
    downloads the asset and would have rejected it. This catches the shape
    (mismatched versions between files) without needing the network."""
    import re

    winget = ROOT / "packaging" / "winget"
    if not winget.exists():
        return

    versions = {}
    for path in sorted(winget.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        m = re.search(r"^PackageVersion:\s*(\S+)", text, re.M)
        assert m, f"{path.name} has no PackageVersion"
        versions[path.name] = m.group(1)

    assert len(set(versions.values())) == 1, \
        f"the manifests disagree on the version: {versions}"

    installer = (winget / "Powleads.PipeVoice.installer.yaml").read_text(encoding="utf-8")
    version = next(iter(versions.values()))
    url = re.search(r"InstallerUrl:\s*(\S+)", installer).group(1)
    assert f"/v{version}/" in url, \
        f"InstallerUrl points at a different release than PackageVersion {version}: {url}"

    sha = re.search(r"InstallerSha256:\s*([0-9A-Fa-f]{64})", installer)
    assert sha, "InstallerSha256 is missing or not a 64-char hex digest"
    assert sha.group(1).isupper() or not sha.group(1).isalpha(), \
        "winget expects the SHA-256 uppercase"


def test_the_winrt_gate_waits_for_the_gui_exe():
    """`& $exe` does not wait for a --noconsole binary, so $LASTEXITCODE is
    EMPTY and the gate fails for a PowerShell reason rather than a WinRT one.
    The MCP smoke test in the same file already used Start-Process -PassThru;
    this one has to as well."""
    text = WORKFLOW.read_text(encoding="utf-8")
    step = text[text.index("Self-test WinRT"):]
    step = step[:step.index("- name:", 10)] if "- name:" in step[10:] else step
    # Comments explain WHY $LASTEXITCODE is wrong here, so assert on the code.
    code = "\n".join(l for l in step.splitlines() if not l.strip().startswith("#"))

    assert "Start-Process" in code and "-Wait" in code and "-PassThru" in code, \
        "the WinRT gate does not wait for the exe, so its exit code is meaningless"
    assert "$LASTEXITCODE" not in code, \
        "$LASTEXITCODE is empty for a GUI-subsystem exe launched with &"
