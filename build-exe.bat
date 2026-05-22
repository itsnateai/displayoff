@echo off
REM build-exe.bat — Nuitka onefile build for displayoff
REM
REM Output:  build\displayoff.exe  (single self-contained .exe, ~55 MB
REM          uncompressed — see --onefile-no-compression note below)
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
REM   build\displayoff.exe --version       (should print "displayoff 1.7.20")
REM   build\displayoff.exe                 (should start tray icon)
REM   build\displayoff.exe --diagnose-paths (should print path-resolver state; exit 0 on success, 1 on broken resolver)
REM
REM ── Why Nuitka and not PyInstaller ──
REM Nuitka compiles Python to C → native binary. Smaller (~15-25 MB vs ~30-40
REM MB PyInstaller), faster startup (~200-500 ms vs ~800-2000 ms), and less
REM AV-flagged because the binary signature differs from the well-known
REM PyInstaller bootloader. Trade-off: 5-10x slower build.
REM
REM ── --onefile vs --standalone ──
REM --onefile produces a single .exe that extracts dependencies to a per-launch
REM temp dir at startup. --standalone produces a directory with .exe + .dll
REM files alongside (no extraction step). We use --onefile so the install is
REM a single file the rename-dance updater can swap atomically.

setlocal
set VERSION=1.7.20
set VERSION_FOUR=%VERSION%.0

REM v1.7.20: Nuitka 4.1.1 preflight. CI is pinned via
REM `pip install nuitka==4.1.1` inside release.yml, but local builds rely
REM on whatever Nuitka happens to be installed in the active venv. A
REM mismatch silently introduces behavior drift (the py3.14 zstd
REM compression bug we're working around might be fixed in a newer
REM Nuitka — in which case --onefile-no-compression should be DROPPED,
REM not kept). Failing fast here forces the human to make that call
REM explicitly. If you intentionally want to test a newer Nuitka:
REM   - bump the findstr below to the new version
REM   - bump release.yml's pip-install pin to match
REM   - re-verify the comment timeline in this file
python -m nuitka --version | findstr /B "4.1.1" >nul
if errorlevel 1 (
    echo === BUILD FAILED ===
    echo Expected Nuitka 4.1.1 (workspace pin); got something else.
    echo Run: pip install nuitka==4.1.1
    echo If you intentionally upgraded Nuitka, update this guard.
    exit /b 1
)

REM Embed the icon as a Windows resource (--windows-icon-from-ico) AND bundle
REM the .ico inside the onefile (--include-data-files) so pystray's
REM Image.open(_ICON_PATH) still finds it at the temp extract dir at runtime.
REM Both are required: the resource is for File Explorer + the .lnk's
REM IconLocation; the data file is for in-process loading by pystray.
REM
REM native_blank and tray_promoter are imported INSIDE functions in
REM displayoff.py (not at module level), so Nuitka's static import scanner
REM might miss them — include explicitly to be safe.

REM --onefile-no-compression bypasses a Nuitka 4.1.1 + Python 3.14 + zstd
REM incompatibility ("Allocation error : not enough memory" in
REM compression.zstd.ZstdError during onefile bootstrap packing — the
REM compiled .dist directory is fine, but zstd's compress() can't accept
REM the dist-file enumeration write under py3.14). Bypass costs ~30 MB of
REM .exe size (55 MB vs ~20 MB compressed).
REM
REM Version-check timeline (re-check before each release; drop the flag
REM if a newer Nuitka has shipped the py3.14 zstd fix):
REM   2026-05-21 (v1.7.13 / v1.7.14): Nuitka PyPI latest = 4.1.1 — bug still present, flag required.
REM   2026-05-21 (v1.7.15):           re-checked `pip index versions nuitka` → 4.1.1 still latest. Flag required.
REM   2026-05-22 (v1.7.16 / v1.7.17): Nuitka still pinned to 4.1.1 (no PyPI release between v1.7.15 and v1.7.17 — same day). Flag still required.
REM   2026-05-22 (v1.7.18 / v1.7.19): Confirmed Nuitka 4.1.1 latest. Flag still required.
REM   2026-05-22 (v1.7.20):           Final maintenance release. Nuitka still pinned to 4.1.1 (no PyPI update). Flag still required.
REM                                   v1.7.20 also adds the `python -m nuitka --version` preflight guard above so a future local
REM                                   build with a different Nuitka pinned in the venv fails fast instead of producing a binary
REM                                   that subtly differs from what CI shipped.
REM
REM Recipe to re-verify: `pip index versions nuitka | head -1` — if the
REM top line shows >4.1.1, install it in a scratch venv, build with the
REM flag dropped, and confirm onefile-pack completes. See build-release.sh
REM for the matching bash recipe (kept in sync).

python -m nuitka ^
    --onefile ^
    --onefile-no-compression ^
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
    --file-description="Force all monitors to sleep without putting the PC to sleep." ^
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

echo.
echo === BUILD OK ===
echo Output: build\displayoff.exe
for %%I in (build\displayoff.exe) do echo Size:   %%~zI bytes
echo.
echo Verify:  build\displayoff.exe --version
endlocal
