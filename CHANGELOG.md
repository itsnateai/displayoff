# Changelog — Display Off

## [1.7.26] — 2026-06-04

Fixes the in-app updater, which could leave you with **no tray icon after clicking "Install now"** — and gives the update visible progress instead of a silent void.

### Fixed

- **Self-update no longer kills the app on its way to the new version.** The folder-swap updater spawns the new build as a child process to perform the swap, while the old (current) process exits. Through v1.7.25 the child raced ahead the instant it told the old process "you can exit now" — but the old process was still alive at that exact moment (the v1.7.20 handshake guarantees it). Two things then failed within the same millisecond: the still-running old `.exe` **locked its own install folder** so the rename failed with `WinError 32`, and it **still owned the single-instance lock** so the new process saw "another instance is already running" and exited too. Net result: both processes gone, **no tray icon came back**. The new build now **waits for the old process to fully terminate** (using the parent PID recorded in the relaunch state) before renaming the folder and taking the single-instance lock — so the folder is unlocked and the lock is free by the time it needs them. A bounded retry on the lock acquisition backstops the rare case where the old process can't be waited on directly. (Live-log-confirmed against a real failed update on 2026-06-04.)
- **The updated app now actually reappears in the tray.** Even once the swap succeeds, the process performing it is running from the folder it just renamed — so its later resource and module lookups (the tray icon, and `pystray` itself) pointed at the now-gone old path, and the app silently dropped to a one-shot "blank once and exit" mode with **no tray icon**. The swap now hands off to a freshly launched instance from the final install path (which has correct paths and starts up normally) and exits, so the tray comes back reliably after an update. Caught by an end-to-end test on the compiled `.exe`, which the unit tests and code review could not see.
- **"Install now" now shows progress.** Previously the whole download → verify → extract → restart ran silently in the background and the window simply vanished when the new version took over — no indication anything was happening. There is now a small progress window (*Downloading → Verifying → Extracting → Restarting*) for the duration, and the new version fires a **"Updated to vX.Y.Z"** tray notification once it's up, so a successful self-update gives clear feedback start to finish.

### Internal

- New `_wait_for_parent_exit(pid)` helper (`OpenProcess(SYNCHRONIZE)` + `WaitForSingleObject`, 10s bound, fail-open) and a `retry_until_s` grace window on `_acquire_single_instance` scoped to the post-update relaunch path only — every normal launch keeps the historical single-attempt, instant second-instance detection.
- New `_relaunch_after_swap()` + internal `--updated-to <version>` flag: a successful folder swap now spawns a fresh instance from the canonical exe and exits, instead of continuing in-process with module paths pointing at the renamed-away `.new` dir.
- Added `tests/test_update_handoff.py` — real-process (no-mock) regression guard for the wait helper and the back-compatible acquire signature.

No change to monitor blanking, hotkeys, idle-blank, autostart, or the DPI work from v1.7.24–v1.7.25.

## [1.7.25] — 2026-06-04

High-DPI correctness pass: the Settings, About, and message/update dialogs now render proportionally identical at 100% and 125%/150%+ display scale, by construction. This completes the v1.7.24 footer fix across the whole UI.

### Fixed

- **All dialog spacing now scales with display DPI.** v1.7.24 fixed only the settings footer gutter; the rest of the UI still used raw-pixel pad literals (`PAD = 20`, the inter-button `padx`, the About dialog's `padx=20, pady=15`, the themed dialog's `wraplength=460`, …). Tk grows point-sized fonts with display DPI but leaves pixel literals fixed, so at 125%/150% the fonts enlarged while the gaps between rows stayed put — spacing got tighter the higher the scale, and it looked correct only on a 100% monitor. Measured before the fix: the settings window's height scaled only **1.57×** from 100%→200% (a proportional layout scales ~2.0×), and the vertical gap between two rows was a **constant 14 px at every scale**. Now every pad goes through a `_dpi_scale()` helper tied to the live Tk scaling, so spacing tracks the fonts — settings height scales **1.94×** and the surfaces stay proportional at every scale.

### Changed / internal

- **Deterministic font scaling.** Tk's point→pixel `scaling` is now pinned to the real system DPI (`GetDpiForSystem`, Win10 1607+) right after each window is created, instead of relying on Tk's Windows auto-detect.
- **DPI awareness declared once, at the GUI entry.** `SetProcessDpiAwarenessContext(PerMonitor-V2)` now runs at the top of `run_tray()`, before any window exists, so every dialog inherits it on every entry path (previously it was set lazily inside the Settings-open path only).
- **About dialog** gained the content-driven `minsize` the Settings and themed dialogs already had, so a later font-cache / DPI re-solve can't clip its button row.
- Added `tests/test_dpi_layout.py` — a regression guard asserting the surfaces scale proportionally, so a future change can't silently reintroduce a fixed-pixel pad.

No behaviour change to monitor blanking, hotkeys, idle-blank, single-instance, or autostart — this release is layout/DPI only.

## [1.7.24] — 2026-06-01

UX polish: settings-dialog footer spacing on high-DPI displays, plus refreshed README screenshots.

### Fixed

- **Settings footer "Apply" button cramped against "Updates" on high-DPI displays.** The settings window width was a hardcoded `460` px, but the six footer buttons are sized in character units (`width=8`), which scale with display DPI while the pixel constant did not. At 100% scaling the two button groups (`[GitHub] [About] [Updates]` left, `[Apply] [Save] [Cancel]` right) had a comfortable centre gutter; at 125%/150% (common on laptops) the buttons rendered wider in pixels, consumed the whole interior, and the gutter collapsed to zero — so "Apply" butted directly against "Updates". Two complementary fixes, both mirroring the durable sizing the themed message-dialog helper (`_themed_dialog`, used for the update-check prompts) already used:
  - **Guaranteed gutter spacer.** `_build_footer` now packs a childless, DPI-relative (~0.3 in, min 24 px) spacer frame between the info group and the action group. Because it contributes to the footer's requested width, the window sizing always reserves room for it; any extra width from a wider window pools into the same gutter, so the gap is always at least the spacer width.
  - **Content-driven window width + sticky minsize.** The settings window now grows to `max(460, winfo_reqwidth())` after all widgets are built (instead of a flat 460) and pins a `minsize`, so the footer can't overflow the gutter and a later font-cache / DPI re-solve can't clip it.

### Changed

- **Refreshed README screenshots** (tray menu + settings dialog) to the current UI, and moved the **Screenshots** section up to directly below the intro — visible immediately instead of buried after *Why this exists → Quickstart → Features*.

## [1.7.23] — 2026-05-27

Same-day follow-up to v1.7.22. Two small UX fixes surfaced once the v1.7.22 standalone bundle landed on a real install path with autostart re-enabled.

### Fixed

- **Windows Startup-Apps toast / Task Manager showed the long file description as the program name.** v1.7.22 set `--file-description="Force all monitors to sleep without putting the PC to sleep."` so Windows would display that full sentence anywhere it sourced the user-facing app name from the PE `FileDescription` field — most visibly in the "App is now configured to run when you sign in" toast that fires when autostart is enabled. Shortened to `"Display Off"` in both `build-release.sh` and `build-exe.bat`. The longer descriptive sentence lives in the README and the GitHub release description, which is where it belongs. `--product-name` was already `"Display Off"`; this aligns `FileDescription` with it.

### Added

- **Tray right-click submenu: "Auto-blank when idle"** with presets Off / 5 minutes / 10 minutes / 30 minutes. Each item is a radio (only one checked at a time, reflecting `cfg['idle_blank_minutes']`). Clicking a preset persists the value via `save_config` + re-renders the menu + fires a `Display Off — Auto-blank: X min idle` toast for confirmation. Before v1.7.23 the only way to change the idle threshold was the Settings dialog spinbox; the submenu makes the common-case toggle a single right-click instead of opening the dialog.
  - **Custom (non-preset) values still work** via Settings. If `idle_blank_minutes` holds e.g. 15, none of the submenu radio items render checked, matching standard radio-button UX — clicking any preset overwrites the custom value (intentional; that's the meaning of clicking a preset).
  - **`_idle_check` / `_idle_set` helpers** live inside `run_tray()`, read `load_config()` fresh on every render and click so a stale value never wins against a config edit from elsewhere. 4 small JSON reads per right-click is bounded — `displayoff_config.json` is <200 bytes, <1ms per read.

## [1.7.22] — 2026-05-27

**Breaking-ish distribution change.** v1.7.13–v1.7.21 shipped a single-file `displayoff.exe` built via Nuitka `--onefile`. v1.7.22 switches to Nuitka `--standalone` and ships a zipped folder bundle (`displayoff-v1.7.22.zip` → extracts to `displayoff/displayoff.exe` + ~150 runtime files). The previous in-app self-updater can't reach v1.7.22 (it expects a `.exe` release asset; v1.7.22 ships a `.zip`) — manual one-time reinstall required. From v1.7.22 onward the new folder-swap self-updater handles automatic upgrades.

### Why this changed

The `--onefile` mode extracts bundled DLLs to `%TEMP%\onefile_<pid>_<rand>\` on every launch and runs from there. That extraction pattern matches Microsoft Defender's `Trojan:Win32/Bearfoos.A!ml` heuristic almost exactly — small unsigned Nuitka onefile binaries that also do global keyboard hooks (pynput), Win32 syscalls (ctypes), and spawn subprocesses (powercfg) fingerprint as malware-staging behavior to Defender's ML model. Verified false-positive on a daily-driver install 2026-05-27: the auto-idle-blank fired, Nuitka bootstrap extracted `displayoff.dll` to a fresh `onefile_*` Temp folder, and Defender quarantined it within seconds (event ID 1116, threat `Trojan:Win32/Bearfoos.A!ml`). The detection was a pure pattern hit, not a signature — the binary itself was clean, the bytes were just statistically suspicious.

Switching to `--standalone` eliminates the Temp extraction step. The .exe and all its dependencies live persistently in `displayoff/` next to each other; nothing gets unpacked on launch. Same runtime behavior, no Bearfoos.A!ml trigger, slightly faster cold-start (no extraction cost per launch). Trade-off: install footprint goes from one 52 MB `.exe` to a 52 MB folder with ~150 files, distribution moves from a bare `.exe` to a zip, and the self-updater's old "atomic single-file rename" trick has to become a folder-swap protocol (see v1.7.22 self-updater rewrite below).

### Build + release pipeline

- **`build-exe.bat` and `build-release.sh` switched to `--standalone`.** Both build scripts drop `--onefile --onefile-no-compression` (the latter was a workaround for a Nuitka 4.1.1 + py3.14 zstd packing bug specific to onefile-pack — under standalone there is no zstd step, so the workaround is moot). The Nuitka 4.1.1 pin stays until we've smoke-tested a newer Nuitka under standalone mode on Win11.
- **Post-build step: rename + zip.** Nuitka standalone outputs `build/displayoff.dist/`; the build scripts rename that to `build/displayoff/` (clean folder name matching the documented install layout) then `python -m zipfile -c build/displayoff-vX.Y.Z.zip displayoff/` packages it. SHA256SUMS.txt now hashes the zip (the actual release artifact) instead of a bare .exe.
- **`.github/workflows/release.yml` upload list switched from `build/displayoff.exe` to the glob `build/displayoff-v*.zip` + `build/SHA256SUMS.txt`.** Glob means the workflow doesn't need a parallel source of truth for the version string — build-release.sh derives the version from `__version__` in displayoff.py, the workflow just uploads whatever zip it produced.
- **CDN redirect-host smoke test URL bumped from `displayoff.exe` to `displayoff-${TAG}.zip`.** Same allowlist + same defensive purpose; just the asset path under the release URL changed.
- **README.md install instructions updated.** Option A now says "download zip, extract to `<install_dir>/`, end up with `<install_dir>/displayoff/displayoff.exe`" instead of "drop the .exe in `<install_dir>/`". Added a "Why a folder, not a single .exe?" section linking the Bearfoos.A!ml context.

### Folder-swap self-updater (displayoff.py)

The v1.7.13–v1.7.21 rename-dance atomically swapped `displayoff.exe` for `displayoff.exe.tmp` via `os.rename` and let the OS clean up the old file's locks via the spawned-then-exited-parent pattern. That trick doesn't work for a folder of ~150 locked DLLs — Windows allows directory rename with open files inside, but in-place file-by-file replacement of memory-mapped DLLs fails immediately. The unit of swap moves from a single file to the whole bundle directory, with two structural changes:

- **Staging artifacts live as SIBLINGS of `_INSTALL_DIR`,** not inside it: `<install_parent>/displayoff.new.zip` (downloaded), `<install_parent>/displayoff.new/` (extracted bundle), `<install_parent>/displayoff.new.staging/` (interrupted extracts), `<install_parent>/displayoff.old/` (pre-swap backup). Sibling placement is load-bearing because the dance renames `_INSTALL_DIR` itself — staging inside would carry the staging dirs along.
- **The child process performs the rename, not the parent.** The parent extracts the bundle to `displayoff.new/` then spawns `<new_bundle>/displayoff.exe --after-update-folder-swap` and exits (releasing the install-dir lock). The child — now running from `displayoff.new/` — renames the old install dir → `.old/`, renames its own `.new/` dir → canonical install dir name, re-resolves `_EXE_PATH` via `GetModuleFileNameW(NULL)` (Windows updates this on directory rename, unlike Nuitka's compile-time `__compiled__.original_argv0` which captures the launch path), and best-effort cleans up the `.old/` backup. The existing `Local\DisplayOff_UpdateChildReady` event handshake protocol is reused — child signals first, parent exits, child swaps.

### Runtime constant changes

- **`_UPDATE_EXE_NAME = "displayoff.exe"` → `_UPDATE_ZIP_SUFFIX = ".zip"`.** Asset matching shifts from exact-filename comparison to suffix matching; the dance picks the first `*.zip` asset on the release (preferring `displayoff-*.zip` if multiple zips are uploaded). The version-stamped zip name (`displayoff-v1.7.23.zip`) changes every release, so a stable-name match across versions isn't possible.
- **`_UPDATE_MIN_EXE_SIZE = 40_000_000` → `_UPDATE_MIN_ZIP_SIZE = 15_000_000`.** New floor matches the expected compressed zip size (deflate ratio ~0.5–0.6 against the ~52 MB standalone bundle gives ~25–35 MB; floor at 15 MB catches 200-OK HTML disguised-as-zip).
- **New constants for staging dir naming:** `_UPDATE_NEW_ZIP_SUFFIX = ".new.zip"`, `_UPDATE_NEW_DIR_SUFFIX = ".new"`, `_UPDATE_OLD_DIR_SUFFIX = ".old"`. The old `_UPDATE_TMP_SUFFIX` / `_UPDATE_OLD_SUFFIX` constants are removed (they were `.exe.tmp` / `.exe.old`).

### Runtime helper changes

- **`_extract_zip_bundle(zip_path, install_parent)`** — new helper. Extracts the zip via a staging dir to defeat Zip Slip (per-entry rejection of absolute paths, drive letters, `..` segments before extraction), verifies the zip contains a top-level `displayoff/` dir with `displayoff.exe`, and promotes the inner dir to `<install_parent>/displayoff.new/`.
- **`_re_resolve_exe_path_post_swap()`** — new helper. After the child renames `displayoff.new/` → `displayoff/`, the module-level `_EXE_PATH` and `_INSTALL_DIR` (resolved at module import) point at the stale `.new/` path. Calls `GetModuleFileNameW(NULL)` (which Windows updates to track the process's current image path post-rename) and reassigns the globals.
- **`_find_release_zip_asset(assets)`** — new helper. Returns `(zip_filename, zip_url)` for the first `.zip` asset in the release's assets dict, preferring `displayoff-*.zip` if multiple zips coexist.
- **`_execute_rename_dance(exe_url, exe_sha256, new_version)` → `_execute_rename_dance(zip_url, zip_sha256, new_version, zip_filename)`.** Wholesale rewrite: download zip → SHA256 → `_extract_zip_bundle` → delete zip → write relaunch-state with `old_install_dir` + `new_install_dir` → spawn child `--after-update-folder-swap` from `displayoff.new/displayoff.exe`. Returns a new `extract_failed` status for `_extract_zip_bundle` errors (bad zip, Zip Slip rejection, missing inner `displayoff/` directory).
- **`_write_update_relaunch_state(new_version)` → `_write_update_relaunch_state(new_version, old_install_dir, new_install_dir)`.** State JSON now records the dirs the child needs to rename.
- **`_recover_from_failed_update()` rewritten** for the new artifact set (`displayoff.new.zip`, `displayoff.new/`, `displayoff.new.staging/`, `displayoff.old/` siblings). Includes a realpath-based identity guard so a junction/symlink loop can't trick the cleanup into deleting the current install dir.
- **`main()` `--after-update` → `--after-update-folder-swap`** with the rename logic inline. The new branch signals the parent first (existing event protocol), reads + clears relaunch state, validates the discovered swap paths against the running process's `_INSTALL_DIR` (refuses to rename non-canonical layouts), performs the two renames with full rollback on failure (restore `.old` → canonical if the `.new` → canonical step fails), re-resolves `_EXE_PATH` via `GetModuleFileNameW`, best-effort deletes `.old/`, then falls through to normal tray startup. Mid-swap failures are non-destructive — if anything fails the user is left running v-new from `.new/` with a log warning rather than losing their install.
- **Freeze-mode comment block updated** to document `--standalone` mode paths alongside the existing `--onefile` and `.py` source paths. Strategy 0 (`__compiled__.original_argv0`) and Strategy 2 (`sys.argv[0]`) in `_resolve_on_disk_exe_path()` both work identically under standalone because Nuitka populates `original_argv0` the same way and there's no Temp extraction to confuse `sys.argv[0]`.

### Ancillary

- **`tray_promoter.py` docstring** updated to acknowledge `--standalone`'s `sys.executable == on-disk .exe` behavior (the v1.7.20 fix was specific to the onefile pattern where `sys.executable` is the temp-extracted python.exe). The `_EXE_PATH or sys.executable` fallback pattern stays correct under both modes.

## [1.7.21] — 2026-05-23

**Maintenance-mode exception** — one user-reported UX gap surfaced after v1.7.20's "final release" tag: when something held `ES_DISPLAY_REQUIRED` (PowerToys Awake's "Keep screen on", a fullscreen video player, presentation mode), the hotkey appeared to silently no-op. The blank attempt actually fired correctly, but the kernel's native idle-blank path respects display wake-locks by design — so the user saw "nothing happens" with no signal as to why.

### Added

- **Blocked-blank tray notification.** Before each blank attempt on the native path, `turn_off_monitors()` calls a new `_check_display_blocked()` helper which uses `CallNtPowerInformation(SystemExecutionState, ...)` to read the kernel's aggregate `EXECUTION_STATE` bitmask. If `ES_DISPLAY_REQUIRED` is set, a tray toast fires naming the most common culprit ("an app is keeping the display awake (e.g. PowerToys Awake)"). The blank attempt still runs afterward — the check is advisory, not a suppression gate. Skipped on the legacy `SC_MONITORPOWER` path because that path bypasses the wake-lock on most hardware; warning there would scare the user about a state that doesn't actually affect them.
- **`warn_on_blocked_blank` config key (default: True).** New Settings checkbox: "Warn when something is keeping the display awake". Stored in `displayoff_config.json`; backfilled by `load_config` for existing configs from older versions.

### Why `CallNtPowerInformation` rather than `powercfg /requests`

`powercfg /requests` would give us the responsible process names (e.g. `[PROCESS] \Device\HarddiskVolumeX\Program Files\PowerToys\PowerToys.Awake.exe`) but it requires administrator privileges. Displayoff intentionally runs under the user's standard token — adding elevation just to show a tooltip would mangle the tray-attach UX and trip UAC on every launch. `CallNtPowerInformation(SystemExecutionState)` is the unprivileged equivalent for the `SetThreadExecutionState` side of the API, which is the side PowerToys Awake uses. The trade-off is that we lose process names and we miss any wake-locks set via `PoCreatePowerRequest` (rare; mostly old media players). The toast text generalizes to "an app is keeping the display awake (e.g. PowerToys Awake)" rather than naming a specific process, which is honest about what we can and can't see.

### Mechanism details

- New `powrprof.dll` binding alongside the existing `kernel32` / `user32` / `advapi32` block, with try-import fallback so a stripped `powrprof.dll` on a hardened Win image leaves the helper as a silent no-op rather than crashing the tray.
- Module-level `_tray_icon_ref` (set inside `run_tray()` immediately after `pystray.Icon(...)` constructs) so `turn_off_monitors()` can fire `icon.notify()` from the hotkey / idle-watcher / icon-double-click paths without threading the `icon` reference through every call site. None-guarded — pre-tray paths (`--off` CLI, etc.) skip the toast cleanly.
- `_check_display_blocked()` fails quiet on every error path: missing binding, `OSError`, non-zero NTSTATUS. The blank itself is the contract; the toast is a hint. A debug-level log entry records the failure mode for forensic purposes.

### Settings dialog

- New checkbox added to `_build_options_section`: "Warn when something is keeping the display awake". One new builder argument, one new row index. Footer row bumped 7 → 8 to make room. The dialog's other options remain in the same visual order.

### Verifier-round convergent (mid-round)

A 6-agent verifier pass (3 topics × Sonnet + Opus pair-by-topic) caught four issues that were rolled into v1.7.21 before tag:

- **`build-exe.bat` VERSION pin was still `1.7.20`** — local builds (Nate's daily-driver path; CI uses `build-release.sh` which scrapes `__version__` from source) would have stamped a v1.7.21-source binary with `--product-version=1.7.20.0` / `--file-version=1.7.20.0`. Bumped to `1.7.21`. The `REM Verify:` comment line was also updated to `1.7.21`. **Convergence:** T2-Opus HIGH.
- **Idle-watcher + rapid-hotkey toast spam** — both T2-Sonnet and T2-Opus flagged that the idle watcher refires `turn_off_monitors()` every `_IDLE_REFIRE_COOLDOWN_SECS` (60 s) while the user stays idle, and that the original implementation would toast on every refire. Replaced the time-only rate-limit with a state-transition logic: toast IMMEDIATELY on a fresh `not-blocked → blocked` transition (so the user toggling PT Awake off-and-back-on re-warns immediately), suppress back-to-back `blocked` detections within a 5-minute window, reset state on any `not-blocked` read. The blank attempt still fires on every call — the rate-limit only gates the notification, not the action. **Convergence:** T2-Sonnet LOW + T2-Opus MEDIUM.
- **Stale row-layout comment block above `_build_header`** — the comment documented the old 3-option / footer-at-row-7 layout, and silently misled a future contributor adding a fifth row. Updated to show row 6 = warn checkbox, row 7 = idle spinbox, row 8 = footer. **Convergence:** T1-Opus LOW.
- **CHANGELOG / project CLAUDE.md referenced a `setup_tray()` symbol that doesn't exist** — the ref-stash code lives inline in `run_tray()`, not a separate `setup_tray()`. Future readers would have grepped for a non-existent symbol. **Convergence:** T2-Opus LOW.

Verifier output landed in `~/.claude/state/verification-log.jsonl`. The blue-team-only paths (Settings dialog Cancel doesn't revert in-memory cfg, hotkey lock-edge race window, toast-text doesn't name non-PT-Awake culprits, syscall inside lock window) were intentionally left as-is — each one matches existing v1.7.20 convention or a documented trade-off.

### Verifier-round convergent (Round 2 doc-only)

A second 6-agent verifier round (after the mid-round hardening above) returned `[VERIFY-CLEAN]` from every agent. Two pair-convergent LOW observations were addressed as doc-only follow-ups:

- **T3 pair (Sonnet + Opus) convergent:** the `powrprof.dll` binding comment at the top of the file claimed `CallNtPowerInformation(SystemExecutionState)` was a per-session aggregate. T3-Sonnet fetched Microsoft Learn live; the docs name the value "system execution state buffer" without explicit per-session qualifier, and `SetThreadExecutionState` is documented as a machine-global mechanism — together those put the system-wide interpretation on solid ground, even if MS Learn doesn't state the cross-session corollary in so many words. Comment updated to say SYSTEM-WIDE and the Fast-User-Switching false-positive case (User B's wake-lock causing User A's blocked-toast under FUS) is now called out inline so future readers don't expect session-scoping.
- **T2 pair (Sonnet + Opus) convergent (post-fix residual):** the original toast-spam was rated MEDIUM by T2-Opus and LOW by T2-Sonnet in Round 1; both drove the mid-round state-transition rate-limit fix. After that fix landed, both agents independently re-evaluated the residual 5-min `_WARN_COOLDOWN_SECS` window — which still permits ~12 toasts/hour for a user who has idle-blank set AND PT Awake intentionally on (e.g. watching a long video) — and called the residual a defensible trade-off rather than a bug. `warn_on_blocked_blank` is the user-facing escape hatch. Added an inline comment near `_WARN_COOLDOWN_SECS` documenting the acceptance and pointing to the Settings checkbox as the user-visible workaround.

Neither change touches behavior. Both are anchored next to the relevant code so a future maintainer doesn't re-derive the trade-off from scratch.

## [1.7.20] — 2026-05-22

**Final planned release. Maintenance-only mode after this.** Bundle of every deferred and out-of-scope item from the v1.7.17 / v1.7.18 / v1.7.19 train: 14 items grouped as resolver hardening (A1–A3), UX + observability (B4–B6), build + release hygiene (C7–C12), cosmetic (D13–D14).

### Fixed — A. Resolver hardening

- **`_path_under_protected()` helper rejects `%LOCALAPPDATA%\Microsoft\WindowsApps\`** (Strategy 0/1/2 in `_resolve_on_disk_exe_path()`). The Store-stub directory survived `_path_under_temp` because it isn't a TEMP dir, but a malicious argv[0] pointing inside it would steer the rename-dance at a reparse-point stub the Store ACL forbids writes to. Filtering up front gives a clean rejection rather than a partway-through-the-dance failure. The "known gap" comment block in v1.7.19's Strategy 0 docstring is replaced with a closure note. Symmetric resolution (realpath + normcase) with `_path_under_temp` so junctions / symlinks / 8.3 short names compare equal.
- **`_download_url_allowed()` rejects path-traversal segments.** After `urlsplit`, the URL path is normalized via `os.path.normpath` (with backslash → forward-slash rewrite for portability on Windows builds) and rejected if it starts with `/..` or contains `/../`. The github.com branch's `startswith("/itsnateai/displayoff/")` check is no longer satisfiable via `/itsnateai/displayoff/../other-repo/...`. SHA256 verification is still the actual integrity boundary, but the allowlist's prefix-check should not be permissive against malformed inputs.
- **`_migrate_legacy_data()` cross-device-atomic two-step.** Replaces `shutil.move(src, dst)` (which collapses to a non-atomic `shutil.copy2` + `os.remove` when src/dst are on different volumes) with an explicit `shutil.copy2 → _sha256_file equality check → os.remove(src)` chain. A crash between copy and unlink no longer leaves bytes in both locations or partial-bytes in dst (the SHA256 check detects the partial copy and removes the dst so the next launch re-attempts cleanly). Only matters when `%APPDATA%` and the install dir live on different volumes (portable USB install, NTFS-junctioned roaming profile).

### Fixed — B. UX + observability

- **`--diagnose-paths` exit code now signals resolver outcome.** Was always `0` regardless of whether the resolver succeeded. Now: `sys.exit(1 if _is_frozen() and not _EXE_PATH else 0)` — exit-0 = healthy (or .py source mode), exit-1 = frozen build with broken path resolution. A health script polling for updater readiness gets a useful signal. CONTRACT CHANGE for any caller currently parsing exit codes.
- **Rename-dance child-ready handshake replaces 300 ms parent-sleep.** New named event `Local\DisplayOff_UpdateChildReady`: parent CreateEventWs (manual-reset, initial-state=False) BEFORE spawning the `--after-update` child, then waits via `WaitForSingleObject(handle, 5000)` instead of the v1.7.13 fixed `time.sleep(0.3)`. Child OpenEventWs + SetEvents as the very first act of `--after-update` (before reading state, before mutex acquire — signaling before mutex avoids the parent-must-exit-first deadlock; child's signal attests "Python interpreter is alive" which is what the parent's wait is actually for). 5 s timeout is a generous ceiling; if the child genuinely never starts the parent falls through to `os._exit(0)` anyway. Fallback to legacy 0.3 s sleep when `CreateEventW` returns NULL.
- **`_themed_dialog` sticky `minsize()` floor.** v1.7.16's button-row width fix used one-shot `geometry()` which doesn't survive Tk re-solves (font cache refresh, DPI change, grab-set side effects). v1.7.20 adds `dlg.minsize(w, h)` after the `geometry()` call so the constraint persists for the dialog's lifetime.

### Fixed — C. Build + release hygiene

- **`release.yml` permissions tightened to least-privilege.** Workflow root: `contents: read`. Job level on `build-windows-exe`: `contents: write` (only the job that contains the `softprops/action-gh-release` upload step gets the write token). Step-level `permissions:` is not a supported GitHub Actions key — see the verifier-convergent F1 fix below for the bug that the first-pass introduced and how it was caught.
- **Post-upload CDN redirect-host smoke test in `release.yml`.** New CI step downloads the uploaded asset URL via `curl -sIL`, captures the final `url_effective` after redirects, and asserts the final host matches one of `release-assets.githubusercontent.com` / `objects.githubusercontent.com` / `objects-origin.githubusercontent.com`. If GitHub silently swaps CDN hosts again (the way they did mid-2025 when `release-assets.*` landed and broke v1.7.13–v1.7.15), the next release CI run fails BEFORE the broken build ships into users' update flow.
- **`objects-origin.githubusercontent.com` added to in-app allowlist** — forward-compat defense for the same CDN-migration risk #8 catches at the CI layer. Microsoft's storage layer occasionally serves the origin host directly in long redirect chains; covering the host name now means the in-app updater stays working if GitHub points us at it later.
- **`_UPDATE_MIN_EXE_SIZE = 1 MB` → `40 MB`.** Real `.exe` is ~55 MB (Nuitka 4.1.1 onefile + `--onefile-no-compression` workaround). 1 MB floor only caught 200-OK HTML error pages; 40 MB catches mis-shipped stub builds too. Comment includes the loosen-if note for a future Nuitka zstd-compression unlock that would shrink the .exe to ~20 MB.
- **`build-exe.bat` Nuitka 4.1.1 preflight guard.** New `python -m nuitka --version | findstr /B "4.1.1"` check at the top of the .bat that fails fast if the active venv has a different Nuitka pinned. CI is already pinned via `pip install nuitka==4.1.1` in release.yml; local builds previously relied on whatever was installed. Mismatch silently introduced behavior drift (the py3.14 zstd compression bug we work around might be fixed in a newer Nuitka — at which point `--onefile-no-compression` should be DROPPED, not kept, but only after an explicit human decision).
- **`build-exe.bat` Nuitka-version timeline entries refreshed for v1.7.16 / v1.7.17 / v1.7.18 / v1.7.19 / v1.7.20.** Confirms Nuitka 4.1.1 is still the workspace pin across the entire v1.7.16–v1.7.20 release window.

### Fixed — D. Cosmetic

- **`DwmSetWindowAttribute` bound at module load instead of every `_apply_dark_titlebar` call.** Per the workspace convention "all bindings live in the `if sys.platform == "win32":` block at the top of the file with explicit `argtypes`/`restype`". `_apply_dark_titlebar` had been an exception since v1.7.0 because `dwmapi.dll` is missing on pre-Win10 1607 builds; v1.7.20 wraps the `ctypes.WinDLL("dwmapi")` load in `try/OSError` so those builds gracefully no-op (the function checks `DwmSetWindowAttribute is None` and early-returns).
- **`tray_promoter.py:121` docstring fix.** The template-portable example previously read `current_exe_path=sys.executable` — under Nuitka onefile freeze, `sys.executable` is the per-launch TEMP-extracted python.exe (NOT the on-disk .exe), so a freeze-mode template-copier would tag the wrong path. Corrected to `current_exe_path=_EXE_PATH or sys.executable`. The actual call site in `displayoff.py`'s `run_tray()` (search for `sweep_stale_entries(our_exe_name="displayoff.exe"`) is already correct — it's inside an `if _is_frozen() and _EXE_PATH:` guard so `_EXE_PATH` cannot be None at the call. Only the docstring example needed the fix. (Line-number references are intentionally avoided here since they drift across releases — search by symbol name instead.)

### Fixed — Verifier-round convergent (mid-round)

A 6-agent verifier round (3 topics × Sonnet + Opus pair-by-topic) caught four issues that were rolled into v1.7.20 before tag:

- **CRITICAL — `release.yml` step-level `permissions:` is silently ignored.** GitHub Actions only supports `permissions:` at workflow root and job level; the v1.7.20 first-pass put it on the softprops upload step where it would be a no-op, leaving the upload with a read-only `GITHUB_TOKEN` and failing every release push with a 403. T1-Sonnet + T1-Opus convergent. **Fix**: moved `permissions: contents: write` to the `build-windows-exe` job level. Workflow root stays `contents: read`.
- **CRITICAL — `_download_url_allowed` URL-traversal check was a logical no-op against the documented attack.** `os.path.normpath` COLLAPSES `..` segments by design, so the `"/../" in normalized_path` check is dead code that can never match a real traversal URL. Meanwhile the github.com branch's prefix check used `parts.path` (raw, un-normalized), which still contains the literal `/../` prefix. A URL like `https://github.com/itsnateai/displayoff/../other-repo/release.exe` would pass: raw-path startswith fires True, normalized-path's `/../` check fires False. The exact bypass v1.7.20's first-pass CHANGELOG claimed to close was still satisfiable. T1-Sonnet + T1-Opus + T3-Opus convergent (3-of-6 critical). **Fix**: two-layer defense — (1) reject any `..` segment in the raw path BEFORE normalization, (2) do the github.com prefix check against the NORMALIZED path (with trailing-slash padding to keep the bare repo root acceptable). Regression-tested with 12 cases including the F2 attack URL.
- **HIGH — `_update_child_ready_handle` ABA leak on repeat update attempt.** If the user cancels an update, dismisses the error, and clicks "Install now" again in the same session, the previous `_update_child_ready_handle` (if non-None from a prior successful flow) would be silently overwritten by the new `CreateEventW` call, leaking the kernel handle. T3-Sonnet HIGH. **Fix**: pre-create `CloseHandle` + zero guard in `_execute_rename_dance` before assigning a fresh handle.
- **HIGH — `_migrate_legacy_data` TOCTOU on src hash.** The first-pass A3 fix hashed `src` AFTER `shutil.copy2`. The files being migrated include `displayoff.log` (an active RotatingFileHandler target in the same process). A post-copy hash could read bytes appended between the copy and the hash, producing a spurious mismatch that triggered a partial-copy cleanup loop indefinitely. T3-Sonnet HIGH. **Fix**: hash `src` BEFORE `shutil.copy2`, compare against the post-copy `dst` hash. Pre-copy hash is immune to post-copy modification.

Plus two LOW observability nits from T1-Sonnet:
- Strategy 2 now logs its rejection reason (symmetric with Strategies 0 and 1).
- `_update_child_ready_handle` is zeroed after `CloseHandle` in `_run_rename_dance_flow._worker` (cosmetic — `os._exit(0)` follows, but consistency with the H1 pre-create guard matters if a future refactor replaces `os._exit`).

The T2 pair (Gap-audit) reported ALL-14-COMPLETE; they did surface-check rather than semantic-trace, which is why the URL-traversal bypass slipped past T2 but caught by the T1 + T3 pairs. T2-Opus called out two improvements over spec (C8 host-list is 3-host strict; B5 Popen-failure handle cleanup) — both intentional improvements, kept.

### Notes — maintenance mode

After v1.7.20 ships, displayoff enters maintenance-only mode. The path-resolution bug class that drove v1.7.13–v1.7.19 is closed; the rename-dance has been proven to work end-to-end; the resolver has four layered strategies + the WindowsApps filter + the TEMP filter; the updater allowlist covers the three known GitHub CDN hosts + has CI-level CDN-change detection; and `--diagnose-paths` makes any future failure observable.

Future failures, if they emerge:

1. **Observable** via `path-resolver:` log lines or `--diagnose-paths` output. Both fire on every startup post-v1.7.19.
2. **Recoverable** via manual install from the releases page. The SHA256SUMS.txt manifest is canonical.
3. **Reproducible** via the resolver candidates dict (sys.executable / sys.argv[0] / NUITKA_ONEFILE_PARENT / `__compiled__.original_argv0` / `__compiled__.containing_dir`).

A future session should NOT re-open displayoff unless one of those three observability properties breaks. The maintenance bar is "if a user reports a bug AND it's not in the v1.7.20 known-gap set, hotfix it. Otherwise, don't touch."

### Notes — known gaps remaining (intentional)

These are intentionally unfixed in v1.7.20 — either by-design or because the risk/reward doesn't justify a code change in maintenance mode:

- **Pre-v3 config migration**: this version's tray-app pattern doesn't have a pre-v3 cohort to migrate; the closest equivalent is the v1.7.8 → v1.7.9 `_HERE` → `%APPDATA%\displayoff` move, which is already handled.
- **`shell32.SHGetKnownFolderPath` instead of `%APPDATA%` env var lookup**: env-var lookup works for every Windows install discoverable; the KNOWNFOLDER API is more robust on locked-down policy edges but the failure mode is already handled (fallback to `_HERE`).
- **`ctypes.windll.dwmapi` still vs `_dwmapi` lookup hot-path**: D13 moves the binding to module load; the `DwmSetWindowAttribute` call itself stays unchanged. No further optimization needed.
- **No `--quit-then-update` CLI flag**: the rename-dance already handles in-process updates correctly; an out-of-process update CLI flag would be a new feature, not a maintenance fix.

## [1.7.19] — 2026-05-22

Path-resolver hotfix + diagnostic-observability fix triggered by the v1.7.17 → v1.7.18 inaugural in-the-wild rename-dance attempt. The dance failed: pid 18996 had `_EXE_PATH` resolved to `<%TEMP%>\onefile_18996_.../python.exe` (Strategy 3 — the broken last-resort), and the rename target was the temp `python.exe` rather than the on-disk `displayoff.exe`. Worse, pid 18996's resolver candidates **were never written to the log file** — v1.7.17/v1.7.18 buffered them through `_MIGRATION_LOG` which only flushes the prefix `data-dir migration:` when the resolver also did data-dir migration. Pid 18996 was already migrated, so its resolver candidates stayed in-memory only. v1.7.19 fixes both the silent failure AND the diagnostic gap.

### Fixed

- **New Strategy 0 in `_resolve_on_disk_exe_path()`: `__compiled__.original_argv0`.** Nuitka sets this on the compiled module to the absolute path the user invoked — available even after the bootstrap parent process exits (which is one plausible Strategy 1 failure mode). In every resolver-candidate log line collected to date, `__compiled__.original_argv0` pointed at the correct on-disk `.exe` path, even when `sys.executable` was the temp-extracted python.exe and `NUITKA_ONEFILE_PARENT` was set but Strategy 1's `QueryFullProcessImageNameW` evidently returned a stale or wrong path. Tried before Strategy 1 because it doesn't depend on a live parent process. Same hardening triple as Strategies 1 and 2: `.exe` extension, `os.path.isfile`, not under any TEMP-like dir (`_path_under_temp` helper from v1.7.18).
- **Path-resolver diagnostics now log on EVERY startup, not gated on data-dir migration.** New `_RESOLVER_LOG` module-level buffer, separate from `_MIGRATION_LOG`. Resolver populates it at module-import time; `main()` flushes it with prefix `path-resolver:` (not the misleading `data-dir migration:` prefix v1.7.18 used). Both buffers get the same stderr-fallback drain when `_DATA_DIR` is unwritable. This means a future "Install now appeared to succeed but the version didn't change" report will always carry the candidates + winning-strategy line, so root-cause is one log scrape away.
- **`displayoff.exe --diagnose-paths` CLI flag.** Prints `__version__`, `frozen` state, `_EXE_PATH` (the resolved on-disk path), and every line from `_RESOLVER_LOG` to stdout, then exits cleanly. Runs BEFORE `_ensure_data_dir()` / `basicConfig` so the flag works even when `%APPDATA%` is unwritable or the log file is locked. Anyone reporting a future dance failure can paste the output directly into a GitHub issue without needing to attach the log file.
- **Strategy 3 WARNING text updated to point users at `--diagnose-paths`.** Previous WARNING said "report displayoff.log"; new WARNING says "run `displayoff.exe --diagnose-paths` and report the output". The flag is the one-shot artifact a non-developer can produce.
- **Docstring + Strategy 3 comment updated** to reflect the four-strategy chain (was "BOTH primary strategies failed" — now "all three primary strategies failed") and to document the v1.7.18 pid 18996 incident as the empirical motivator for Strategy 0.

### Notes — what v1.7.19 does NOT fix

- **The v1.7.17 → v1.7.18 dance still fails** for anyone still on v1.7.17 — v1.7.19's hardening only helps the v1.7.19 binary and later. v1.7.17 users have to download v1.7.18 (or v1.7.19+) manually from the releases page and replace `displayoff.exe` themselves. There is no automatic recovery path for the v1.7.17 binary's broken Strategy 3.
- **The 300 ms parent-`os._exit` vs child `_acquire_single_instance` race** is still on the backlog. Not the cause of the v1.7.18 dance failure (that was the path resolver), but worth fixing before another in-the-wild upgrade cycle.
- **Strategy 0 hardening triple does NOT exclude every protected directory.** The `.exe` extension + `os.path.isfile` + `_path_under_temp` negation correctly excludes TEMP scratch files and missing paths, but `%LOCALAPPDATA%\Microsoft\WindowsApps\` Store stubs would survive the filter. Theoretical attack vector: an attacker who can `CreateProcessW` displayoff.exe with `lpCommandLine[0]` set to a Store stub path would redirect the rename-dance at that stub. SHA256 verification against the release manifest bounds *what bytes* get written (a real `displayoff.exe` build), but not *where*. Same trust posture as Strategy 1 — the attacker needs local code-exec to spawn us with that argv anyway. Logged in the Strategy 0 comment block; mitigation deferred to v1.7.20 (add `WindowsApps` to the filter, possibly via a new `_path_under_protected` helper that's distinct from `_path_under_temp`).
- **`--diagnose-paths` exit code is always 0**, even when the resolver returned None under freeze (the failure case the flag was designed to diagnose). Semantically defensible — the flag's job is to dump state, not to encode the resolver's success — but a script polling for resolver health can't distinguish "diagnose ran cleanly, resolver succeeded" from "diagnose ran cleanly, resolver failed". v1.7.20 candidate: `sys.exit(1 if _is_frozen() and not _EXE_PATH else 0)`.
- **The remaining v1.7.18-deferred backlog** (size floor, `release.yml` permissions, minsize sticky, Nuitka pin guard, tray_promoter docstring, `_download_url_allowed` URL parser hardening, `_migrate_legacy_data` cross-device atomicity, `_DwmSetWindowAttribute` rebinding) is deliberately held back so v1.7.19's changelog is narrowly about "the dance failed; here's what we did to make it observable and recoverable". Bundle the polish into v1.7.20.

### Notes — testing the v1.7.18 → v1.7.19 dance

This release is the SECOND in-the-wild dance vehicle. The v1.7.18 binary's resolver has Strategies 1, 2, 3 — no Strategy 0 — but its Strategy 1 worked at startup on the local install (verified via the resolver-candidates log line emitted at first run from `proggy\Tools\displayoff.exe`). So Strategy 1 should win for the v1.7.18 → v1.7.19 dance from a fresh-install v1.7.18, and the dance should complete successfully.

If it fails: now we have `displayoff.exe --diagnose-paths` on the post-recovery v1.7.19 to dump the resolver state, and the `path-resolver:` log lines from v1.7.19 onward will record every future startup's resolver outcome. We're no longer flying blind on the resolver question.

## [1.7.18] — 2026-05-22

Post-tag hardening of the v1.7.17 path resolver fallback paths, surfaced by the v1.7.17 8-agent verifier round. The v1.7.17 binary works empirically (Strategy 1 — `NUITKA_ONEFILE_PARENT` + `QueryFullProcessImageNameW` — always wins under real Nuitka onefile usage, verified in `displayoff.log`), but the fallback Strategy 2/3 paths were soft against several edge cases. v1.7.18 closes those gaps and is also the **inaugural in-the-wild rename-dance exercise**: a v1.7.17 user clicking Settings → Check for updates → Install now against this release is the actual first end-to-end test of the dance.

### Fixed

- **`_path_under_temp(path)` helper — robust multi-env-var TEMP detection.** v1.7.17's Strategy 2 used a single `%TEMP%` string-prefix check, which was fragile against (a) `%TEMP%` unset in restricted accounts or sandboxed services, (b) 8.3 short-name vs long-name resolution drift, and (c) junctions/symlinks resolving differently. New helper resolves both sides via `os.path.realpath` + `os.path.normcase` and checks across `TEMP`, `TMP`, and `LOCALAPPDATA\Temp`. Used by Strategies 1 and 2 in the resolver. Flagged convergent by v1.7.17 T2-Sonnet (CRITICAL) + T2-Opus.
- **Strategy 1 now rejects results under TEMP-like dirs, missing-on-disk paths, and non-`.exe` extensions.** Previously the strategy returned whatever `QueryFullProcessImageNameW` reported, gated only by `.endswith(".exe")`. If a future Nuitka version spawned the bootstrap via a chain where the parent process itself was an extracted-temp `python.exe`, Strategy 1 would silently re-introduce the v1.7.13 bug class. Defense layered via the new `_path_under_temp` helper + `os.path.isfile`. Flagged by v1.7.17 T2-Opus.
- **Strategy 2 adds `os.path.isfile(argv0)` + multi-TEMP-env check.** A synthetic `sys.argv[0]` (relative path, network path, or path to a deleted .exe — possible if a caller invokes `displayoff.exe` with a custom argv from a wrapper script) no longer silently propagates to the rename-dance, autostart `.lnk`, and tray promoter. Flagged convergent by v1.7.17 T2-Sonnet (CRITICAL).
- **Strategy 3 returns `None` (was `sys.executable`).** When both primary strategies fail, the resolver now signals "no valid path" rather than handing downstream consumers the same broken value the v1.7.13 bug used. The rename-dance, autostart `.lnk` creator, and `tray_promoter` all check `if _EXE_PATH and ...` before acting, so `None` makes those paths skip cleanly instead of mis-targeting. The v1.7.13–v1.7.16 incident proved "WARNING log + wrong path" is worse than "no path" — users don't read warnings, but they DO notice a feature silently doing nothing. Flagged HIGH by v1.7.17 T3-Opus.
- **`_autostart_target_pythonw`: `assert` → `if/raise RuntimeError`.** v1.7.17 added an `assert not _is_frozen()` defense against a future refactor accidentally invoking this source-mode-only function under freeze. But `assert` compiles to a no-op under `python -O`, which would silently revive the v1.7.13 `.lnk`-points-at-temp-path bug. v1.7.18 promotes the guard to an unconditional `raise RuntimeError`, satisfying workspace rule 12 ("fail loud"). Flagged HIGH by v1.7.17 T3-Opus.
- **`release-notes.md` private-path scrub.** v1.7.17's public release notes referenced `proggy\Tools\displayoff.exe` (a personal install path) in the empirical-proof section. v1.7.18 scrubs to `<%TEMP%>` for the temp-path example and removes the specific install path entirely. The live v1.7.17 release notes were also updated via `gh release edit v1.7.17 --notes-file release-notes.md`. Flagged by v1.7.17 T3-Sonnet per workspace `feedback_no_personal_names_in_public_repos` policy.

### Notes — what's still on the v1.7.19+ backlog

Items the v1.7.17 8-agent round surfaced that didn't make v1.7.18's cut:

- `_UPDATE_MIN_EXE_SIZE = 1 MB` → tighter floor (real .exe is ~52 MB).
- `release.yml` permissions tightening (`contents: read` at workflow root, `write` only on the upload step).
- `release.yml` post-upload redirect-host smoke test (proactive future-CDN-change detection).
- `objects-origin.githubusercontent.com` defensive allowlist add (forward-compat for future CDN host migrations).
- `_themed_dialog` `dlg.minsize()` sticky-floor (currently one-shot `geometry()`).
- 300 ms parent-`os._exit` vs child-`_acquire_single_instance` race in the rename-dance child relaunch.
- `_download_url_allowed` URL parser hardening (`urlsplit` doesn't normalize `..` traversal; SHA256 is the integrity boundary so any exploit is bounded but the false-positive surface is wider than ideal).
- `_migrate_legacy_data` `shutil.move` not atomic cross-device (only matters if `%APPDATA%` is on a different volume than the install dir).
- `_DwmSetWindowAttribute` re-bound on every `_apply_dark_titlebar` call (convention violation; cosmetic).
- `tray_promoter.py:121` docstring example still shows `current_exe_path=sys.executable` (real call site in `displayoff.py`'s `run_tray()` is correct; only the docstring will mislead template-copiers).
- `build-exe.bat` Nuitka pin guard (CI is already pinned via `pip install nuitka==4.1.1`; local builds aren't).

### Notes — the inaugural in-the-wild rename-dance exercise

v1.7.17 was the first release the dance *could* work against, but no user had exercised it end-to-end yet (the manual-install upgrade path was the only documented one). v1.7.18 is the first release where the v1.7.17 cohort can use Settings → Check for updates → Install now against a real new release and complete the full dance — download `.exe` to `.tmp` → SHA verify → atomic rename `displayoff.exe` → `displayoff.exe.old` → atomic rename `displayoff.exe.tmp` → `displayoff.exe` → spawn `displayoff.exe --after-update` → child cleans up `.old` and the v1.7.18 tray icon appears.

If anything in this chain breaks for a v1.7.17 user, the failure mode is graceful (the dance leaves either the original `.exe` or the `.old` intact — both are recoverable via manual rename) but the symptom will be "Install now appeared to succeed but the version didn't change". Capture `displayoff.log` from any such report and a v1.7.19 hotfix lands the same day.

## [1.7.17] — 2026-05-22

The actual fix for the rename-dance, tray promoter, and autostart `.lnk` — each of which has been **structurally broken under freeze since v1.7.13**. v1.7.13's freeze pass added a comment block claiming `sys.executable` returns the on-disk `displayoff.exe` under Nuitka onefile (the PyInstaller-onefile behavior). That assumption is empirically false on Nuitka 4.1.1: `sys.executable` returns the per-launch temp-extracted python.exe (e.g., `C:\Users\<user>\AppData\Local\Temp\onefile_<pid>_<rand>_<hash>\python.exe`), not the on-disk .exe. v1.7.16's release notes claimed the dance "should work end-to-end this time" — also false; the v1.7.16 URL-allowlist fix made the network step pass but the rename targeted the wrong directory. v1.7.17 ships the path-resolution fix that finally makes the dance work.

### Fixed

- **`_resolve_on_disk_exe_path()` — corrected on-disk .exe path resolver under Nuitka onefile.** Layered fallback chain that no longer trusts `sys.executable`:
  1. **`NUITKA_ONEFILE_PARENT` + `QueryFullProcessImageNameW(parent_pid)`** (primary). Nuitka sets `NUITKA_ONEFILE_PARENT` in the child process's env to the bootstrap (parent) process's PID. The bootstrap IS the on-disk `displayoff.exe`. `QueryFullProcessImageNameW` returns the kernel-tracked image path of any open process handle — ground truth, immune to argv tampering by upstream parents. ctypes bindings inline in the helper (self-contained because the file's main win32 bindings block is defined later in the module init order) with explicit `argtypes`/`restype` matching the file's pointer-width discipline.
  2. **`os.path.abspath(sys.argv[0])`** if it ends in `.exe` and is NOT under `%TEMP%`. Per Nuitka docs `sys.argv[0]` is the original onefile binary path. Used as fallback in case `NUITKA_ONEFILE_PARENT` is unset (older Nuitka, or a future Nuitka that changes its env contract) or `OpenProcess` fails (Defender / EDR blocking process queries).
  3. **`sys.executable`** with a WARNING log (last resort). If we land here, both primary strategies failed and the rename-dance / autostart / tray promoter will mis-target — but the WARNING points the user at the issue tracker so we can patch in v1.7.18.

   Every candidate value (`sys.executable`, `sys.argv[0]`, `NUITKA_ONEFILE_PARENT`, `__compiled__.original_argv0`, `__compiled__.containing_dir`) is logged to `displayoff.log` at module import so future verifier rounds can read the empirical answer rather than re-derive it. Under .py source mode the resolver returns `None` (no freeze, no rename-dance applicable) and `_INSTALL_DIR` falls back to the script directory as before.

- **`_EXE_PATH` and `_INSTALL_DIR` rewired through the resolver.** Every downstream call site picks up the corrected path automatically:
  - `_autostart_target()` — the Startup-folder `.lnk` now points at the persistent on-disk `displayoff.exe` instead of a per-launch temp path. v1.7.13 → v1.7.16 users who had autostart enabled would see "Stale startup shortcut" in `displayoff.log` every relaunch as the symptom; v1.7.17 next-Save (Settings → Autostart → toggle off + on) rewrites the `.lnk` correctly.
  - `_execute_rename_dance()` — downloads `displayoff.exe.tmp` into `_INSTALL_DIR` (the on-disk install dir), atomic-renames `displayoff.exe` → `displayoff.exe.old` and `displayoff.exe.tmp` → `displayoff.exe`, then spawns `displayoff.exe --after-update` from the persistent path. v1.7.13 → v1.7.16 attempts would download to + rename inside the temp extraction dir and never touch the actual installation — the on-disk binary stayed at the old version no matter how many times "Install now" was clicked.
  - `tray_promoter.promote_in_background(exe_path=...)` — receives the on-disk `displayoff.exe` path so it matches Win11's `NotifyIconSettings\<hash>\ExecutablePath` (which the kernel correctly records for the on-disk .exe via `CreateProcessW` attribution). Match succeeds → `IsPromoted=1` is written → tray icon stays visible across Explorer restarts and login cycles. v1.7.13 → v1.7.16 users would see "tray-icon promotion timed out — entry for 'Display Off' not found in registry" repeatedly in `displayoff.log` and have to manually flip Settings ▸ Personalization ▸ Taskbar ▸ Other system tray icons every install.

- **`_PING_FIRED_THIS_PROCESS` is now lock-guarded** via `_PING_GATE_LOCK` + `_try_claim_ping_gate()` / `_release_ping_gate()` claim-then-fire-then-release pattern. v1.7.15 introduced the in-process dedupe as a bare module-level boolean — flagged by the v1.7.16 8-agent verifier round as a violation of the workspace's "no GIL-only assumptions on shared state" discipline. The new pattern claims the gate atomically up front (so two simultaneous spawns of `_frozen_promote_ping` — today there's only one, defended against future refactors — cannot both pass the check) and releases it only on `icon.notify` failure so a retry can still fire on next launch. Successful notify keeps the gate claimed for the lifetime of the process.

- **`_themed_dialog` chrome margin is DPI-relative** (`dlg.winfo_pixels("0.4i")` ≈ 38 px at 100% DPI, scales correctly at 125%/150%/175%/200%). v1.7.16's hardcoded `chrome_margin = 40` was a fixed-pixel budget that under high-DPI scaling could leave the button row clipped on the right edge — the original problem v1.7.16 had just closed at 100% DPI. The 0.4" choice keeps the 100% DPI behavior identical to v1.7.16 (≈38 vs 40 px is below the practical threshold for layout drift) while scaling correctly above. Flagged by the v1.7.16 8-agent verifier convergent finding.

- **`_recover_from_failed_update` preserves manual rollback artifacts.** v1.7.13 → v1.7.16 unconditionally deleted `displayoff.exe.old` on every launch, which was hostile if a user had manually backed up the current install (e.g., `copy displayoff.exe displayoff.exe.old` after install — NTFS bumps `.old`'s mtime to "now" while current's mtime stays at the original rename-dance time). v1.7.17 compares mtimes: if `.old` is newer than the current `.exe`, the cleanup is skipped and the artifact stays put. The `.tmp` cleanup is unchanged (untrusted download bytes; always clean). v1.7.17 also fixes a related edge case caught by the verifier round: if `os.path.getmtime(_EXE_PATH)` raises (e.g., crash mid-rename left the install in pieces), we now PRESERVE `.old` rather than fall through to delete — destroying the only good copy was the prior behavior's hostile failure mode. Flagged by the v1.7.16 8-agent verifier convergent finding + the v1.7.17 6-agent round T2-Sonnet gap finding.

- **`_autostart_target_pythonw()` now asserts `not _is_frozen()`.** Defensive — the function uses `sys.executable` directly (legitimate under source mode, where `sys.executable` IS the Python interpreter), and is only called from the source-mode branch of `_autostart_target()`. But the v1.7.13 → v1.7.16 latent bug taught us that "called from a safe branch today" is fragile; a future refactor that accidentally plumbs this under a frozen branch would re-introduce the same temp-path `.lnk` bug. The assert makes the source-only contract enforced at runtime. Flagged by the v1.7.17 6-agent round T3-Opus + T2-Sonnet convergent finding.

- **`.gitignore *.old` narrowed to `*.exe.old`.** v1.7.16's broad `*.old` pattern would have shadowed any legitimate `.old` file anywhere in the repo (backup notes, test fixtures, manual saved copies). Only the rename-dance intermediate needs to be ignored; the specific pattern matches.

- **`build-exe.bat` version + comment refreshed.** `VERSION=1.7.16` → `1.7.17`, stale "should print 'displayoff 1.7.13'" verification comment updated to match the actual current version. The earlier sandbox-test recommendation reference is removed (it pointed at a workspace-private path that doesn't belong in a public file).

### Notes — what was broken from v1.7.13 through v1.7.16

Be honest about the regression scope: the rename-dance has never worked end-to-end since it was introduced in v1.7.13. v1.7.13 was the freeze pass that introduced the wrong `sys.executable` assumption. v1.7.14 hardened the first-launch promote ping (correct in its own scope, didn't touch the path bug). v1.7.15 added the CI release workflow and the in-process ping dedupe (also correct, also didn't surface the bug). v1.7.16 hotfixed the GitHub release-asset CDN allowlist (necessary, but not sufficient — the URL fix made the network step pass but the rename still targeted the temp dir). The 8-agent verifier rounds on each release couldn't catch this because the v1.7.13 comment block claimed `sys.executable` was correct, and the verifiers trusted the docstring over the actual runtime behavior.

The empirical proof landed in `displayoff.log` after v1.7.16 was installed at `proggy\Tools\displayoff.exe`: the log lines `current launcher is 'C:\Users\nate\AppData\Local\Temp\onefile_42604_561348_DreZIYVFd8M\python.exe'` showed `sys.executable` returning the temp path. That's what triggered the v1.7.17 work.

User-facing impact for the v1.7.13 → v1.7.16 cohort:

- **Tray icon defaulted to hidden** on every install / relocation, requiring a manual flip of Settings ▸ Personalization ▸ Taskbar ▸ Other system tray icons. The `tray_promoter` polling timed out without finding our subkey because the path it polled with (the temp dir) never matched what Explorer recorded (the on-disk dir).
- **"Install now" appeared to succeed but the on-disk .exe never changed**. Network download succeeded, SHA verified, status returned `relaunched` — but the rename happened inside the per-launch temp dir, which got cleaned up on process exit anyway. Next launch was still the old version.
- **Autostart `.lnk` pointed at a transient temp path** that changed every launch. Result: the `.lnk` would silently break every time, and `displayoff.log` would log "Stale startup shortcut" on relaunch. Re-saving from Settings ▸ Autostart would briefly fix it until the next launch.

After installing v1.7.17 manually (download from the release page, replace `proggy\Tools\displayoff.exe`), all three issues are resolved automatically — the resolver runs at module import, picks up the correct on-disk path, and the rename-dance / autostart / tray promoter all start working as designed. The next "Install now" exercise (v1.7.17 → v1.7.18, whenever there is one) will be the actual inaugural successful in-the-wild dance.

### Notes — what didn't get fixed yet (deferred)

From the v1.7.16 verifier backlog, lower-priority items still pending:

- `objects-origin.githubusercontent.com` defensive allowlist add (forward-compat for future GitHub CDN-host changes).
- `release.yml` permissions tightening (`contents: read` at workflow level, `write` only on the upload step).
- Post-upload redirect-host smoke test in `release.yml` (proactive future-CDN-change detection).
- `_UPDATE_MIN_EXE_SIZE = 1 MB` floor → tighter (real .exe is ~52 MB).
- 300 ms parent-`os._exit` vs child-`_acquire_single_instance` race in the rename-dance child relaunch.
- `_themed_dialog.minsize()` sticky-floor (currently one-shot `geometry()`-based — survives DPI changes during dialog lifetime, but not Tk geometry re-solves).

None block the v1.7.17 ship; the rename-dance + tray promoter + autostart triad were the critical path. Backlog reconsidered for v1.7.18.

## [1.7.16] — 2026-05-21

Hotfix. The v1.7.14 → v1.7.15 rename-dance update attempt — the first time the dance ran in production after v1.7.13 introduced it — failed because GitHub had migrated the release-asset CDN to a domain not in the hardcoded allowlist. The dialog button row also clipped at default DPI. Both fixed here. v1.7.16 is the third-time's-the-charm — v1.7.13/14 were dance code-but-untested, v1.7.15 was dance live-but-broken-at-GitHub-end, v1.7.16 should be dance live-and-working.

### Fixed

- **`release-assets.githubusercontent.com` added to the update-host allowlist.** GitHub migrated the release-asset CDN from `objects.githubusercontent.com` → `release-assets.githubusercontent.com` over 2025. The current canonical redirect target for `https://github.com/<owner>/<repo>/releases/download/<tag>/<asset>` is the new host (verified 2026-05-21 via `curl -sIL` against the v1.7.15 asset — the JWT inside the signed URL even names the new host explicitly via the `aud` claim). The old `objects.githubusercontent.com` stays in the list for any older release whose asset URLs were baked before the migration (defensive — both may coexist for some time, and the SHA256 manifest verification is the actual integrity boundary; the host allowlist just bounds the redirect chain to known GitHub infrastructure). Surfaced live by the v1.7.14 → v1.7.15 update attempt: `<urlopen error redirect target not in allowlist: 'https://release-assets.githubusercontent.com/...'>`. The 8-agent code review couldn't have caught this — it's a GitHub-end change. Sandbox-test-style integration testing belongs in the release gate for any future rename-dance touch (the v1.7.15 deferral of the sandbox test was the actual root cause of this hotfix).
- **Update-available dialog button row no longer clips at default DPI.** v1.7.13 introduced the three-button row (`Install now` / `Open releases page` / `Cancel`) but the middle button "Open releases page" rendered ~18 chars wide — the dialog inherited its width from the body Label's wrapped text (`wraplength=460`), which sized narrower than the button row needed. Result: middle button clipped to "open releases pa" with both ends shaved. v1.7.16 fixes this two ways: (a) middle button label shortened to "Releases page" (13 chars) at both call sites — the update-available dialog and the rename-dance-failed error fallback; (b) `_themed_dialog` now computes `btn_frame.winfo_reqwidth()` + chrome margin and floors the dialog's geometry width to it, so any future widening of button labels can't silently re-introduce the clip. The body prose still describes the button by its functional name ("Releases page lets you download manually instead.") for accessibility.

### Notes — the deferred sandbox test was load-bearing

v1.7.15's session brief explicitly deferred the sandbox-style end-to-end test of the rename-dance ("skip any sandbox tests though please"). The 8-agent code review caught structural bugs in v1.7.13, the 6-agent normal-stakes review caught more in v1.7.14/v1.7.15 — but neither could catch a GitHub-end CDN-domain change. A live update attempt in a sandbox VM with v1.7.13 → fake-v1.7.14 would have surfaced this in ~5 minutes. v1.7.16 ships without one as well, but the URL-allowlist fix is testable directly: `python -c "from displayoff import _download_url_allowed; print(_download_url_allowed('https://release-assets.githubusercontent.com/test'))"` returns `True`. The next live exercise is when a v1.7.14 or v1.7.15 user clicks "Install now" against v1.7.16 — if that works end-to-end, the dance is finally validated in the wild.

## [1.7.15] — 2026-05-21

Backlog drain — sub-threshold items from the v1.7.13 + v1.7.14 verifier rounds, plus a build-infrastructure pass that auto-builds the .exe on tag push instead of from the maintainer's machine. No new user-facing features. Two `displayoff.py` behavioral changes (sweep + in-process ping dedupe), two cosmetic-but-coupling code changes (settle-time constant + step-numbering reconciliation), and the GitHub Actions release workflow.

### Added

- **`.github/workflows/release.yml` — auto-build on tag push.** Triggers on any annotated tag matching `v*.*.*` and on manual `workflow_dispatch`. Runs on `windows-latest`, installs Python 3.14 + requirements + pinned Nuitka 4.1.1, runs `build-release.sh`, and uploads `build/displayoff.exe` + `build/SHA256SUMS.txt` to the matching release via `softprops/action-gh-release@3bb1273…` (v2.6.2, pinned by full commit SHA per workspace supply-chain baseline). `fail_on_unmatched_files: true` so a silent Nuitka failure (no .exe produced) doesn't ship an asset-less release. `timeout-minutes: 30` absorbs the first-time MinGW64 download path on a cold runner cache. The previous flow — `bash build-release.sh` on Nate's machine followed by manual `gh release upload` — still works but is no longer the supply-chain root of trust. Pinned SHAs: `actions/checkout@de0fac2…` (v6.0.2), `actions/setup-python@a309ff8…` (v6.2.0). Re-resolve via `gh api repos/<repo>/git/refs/tags/<tag>` before bumping.

### Changed

- **`tray_promoter.sweep_stale_entries(our_exe_name="displayoff.exe", current_exe_path=_EXE_PATH)` is now called from `run_tray` under freeze**, immediately before `capture_baseline`. The promoter has shipped the sweep helper since the tray-apps audit pass, but `displayoff.py` never invoked it because pre-v1.7.13 displayoff ran as `pythonw.exe` (basename too broad to scope safely — would match every other Python tray app the user has) and the function explicitly no-ops for `pythonw.exe` / `python.exe`. v1.7.13's frozen .exe build changed the basename to a stable `displayoff.exe`, unblocking the call. Guarded with `_is_frozen() and _EXE_PATH` so .py source mode skips (under source, `_EXE_PATH` is `None` and the sweep function would fall back to `os.path.abspath("")` which resolves to cwd — defensive). Cleans `NotifyIconSettings` subkeys that point to `displayoff.exe` paths no longer on disk (e.g., a user who relocated the .exe between releases from one folder to another leaves a stale subkey for each prior location). Pre-existing v1.7.12+ tray-overflow-cruft behavior; v1.7.15 is the first version with the basename to act on it. (T4-Opus HIGH from v1.7.13 round, deferred to v1.7.15.)
- **Step numbering of the rename-dance reconciled across CHANGELOG + code.** v1.7.13's CHANGELOG describes the dance in 9 steps (steps 1-2 in the caller, steps 3-8 in `_execute_rename_dance`, step 9 in the `--after-update` child). The code's inline comments diverged — the outer `── Rename-dance updater ──` block used a 1-7+8 framing, and `_execute_rename_dance`'s body labelled its inline blocks as "Step 1+2" / "Step 3" / ... / "Step 6". v1.7.15 relabels the code to match the CHANGELOG's 9-step framing so a maintainer cross-referencing the public release notes lands on the right inline comment. The function docstring now reads "Execute steps 3-8 of the rename-dance (steps 1-2 are the caller's API + manifest fetch; step 9 is the --after-update child)". (T1-Sonnet + T1-Opus MINOR from v1.7.13 round, deferred to v1.7.15.)
- **`build-exe.bat` + `build-release.sh` Nuitka-workaround comments refreshed with a version-check timeline.** v1.7.15 re-verified `pip index versions nuitka | head -1` → latest is still 4.1.1, so the `--onefile-no-compression` flag stays (dropping it requires a Nuitka build with the py3.14 zstd fix, which hasn't shipped). Comment now lists each version where the check was performed + a recipe to re-verify, so a future maintainer doesn't have to re-derive the rationale. (T3-Sonnet LOW + Phase 3b from this session's backlog.)

### Fixed

- **`_TRAY_SETTLE_SECS = 1.0` named constant extraction.** v1.7.14's `_frozen_promote_ping` and the first-run welcome notification both used hardcoded `time.sleep(1.0)` before `icon.notify()` — the settle window for pystray's `NIM_ADD` to register the icon before Explorer renders the `NIF_INFO` balloon. Extracted to a module-level constant near `_NATIVE_PROD_SETTLE_SECS` so a future timing change stays coupled. Cosmetic-but-coupling: the two settle windows must move together (they serve the same purpose against the same OS race), and the named constant makes that a one-line edit instead of a two-line drift surface. (T3-Sonnet LOW from v1.7.14 verifier round.)
- **In-memory `_PING_FIRED_THIS_PROCESS` dedupe for the frozen-first-launch promotion ping.** v1.7.14 acknowledged a gap (T2 C2 convergent): if `%APPDATA%` is read-only or AV holds `displayoff_config.json` consistently, `save_config` raises `OSError` and the persisted `_frozen_promoted_pinged` flag never lands on disk — so the next launch re-fires the toast. The cross-launch case is genuinely unfixable without write access somewhere on disk and is acceptable as documented (RO-APPDATA is a rare edge case; one toast per launch beats no tray icon visible). But the same gap also exposes a same-session re-fire if any future code path re-invokes `_frozen_promote_ping` (Explorer-restart handler, mid-session promote retry, etc.). v1.7.15 adds a module-level boolean checked at the top of the worker — once the ping has fired in this process, the bool stays True and subsequent invocations log + return without firing a second toast. Set immediately after a successful `icon.notify()` (so a failed notify doesn't burn the one-shot silently) and BEFORE the disk-write attempt (so the in-process gate holds even when the disk write fails). Note that `icon.notify()` returns OK as soon as the `NIF_INFO` toast is submitted to Explorer, not when it's actually displayed — under Focus Assist / Quiet Hours the toast is suppressed but `notify` still returns success and the flag still flips. That's the documented behavior; the dedupe is a "fired-this-process" gate, not a "user-saw-it" gate. The documented RO-APPDATA degradation is now "one toast per launch under read-only APPDATA"; the v1.7.14 behavior was "one toast per launch, plus a same-session double-fire if anything ever re-enters". (T2 C2 follow-up from v1.7.14 verifier round; Focus-Assist semantics surfaced by T2-Opus v1.7.15 verifier round.)

### Notes — release flow change

Releases from v1.7.15 onward are built by CI, not locally. The correct shipping sequence is:

1. Author the GitHub release as a `--draft` with `release-notes.md` extracted from the CHANGELOG entry, **no asset files** (CI uploads them).
2. `git tag v1.7.15 && git push origin v1.7.15`.
3. The `release.yml` workflow fires on the tag push, builds `displayoff.exe` + `SHA256SUMS.txt` on a windows-latest runner, and uploads them to the draft release (~5-10 min wall-clock).
4. Once CI has uploaded, `gh release edit v1.7.15 --draft=false` to promote.

`build-release.sh` still runs locally for the maintainer's own SHA-cross-check; its "Next steps" banner now points at this flow rather than `gh release upload`. Don't upload locally-built bytes alongside CI bytes — that breaks the SHA256-transparency claim.

### Notes — historical step numbering in older CHANGELOG entries

The v1.7.13 entry below uses an earlier ad-hoc numbering scheme in a couple of bullets (e.g., `--after-update` "spawned by step 6"). v1.7.15 reconciles only the **code** to the 9-step framing — historical CHANGELOG prose is intentionally left as-shipped (those release notes are immutable on the corresponding GitHub release pages). New entries should use the 9-step framing where step numbers come up.

### Notes — gaps acknowledged but not patched

- **Persistent `save_config` failure → recurring toast on every launch** (still). The in-process dedupe added in v1.7.15 fixes the same-session double-fire but does nothing across launches under a permanently-RO `%APPDATA%`. Mitigation requires write access on a fallback path — e.g., a per-machine ProgramData lock-file or a registry-backed flag — which is significantly more surface area than the bug warrants. Acceptable as-is; the failure mode is rare and benign (an extra toast, no functional impact).
- **Rename-dance has still never been exercised end-to-end in a live update flow.** v1.7.15 ships with the same disclaimer as v1.7.14: the dance has been code-reviewed by 8 verifier agents over two rounds, but no production user has actually clicked "Install now" and watched the .exe rename + child relaunch + .old cleanup happen on real hardware. v1.7.13 → v1.7.15 is the first chain that *could* exercise the dance — a user on v1.7.13 or v1.7.14 clicking "Install now" against the v1.7.15 release is the test cohort. Worst-case failure is the user has to manually re-download from the releases page; the recovery path on the next launch (`_recover_from_failed_update`) cleans up partial-state artifacts automatically.

## [1.7.14] — 2026-05-21

Same-day hardening of v1.7.13's `_frozen_promoted_pinged` first-launch ping. A 6-agent verifier round on the v1.7.13 hotfix (T1 Diff-clean / T2 Gap-audit / T3 Code-review × Sonnet+Opus) returned 2× APPROVE / 4× CONCERNS with convergent findings on the new ping flow.

### Fixed

- **Gate uses strict identity (`is not True`) instead of truthiness** in `displayoff.py`. v1.7.13 used `not cfg.get("_frozen_promoted_pinged", False)` — a hand-edited config with `null`, `0`, or `""` evaluated falsy, re-firing the toast every launch even though the user might have intended to suppress it. Strict identity means ONLY a literal Python `True` suppresses the notification; everything else triggers a retry. Surfaced by T2 Sonnet+Opus + T3 Opus (convergent).
- **Flag is set ONLY on successful `icon.notify()`**, not unconditionally after the try/except. v1.7.13 set `_frozen_promoted_pinged = True` even when `notify()` raised (icon not yet registered, Focus Assist blocking NIF_INFO, AV race) — burning the one-shot silently so the icon stayed hidden indefinitely. v1.7.14 gates the flag-write on a `notify_ok` flag that only flips inside the successful branch. Failed pings now retry on next launch instead of becoming a permanent stuck-hidden state. Surfaced by T2 Opus + T2 Sonnet (convergent).
- **`hotkey_name[0]` captured BEFORE the 1 s settle** so a user who reconfigures the hotkey via Settings ▸ Save during the settle window doesn't see a stale label in the toast vs the active listener. Same capture pattern applied to the first-run welcome notification for symmetry. Surfaced by T3 Opus (single-finding HIGH).
- **Config persistence uses read-modify-write off the on-disk config** in `_frozen_promote_ping` instead of `save_config(closure_cfg)`. v1.7.13's closure-cfg save would clobber a concurrent Settings ▸ Save's just-written user edits (new hotkey / idle minutes / lock-on-blank) with the stale snapshot from `run_tray`'s entry. v1.7.14 calls `load_config()` fresh, mutates only the one key, then `save_config(disk_cfg)` — the user's other edits survive. Surfaced by T3 Opus (single-finding HIGH).
- **`first_run + frozen` users no longer get a duplicate toast on launch 2.** v1.7.13's `if first_run: ... elif _is_frozen() and not pinged: ...` chain meant a fresh-install frozen user got the welcome notification on launch 1, then the SEPARATE promotion ping on launch 2 (welcome → set defaults; ping branch fires because flag still False). v1.7.14 pre-sets `cfg["_frozen_promoted_pinged"] = True` inside the `first_run` branch under freeze, so the welcome notification doubles as the catalog-forcing ping. Surfaced by T3 Opus MEDIUM.
- **Toast title is just "Display Off"** instead of `"Display Off v" + __version__`. The v1.7.8 User-Agent hardening explicitly removed `__version__` from network traffic for fingerprinting reasons; emitting the same string in a Win11 toast was inconsistent (visible under screen-share / OBS recording). Surfaced by T3 Opus MEDIUM.

### Notes — gaps acknowledged but not patched

- **Persistent `save_config` failure → recurring toast on every launch** (T2 Sonnet+Opus C2 convergent). If `%APPDATA%` is read-only or AV holds the config file consistently, the flag never persists and the toast fires every launch. Logged as `log.warning(... "Harmless beyond the extra toast.")`. Mitigation deferred to v1.7.15: an in-memory dedupe survives a single-session double-fire, but cross-launch behavior requires write access somewhere. Acceptable for the rare RO-APPDATA case.
- **Daemon-thread + `os._exit` race vs rename-dance** (T3 Opus LOW). If the user clicks "Install now" while `_frozen_promote_ping` is mid-`save_config`, `os._exit(0)` skips the atomic rename and leaves `displayoff_config.json.tmp` orphaned. Cleaned up by the next `save_config`'s `os.replace`. Not patched — the window is sub-second and recovery is automatic.

## [1.7.13] — 2026-05-21

First public single-file `displayoff.exe` (Nuitka onefile build) and the rename-dance self-updater that goes with it. Python source distribution continues alongside as a parallel channel — the same `displayoff.py` runs in both modes, dispatching on a `_is_frozen()` helper that detects the Nuitka `__compiled__` sentinel.

### Added

- **`displayoff.exe` — single-file Windows build via Nuitka onefile.** First public .exe release. Compiles the Python source to C and bundles the runtime, pystray, Pillow, pynput, tkinter, and the icon resource into one ~55 MB executable. Build recipe lives at `build-exe.bat` (Windows cmd) and uses `python -m nuitka --onefile --onefile-no-compression --windows-console-mode=disable --windows-icon-from-ico=displayoff.ico --include-data-files=displayoff.ico=displayoff.ico --include-module=native_blank --include-module=tray_promoter --include-module=PIL.Image --enable-plugin=tk-inter`. The `--onefile-no-compression` flag is a Nuitka 4.1.1 + Python 3.14 workaround — the zstd compressor used in the onefile bootloader throws `zstd.ZstdError: Allocation error : not enough memory` despite ample free memory. Compiled binary is fine; only the compression step fails. Once Nuitka ships the py3.14 zstd fix, dropping that flag would cut the .exe to ~20 MB. Tracking issue noted in `build-exe.bat`.
- **Rename-dance self-updater (frozen .exe only).** Replaces the v1.7.12 "open release page in browser" flow when running as `displayoff.exe`. Mechanics adapted from the workspace's C# add-self-update canonical checklist (MicMute / SyncthingPause):
  1. Hit GitHub releases API for the latest tag + assets (existing flow).
  2. Fetch `SHA256SUMS.txt` from the same release; parse `<64_hex>  <filename>` lines for the `displayoff.exe` entry.
  3. Download new exe to `<install_dir>/displayoff.exe.tmp`.
  4. SHA256-verify the .tmp against the manifest digest. Mismatch aborts and preserves the .tmp for forensics.
  5. `os.rename(displayoff.exe, displayoff.exe.old)` — atomic on NTFS.
  6. `os.rename(displayoff.exe.tmp, displayoff.exe)` — atomic. On failure, restore from `.old`.
  7. Write `%APPDATA%\displayoff\_update_relaunch.json` with the new version string + parent PID for forensics.
  8. `subprocess.Popen([displayoff.exe, "--after-update"], DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)` then `os._exit(0)`.
  9. The new child process reads + deletes the relaunch-state file, deletes `.old`, logs the version transition, and falls through to normal tray startup.
- **Hardcoded URL allowlist `_ALLOWED_UPDATE_HOSTS`.** `("https://github.com/itsnateai/", "https://objects.githubusercontent.com/")` — both prefix-validated case-insensitive before any fetch. Never read from config or env — a config-driven allowlist becomes admin-elevation bait. `github.com/itsnateai/...` is the canonical asset URL returned by the API; `objects.githubusercontent.com/...` is what github.com redirects to (S3-backed with a short-lived signed token). Both are needed because `urllib.request.urlopen` auto-follows the redirect, and the post-redirect URL has to pass implicit allowlist check via host presence in the constant.
- **`SHA256SUMS.txt` manifest format.** GNU coreutils `sha256sum -b` output: one line per asset, `<64_hex>  *<filename>` or `<64_hex>  <filename>`. The release workflow uploads exactly one manifest entry today (for `displayoff.exe`), but the parser tolerates additional lines / blank lines / `#` comments. Manifest fetch caps the read at 16 KiB so a malicious 200-OK with a huge body can't OOM the updater.
- **Recovery from interrupted updates.** `_recover_from_failed_update()` runs at the top of `main()` (before any other CLI flag handling) under freeze mode. Cleans up two independent artifacts: a stale `displayoff.exe.tmp` from a download that crashed before the rename, and a stale `displayoff.exe.old` from a dance that completed but couldn't trigger the `--after-update` cleanup. Both are deleted; no destructive operations beyond removing intermediates the dance is supposed to manage. No-op under `.py` source mode.
- **`--after-update` CLI flag.** Internal — spawned by step 6 of the dance. Reads + clears `_update_relaunch.json`, logs the version transition (new version + parent PID + parent exe path), strips the flag from `sys.argv`, then falls through to the normal tray-startup path. Idempotent if invoked manually (no state file → just logs "launched without relaunch state").

### Changed

- **Freeze-mode dispatch helpers.** New `_is_frozen()` returns True under either Nuitka's `__compiled__` sentinel or PyInstaller's `sys.frozen` (defensive — we ship Nuitka, but the helper handles either). New `_EXE_PATH` / `_INSTALL_DIR` constants resolve at module load: under freeze, `_EXE_PATH` is the on-disk .exe path (`sys.executable`) and `_INSTALL_DIR` is its containing directory; under source, `_EXE_PATH` is None and `_INSTALL_DIR` is the script dir. Existing `_HERE` keeps its semantics — the bundle's runtime extraction dir (Nuitka onefile temp dir or PyInstaller `_MEIPASS`), which is where `displayoff.ico` lands for pystray's `Image.open(_ICON_PATH)`.
- **Autostart `.lnk` now self-rewires after switching from source to frozen.** Refactored `_create_startup_lnk` + `autostart_enabled` to dispatch on `_is_frozen()` through a new `_autostart_target()` helper that returns `(target, arguments, working_dir, icon_location)`. Frozen mode: target is `displayoff.exe`, arguments empty, IconLocation references the .exe's embedded icon resource (`<exe>,0`). Source mode: unchanged from v1.7.12 (pythonw.exe + `"<script_path>"` arg). When a user with a v1.7.12 source-mode `.lnk` upgrades to v1.7.13 frozen and toggles autostart from Settings, the `.lnk` automatically re-creates pointing at the new .exe — `autostart_enabled()` detects the stale target and treats the .lnk as "not enabled" until the next Save reconciles it via `_create_startup_lnk`'s idempotency.
- **`check_for_updates` now returns a 5-tuple** `(has_update, latest, html_url, error, assets)` where `assets` is `{asset_name: browser_download_url}` extracted from the GitHub releases API response. Consumed by `_can_use_rename_dance` to detect whether the latest release publishes the asset names the dance expects. The in-process update-check cache stores 5-tuples now; cold cache on first launch after upgrade (no persistence migration needed — cache only lives in-memory).
- **Update dialog adds "Install now" button when frozen.** When the release publishes both `displayoff.exe` and `SHA256SUMS.txt` on allowlisted hosts AND we're running as the frozen .exe, the Update-available dialog shows `[Install now] [Open releases page] [Cancel]` instead of the v1.7.12 `[Yes] [No]` browser-only flow. "Install now" runs the rename-dance in a background worker; on success the worker `os._exit(0)`s after a 300 ms settle for the spawned child to claim the tray. On any failure (network, SHA mismatch, .exe locked, spawn failed) the worker marshals an error dialog back to the Settings Tk thread with the failure reason + "Open releases page" fallback.

### Fixed

- **`_migrate_legacy_data` `_MIGRATED` flag no longer suppresses retry on transient failures** (`displayoff.py`). v1.7.12 unconditionally set `_MIGRATED = True` after the migration loop completed, regardless of whether any file move had raised `OSError`. A transient lock (AV scanner momentarily holding the source open, OneDrive in-progress sync, locked `displayoff.log` from a not-quite-released file handle) would permanently strand the file in `_HERE` for the process lifetime — a retry 30 seconds later would have succeeded but the flag suppressed it. v1.7.13 tracks `had_recoverable_failure` across the loop; the flag is set only when every file either moved cleanly or hit a "benign race" (destination materialized post-failure, meaning a concurrent launcher won). True unrecoverable failures leave `_MIGRATED = False`, allowing the next call to retry the loop. Surfaced by T2 Sonnet+Opus round 5 verifiers (convergent).
- **DPI awareness function declarations moved to the module-level bindings block.** v1.7.12 set `fn.argtypes` / `fn.restype` on each tier's function object every time `_set_dpi_awareness()` ran — idempotent in practice (function called once at startup) but a shared-state mutation that defeats the bindings-block convention's intent. v1.7.13 declares `_SetProcessDpiAwarenessContext`, `_SetProcessDpiAwareness`, `_SetProcessDPIAware` once at module load with try/AttributeError guards so missing entry points on older Windows leave the corresponding bound name as `None`; the function body now just checks `is not None` and calls. Surfaced by T2 Opus + T3 Sonnet round 5 verifiers (convergent).
- **`uxtheme` ordinal lookups use bound `WinDLL` instead of `ctypes.windll.uxtheme`.** `_enable_dark_mode_menus` now reads from a new module-level `_uxtheme = ctypes.WinDLL("uxtheme", use_last_error=True)` (or None on platforms without uxtheme.dll) instead of the raw `ctypes.windll.uxtheme` lookup. No live bug (downstream code doesn't consult `LastError` for these calls), but the consistency closes a future-bug surface where a `ctypes.get_last_error()` after a uxtheme call could read a stale value from an unrelated DLL's syscall. Carried over from v1.7.12 backlog (T3 Opus round 1).

### Notes — verifier-round security hardening

After the initial v1.7.13 implementation, a high-stakes 8-agent verifier dispatch (T1 Diff-clean / T2 Gap-audit / T3 Code-review / T4 Blast-radius × Sonnet+Opus) surfaced three CRITICAL findings that converged across pairs. All three are addressed in this release:

- **`.tmp` is now deleted on SHA256 mismatch** instead of preserved "for forensics." The previous design kept the failed-verification bytes on disk in `_INSTALL_DIR` with the `.tmp` suffix — a file-write primitive for an attacker who could control a release manifest. The actual SHA is now logged inline (so debug info isn't lost) and the file is removed; `log.warning` covers any cleanup-failure case. (T2-Opus C2 + T3-Sonnet S1, convergent.)
- **URL allowlist tightened to parsed (scheme, netloc, path) checks.** v1.7.13-rc1 used `url.lower().startswith(host.lower())` which accepted any path under `https://github.com/itsnateai/`. The release version uses `urllib.parse.urlsplit` and requires (a) `scheme == "https"`, (b) netloc is exactly `github.com` AND path starts with `/itsnateai/displayoff/`, OR (c) netloc is exactly `objects.githubusercontent.com`. This rejects user-prefix attacks (`itsnateai-attacker`), path-prefix attacks (`evil/itsnateai/displayoff/...`), HTTP downgrade, `file://`, `javascript:`, and any future repo Nate publishes under `itsnateai/` that isn't `displayoff`. (T2-Sonnet C2 + T3-Opus M1 + T2-Opus C1, convergent.)
- **HTTPRedirectHandler re-validates every redirect hop against the allowlist.** `urllib.request.urlopen` follows redirects without any check — a compromised github.com asset row could 302 to anywhere. SHA256 verification catches tampered bytes, but the redirect itself leaks request fingerprint (IP/UA/timing) to attacker-controlled domains via the Location-header GET. The new `_AllowlistedRedirectHandler` raises `URLError` on any hop whose target fails `_download_url_allowed`. (T3-Opus H1 + T2-Sonnet C2, convergent.)
- **`logging.shutdown()` runs before `os._exit(0)`** to flush the `RotatingFileHandler`'s pending writes. Without this, the dance's last 3-4 log lines ("renaming", "spawning child") were lost in the buffer when `os._exit` bypassed normal Python teardown — the lines that matter most for diagnosing an update problem. (T3-Sonnet S3 + T3-Opus M3, convergent.)
- **`--after-update` cross-checks the state file's version against `__version__`** and logs a warning on mismatch. Surfaces stale-state corruption (a left-over `_update_relaunch.json` from a previously-failed dance consumed by a manually-launched `--after-update`) instead of silently logging bogus forensics. (T2-Opus C3, single-finding but cheap defense-in-depth.)

### Notes — distribution

- **Parallel distribution.** v1.7.13 ships the `displayoff.exe` alongside the existing Python source — not replacing it. Users who already run from `python displayoff.py` or `pythonw displayoff.py` keep working; users who download the .exe get the same logic plus the rename-dance updater. Whether to deprecate the source channel in v1.8.0 is deferred until the .exe has a few weeks of real-world telemetry.
- **`build-exe.bat` not in the GitHub release.** The build script + `build/` directory are committed to source control so future maintainers can rebuild, but they don't ship as release assets. The release publishes only `displayoff.exe` + `SHA256SUMS.txt`.

### Fixed — tray icon was hidden by default on first .exe launch

**Reported during the v1.7.13 rollout** by the first .exe tester (the developer): on a workstation that already had a v1.7.12 source-mode tray with `IsPromoted=1` for its `pythonw.exe` entry, the v1.7.13 .exe registered a brand-new `ExecutablePath` in `NotifyIconSettings`, and Win11 22H2+ defaulted that new entry to hidden-in-overflow. The existing `tray_promoter` couldn't flip `IsPromoted=1` because Explorer hadn't yet catalogued the new icon — Explorer's catalog write is lazy and only fires when the user opens the overflow flyout or Settings ▸ Other system tray icons. Result: invisible icon until the user manually interacted with overflow.

**Fix:** new `_frozen_promoted_pinged` config flag (default `false`) gates a one-shot `icon.notify(...)` call that fires on the first .exe launch for any user who already has a config from a previous source-mode install. The `NIF_INFO` balloon notification forces Explorer to catalog the icon synchronously (the balloon needs the icon's screen position), and once catalogued, `tray_promoter`'s background poll finds the new subkey and writes `IsPromoted=1`. The flag persists in `displayoff_config.json` so subsequent launches don't re-fire the notification (Explorer remembers `IsPromoted=1` once set).

The fallback is unchanged: users can still manually toggle Display Off → On in Settings ▸ Personalization ▸ Taskbar ▸ Other system tray icons. The notification just makes the automatic path work for the typical-Win11 case where the user wouldn't otherwise know to do that.

## [1.7.12] — 2026-05-21

Pre-existing-cleanup pass. Two real fixes from carried-forward backlog plus a Notes section correcting documentation drift in v1.7.11's CHANGELOG entry.

### Changed

- **`_migrate_legacy_data` now short-circuits on a module-level `_MIGRATED` flag** in both `displayoff.py` and `native_blank.py`. Previously, the migration function ran its 10-entry file-existence loop on every invocation — idempotent, but wasted ~5 `os.path.exists` syscalls per blank fire on a fully-migrated install. On an active workstation firing the idle-blank watcher 40-60 times/day, that's a few hundred wasted stat calls. The flag short-circuits the entire loop after the first successful pass; mirrors the standard "one-shot completion gate" pattern. Resets only on process restart. Surfaced by T2 Opus round 4.
- **`_set_dpi_awareness` now uses bound `WinDLL` names instead of raw `ctypes.windll.user32` / `ctypes.windll.shcore`** — converted to the workspace convention "Never call `ctypes.windll.*` directly outside the bindings block." The bindings block gained a lazy `_shcore = ctypes.WinDLL("shcore", use_last_error=True)` wrapped in `try/except OSError` so Win7 (no `shcore.dll`) still loads the module — the per-monitor Win8.1+ tier is silently skipped on those systems, falling through to the universally-available `SetProcessDPIAware` third tier. Bound function declarations replace the inline `getattr`-via-`windll` pattern. No live bug (DPI awareness returns are not currently `LastError`-inspected), but the consistency closes a future-bug surface where a downstream `ctypes.get_last_error()` call could read a stale value from a different DLL's syscall. Surfaced by T3 Opus round 1.

### Notes — corrections to v1.7.11 CHANGELOG entry

The v1.7.11 entry below carries two documentation inaccuracies surfaced by the round-4 verifier dispatch but not corrected in-place to avoid divergence with the v1.7.11 GitHub release notes (extracted at tag time):

- v1.7.11 "Removed" section says `restore_ok` lived at `native_blank.py:639-642`. The actual range of the removed variable was lines 639 + 642 (set to False then True around the `_write_display_timeouts` call); the line that now remains is the historical reference at line 648 of the post-removal file. (T2 Opus round 4.)
- v1.7.11 "Fixed" section describes the `_MIGRATION_LOG.clear()` gate as "gated on whether any non-`NullHandler` is attached to the root logger" — accurate for `displayoff.main()` (which inspects `logging.getLogger().handlers`), but `native_blank._flush_migration_log` uses a local `drained` flag set before `basicConfig` runs. The two implementations are functionally equivalent but the prose described only the displayoff mechanism. (T2 Sonnet round 4.)

### Backlog for v1.7.13+

- **`_set_pystray_dark_titlebar` / dark-mode menu setup uses `ctypes.windll.uxtheme` for ordinal lookups** (line 2397). Different shape from `_set_dpi_awareness`'s named-symbol pattern (uxtheme symbols 135 + 136 are name-less exports, only resolvable by ordinal), so the conversion is non-trivial. Same convention violation, lower priority — uxtheme failures fall through to a try/except with no `LastError` consultation.
- **Rename-dance updater** — applies once frozen to `.exe`, currently inapplicable.

## [1.7.11] — 2026-05-21

Backlog cleanup pass — five small fixes from the v1.7.9 + v1.7.10 verifier rounds that were deferred. No new features.

### Fixed

- **`native_blank.py` logging fallback is now symmetric with `displayoff.py main()`.** v1.7.10 hardened `displayoff.main()` against an unwritable `%APPDATA%` via a `try/except OSError` around `RotatingFileHandler` + `NullHandler` degenerate-case fallback. `native_blank.py`'s `_setup_logging()` (standalone-CLI entry point, `python native_blank.py --blank`) and `_ensure_module_logger_has_filehandler()` (import-driven entry point) had the same vulnerability — they would raise `OSError` uncaught from the `RotatingFileHandler` constructor when `_DATA_DIR` was unwritable, crashing the standalone CLI with a traceback or the import with an uncaught exception. Both functions now mirror `displayoff.main()`'s posture: `try/except OSError` around the file-handler creation, stderr breadcrumb dump when stderr is attached, NullHandler fallback when it isn't. Surfaced by 2 of 6 round-3 verifiers (T2 Sonnet+Opus convergent).
- **`_MIGRATION_LOG.clear()` no longer wipes the buffer when nothing drained it.** When the NullHandler degenerate path fires (pythonw + unwritable `%APPDATA%`), `log.info("data-dir migration: %s", ...)` calls go to `/dev/null`. The previous unconditional `_MIGRATION_LOG.clear()` then erased the breadcrumbs with no forensic surface for the user. The clear is now gated on whether any non-`NullHandler` is attached to the root logger; the buffer survives in the silent path so a future About-dialog readout, `/diagnostics` CLI flag, or exception handler could surface what migration was attempted.

### Changed

- **`native_blank.py` no longer mutates the filesystem at module-import time.** Previously `_ensure_data_dir()` and `_migrate_legacy_data()` both ran unconditionally at module-level scope, so any `import native_blank` from a test harness, REPL, or peer module triggered `os.makedirs` + `shutil.move` calls under `%APPDATA%`. `_migrate_legacy_data()` is now invoked lazily from inside the two logging-setup entry points (`_setup_logging` for standalone-CLI usage, `_ensure_module_logger_has_filehandler` for imported usage), each running before the file handler attaches. Both calls are idempotent, so duplicate invocation across both entry points (the imported case where displayoff.main() already migrated) is a safe no-op. `_ensure_data_dir()` still runs at module load — it's a cheap idempotent `os.makedirs(exist_ok=True)` with no destructive side effects. Surfaced by T3 Opus rounds 1+2.
- **Code-comment precision at the v1.7.10 `basicConfig(handlers=[])` fallback site.** The previous comment said `basicConfig(handlers=[])` "leaves the root logger with its default WARNING threshold." Empirically not quite right — `basicConfig` *also* fails to apply the `level=INFO` kwarg when it bails on the empty handlers list, but the silence-on-INFO behavior comes from `lastResort`'s WARNING gate (the module-level fallback handler at `logging.lastResort` that fires when no handlers exist), not from any threshold on root itself. Comment now describes the actual mechanism so a future maintainer reading it doesn't misdiagnose a regression. Surfaced by T2 Sonnet round 3.

### Removed

- **Dead `restore_ok` boolean in `native_blank.py:639-642`.** The variable was set in the `try`/`except` around `_write_display_timeouts` but never read — the verification gate that actually decides whether to clear the sentinel is the post-restore `_read_display_timeouts` comparison, not the boolean. Deleting it removes a misleading read-signal that suggested it was load-bearing. Surfaced by T3 Sonnet round 1.

## [1.7.10] — 2026-05-21

Closes a silent-zombie failure mode introduced by v1.7.9's hardening commit (`5650712`), and retroactively documents that hardening commit (which v1.7.9's CHANGELOG entry omitted).

### Fixed

- **Logging fallback no longer silently drops every log call under `pythonw.exe` + unwritable `%APPDATA%`.** v1.7.9 wrapped the `RotatingFileHandler` init in a `try/except OSError` so an unwritable `_DATA_DIR` wouldn't crash `main()`. But when both the file handler failed AND `sys.stderr is None` (the exact case `pythonw.exe` lands in), the resulting `_handlers` list was empty, and `logging.basicConfig(handlers=[])` is a documented no-op — it leaves the root logger at its default `WARNING` threshold with no handlers attached. Every subsequent `log.info(...)` then dropped silently via Python's `lastResort` handler, which only fires at `WARNING` or higher. The tray ran but produced zero log output for its entire lifetime — exactly the silent-zombie state the hardening was supposed to prevent. Now: a `NullHandler` is appended when `_handlers` would otherwise be empty, so `basicConfig` stays in its happy path. The migration breadcrumbs are still lost (no destination exists to hold them), but the rest of the app remains observable to any later logging reconfiguration. Surfaced by 4 of 6 verifiers (T2 Sonnet+Opus, T3 Sonnet+Opus convergent CRITICAL) on the v1.7.9 round-2 audit.

### Notes — retroactive documentation for v1.7.9 hardening (commit `5650712`)

The v1.7.9 CHANGELOG entry below describes only the original v1.7.9 scope (APPDATA migration, right-click reset, `_themed_dialog` typo guard, tooltip non-BMP comment). It omits the three verifier-hardening fixes that shipped in v1.7.9 commit `5650712`:

- `RotatingFileHandler` init wrapped in `try/except OSError`, falling back to console + stderr breadcrumb dump. (The bug v1.7.10 closes above is a degenerate case of THIS fallback.)
- `_migrate_legacy_data` race-loss handling: if `shutil.move` raises but `dst` exists post-failure, log as benign concurrent-launch race rather than user-facing migration failure. Mirrored in `displayoff.py` and `native_blank.py`.
- `_menu_header_text` body wrapped in `try/except Exception` so a callable failure on pystray's menu-paint thread doesn't render an empty header label (pystray's Win32 backend silently swallows exceptions from dynamic-property callables).

Future releases will document hardening commits in their own CHANGELOG section.

## [1.7.9] — 2026-05-21

Closes the two items v1.7.8 deferred: relocation of per-user state to `%APPDATA%\displayoff\`, and right-click reset of the double-click timer. Plus two non-blocking nits from the v1.7.8 verifier pass — `_themed_dialog` typo'd-kind hardening and a tooltip non-BMP analysis comment.

### Changed

- **Config, logs, and the crash-recovery sentinel now live in `%APPDATA%\displayoff\` instead of the script directory.** A shared install (e.g. one clone in `C:\Program Files\` used by two Windows accounts) previously had every user reading and writing the same `displayoff_config.json`, `displayoff.log` (+ rotated `.1`/`.2`/`.3`), `native_blank.log` (+ rotated), and `.native_blank_in_progress.json` — leaking one user's idle-pattern history, log file, and in-progress sentinel into another user's session. Each user now has their own private `%APPDATA%\displayoff\` directory, matching the per-user discipline that's already used for the Startup-folder `.lnk`. The icon `displayoff.ico` stays bundled with the script as a read-only asset. Existing files in the script directory are auto-migrated to the new location on first launch of v1.7.9; the migration is one-shot and idempotent — a partial migration safely resumes on the next launch, and breadcrumbs land in the new `displayoff.log` so support can diagnose any moves that failed. If you're upgrading in-place, quit the running v1.7.8 tray first (otherwise the held log handle blocks its own migration until the next clean launch).

### Fixed

- **Right-click on the tray icon now resets the pending double-click timer.** A user who double-clicked (firing a blank), then immediately right-clicked to open the menu, then left-clicked twice more inside the 500ms double-click window could see the second left-click pair interpreted as a fresh double-click — firing a second blank while the context menu was on screen. Pystray doesn't expose a menu-open event, so the fix piggybacks on the existing dynamic-text callable that pystray re-evaluates whenever it paints the right-click menu: a side effect on the version-header item clears `last_icon_click` if it's nonzero. Left-clicks bypass menu rendering entirely (they route to the hidden `default=True` item), so legitimate double-click detection is unaffected.
- **`_themed_dialog` now logs and coerces unknown `kind` values instead of silently rendering no glyph.** Typos like `kind="warn"` (vs `"warning"`) or `kind="err"` (vs `"error"`) previously fell through the dict lookup and produced a glyph-less dialog with no indication anything was wrong. Now: a `log.debug` line records the unknown kind and the call is coerced to `"info"` so the user still gets a severity glyph. Surfaced by both Sonnet and Opus T2 verifiers during v1.7.8 review.

### Notes

- **Tooltip 127-character truncation** in `tray_promoter.py` now carries a comment explaining the non-BMP-codepoint analysis (Python `str[:127]` counts code points; Win32 NIF_TIP truncates by `wchar_t` / UTF-16 code unit, so an emoji-bearing tooltip would diverge between the two paths). The comparison is symmetric on both sides of `==`, so equality still holds for any tooltip Display Off produces, and Microsoft documents `NotifyIconData_W` as never truncating mid-surrogate-pair on the Win32 side. Defer the real `wchar_t`-aware truncation until / if Display Off ever needs a non-BMP tooltip.
- Still on the backlog for a future release: the rename-dance self-updater (`.tmp` → rename current `.exe` to `.old` → move new in place → relaunch with `--after-update` → clean up `.old` on next launch), required once Display Off eventually freezes from Python source to a single `.exe`. Currently inapplicable.

## [1.7.8] — 2026-05-21

P2/P3 backlog from the pre-public-release audit. Seven items: five tray-correctness fixes, one network-fingerprint reduction, one dialog-severity affordance. No functional behavior changes beyond closing the bugs and clarifying the surface.

### Fixed

- **Hotkey-capture state no longer gets stuck if pynput fails to import or initialize.** Companion to the TclError variant fixed in v1.7.6. If a broken/partial install or AV quarantine left `pynput` un-importable, clicking the hotkey field set `recording["active"] = True`, ran the import at line 1693, raised ImportError out of the function, and stranded the UI in the "Press your hotkey..." state for the rest of the session — the field would not accept further clicks. Now: import and listener startup are each wrapped in a guard that restores the UI (display text, sunken relief) and clears the recording flag on failure, with the ImportError path also surfacing a themed dialog pointing the user at `pip install --upgrade pynput`.
- **Update-check dialog no longer logs ERROR when the user closes Settings mid-request.** The result-marshalling and dialog-creation paths in `_run_update_check` both raise `tkinter.TclError` once `parent_root` is destroyed — entirely expected when the user dismisses Settings before the GitHub API call lands. v1.7.7 caught these under the catch-all `except Exception` and routed them through `log.exception`, producing noisy ERROR-level traceback spam every time someone closed Settings during the 5-second update window. Now: a dedicated `except tk.TclError` branch logs at DEBUG with "(expected when parent window closed mid-request)".
- **Tray-promoter tooltip comparison normalizes whitespace and applies the NIF_TIP 127-char cap to both sides.** Phase 1 of `tray_promoter.try_promote` matched the `(ExecutablePath, InitialTooltip)` tuple byte-for-byte against the stored registry value. Explorer normalizes whitespace and truncates at 127 chars before persisting the InitialTooltip — comparing the raw 130-char tooltip we passed at `NIM_ADD` against the 127-char stored value would silently miss the otherwise-perfectly-matched subkey, stranding the poll thread on the every-30-seconds backoff interval. Both sides now flow through `.strip()[:127]` so the comparison matches what Explorer actually wrote.
- **`ctypes.WinDLL` `use_last_error=True` is now uniformly applied** to `user32`, `kernel32`, `shell32`, `dwmapi`, and `advapi32`. Previously `user32` and `shell32` were loaded via the global `ctypes.windll.*` shortcut, which does NOT enable the LastError thread-local capture — a `ctypes.get_last_error()` call following a user32 syscall would read 0 or a stale value from a different binding's syscall. No live bug from this in 1.7.7 (kernel32 was the only call site reading LastError), but the consistency closes a future-bug surface.
- **Foreground-elevation watcher logs a per-miss hint** when an elevated window has focus and the global hotkey is being silently suppressed by UIPI. Previously a single INFO line at startup told the user "may not fire while elevated window has focus" — users would routinely miss it 30 minutes into a session, fail to fire the hotkey while Task Manager / an admin terminal was foreground, and assume the app was broken. Now: a 30-second-poll daemon thread queries `GetForegroundWindow → GetWindowThreadProcessId → OpenProcess → OpenProcessToken → GetTokenInformation(TokenElevation)` and re-emits the same INFO line, rate-limited to once per 60 seconds while exposure continues. Probe is no-op when we ourselves are elevated. ACCESS_DENIED across the UIPI boundary is treated as evidence of elevation, since a non-admin OpenProcess can't probe a higher-IL process.

### Added

- **Themed dialog severity glyphs.** `_themed_dialog` now accepts a `kind` parameter (`info` / `warning` / `error` / `none`, default `info`) that prepends a Unicode glyph (ℹ︎ / ⚠︎ / ❌) to the body text. Saves-failure, hotkey-block, and update-error dialogs surface as errors; idle-validation, hotkey-safety warning, and autostart-failure dialogs surface as warnings; update-available and up-to-date as info. Replaces the v1.7.7 visual-monotony of every dialog looking identical regardless of severity.

### Changed

- **Update-check User-Agent is now `displayoff-updater` instead of `DisplayOff/{version}`.** GitHub requires a non-empty UA on API requests and ignores its content; the previous value let any passive network observer (corporate proxy, ISP, GitHub's own request log) fingerprint the exact installed build of every Display Off user behind the same exit IP. The generic value still satisfies the API and removes the version side-channel.

### Notes

- This release closes all but two items from the v1.7.6 P2/P3 backlog. Deferred to v1.7.9: (1) config + log + sentinel relocation from script directory to `%APPDATA%\displayoff\` (needs careful migration testing across multi-user installs), (2) right-click reset of the double-click timer (needs pystray-API spelunking to confirm the event hook). Also added to the v1.7.9+ backlog: if/when displayoff ships as a frozen `.exe`, the self-updater must follow the workspace rename-dance pattern — download to `.tmp`, rename current exe to `.old`, move new in place, relaunch with `--after-update`, cleanup `.old` on next launch — with the same allowlist of `github.com/itsnateai/` + `objects.githubusercontent.com/` used elsewhere in the workspace.

## [1.7.7] — 2026-05-21

UX polish: all in-app dialogs now render in the dark theme.

### Fixed

- **Update-check result, settings-save error, hotkey-safety warning, and autostart-failure dialogs all stopped being white-themed.** Every previous use of `tkinter.messagebox.*` (which delegates to the native Win32 MessageBox primitive) painted stock light-mode chrome regardless of our app theme — a visible white flash next to the dark Settings/About windows. Replaced with `_themed_dialog`, a `tk.Toplevel`-based modal that reuses the same dark palette + DWM titlebar trick the Settings and About dialogs already use. All 8 dialog sites converted. Enter fires the default button, Esc / window-close dismiss without action.

## [1.7.6] — 2026-05-21

Audit-driven correctness + security pass. Seven fixes surfaced by a pre-public-release code audit; no functional behavior changes beyond closing the bugs.

### Fixed

- **Sentinel file write is now atomic.** `_write_sentinel` previously wrote the saved AC/DC display-off timeouts directly via `open(..., "w") + json.dump`. A kill mid-write (BSOD, OOM, Task Manager) left a partial JSON on disk; `_recover_from_stale_sentinel` would catch the JSONDecodeError and **delete the corrupt sentinel** — permanently losing the saved values and leaving the user trapped in a 1-second display-off timeout. Now: write to `_SENTINEL_PATH + ".tmp"` + `f.flush()` + `os.fsync()` + `os.replace()`. Either the full sentinel commits to disk or nothing does.
- **GitHub API "rate limit reached" now shows a specific error message** instead of the misleading generic "Verify your internet connection". GitHub's unauthenticated API limit is 60 req/hr/IP, shared across `gh`, GitHub Desktop, VS Code extensions, and any other tool hitting the API from the same network. The 403/rate-limit error path now spells this out and points the user to the releases page.
- **`html_url` from GitHub API response is now validated before being opened** in the browser. A compromised release or MITM-injected JSON could previously set `html_url` to a `file://` or `javascript:` URI which `webbrowser.open` would hand to the OS handler. Now: allowlist `https://github.com/` prefix; fall back to the hardcoded releases URL otherwise.
- **Hotkey-capture state no longer gets stuck if the Settings dialog is closed mid-recording.** Clicking Cancel while "Press your hotkey…" was active left `recording["active"] = True` (because the queued `poll_capture` raised TclError into Tk's report_callback_exception, never reaching the cleanup line) AND left the pynput listener alive consuming input. Now: `poll_capture` catches TclError, stops the listener, and resets the flag.
- **About dialog no longer hangs the Tk event loop on cold-boot Win11.** `_show_about` was re-calling `autostart_enabled()` on the Tk thread — that helper spawns a PowerShell subprocess with a 30-second timeout, which on a cold-boot Win11 system with AV scanning can take 10-30s and visibly hangs the About dialog. Now: the Settings dialog passes its already-cached `autostart_state["enabled"]` value into `_show_about`. The optional parameter falls back to the helper call when no cached value is provided.
- **`DwmSetWindowAttribute` is now bound with explicit `argtypes`/`restype`.** Previously called via raw `ctypes.windll.dwmapi.DwmSetWindowAttribute(...)`; HWND is pointer-sized on x64, default-c_int argtype silently truncates handles above 2 GB. HRESULT restype default (c_int) was actually correct, but the binding hygiene matches the workspace convention "never call ctypes.windll.* directly outside a bound-name pattern".

### Added

- **Update check is now cached for 6 hours.** Repeated clicks of *Settings → Updates → Check for Updates* within the cache TTL hit the in-memory cache instead of GitHub's API, avoiding burns of GitHub's 60-req/hr unauthenticated rate-limit budget. Errors are NOT cached — a transient outage doesn't poison future checks. Internal `force=True` kwarg bypasses the cache (not currently wired to any UI affordance).

### Notes

- This release closes all P1 items raised during the pre-public-release code audit. The audit also surfaced ~10 P2/P3 items (UIPI per-miss logging, dwmapi `use_last_error=True`, multi-user file-permission consideration, etc.) — those are non-urgent polish and may land in a future release.

## [1.7.5] — 2026-05-20

UX + correctness pass.

### Fixed

- **Idle watcher gave up forever if its blank attempt didn't actually take effect.** The watcher set a single-shot `fired = True` flag when it triggered a blank, and only re-armed when the user became active again. But on some hardware/software combos the kernel's native idle-blank silently fails (a stay-awake tool resetting `GetLastInputInfo` during our 5-second policy window, a peripheral driver injecting a phantom mouse event, PowerToys Awake on a timer, etc.) — the watcher would record "fired", the screen would never blank, the user would stay idle, and the watcher would refuse to retry until the user came back. Net result: monitor lit after a long absence even with `idle_blank_minutes = 5` configured correctly. Now: if the cooldown expires and the user is *still* idle past threshold, the watcher infers the previous blank didn't stick and re-fires. Adds a heartbeat log every 5 minutes so a silently dead watcher thread is easy to spot in `displayoff.log`.
- **Quit menu killed in-flight blanks mid-restore.** Clicking Quit (or any path through `icon.stop()`) returned from `icon.run()` immediately, ran `main()` to its end, and let the interpreter shut down — even if the blank worker thread was still inside `blank_via_idle_path` holding the 1-second VIDEOIDLE timeout. The daemon thread would be killed mid-restore, the powercfg restore call never ran, and the user was left with a 1-second display-off timeout until the next launch's sentinel recovery fired. `native_blank`'s per-invocation `atexit` handler covered most of the cases in practice, but the race was real. Now: `on_quit` checks `_turn_off_lock.locked()` and waits up to 6 seconds for the in-flight blank to release the lock (cleanly finishing its own try/finally restore) before stopping the tray. Falls through to the `atexit` belt-and-suspenders if the wait times out.
- **About dialog flashed open at (0, 0) then jumped to centre + repainted with default-light titlebar before applying dark theme.** Matched the pattern already used by the Settings dialog: `withdraw()` immediately after `Toplevel()` creation, build all widgets and compute geometry against `winfo_reqwidth`/`reqheight`, apply dark titlebar, then `deiconify()` to show in final form.
- **About dialog crashed with `TclError: bad screen distance "0 10"`** as soon as the link Label was created. Tuple-form padding (`pady=(0, 10)`) is only valid on the geometry manager (`pack`/`grid`/`place`); on the widget constructor Tk parses the value as a single screen-distance and raises. Moved the tuple to `link.pack(padx=20, pady=(0, 10))` and dropped the unused constructor padding. Probably also explains the user-reported "About window draws laggy" — the body Label rendered, then the link Label exploded mid-build, leaving the dialog half-painted before the surrounding try/except swallowed the error.
- **About button in Settings appeared to do nothing.** Settings sets `-topmost True` on its root window. About is a `tk.Toplevel(parent_root)` and the original code deliberately *avoided* `-topmost`/`transient` "for fully independent z-order" — but the consequence is that About opens at normal Z-order, is immediately covered by the always-on-top Settings window, and the user sees no visible change. Now: About also sets `-topmost True` — the younger window (About) renders above the elder (Settings); closing About leaves Settings on top as expected. Both still stay above other apps while open, which matches user expectation for a modal info dialog.
- **Settings + About dialogs flickered on open** — multiple visible flashes between window creation, withdraw/deiconify, and dark-titlebar application. Root cause: Win11 paints default-light chrome on a previously-withdrawn window's first-show event, BEFORE our `DwmSetWindowAttribute(DWMWA_USE_IMMERSIVE_DARK_MODE)` re-paints it dark. The "re-assert dark titlebar after deiconify" line was already in place but ran on a visible window. Switched both dialogs to the alpha-mask pattern: `attributes("-alpha", 0)` → `deiconify()` → `update()` → `_apply_dark_titlebar()` → `update()` → `attributes("-alpha", 1)`. All chrome-repaint churn now happens with the window invisible; the user only sees the final, fully-themed window.
- **Right-click menu carried 5–7 chars of extra width** from the longest item `"  • Double-click this icon"` (26 chars with leading 2-space indent + bullet). pystray's native Win32 menu auto-sizes to the longest label, so trimming that item naturally narrows the whole popup. Now: `"• Double-click icon"` (19 chars) — same information, ~25 % less menu width.

### Added

- **Safe-hotkey guard.** The Settings dialog's only prior validation was "you pressed at least one non-modifier key" — meaning users could pick a bare letter (intercepts every press of that letter system-wide, can't type it normally any more), a bare F-key (intercepts F12 in every other app), a reserved combo like Alt+F4 / Alt+Tab / Ctrl+Esc (Windows gets them before pynput, hotkey silently dead), or a common-app shortcut like Ctrl+S / Ctrl+V (intercepts copy/paste/save in every other app — technically works, but probably not what the user meant). `_validate_hotkey_safety` now classifies the captured combo into one of three buckets:
  - **Block** (refuse to save): no modifier, modifier-only-no-key, or OS-reserved combo. Error dialog explains *why* the combo would silently fail and asks the user to pick another.
  - **Warn** (yes/no confirm before saving): common app shortcuts like Ctrl+C / V / X / Z / S / P / F / W / T / N / O / A / Y / Q. Users who genuinely want Ctrl+P (Print) as their blank-displays hotkey can still proceed.
  - **Allow**: everything else.

## [1.7.4] — 2026-05-16

Follow-up to v1.7.3 after a thorough code review. Five real findings + four stale-doc claims fixed; the rest were either false positives or out of scope.

### Fixed

- **`--off` / `--lock-and-off` / `--no-lock-off` / `--native-off` / `--legacy-off` / `--start-off` CLI paths bypassed `recover_stale_sentinel()`.** Eager sentinel recovery normally fires inside `run_tray()`, but every one-shot CLI-blank path `return`ed before reaching it. If a previous tray process was killed mid-blank (BSOD, power loss, Task Manager), the on-disk sentinel still named the original AC/DC display-off timeouts to restore; running `--off` in that state would have written a fresh sentinel over the saved values and trapped the user in a 1-second display-off loop. `main()` now runs `recover_stale_sentinel()` up-front whenever any of the off-flags is present in `sys.argv`. Idempotent + safe no-op when no sentinel is on disk; layered with the existing in-`run_tray` call so tray-mode launches still get the same protection.
- **`_run_update_check` blocked the Tk event loop for up to 5 seconds.** Clicking "Check for Updates" froze the Settings window during the GitHub API request; users would double-click thinking it had hung, queuing a second request, and the dialog window appeared crashed. Network call now runs in a `daemon=True` worker thread; result is marshalled back to the Tk thread via `parent_root.after(0, ...)` so the dialog stays responsive.
- **`_resolve_key` silently returned `None` for `vkNNN` config values.** `_pynput_key_to_name` emits `f"vk{key.vk}"` for KeyCodes with no printable char (media keys, app-defined keys), but the round-trip path on next launch had no branch for the `vk` prefix — so the recorded hotkey silently disabled itself. Added `keyboard.KeyCode.from_vk(int(name[2:]))` for `vk*` inputs.
- **`webbrowser.open()` return value was silently dropped** in three sites (Settings "GitHub" button, About dialog link, update-check "Open release page"). On locked-down user profiles where no URL handler is registered for `http://`, the click did nothing and there was no log entry to diagnose it. Extracted `_open_url(url)` wrapper that logs a warning when `webbrowser.open` returns False.
- **`_create_icon_image()` would `AttributeError` on Pillow < 8.2.** `rounded_rectangle` was added in Pillow 8.2 (2021). Production installs use the pinned `pillow==12.2.0` from `requirements.txt` and are unaffected, but a bare clone that `pip install`ed Pillow loose on an old Python environment would crash inside the fallback path — which is itself the safety-net for missing `.ico`. Now wraps the rounded calls in try/except `AttributeError` and degrades to a plain `rectangle` (square corners instead of rounded), with a one-line warning telling the user to upgrade Pillow.

### Cleanup

- Removed dead `on_turn_off` function in `run_tray` — defined but never wired to any menu item (the right-click menu deliberately has no clickable "Turn Off Displays" item per v1.6.0 — empirically the menu-item path triggered the identical code chain but the kernel never acted on the policy change). The orphan function would have been a footgun on any future menu refactor.
- `_RELEASES_API` was hardcoded in three places (`itsnateai/displayoff` URL duplicated in update-check, About dialog, GitHub-button). Consolidated to `_GITHUB_REPO`, `_GITHUB_REPO_URL`, `_GITHUB_RELEASES_URL`, `_RELEASES_API` — derived from one source-of-truth string. Renaming or forking the repo is now a one-line change.
- Documented the `DARK_BG` color invariant in `_create_icon_image()`: the moon-bite ellipse uses `DARK_BG` to carve the crescent out of the gold disc, which works because the icon's interior fill is also `DARK_BG`. If a future change ever introduces a different fill inside the monitor frame, the bite would become visible as a pixel-mismatch — comment now makes the constraint explicit so it doesn't silently drift.

### Documentation

- `README.md`: "Run at Windows startup" entry corrected from "registered in `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`" to "creates a `.lnk` shortcut in the user's Startup folder" — the registry path was the pre-v1.7.0 mechanism. v1.7.0+ uses Startup-folder `.lnk` and auto-cleans the legacy registry key on first toggle.
- `README.md`: Log-rotation note corrected from "plain `FileHandler` (no rotation) — clear them manually" to "`RotatingFileHandler` with 1 MB cap × 3 backups (v1.7.2+)". No manual cleanup needed.
- `CHANGELOG.md` v1.7.3 entry: removed the "Previous .ico preserved at `displayoff.ico.pre-v171-vis.bak`" line — that backup file was deleted during the wrap-up cleanup, so the claim was false. Rollback path is git history (`git checkout b130492 -- displayoff.ico`). Also reformatted the entry from one giant paragraph to bullets for skim-readability, and softened the "legible on any background" claim — only tested against Win11 dark-mode taskbar.

## [1.7.3] — 2026-05-16

### Changed

- **Tray icon redesigned for visibility on the Windows 11 dark taskbar.** The previous icon was a `(15, 15, 30)` dark navy disc with a `(100, 100, 200)` muted blue monitor outline and a small yellow moon. Against the `(32, 32, 32)` Win11 dark-mode taskbar the disc and the monitor outline both fell below the contrast threshold and the icon read as "a small yellow dot you could miss."
- **Design changes**:
  - Rounded square (~14% corner radius, Win11 app-icon convention) instead of a circle — fills more of the tray cell.
  - Bright cyan rim `(130, 200, 255)` is the load-bearing silhouette element on the Win11 dark taskbar (only tested against dark-mode; on light-mode taskbars the rim has lower contrast and the dark interior carries the silhouette instead).
  - Dark monitor `(18, 24, 40)` interior with a near-white frame `(235, 240, 250)`.
  - Gold crescent moon `(255, 210, 95)` inside the monitor.
- **Multi-size `.ico` re-baked at 9 sizes (16/20/24/32/40/48/64/128/256).** 16/20/24 are hand-drawn since downsampling the 256 design at tray sizes produced mush — the small variants drop detail progressively (16 = dominant gold crescent, no monitor; 20/24 add a hinted monitor; 32+ use the full design).
- **Programmatic fallback `_create_icon_image()` synced** to the same palette + shape so bare clones without `displayoff.ico` don't render a second-class icon. PIL < 8.2 lacks `rounded_rectangle`; the fallback now catches `AttributeError` and degrades to a plain `rectangle` so the tray still starts.
- **Rollback path**: git history. The pre-v1.7.3 `.ico` lives at commit `b130492` (`git checkout b130492 -- displayoff.ico` to restore the previous icon).

## [1.7.2] — 2026-05-15

24/7-readiness pass. Three parallel reviews (native handles / Python heap+threading / OS resources) against v1.7.1 with the program now expected to stay resident continuously. Two reviews returned SHIP with no findings; the OS-resource review caught one real unbounded-growth path.

### Fixed

- **`native_blank.log` would grow unbounded over the process lifetime.** `native_blank.py` used plain `logging.FileHandler` at both setup sites (`_setup_logging` and `_ensure_module_logger_has_filehandler`), while the sibling `displayoff.log` already used `RotatingFileHandler(maxBytes=1_000_000, backupCount=3)` with an explicit comment about why. With native idle-blank now the default path (v1.6.0+) every blank trigger writes ~10–15 log lines (pre-blank settle, idle samples every 250 ms during the 5-8 s blank, post-restore); at ~10 fires/day that's ~12 KB/day, ~4 MB/year — slow but unbounded, and the policy mismatch with `displayoff.log` was the real signal. Both handlers now use `RotatingFileHandler` with the same 1 MB × 3 budget. The `isinstance(h, logging.FileHandler)` idempotency check is preserved (RotatingFileHandler is a FileHandler subclass).
- **PIL `Image.open(_ICON_PATH)` held a file descriptor on `displayoff.ico` for the entire process lifetime.** PIL's lazy-load keeps the source fd open until the image is consumed or closed; pystray's consumption path doesn't guarantee a prompt close. On a 24/7 tray, that means the .ico file is locked against any in-place icon refresh or asset replacement for as long as the program runs. Wrapped in `with Image.open(...) as _im: icon_image = _im.copy()` to release the fd immediately while keeping the pixel data live. The `.copy()` is wrapped in try/except — `with`-block forces eager decode, so a truncated / 0-byte / corrupt `.ico` (Syncthing partial, OneDrive placeholder, AV quarantine-restore mid-read) raises here instead of being deferred into pystray. Failures fall through to the `_create_icon_image()` programmatic fallback that previously only fired on `isfile=False`.

### Hardened (follow-up to review round 2)

Five surfaced concerns from the review, fixed in scope so the 24/7 runtime guarantees hold under more failure modes:

- **`logging.StreamHandler()` would noisily emit-fail under `pythonw.exe`.** Both `displayoff.py:main` and `native_blank.py:_setup_logging` passed a bare `StreamHandler()` whose default `sys.stderr` is `None` under `pythonw.exe`. Every log emit raised `AttributeError: 'NoneType' object has no attribute 'write'` and was caught by `Handler.handleError`, but the swallowing path itself is wasteful and a footgun for future refactors. Both sites now conditionally append `StreamHandler()` only when `sys.stderr is not None`. File logging unchanged.
- **Idle-watcher could re-fire faster than intended on rapid input-jitter.** The `fired` flag normally serves as the cooldown (stays True while idle ≥ threshold, resets when user input drops it below), but the reset semantics are fragile to future refactors. Added `_IDLE_REFIRE_COOLDOWN_SECS = 60` — a hard wall-clock floor between fires regardless of idle state, as a belt-and-suspenders defense.
- **Cross-process race between tray and CLI.** `python native_blank.py --blank` run while the tray is mid-blank would see the in-flight sentinel as "stale crash recovery" and clobber the saved AC/DC values, leaving the user stuck at a 1 second display-off timeout. Added a named Win32 mutex `Local\DisplayOff_NativeBlank` that serializes all sentinel + powercfg writes across processes. Wrap points: `blank_via_idle_path` (tray import path), `recover_stale_sentinel` (eager startup recovery), and the CLI `--toggle` / `--blank` entries in `native_blank.main`. `--read` stays unguarded — it's truly read-only. `WAIT_ABANDONED` (previous owner crashed) is treated as a successful acquire; the next `_recover_from_stale_sentinel` call cleans up.
- **`_apply_dark_titlebar` called `ctypes.windll.user32.GetParent` directly,** violating the documented constraint "Never call `ctypes.windll.*` directly outside the bindings block" — default `c_int` restype truncates HWNDs above 2 GB on 64-bit. Added `GetParent` to the bound-name block with `argtypes=[HWND]`, `restype=HWND`; `_apply_dark_titlebar` now uses the bound name.
- **`CreateMutexW` + separate `GetLastError` binding could race across GIL release.** ctypes captures Win32 LastError into a thread-local IMMEDIATELY after the call returns when the binding handle has `use_last_error=True`; without it, a stray Python operation between `CreateMutexW(...)` and `GetLastError()` could clobber the value. Switched `_kernel32` in both `displayoff.py` and `native_blank.py` to `ctypes.WinDLL("kernel32", use_last_error=True)`. Read sites now call `ctypes.get_last_error()` (`_acquire_single_instance`, `_signal_other_to_quit`, `_watch_quit_event`). The bound `GetLastError = _kernel32.GetLastError` symbol was removed entirely — keeping it on a `use_last_error=True` DLL is a latent footgun, because calling it via ctypes would itself reset the saved thread-local and silently poison the next `get_last_error()` read. A docstring comment in the bindings block warns future contributors not to add it back.

### Round 3 follow-up

A third review round caught two ordering bugs the round-2 fixes introduced:

- **Idle-watcher cooldown gate ran BEFORE the `fired` reset.** During the 60 s cooldown window, the `continue` short-circuited past the `if idle < threshold: fired = False` reset — so a user who triggered a blank, woke the display, and then went idle again would never re-fire because `fired` stayed True past cooldown expiry. Reordered: `fired` reset now runs first (so user activity during cooldown is observed), `fired` check next, cooldown wall last (only blocks the actual fire). Also resets `last_fire = 0.0` when `idle_blank_minutes` is set to 0, so re-enabling within 60 s of a prior fire isn't blocked by stale cooldown state.
- **`_blank_mutex` fail-open vs `_acquire_single_instance` fail-closed asymmetry on CreateMutexW NULL** is intentional (different stakes — duplicate-tray cost > sentinel-clobber cost) but was undocumented. Added an explicit comment in `_blank_mutex` explaining the asymmetry, and upgraded the fail-open log from `warning` to `error` so the rare condition is more visible if it ever fires in production.

### Verified (no change)

- **No native Win32 / GDI handle leaks.** No `SetThreadExecutionState` calls (ES_DISPLAY_REQUIRED balance is vacuously satisfied). No `CreateWindow` / `RegisterClass` / `GetDC` paths in `native_blank.py` — the "native" in the name refers to the kernel's native idle-blank policy chain, not a Win32 window. The single-instance mutex and quit-event are held once for process lifetime and reaped by the kernel on exit.
- **No Python heap / threading retention.** Every `threading.Thread` is daemon-flagged; the watchdog, idle-watcher, and quit-watch are single-instance; per-blank thread spawn is gated by `_turn_off_lock.acquire(blocking=False)` so duplicates drop. `current_keys` is capped at 20 (`_KEY_TRACKER_OVERFLOW_CAP`). The `atexit.register` / `atexit.unregister` pattern in `native_blank.py` was already hardened against per-invocation accumulation (named function, unregister-in-finally) — a previous hardening pass that the audit confirmed still holds. Tk roots (Settings / About / Update-check) are destroyed on every close path; `root.after(50, poll_capture)` self-terminates when the pynput record-listener stops.
- **`subprocess.run` calls are fully waited.** No `Popen` fire-and-forget anywhere. Config JSON I/O is atomic (`.tmp` + `os.replace`) and `with`-guarded.

## [1.7.1] — 2026-05-14

Patch release closing the gaps surfaced by a v1.7.0 follow-up review. v1.7.0 introduced new helpers (`_ps_sq_escape`, `_read_lnk_target_path`, `_normalize_path`, etc.) which themselves carried second-order bugs — this release closes them before they reach production.

### Fixed

- **UTF-8 BOM in `_read_lnk_target_path` would have made stale-detection backfire** — `Write-Output` under `pythonw.exe` on Win11 can prepend a UTF-8 BOM (`﻿`) to the first line; the previous code's `.strip()` doesn't remove BOMs, so `os.path.normcase` comparison in `autostart_enabled()` would have failed forever (BOM-prefixed string never equals clean string). Symptom: every Settings open logs "Stale startup shortcut" and re-creates the .lnk on every Save. Fixed by adding `$OutputEncoding = [System.Text.UTF8Encoding]::new($false)` to the PS script (no-BOM directive) AND defensively stripping any residual BOM via `.lstrip("﻿")`.
- **Double-quote injection in the `Arguments` field** — `_create_startup_lnk` embeds `script` inside an inner double-quoted context (`'"{script_q}"'`) but only ran `_ps_sq_escape`. A path containing `"` (legal NTFS, rare but possible) would break out of the inner DQ context. New `_ps_dq_escape` helper doubles `"` per PS DQ rules; `script` is now passed through `_ps_dq_escape(_ps_sq_escape(...))` for both contexts.
- **`_read_lnk_target_path` hardcoded `timeout=10`** while every other PS call used `_PS_AUTOSTART_TIMEOUT_SECS = 30`. Cold-boot Win11 systems where PS JIT exceeds 10s would silently return `None` from the read, and `autostart_enabled()` would fall through to "assume valid" — a false-positive on the stale-detection path. Now uses the shared module constant via `_ps_run` default.
- **`autostart_enabled()` path comparison missed NTFS junctions / 8.3 short names / symlinks** — `os.path.normcase(os.path.abspath(...))` doesn't resolve any of those. Enterprise folder-redirected user profiles, installs under `C:\PROGRA~1`, or `WScript.Shell.TargetPath` returning the short form would all spuriously trip stale-detection. New `_normalize_path()` helper uses `os.path.realpath` + `normcase` to canonicalize before comparison; falls back to `abspath` if `realpath` raises (e.g., target doesn't exist).
- **`set_autostart()` introduced a `bool|str` type pollution** in the v1.7.0 commit — `legacy_state = _legacy_run_key_present()` could be `bool`, then on `OSError` rebound to `"unreadable"` (str). Harmless today (only used in log.info) but a footgun for future `if legacy_state:` refactors that would silently treat a locked hive as "present". Refactored to build a `legacy_desc` string for the log line only, keeping `_legacy_run_key_present`'s return contract a clean `bool|raise`.

### Changed (UX)

- **Settings dialog now caches the autostart on-disk state at open time** instead of re-spawning a PS subprocess on every Save's change-detection. Previously, opening Settings + clicking Save triggered TWO PS subprocesses to answer "did the checkbox change?" — each potentially adding multiple seconds to the dialog's response time on cold-boot systems.
- **Autostart-failure messagebox text fixed.** v1.7.0's text told the user to "Dismiss this dialog, then re-open Settings to retry" — but v1.7.0 also changed `_apply_settings` to return `False` on autostart failure, which keeps the dialog open. The messagebox is now consistent: "Your other settings were saved. Adjust and click Save again to retry — the dialog stays open."

### Docs

- `CLAUDE.md` Tech Stack section now reflects v1.7.0+ reality — `.lnk` shortcut via PowerShell + `WScript.Shell` COM is canonical; `winreg` is retained for legacy-cleanup only. LOC count bumped from "~1200" to "~2200" (the autostart hardening grew the file).

## [1.7.0] — 2026-05-14

### Fixed

- **"Run at Windows startup" + Save silently did nothing.** `_create_startup_lnk` referenced `subprocess.STARTUPINFO`, `subprocess.run`, and `subprocess.STARTF_USESHOWWINDOW` against an undefined name — `subprocess` was never imported at module top. Every Save click with the autostart checkbox ticked raised `NameError` inside Tk's button callback. Under `pythonw.exe` (no console), Tk's default `report_callback_exception` writes the traceback to a stderr that has nowhere to go, so the exception evaporated with no error dialog and no log entry. The Settings dialog stayed open because `root.destroy()` was never reached; the user saw "Save does nothing." Root-cause fix: `import subprocess` at module top, plus 3 layers of defense-in-depth listed below.

### Changed (autostart subsystem hardening — 2026-05-14)

- **Switched from HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run registry entry to a `.lnk` shortcut in the user's Startup folder** (`%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\Display Off.lnk`). The `.lnk` is visible and manageable in File Explorer. Legacy HKCU Run entries from v1.6.0 are detected and removed automatically on first toggle (logged as `Removed legacy HKCU Run\\DisplayOff autostart entry (migrated to Startup-folder .lnk)`).
- **`autostart_enabled()` now validates the .lnk's `TargetPath`** against the current `pythonw.exe` path resolved by `_autostart_target_pythonw()`. A stale shortcut pointing at a Python install that was upgraded or moved is treated as "not enabled" so the next Save automatically refreshes it instead of silently leaving autostart broken. Validation uses the same `WScript.Shell` COM API to read `TargetPath`; if reading fails (PS missing / COM error / timeout) we conservatively assume the .lnk is valid and let the next user-initiated Save reconcile.
- **PowerShell single-quote injection in `_create_startup_lnk`** — every interpolated value (`_STARTUP_LNK_PATH`, `py`, `script`, `working_dir`, `icon_path`) is now run through new `_ps_sq_escape` helper which doubles every `'` per PS literal rules. Paths containing single-quotes (legal NTFS, e.g., `C:\\Users\\O'Brien\\...`) previously would have terminated the PS string early, producing either a parse error or — in a pathological hostile-path case — arbitrary PS execution.
- **`_remove_startup_lnk` is now TOCTOU-safe** — uses `try: os.remove ... except FileNotFoundError: pass` instead of an `os.path.exists` precheck. Added a post-removal verify-back symmetric to the create-side verify, so a sync-software replication (OneDrive, Syncthing) or AV restore-from-quarantine putting the .lnk back gets surfaced as an error instead of silently flipping autostart back on at next logon.
- **PowerShell timeout bumped 10s → 30s** via new `_PS_AUTOSTART_TIMEOUT_SECS` constant. 10s could fire `subprocess.TimeoutExpired` on cold-boot Win11 systems where first-launch PS JIT, group-policy script-block-logging, or AV real-time scanning briefly delay PS startup past the budget. `TimeoutExpired` is NOT an `OSError` subclass — it would have escaped the v1.6.0 `except OSError` guard silently.
- **New `_ps_run` wrapper** catches `FileNotFoundError` (powershell.exe not on PATH — PSCore-stripped systems / locked-down profiles) and `subprocess.TimeoutExpired`, translating both to `OSError` with a clear diagnostic message so `set_autostart`'s "Raises OSError on creation failure" docstring contract is truthful.
- **`_legacy_run_key_present` distinguishes `FileNotFoundError` (definitely absent) from `PermissionError` (locked hive / Group Policy / can't tell)** — previously broadly caught both as "absent" which silently broke the v1.6.0→v1.7.0 migration on locked profiles. Caller in `autostart_enabled()` still treats "can't tell" as "not enabled" (best-effort) but the warning lands in `displayoff.log` so a user with a locked Run hive can see why their legacy entry persists.
- **`_delete_legacy_run_key` logs (does NOT raise) on `PermissionError`** — caller treats legacy cleanup as best-effort but the warning is now visible.
- **`APPDATA` environment-variable check at module load** — if `APPDATA` is unset, `_STARTUP_LNK_PATH` is empty and every autostart function raises a clear `OSError("APPDATA environment variable is not set...")` instead of the v1.6.0 behavior of silently joining onto an empty string and writing/reading a CWD-relative path that wouldn't actually autostart.

### Changed (Tk silent-failure prevention — applies to Settings, About, Updates dialogs)

- **`root.report_callback_exception` is hooked to the logger** in `_open_settings_impl` immediately after `tk.Tk()`. Tk's default callback handler writes tracebacks to stderr, which is /dev/null under pythonw.exe — any exception in a button command, key bind, `after()` callback, or virtual-event handler that wasn't explicitly caught (e.g., a future `NameError`, an `AttributeError` on a pynput KeyCode shape change) would otherwise vanish with no log entry. With this hook, every Tk-callback exception now lands in `displayoff.log` with full traceback. This single line of defense protects every other button in the dialog (Cancel, About, Updates, GitHub link) without per-callback try/except boilerplate.
- **`_apply_settings` autostart exception catch widened from `OSError` to `Exception`** with `log.exception` and a more informative messagebox that includes the exception type and prompts the user to re-open Settings and retry. `NameError`, `AttributeError`, `subprocess.TimeoutExpired`, and `TclError` are NOT `OSError` subclasses and would have escaped the v1.6.0 guard.
- **`_apply_settings` now returns False on autostart failure** so the Settings dialog stays open for retry instead of destroying the root and forcing the user back to the tray menu. The autostart_var is also refreshed to the actual on-disk state via `autostart_var.set(autostart_enabled())` so the checkbox visually matches reality.

### Added (observability)

- **`log.info` instrumentation in every autostart entry/exit** — `set_autostart()` logs the desired state plus current `.lnk` and legacy-registry presence; `_create_startup_lnk()` logs the target/args/lnk paths before invocation and a post-create confirmation with byte size; `_remove_startup_lnk()` logs both the successful-remove and the no-op-already-absent path. Catches future regressions where a UI element claims success but no underlying state actually changed.
- **PowerShell stderr-on-success is now logged at DEBUG level.** rc=0 with non-empty stderr is usually deprecation warnings or profile-script noise; previously thrown away silently.
- **`displayoff.log` rotation** — switched from unbounded `FileHandler` to `RotatingFileHandler(maxBytes=1_000_000, backupCount=3)`. A tray app logs every icon click, blank-trigger, listener-watchdog tick, idle-watcher sample; unbounded growth was an inevitability waiting to bite an active user.
- **Verify-back on `.lnk` creation** — `PowerShell rc=0` doesn't guarantee the file landed on disk (AV quarantine, COM `Save()` silent no-op on locked-down profiles, exec-policy edge cases). Post-write `os.path.exists` check raises `OSError` with a diagnostic message including stdout/stderr from the PS run. Same pattern as the post-publish GitHub release-asset verify-back used by the workspace's sibling tray apps.

### Notes

- The v1.6.0 `HKCU\\...\\Run\\DisplayOff` autostart code was the only path that ever shipped. The `.lnk`-based code in v1.6.0's source tree was staged but broken from the first commit (missing import, never tested via the Settings GUI under pythonw); no user ever successfully used the .lnk path on v1.6.0. v1.7.0 is the actual first working release of the Startup-folder shortcut migration.
- The Tk-silent-swallow trap under pythonw is a general risk for any Python tray app using Tk dialogs — the `report_callback_exception` hook here is the recommended defense pattern.

## [1.6.0] — 2026-05-14

### UX

- **Double-click the tray icon to blank.** Single-click is a no-op (opens menu / does nothing visible). Ctrl+Alt+F12 hotkey unchanged.
- **No clickable "Turn Off Displays" menu item.** The right-click menu shows a disabled informational label documenting the two paths that work (double-click + hotkey). The clickable menu item was removed after empirical testing on the developer's hardware: the menu-item path ran the identical code chain as double-click and hotkey (verified via `displayoff.log` + `native_blank.log` instrumentation — same `_fire_native_idle_blank` → `blank_via_idle_path` → powercfg writes → idle counter accumulating past threshold per `GetLastInputInfo` polling, with `powercfg /requests` confirming nothing was holding the display awake), but the kernel never acted on the policy change for menu-triggered invocations. Hypothesis: `powercfg /setactive SCHEME_CURRENT` is a lazy refresh that gets optimized away when the active scheme is unchanged, and the kernel only re-reads the live policy when prodded by the right state changes — the two working paths produce some side effect the menu path doesn't. Rather than ship a silently-broken click, the item is now an informational label.
- **Click-timing implementation:** pystray on Windows fires `default=True` menu items on every left-click (single, double, triple) — its API has no separate single-vs-double event. To get true double-click semantics, the tray menu includes a hidden `default=True` item (`visible=False`) that's routed to a click-gap handler. The handler measures the time between successive icon clicks and only fires the blank when two land within 500ms (matching Windows' `GetDoubleClickTime()`). First click records a timestamp and exits silently; second click within the window fires the blank and resets the pair.

### Diagnostics

- **`displayoff.log`** — new file-backed logger so pythonw.exe runs are debuggable. Records every blank trigger source (icon-double-click, menu-turn-off via the now-removed item, hotkey path) and lock-collision drops. Previously every `log.*` call under pythonw went to a NullHandler.
- **`native_blank.py` import-path logging** — when imported by displayoff.py (rather than run as a script) it now attaches a FileHandler to its own logger so log entries still reach `native_blank.log`. Without this, blank invocations from the tray left zero forensic trail.
- **Idle-counter sampling during the sleep window** — `_sleep_with_idle_log` polls `GetLastInputInfo` every 250ms and logs the idle-seconds samples. Made it possible to prove that the menu-item path's failure was NOT an idle-reset issue (samples cleanly accumulate past threshold) but a kernel-policy-refresh issue.

### Hardening (post-implementation review)

- **`_fire_native_idle_blank` no longer falls back to `SC_MONITORPOWER` on `ImportError`.** The whole reason v1.6.0 exists is that `SC_MONITORPOWER` cycles the display on affected hardware; silently falling back to it on a broken install would re-introduce the very bug v1.6.0 ships to fix. Now refuses to blank, logs loudly.
- **`native_blank()` finally block is now resilient.** Previous version called `_write_display_timeouts(saved_ac, saved_dc)` followed by `_clear_sentinel()`. If powercfg failed during restore, the un-wrapped `RuntimeError` propagated out before `_clear_sentinel()` could run — leaving the sentinel orphaned on disk forever. Wrapped in try/except; verifies values match before clearing sentinel; logs manual-recovery command if restore fails.
- **`_recover_from_stale_sentinel` deletes corrupt/invalid sentinels.** Previous version logged a warning and left the unreadable file in place; every subsequent launch hit the same wall and bailed. Now deletes the unreadable file so the system can recover from a one-shot corruption.
- **Hidden powercfg subprocess windows.** Under `pythonw.exe`, every `subprocess.run("powercfg.exe", ...)` call was allocating a fresh console window, producing ~10 visible terminal flashes per blank invocation. Added `creationflags=CREATE_NO_WINDOW` + `STARTUPINFO(dwFlags=STARTF_USESHOWWINDOW, wShowWindow=SW_HIDE)` in `native_blank._run_powercfg`. The window churn was also resetting Windows' idle-input counter, preventing the native blank from firing — hiding the subprocesses fixed both symptoms.

### Changed

- **Native idle-blank is now the default mechanism** for *every* blank trigger: tray icon click, tray menu "Turn Off Displays" item, Ctrl+Alt+F12 hotkey, idle-blank watcher, and `--off` / `--lock-and-off` / `--no-lock-off` / `--start-off` CLI flags. All paths now route through `turn_off_monitors()` which dispatches to `_fire_native_idle_blank()` by default and to the legacy `_fire_sc_monitorpower()` only when explicitly opted in.
- **Production blank window** in `_fire_native_idle_blank()` is **5 seconds** (down from the 8s test-harness default in `native_blank.py`). Bumped from the originally-planned 2.5s after empirical "menu click → no blank" reports — when the user navigates the right-click menu, the mouse moves continuously and the kernel's idle counter keeps resetting, so a tight window of 2.5s left no time for the kernel to cross the 1s threshold. 5s tolerates ~3s of post-click motion. Combined with the 0.5s pre-blank settle (`_NATIVE_PROD_SETTLE_SECS`), the dispatcher lock is held ~5.5s per blank — silently dropped duplicate triggers are now explicitly logged in `displayoff.log`.

### New

- **`use_legacy_sc_monitorpower` config key** (default `false`) — set to `true` in `displayoff_config.json` to force every blank trigger back to the v1.0–v1.5 `SC_MONITORPOWER` behavior. Useful on hardware where the legacy path works fine and you want the slightly faster blank (~0.5s vs ~1–2s).
- **`--native-off` CLI flag** — forces the native idle-blank path regardless of config. Identity-clear opt-in for scripts/shortcuts that must blank via this path no matter what.
- **`--legacy-off` CLI flag** — forces `SC_MONITORPOWER` regardless of config. Symmetric counterpart to `--native-off`, useful for testing or for users who want one-shot legacy behavior without mutating their config.
- **`force_path` parameter** on `turn_off_monitors()` — `"native"` / `"legacy"` / `None` (honor config). Both new CLI flags route through the unified dispatcher so they inherit the single-instance lock, RDP early-return, and `lock_first` handling.

### Why this is the right default

The v1.5.0 changelog explained why the native path is required on some hardware (Modern Standby + hybrid GPU laptops where `SC_MONITORPOWER` triggers a wake-handshake loop). v1.6.0 takes the conclusion to its logical end: native is strictly safer (works on every Windows version since Win95, is OEM-driver-friendly, uses the same code path as the built-in Settings dropdown). The only downside is a ~1-second-slower blank, which doesn't matter for the "click and walk away" use case. Users on hardware where `SC_MONITORPOWER` is fine can opt back in with one config key.

## [1.5.0] — 2026-05-14

### New Features

- **`--native-off` CLI flag** — turns off displays via Windows' own idle-display-off code path instead of `SC_MONITORPOWER`. Temporarily writes `GUID_VIDEO_POWERDOWN_TIMEOUT = 1s` via `PowerWriteACValueIndex` + `PowerSetActiveScheme`, waits ~8s for the kernel to fire its native idle-blank, then restores the original AC/DC timeouts. No `SC_MONITORPOWER` message is sent — uses the exact mechanism wired to **Settings ▸ Power ▸ "Turn off the display after N minutes."** Required on Modern Standby + hybrid-GPU hardware where `SC_MONITORPOWER` triggers a wake-handshake loop (verified empirically — repeated SC_MONITORPOWER events fire in rapid succession with no input recovery).
- **`native_blank.py`** — standalone helper module with three modes:
  - `--read` — print current AC/DC display-off timeouts (zero risk, no writes)
  - `--toggle` — write 1s timeouts, sleep 0.5s (too short to actually blank), restore (plumbing test)
  - `--blank` — full sequence with 8s blank window and 6s "hands off keyboard/mouse" countdown
  - Crash-safe: writes a sentinel file before changing timeouts; uses `try/finally` + `atexit` + sentinel-based recovery on next launch so a hard kill mid-run cannot leave the user stuck with a 1-second display timeout. Logs to `native_blank.log`.

### Why a second code path

`SC_MONITORPOWER` is the documented, canonical API and works on virtually every Windows PC. But on certain Modern Standby + hybrid-GPU laptops, the userland-message → kernel-power-policy handoff lands in a no-recovery wake loop. The native idle-display-off path (the one the Settings dropdown writes to) has been working reliably on every Windows version since Win95 and is OEM-driver-friendly. `--native-off` is the safe fallback for users on affected hardware. `--off` and `--lock-and-off` continue to use `SC_MONITORPOWER` for backward compatibility on hardware where it works fine.

## [1.4.0] — 2026-05-08

### New Features

- **Lock-and-off** — optional Settings checkbox + `--lock-and-off` CLI flag. Locks the workstation before powering off displays, so a passerby can't wake the screen and see your work.
- **Autostart toggle** — Settings checkbox to register Display Off in `HKCU\…\Run` (uses `pythonw.exe` so there's no console flash at logon).
- **Auto-blank when idle** — Settings spinbox sets a "blank after N minutes idle" threshold. A 15-second-poll watcher reads `GetLastInputInfo` and fires once when the threshold is crossed; the "fired" flag re-arms the next time idle drops below the threshold (so brief activity windows shorter than the 15-second poll may permit a second fire). 0 = off (default).
- **About dialog** — new tray menu item showing version, current hotkey, lock/idle/autostart state, and the project URL.
- **Check for Updates** — new tray menu item that hits the GitHub releases API and offers to open the release page in the browser if a newer version is available. No automatic phone-home — manual only.
- **First-run notification** — one-time tray balloon on initial launch announcing the configured hotkey.
- **Apply button** in Settings — persist changes without closing the dialog. Save persists and closes; Cancel just closes the window (any in-dialog edits not yet Saved/Applied are discarded — already-Applied changes are persisted to disk).
- **CLI flags** — `--lock-and-off` (force lock + blank), `--no-lock-off` (force blank-without-lock; overrides config), `--quit-other` (signal a running tray instance to quit), `--reset-config` (delete the config file).
- **Esc cancels hotkey recording** in the Settings dialog.
- **Listener watchdog** — a 30-second poll restarts the global hotkey listener if pynput's hook is missing or its thread is dead. Common causes are session lock, RDP connect, and fast-user-switch; the watchdog detects them indirectly via liveness polling, not via session-event subscription.

### Fixed

- **Settings dialog could spawn two Tk roots and crash** if clicked twice in quick succession — now claims the slot under a lock before the worker thread is spawned, and the dialog flag is always cleared via try/finally.
- **`current_keys` overflow guard fired on the wrong path** — moved from `on_release` (where the set just shrank) to `on_press` (where missed-release accumulation actually grows it).
- **`save_config()` exceptions left the dialog flag stuck** — read-only file or full disk would silently disable the hotkey for the rest of the session. Now caught with a user-facing error and the flag reset cleanly.
- **Single-instance mutex used `Global\` scope**, blocking second-user sessions under Fast User Switching. Switched to `Local\` (per-session).
- **`SC_MONITORPOWER` was a confusing no-op inside RDP sessions** — now early-returns with a log message when `GetSystemMetrics(SM_REMOTESESSION)` is non-zero.
- **Tk dialogs were not DPI-aware** — calls `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` before creating the root, falling back gracefully on older Win10 builds.
- **Hotkey listener restart could briefly double-fire** — old listener thread is now joined before the new one starts.
- **Hotkey-only-modifier capture left recording stuck** — pressing only Ctrl/Alt/Shift and releasing no longer hangs the recorder; Esc cancels.
- **Save-time hotkey validation** — refuses to save a binding without at least one non-modifier key.
- **First-run welcome could clobber a user's saved settings** — the welcome thread now re-checks `displayoff_config.json` existence after the 1-second delay, so a fast user who opens Settings and saves before the welcome fires won't see their config overwritten with defaults.
- **`_get_modifier_map` lazy init was not thread-safe** under nogil/free-threaded Python. Now uses double-checked locking.

### Code Hygiene

- `logging.basicConfig` moved into `main()` — no longer clobbers a host application's root logger if the module is ever imported.
- `hotkey_display_name(cfg)` no longer has a `cfg=None` default that silently does I/O.
- Magic numbers (`20`, `0.5`, `5000`, `183`) named as module constants.
- `os.startfile(URL)` for the GitHub button replaced with `webbrowser.open(URL)`.
- Pinned `requirements.txt` to known-good versions (was floating `>=` bounds).
- `_create_icon_image` documented as fallback-only.
- **Settings dialog decomposed** into row builders (`_build_header`, `_build_hotkey_row`, `_build_options_section`, `_build_footer`). The orchestrating `_open_settings_impl` shrank from 168 lines to 74. Adding a new option row is now a one-line `_build_*(root, row=N, ...)` call in the impl plus a sibling builder.
- **UIPI hint at startup**: when running unelevated, logs a one-line note that the hotkey may not fire while an elevated window has focus (Task Manager, admin terminals, UAC consent). Documented in README's Caveats section.

### Review closeout (post-audit hardening)

The above v1.4.0 changes were reviewed (concurrency, Win32, functional walkthrough, doc-vs-code gap) before tag. The following hardening followed:

- **Win32 HANDLE truncation** — `CreateMutexW`, `CreateEventW`, `OpenEventW` were called via raw `ctypes.windll.*` lookups, defaulting `restype` to `c_int` (4 bytes). On 64-bit Windows the kernel could in theory return a HANDLE with bit 31 set, which would round-trip incorrectly through `c_int` and cause `CloseHandle` on a stale value. Now bound with `restype = HANDLE` (`c_void_p`) in the platform-guarded block.
- **`GetTickCount` signed arithmetic** — without a `restype = DWORD` binding, ctypes returned signed `c_int`, which goes negative after ~24.8 days of uptime and silently breaks idle-blank arithmetic. Now bound `restype = DWORD`; the subtraction is also masked with `& 0xFFFFFFFF` so the wraparound at ~49.7 days produces correct elapsed time.
- **Watchdog stale-listener race** — the watchdog snapshotted `_active_listener` outside `_listener_lock`, then called `is_alive()` on the snapshot. A concurrent Save→restart could leave the watchdog acting on a stale reference and force-restart a healthy listener. Fixed by adding `start_hotkey_listener(force=False)` which performs the liveness check + conditional restart atomically under the lock; the watchdog now just calls that.
- **`_dialog_active` cleared without lock** — three `finally` blocks cleared the flag with a bare assignment. Cleaned up via a `_release_dialog_slot()` helper that takes `_dialog_lock` for forward-compat with free-threaded Python.
- **`SetEvent` / `WaitForSingleObject` failure paths** — `_signal_other_to_quit` collapsed "instance found, signal failed" into the same "no instance" return; now returns a tri-state (`signaled` / `missing` / `error`) and the caller logs accordingly. `_watch_quit_event` now checks for `WAIT_FAILED` and `WAIT_ABANDONED` explicitly.
- **`save_config` non-atomic write** — `open(..., 'w')` truncates before writing; the idle watcher's 15-second `cfg_provider()` could read a half-written file. Replaced with a write-temp-then-`os.replace` pattern (atomic on NTFS).
- **`SetProcessDpiAwarenessContext(-4)`** — the `-4` sentinel needed `c_void_p` for sign-extension on 64-bit. Bound `argtypes`/`restype` correctly.
- **`_LASTINPUTINFO` conditional dead code** — the non-Windows branch of the field-type ternary was unreachable. Simplified to unconditional `c_uint`.

## [1.3.0] — 2026-04-18

### New Features
- Single-instance mutex — launching a second instance brings the existing tray icon forward instead of starting a duplicate
- `--start-off` CLI flag — turn monitors off and then start into tray in one step

### Fixed
- Crash on resume from sleep (now targets desktop window instead of broadcasting)
- Hotkey repeat no longer spawns redundant threads
- Semgrep security scanning workflow on CI

## [1.1.0] — 2026-03-21

### New Features
- `--version` flag to print current version
- `displayoff.ico` — crisp multi-size icon (16–256px) used in tray when present
- `README.md` — user-facing documentation
- Proper `logging` module replaces raw `print` statements

### Bug Fixes
- Fix ctypes 64-bit pointer truncation by setting `argtypes`/`restype` on `SendMessageW`
- Fix mixed modifier hotkey (e.g. Ctrl_L + Alt_R + F12) silently failing
- Guard `ctypes.windll` behind platform check so module imports on non-Windows
- Prevent thread spam from repeated hotkey/tray triggers via `threading.Lock`
- Cap `current_keys` set to prevent unbounded memory growth from missed release events
- Remove unused `MONITOR_ON` constant

## [1.0.0] — 2026-03-21

### New Features
- System tray app with programmatic moon/monitor icon
- Turn off all displays via tray click or global hotkey (Ctrl+Alt+F12)
- `--off` flag for immediate one-shot display off (no tray)
- 300ms delay prevents immediate wake from trigger input
- Graceful fallback if pynput not installed (tray-only mode)
