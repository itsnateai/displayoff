@echo off
REM build-exe.bat — Nuitka standalone build for displayoff (v1.7.22+)
REM
REM Output:  build\displayoff\          standalone bundle directory (~55 MB,
REM                                     contains displayoff.exe + ~150 runtime files)
REM          build\displayoff-vX.Y.Z.zip  zip of that directory, for release upload
REM          build\SHA256SUMS.txt         one-line manifest, sha256sum -b format
REM
REM Requirements:
REM   - Python 3.14
REM   - pip install -r requirements.txt  (pystray, Pillow, pynput, packaging)
REM   - pip install nuitka                (build-time only — not a runtime dep)
REM   - MSVC build tools (auto-detected by Nuitka; falls back to MinGW64)
REM
REM Build time: ~3-8 minutes on first run, ~2-4 minutes incremental.
REM
REM Verify after build:
REM   build\displayoff\displayoff.exe --version       (should print "displayoff 1.7.22")
REM   build\displayoff\displayoff.exe                 (should start tray icon)
REM   build\displayoff\displayoff.exe --diagnose-paths (should print path-resolver state; exit 0 on success, 1 on broken resolver)
REM
REM ── Why Nuitka and not PyInstaller ──
REM Nuitka compiles Python to C → native binary. Smaller (~15-25 MB vs ~30-40
REM MB PyInstaller), faster startup (~200-500 ms vs ~800-2000 ms), and less
REM AV-flagged because the binary signature differs from the well-known
REM PyInstaller bootloader. Trade-off: 5-10x slower build.
REM
REM ── --onefile vs --standalone (CHANGED in v1.7.22) ──
REM v1.7.13–v1.7.21 used --onefile, which produces a single .exe that extracts
REM all bundled DLLs to %TEMP%\onefile_<pid>_<rand>\ on every launch. That
REM extraction pattern matches Microsoft Defender's Trojan:Win32/Bearfoos.A!ml
REM heuristic 1:1 — small unsigned PyInstaller/Nuitka onefile binaries with
REM pynput keyboard hooks + ctypes Win32 calls + powercfg subprocess spawning
REM trip the ML model reliably. Verified false-positive on Nate's machine
REM 2026-05-27 — Defender quarantined the extracted displayoff.dll from
REM Temp\onefile_17168_583395_yKV8JgBRtaU\ within seconds of an auto-blank
REM firing. Switching to --standalone eliminates the Temp extraction entirely:
REM the .exe and all its dependency DLLs live persistently in build\displayoff\
REM and run from there directly. Trade-off: install footprint changes from a
REM single 52 MB .exe to a 52 MB folder with ~150 files; can't atomically
REM rename the running install via single-file os.rename anymore (see
REM displayoff.py's _execute_rename_dance for the folder-swap protocol).
REM Distribution moves from a bare .exe asset to a zip.

setlocal
REM v1.7.22: scrape VERSION from displayoff.py's __version__ instead of
REM hardcoding it here. The dual-source pattern (.bat hardcodes / .sh
REM scrapes from source) was the root of a recurring forgot-to-bump bug
REM caught by the v1.7.21 verifier round (T2-Opus HIGH) and immediately
REM re-occurred at v1.7.22 (caught by the v1.7.22 verifier round,
REM convergent REJECT across 5 of 6 verifier agents). Scraping from a
REM single source of truth is the recurrence-killing fix the v1.7.22
REM T3-Opus verifier suggested. Matches build-release.sh exactly.
REM Pattern: [[reference_settings_apply_dual_propagation_bug]].
for /f "delims=" %%V in ('python -c "import re; print(re.search(r'^__version__ = \"([^\"]+)\"', open(r'displayoff.py', encoding='utf-8').read(), re.M).group(1))"') do set VERSION=%%V
if "%VERSION%"=="" (
    echo === BUILD FAILED: could not scrape __version__ from displayoff.py. ===
    exit /b 1
)
set VERSION_FOUR=%VERSION%.0
echo Building v%VERSION% (scraped from displayoff.py)...

REM v1.7.20: Nuitka 4.1.1 preflight. CI is pinned via
REM `pip install nuitka==4.1.1` inside release.yml, but local builds rely
REM on whatever Nuitka happens to be installed in the active venv. A
REM mismatch silently introduces behavior drift. Failing fast here forces
REM the human to make the bump call explicitly. If you intentionally want
REM to test a newer Nuitka:
REM   - bump the findstr below to the new version
REM   - bump release.yml's pip-install pin to match
REM   - re-verify the comment timeline in this file
REM v1.7.22 note: the py3.14 zstd packing bug only affected --onefile pack
REM (compression.zstd.ZstdError during the onefile-bootstrap zstd step).
REM Under --standalone there is no zstd packing step, so the workaround
REM (--onefile-no-compression) is no longer relevant. The 4.1.1 pin stays
REM until we've smoke-tested a newer Nuitka under standalone mode on Win11.
python -m nuitka --version | findstr /B "4.1.1" >nul
if errorlevel 1 (
    echo === BUILD FAILED ===
    echo Expected Nuitka 4.1.1 (workspace pin); got something else.
    echo Run: pip install nuitka==4.1.1
    echo If you intentionally upgraded Nuitka, update this guard.
    exit /b 1
)

REM Embed the icon as a Windows resource (--windows-icon-from-ico) AND bundle
REM the .ico inside the standalone dir (--include-data-files) so pystray's
REM Image.open(_ICON_PATH) finds it next to the .exe at runtime.
REM Both are required: the resource is for File Explorer + the .lnk's
REM IconLocation; the data file is for in-process loading by pystray.
REM
REM native_blank and tray_promoter are imported INSIDE functions in
REM displayoff.py (not at module level), so Nuitka's static import scanner
REM might miss them — include explicitly to be safe.

REM Wipe previous artifacts so a silent build failure produces no
REM new bundle at all rather than ship a half-stale mix.
if exist build\displayoff rmdir /s /q build\displayoff
if exist build\displayoff.dist rmdir /s /q build\displayoff.dist
del /q build\displayoff-v*.zip 2>nul
del /q build\SHA256SUMS.txt 2>nul

python -m nuitka ^
    --standalone ^
    --windows-console-mode=disable ^
    --windows-icon-from-ico=displayoff.ico ^
    --include-data-files=displayoff.ico=displayoff.ico ^
    --include-module=native_blank ^
    --include-module=tray_promoter ^
    --include-module=PIL.Image ^
    --enable-plugin=tk-inter ^
    --product-name="Display Off" ^
    --product-version=%VERSION_FOUR% ^
    --file-version=%VERSION_FOUR% ^
    --file-description="Display Off" ^
    --copyright="MIT License" ^
    --company-name="itsnateai" ^
    --output-dir=build ^
    --output-filename=displayoff.exe ^
    --assume-yes-for-downloads ^
    displayoff.py

if %errorlevel% neq 0 (
    echo.
    echo === BUILD FAILED ===
    exit /b 1
)

REM Rename the Nuitka .dist directory to a clean "displayoff" name so the
REM zip extracts to displayoff\displayoff.exe (matches the documented install
REM layout in README.md).
move /y build\displayoff.dist build\displayoff >nul
if not exist build\displayoff\displayoff.exe (
    echo === BUILD FAILED: build\displayoff\displayoff.exe not produced. ===
    exit /b 1
)

REM Package the standalone bundle as a zip for release upload.
set ZIP_NAME=displayoff-v%VERSION%.zip
pushd build
python -m zipfile -c %ZIP_NAME% displayoff
popd
if not exist build\%ZIP_NAME% (
    echo === BUILD FAILED: build\%ZIP_NAME% not produced. ===
    exit /b 1
)

REM Generate SHA256SUMS.txt — hash the zip (the actual release artifact).
REM Uses Python's hashlib so we don't depend on sha256sum being on PATH
REM (Git for Windows ships it; vanilla cmd doesn't).
for /f "delims=" %%H in ('python -c "import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest())" build\%ZIP_NAME%') do set SHA=%%H
> build\SHA256SUMS.txt echo %SHA% *%ZIP_NAME%

echo.
echo === BUILD OK ===
echo Bundle: build\displayoff\
echo Zip:    build\%ZIP_NAME%
for %%I in (build\%ZIP_NAME%) do echo Size:   %%~zI bytes
echo SHA256: %SHA%
echo.
echo Manifest contents:
type build\SHA256SUMS.txt
echo.
echo Verify:  build\displayoff\displayoff.exe --version
endlocal
