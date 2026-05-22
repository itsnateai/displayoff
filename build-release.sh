#!/usr/bin/env bash
# build-release.sh - Build displayoff.exe + SHA256SUMS.txt for a GitHub release.
#
# Run BEFORE 'git tag vX.Y.Z' so the artifacts are ready when the tag pushes.
# Does NOT upload. See RELEASE_STEPS.md (printed at end) for the upload recipe.
#
# Output:
#   build/displayoff.exe       the frozen single-file binary
#   build/SHA256SUMS.txt       one-line manifest, sha256sum -b format
#
# Both files MUST be uploaded as release assets. The rename-dance updater
# inside displayoff.exe looks for both names at the latest release's
# assets URL. To change them, also change _UPDATE_EXE_NAME /
# _UPDATE_MANIFEST_NAME in displayoff.py.

set -euo pipefail
cd "$(dirname "$0")"

VERSION=$(grep -E '^__version__ = ' displayoff.py | sed -E 's/.*"([^"]+)".*/\1/')
echo "Building v${VERSION}..."

# Wipe previous artifacts so a silent build failure produces no .exe at all
# rather than ship stale bytes from a previous version.
rm -f build/displayoff.exe build/SHA256SUMS.txt

# Same recipe as build-exe.bat. See that file for the --onefile-no-compression
# rationale (Nuitka 4.1.1 + py3.14 zstd OOM workaround) and the version-check
# timeline. Re-verify before each release: `pip index versions nuitka | head -1`.
# Last checked: 2026-05-21 (v1.7.15) — Nuitka latest still 4.1.1, flag required.
python -m nuitka \
    --onefile \
    --onefile-no-compression \
    --windows-console-mode=disable \
    --windows-icon-from-ico=displayoff.ico \
    --include-data-files=displayoff.ico=displayoff.ico \
    --include-module=native_blank \
    --include-module=tray_promoter \
    --include-module=PIL.Image \
    --enable-plugin=tk-inter \
    --product-name="Display Off" \
    --product-version="${VERSION}.0" \
    --file-version="${VERSION}.0" \
    --file-description="Force all monitors to sleep without putting the PC to sleep." \
    --copyright="MIT License" \
    --company-name="itsnateai" \
    --output-dir=build \
    --output-filename=displayoff.exe \
    --assume-yes-for-downloads \
    displayoff.py

if [[ ! -f build/displayoff.exe ]]; then
    echo "ERROR: build/displayoff.exe not produced. Nuitka silent failure?" >&2
    exit 1
fi

# Generate SHA256SUMS.txt in GNU coreutils sha256sum -b format
# (<64_hex>  *<filename>). The rename-dance manifest parser accepts both
# the binary-mode '*' prefix and the text-mode no-prefix variant.
SHA=$(sha256sum -b build/displayoff.exe | cut -d' ' -f1)
printf '%s *displayoff.exe\n' "${SHA}" > build/SHA256SUMS.txt

echo
echo "=== BUILD OK ==="
echo "Version:  v${VERSION}"
echo "Size:     $(wc -c < build/displayoff.exe) bytes"
echo "SHA256:   ${SHA}"
echo
echo "Manifest contents:"
cat build/SHA256SUMS.txt
echo
echo "=== Smoke test ==="
build/displayoff.exe --version
echo
echo "=== Next steps (CI-driven release workflow, v1.7.15+) ==="
echo "  This script ran a LOCAL build for verification. As of v1.7.15 the"
echo "  canonical release artifacts are built by .github/workflows/release.yml"
echo "  on the GitHub runner — do NOT 'gh release upload' the locally-built"
echo "  .exe (it would replace the CI-built one and break SHA256 transparency)."
echo
echo "  Correct release flow:"
echo "    1. Edit CHANGELOG.md → release-notes.md (extract this version's entry)"
echo "    2. gh release create v${VERSION} --title 'v${VERSION}' --notes-file release-notes.md --draft"
echo "       (NO asset files — CI uploads them when the tag pushes)"
echo "    3. git tag v${VERSION} && git push origin v${VERSION}"
echo "       (release.yml fires on the tag push, builds the .exe + SHA256SUMS"
echo "       in CI, uploads them to the draft release, ~5-10 min)"
echo "    4. gh release edit v${VERSION} --draft=false"
echo "       (promote to public once CI uploaded the assets — verify the"
echo "       SHA256 in the release matches what this local build produced)"
echo
echo "  This local build's SHA256 (for cross-checking the CI build):"
echo "    ${SHA}"
