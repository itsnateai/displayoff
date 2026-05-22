The actual fix for the rename-dance, tray promoter, and autostart `.lnk` — each of which has been structurally broken under freeze since v1.7.13. v1.7.13's freeze pass assumed `sys.executable` returns the on-disk `displayoff.exe` under Nuitka onefile (which is true for PyInstaller, but false for Nuitka 4.1.1). Empirically `sys.executable` returns the per-launch temp-extracted `python.exe` (e.g., `%TEMP%\onefile_<pid>_<rand>_<hash>\python.exe`), so every downstream consumer — the rename-dance, the autostart shortcut, and the tray-icon promoter — got the wrong path and silently mis-targeted.

v1.7.16's release notes claimed the dance "should work end-to-end this time" — also wrong; the v1.7.16 URL-allowlist fix made the network step pass but the rename targeted the wrong directory. v1.7.17 ships the path-resolution fix that finally makes the dance work.

## What's fixed

- **Self-updater finally works end-to-end.** A new `_resolve_on_disk_exe_path()` helper uses `NUITKA_ONEFILE_PARENT` + `QueryFullProcessImageNameW(parent_pid)` to query the kernel-tracked image path of the bootstrap process — which IS the on-disk `displayoff.exe`. Two-layer fallback (`sys.argv[0]` if outside `%TEMP%`, then last-resort `sys.executable` with a WARNING log) covers edge cases without trusting the original wrong API. All candidate values are logged to `displayoff.log` at module import so future debugging has the empirical answer in plain sight.
- **Tray icon stays visible across Explorer restarts / installs.** `tray_promoter.promote_in_background` now receives the correct on-disk path so it matches Win11's `NotifyIconSettings\<hash>\ExecutablePath` and writes `IsPromoted=1`. Prior versions would silently fail the match (the registry recorded the on-disk .exe but the promoter polled with the temp path) and the icon defaulted to hidden, requiring a manual Settings ▸ Personalization ▸ Taskbar ▸ Other system tray icons toggle on every install.
- **Autostart `.lnk` points at the persistent on-disk .exe.** The Startup-folder shortcut no longer references a per-launch temp path that changes every launch. v1.7.13 → v1.7.16 users who had autostart enabled would see "Stale startup shortcut" in `displayoff.log` every relaunch as the symptom. After installing v1.7.17, toggle Settings → Autostart off and back on to refresh the `.lnk` to the corrected path.
- **`_PING_FIRED_THIS_PROCESS` lock-guarded** via a `threading.Lock` + claim-then-fire-then-release pattern (was a bare module-level bool — flagged by the v1.7.16 8-agent verifier as a violation of the project's free-threaded discipline).
- **`_themed_dialog` chrome margin is DPI-relative** (`dlg.winfo_pixels("0.4i")` instead of hardcoded `40` px) so button rows don't clip at 125% / 150% / 175% / 200% DPI scaling.
- **`_recover_from_failed_update` preserves manual rollback artifacts.** If `displayoff.exe.old` is newer than the current `displayoff.exe` (signal of a deliberate manual rollback), the auto-cleanup is skipped. Prior versions deleted it unconditionally.
- **`.gitignore` narrowed** from `*.old` to `*.exe.old` so legitimate `.old` files anywhere in a fork's tree aren't shadowed.

## Upgrade path — **manual install required for v1.7.13 / v1.7.14 / v1.7.15 / v1.7.16**

The bug is in the client's path-resolution logic. v1.7.13 → v1.7.16 clients downloading the v1.7.17 asset would succeed at the network step but still rename inside the temp dir, leaving the on-disk install untouched. So the in-app update WILL "succeed" but won't actually upgrade you. Install manually, once.

**Manual install steps:**

1. Right-click the Display Off tray icon → **Quit** (releases the .exe file lock).
2. Download `displayoff.exe` from this release (the button below).
3. Replace your existing `displayoff.exe` with the new file (same filename, same location).
4. Launch v1.7.17. Your config + autostart `.lnk` + idle settings carry over unchanged (config lives in `%APPDATA%\displayoff\`; the `.lnk` references the .exe by path).
5. Optional: toggle Settings ▸ Autostart off and back on to refresh the Startup-folder shortcut to the corrected on-disk path. Same for any tray-icon hidden-by-default behavior — v1.7.17's auto-promote should fix it on the first launch, but a one-time Settings ▸ Personalization ▸ Taskbar ▸ Other system tray icons toggle is the manual remedy if it doesn't.

**From v1.7.17 onward,** "Settings → Check for updates → Install now" should work end-to-end. The next "Install now" exercise (v1.7.17 → v1.7.18, whenever there is one) will be the inaugural in-the-wild successful dance.

If you're on the `.py` source channel, no action needed — the resolver returns `None` under source mode and the rename-dance is skipped entirely.

## What v1.7.17 exposes about prior releases

Be honest about regression scope: the rename-dance has **never** worked end-to-end since it was introduced in v1.7.13.

- **v1.7.13** — shipped the freeze pass with the wrong `sys.executable` assumption. No prior version to update FROM, so the dance was never exercised live.
- **v1.7.14** — same-day hardening of the first-launch promotion ping (correct in its own scope; didn't touch the path bug).
- **v1.7.15** — added CI release workflow + in-process ping dedupe (also correct; also didn't surface the bug).
- **v1.7.16** — hotfixed the GitHub release-asset CDN allowlist. Necessary, but not sufficient — the URL fix made the network step pass but the rename still targeted the temp dir.
- **v1.7.17** — the actual fix. The 8-agent verifier rounds on each release couldn't catch this because the v1.7.13 comment block claimed `sys.executable` was correct, and the verifiers trusted the docstring over runtime behavior.

The empirical proof showed up in `displayoff.log` after the v1.7.16 manual install:

```
Stale startup shortcut: target='...pythonw.exe' but current launcher is
'<%TEMP%>\onefile_<pid>_<rand>\python.exe'
— treating as 'not enabled' so next Save re-creates it.
```

That's `sys.executable` returning the temp path. That log line is what triggered the v1.7.17 work.

Full changelog: see `CHANGELOG.md`.
