#!/usr/bin/env bash
# build-release.sh - Build displayoff standalone bundle + zip + SHA256SUMS.txt
# for a GitHub release.
#
# Run BEFORE 'git tag vX.Y.Z' so the artifacts are ready when the tag pushes.
# Does NOT upload. See RELEASE_STEPS.md (printed at end) for the upload recipe.
#
# Output:
#   build/displayoff/                  standalone bundle directory
#                                      (~150 files, ~55 MB total — .exe +
#                                      Nuitka runtime DLLs + tkinter +
#                                      Pillow + pystray + pynput, all
#                                      laid out for in-place execution)
#   build/displayoff-vX.Y.Z.zip        zip of that directory, uploaded as
#                                      the release asset users download
#   build/SHA256SUMS.txt               one-line manifest, sha256sum -b format,
#                                      hashing the zip
#
# Both `displayoff-vX.Y.Z.zip` and `SHA256SUMS.txt` MUST be uploaded as
# release assets. The folder-swap updater inside displayoff.exe looks up
# the zip URL from the release's assets list at runtime (it matches by
# `.zip` suffix in the assets dict and parses the SHA256 from the manifest
# by zip filename) — to change the naming convention here, also update
# the consumer side in displayoff.py (search for `_UPDATE_ZIP_SUFFIX` and
# `_parse_sha256_manifest`).

set -euo pipefail
cd "$(dirname "$0")"

VERSION=$(grep -E '^__version__ = ' displayoff.py | sed -E 's/.*"([^"]+)".*/\1/')
echo "Building v${VERSION}..."

# Wipe previous artifacts so a silent build failure produces no new bundle
# at all rather than ship a half-stale mix.
rm -rf build/displayoff build/displayoff.dist
rm -f build/displayoff-v*.zip build/SHA256SUMS.txt

# v1.7.22: switched from --onefile to --standalone. The --onefile mode
# extracts bundled DLLs to %TEMP%\onefile_<pid>_<rand>\ on every launch,
# a pattern that matches Microsoft Defender's Trojan:Win32/Bearfoos.A!ml
# heuristic 1:1 — small unsigned Nuitka onefile binaries with pynput
# keyboard hooks + ctypes Win32 calls + powercfg subprocess spawning trip
# the ML model reliably. Verified false-positive on Nate's machine
# 2026-05-27 — Defender quarantined the extracted displayoff.dll from
# Temp\onefile_*\ within seconds of an auto-blank firing. Switching to
# --standalone eliminates the Temp extraction entirely.
#
# The Nuitka 4.1.1 + py3.14 zstd packing bug that motivated v1.7.13's
# --onefile-no-compression workaround only affected the onefile-pack
# step; --standalone never runs zstd, so the flag is no longer relevant.
# Last re-verified Nuitka latest = 4.1.1 on 2026-05-27.
python -m nuitka \
    --standalone \
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
    --file-description="Display Off" \
    --copyright="MIT License" \
    --company-name="itsnateai" \
    --output-dir=build \
    --output-filename=displayoff.exe \
    --assume-yes-for-downloads \
    displayoff.py

# Nuitka --standalone outputs to build/displayoff.dist/. Rename to plain
# `displayoff/` so the zip extracts to a clean folder name matching the
# documented install layout (README.md Option A: extract zip to
# proggy/Tools/ → proggy/Tools/displayoff/displayoff.exe).
if [[ ! -d build/displayoff.dist ]]; then
    echo "ERROR: build/displayoff.dist not produced. Nuitka silent failure?" >&2
    exit 1
fi
mv build/displayoff.dist build/displayoff

if [[ ! -f build/displayoff/displayoff.exe ]]; then
    echo "ERROR: build/displayoff/displayoff.exe not produced. Nuitka silent failure?" >&2
    exit 1
fi

# Package the standalone bundle as a zip for release upload.
ZIP_NAME="displayoff-v${VERSION}.zip"
( cd build && python -m zipfile -c "${ZIP_NAME}" displayoff/ )

if [[ ! -f "build/${ZIP_NAME}" ]]; then
    echo "ERROR: build/${ZIP_NAME} not produced." >&2
    exit 1
fi

# Generate SHA256SUMS.txt hashing the zip (the release artifact). Format
# matches GNU coreutils `sha256sum -b`: `<64_hex>  *<filename>`. The
# folder-swap updater's manifest parser accepts both binary-mode `*` prefix
# and text-mode no-prefix variants.
SHA=$(sha256sum -b "build/${ZIP_NAME}" | cut -d' ' -f1)
printf '%s *%s\n' "${SHA}" "${ZIP_NAME}" > build/SHA256SUMS.txt

echo
echo "=== BUILD OK ==="
echo "Version:  v${VERSION}"
echo "Bundle:   build/displayoff/ ($(find build/displayoff -type f | wc -l) files)"
echo "Zip:      build/${ZIP_NAME}"
echo "Size:     $(wc -c < "build/${ZIP_NAME}") bytes"
echo "SHA256:   ${SHA}"
echo
echo "Manifest contents:"
cat build/SHA256SUMS.txt
echo
echo "=== Smoke test ==="
build/displayoff/displayoff.exe --version
echo
echo "=== Next steps (CI-driven release workflow, v1.7.15+) ==="
echo "  This script ran a LOCAL build for verification. As of v1.7.15 the"
echo "  canonical release artifacts are built by .github/workflows/release.yml"
echo "  on the GitHub runner — do NOT 'gh release upload' the locally-built"
echo "  artifacts (it would replace the CI-built ones and break SHA256"
echo "  transparency)."
echo
echo "  Correct release flow:"
echo "    1. Edit CHANGELOG.md → release-notes.md (extract this version's entry)"
echo "    2. gh release create v${VERSION} --title 'v${VERSION}' --notes-file release-notes.md --draft"
echo "       (NO asset files — CI uploads them when the tag pushes)"
echo "    3. git tag v${VERSION} && git push origin v${VERSION}"
echo "       (release.yml fires on the tag push, builds + zips in CI,"
echo "       uploads them to the draft release, ~5-10 min)"
echo "    4. gh release edit v${VERSION} --draft=false"
echo "       (promote to public once CI uploaded the assets — verify the"
echo "       SHA256 in the release matches what this local build produced)"
echo
echo "  This local build's SHA256 (for cross-checking the CI build):"
echo "    ${SHA}"
