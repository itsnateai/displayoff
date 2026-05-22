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
REM   build\displayoff.exe --version       (should print "displayoff 1.7.13")
REM   build\displayoff.exe                 (should start tray icon)
REM
REM Sandbox-test recommended path:
REM   _.claude\_tools\sandbox\Sandbox_Diag.wsb  (verify freeze-mode parity)
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
set VERSION=1.7.13
set VERSION_FOUR=%VERSION%.0

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
REM .exe size (55 MB vs ~20 MB compressed). Revisit once Nuitka 4.2+ ships
REM with py3.14 fixes — drop the flag for smaller distribution-side .exe.
REM Tracked: 2026-05-21 — see build-exe.sh for the matching bash recipe.

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
