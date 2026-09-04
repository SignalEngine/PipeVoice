# winget manifests

`winget install PipeVoice` needs three manifest files in Microsoft's
`microsoft/winget-pkgs` repo under
`manifests/p/Powleads/PipeVoice/<version>/`. They live here first so they are
reviewable and version-controlled alongside the thing they describe.

## Before submitting a new version

1. **The SHA-256 must match the release asset**, or Microsoft's CI rejects it:
   ```
   curl -sL https://github.com/SignalEngine/PipeVoice/releases/download/vX.Y.Z/Pipevoice-Setup.exe.sha256
   ```
   winget wants it UPPERCASE.
2. **Point at a STABLE release, never a prerelease.** A manifest referencing a
   prerelease asset installs software the update channel does not consider
   current.
3. **Check the installed version the installer actually reports.** winget reads
   the Add/Remove Programs version to decide whether an upgrade exists. Until
   v2.43.1 the Inno Setup script hardcoded `2.25.0`, so every build claimed to be
   2.25.0 and `winget upgrade` could never have worked. It now comes from
   `wisprlite/__init__.py` via `ISCC /DAppVersion=`, guarded by
   `tests/test_installer_version.py`.

## ProductCode

`{41C3C77C-2125-40AF-AE40-5AAC67809491}_is1` — the `AppId` from
`installer/Pipevoice.iss` plus Inno Setup's `_is1` uninstall-key suffix. If
`AppId` ever changes, this changes with it or winget stops recognising existing
installs.

## Submitting

Fork `microsoft/winget-pkgs`, copy these three files to
`manifests/p/Powleads/PipeVoice/<version>/`, and open a PR. Microsoft's
automation validates the manifest, downloads the installer, and runs it in a
sandbox. An unsigned installer is accepted but may be flagged for a human
review pass, which is slower than the automated path.

## The mistake this README already warned about

On the first real submission the manifest carried v2.43.0's SHA-256 against
v2.44.1's URL: the version strings had been `sed`-updated and the hash had not.
Microsoft's CI would have rejected it after downloading the asset.

**Recompute the hash from the release you are actually pointing at.** Bumping the
version is two edits, not one:

```bash
V=2.44.1
curl -sL "https://github.com/SignalEngine/PipeVoice/releases/download/v$V/Pipevoice-Setup.exe.sha256" \
  | tr -d '[:space:]' | tr 'a-f' 'A-F'
```
