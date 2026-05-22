v1.7.20 is the **final planned release**. Maintenance-only mode after this — no further versions ship unless a user reports a bug that's not already in the v1.7.20 known-gap set. The 14-item polish drain that lands here closes every deferred item from the v1.7.17 / v1.7.18 / v1.7.19 train, hardens the resolver and updater against several latent issues, and tightens the release pipeline.

## What's fixed (14 items, grouped by section)

### A. Resolver hardening (security-adjacent)

- **`_path_under_protected()` helper rejects WindowsApps Store stubs.** Applied to Strategy 0/1/2 in `_resolve_on_disk_exe_path()`. A malicious argv[0] pointing inside `%LOCALAPPDATA%\Microsoft\WindowsApps\` (which the OS-level reparse-point ACL would block writes to anyway) now bounces off the filter up front instead of partway through the rename-dance. Documented gap in v1.7.19's CHANGELOG is closed.
- **`_download_url_allowed()` rejects `..` traversal in URL paths.** SHA256 verification is still the integrity boundary, but the allowlist's `startswith("/itsnateai/displayoff/")` check is no longer satisfiable via `/itsnateai/displayoff/../other-repo/...`. Normalizes via `os.path.normpath` before the prefix check.
- **`_migrate_legacy_data()` cross-device-atomic.** Replaces `shutil.move` (collapses to non-atomic copy+unlink across volumes) with `shutil.copy2 → SHA256 verify → os.remove(src)`. Partial copies are detected and re-tried cleanly on next launch instead of being permanently skipped. Only affects portable installs where `%APPDATA%` and the install dir live on different volumes.

### B. UX + observability

- **`--diagnose-paths` exit code now non-zero on resolver failure.** Returns `1` when running under freeze but `_EXE_PATH` is `None` (the case the flag was designed to surface). `0` otherwise. A health script polling for updater readiness now has a useful signal.
- **Rename-dance child-relaunch handshake.** Named event `Local\DisplayOff_UpdateChildReady` replaces the fixed 300 ms parent-sleep with a 5-second WaitForSingleObject. Parent creates the event before spawning the child, child signals as the first act of `--after-update`, parent waits or times out before `os._exit(0)`. Fallback to the legacy 0.3 s sleep when CreateEventW fails. Symptom this fixes: "no tray after update" on slow systems where the child's Python interpreter took longer than 300 ms to start.
- **Themed dialogs (`_themed_dialog`) now have a sticky minsize floor.** Tk geometry re-solves (font cache refresh, DPI change, grab-set side effects) could collapse the button row below its required width — v1.7.16's one-shot `geometry()` fix didn't survive a re-solve. `dlg.minsize(w, h)` is the durable form of the same constraint.

### C. Build + release hygiene

- **`release.yml` permissions tightened.** Workflow root is `contents: read`; only the `softprops/action-gh-release` upload step has `contents: write`. Least-privilege model — every other step (checkout, install, build) runs read-only.
- **Post-upload CDN redirect-host smoke test in CI.** Curls the uploaded asset URL, follows redirects, asserts the final host matches one of the three values in `_download_url_allowed`'s allowlist. If GitHub silently swaps the CDN host again (the way they did when `release-assets.githubusercontent.com` landed mid-2025 and broke our updater), the next release CI run fails BEFORE the broken build ships to users.
- **`objects-origin.githubusercontent.com` added to the in-app update allowlist.** Forward-compat defense for the same CDN-migration risk #8 catches at the CI layer.
- **`_UPDATE_MIN_EXE_SIZE = 1 MB` → `40 MB`.** Real `.exe` is ~55 MB. 1 MB floor only caught 200-OK HTML error pages; 40 MB catches mis-shipped stub builds too. Loosen if a future Nuitka zstd-compression unlock lands and shrinks the real exe to ~20 MB.
- **`build-exe.bat` Nuitka 4.1.1 preflight.** CI is pinned via `pip install nuitka==4.1.1`; local builds previously used whatever was in the venv. Mismatch silently introduced behavior drift. Failing fast forces the human to make that call explicitly.
- **`build-exe.bat` timeline entries refreshed for v1.7.16–v1.7.20.** Documents that `--onefile-no-compression` is still required and Nuitka 4.1.1 is still the workspace pin.

### D. Cosmetic

- **`DwmSetWindowAttribute` bound at module load instead of every `_apply_dark_titlebar` call.** Convention violation per the file's "all bindings live in the win32 block" rule; `dwmapi.dll` load is wrapped in `try/OSError` so pre-Win10-1607 builds gracefully no-op.
- **`tray_promoter.py:121` docstring fix.** Template-portable example now reads `current_exe_path=_EXE_PATH or sys.executable` so a future freeze-mode template-copier doesn't accidentally tag the per-launch temp python.exe. The real call site at `displayoff.py` was already correct; only the docstring example needed the fix.

## Upgrade path

- **v1.7.19 users** — click Settings → Check for updates → Install now. v1.7.19's Strategy 0 + the new child-handshake event from this build mean the dance should run end-to-end cleanly.
- **v1.7.18 users** — same in-app path. v1.7.18's resolver works at startup on a clean install (Strategy 1 wins), so the dance to v1.7.20 should complete. If it fails, run `displayoff.exe --diagnose-paths` post-recovery and the resolver state will be visible.
- **v1.7.17 / v1.7.16 / v1.7.15 / v1.7.14 / v1.7.13 users** — manual install. Quit the tray, download `displayoff.exe` below, replace the binary in place, and relaunch. Those builds have the structural v1.7.13 path-resolution bug that the in-app updater cannot recover from.
- **.py source-mode users** — no action.

## What's next

Nothing planned. Displayoff is in maintenance mode after this. If a user reports a bug that's not already documented as a known gap in v1.7.20's CHANGELOG.md, a hotfix will land the same day; otherwise, no further work.

Full changelog: see `CHANGELOG.md`.
