# Changelog — Display Off

## [1.7.1] — 2026-05-14

Patch release closing the gaps surfaced by the v1.7.0 audit pair-set's R2 sweep. v1.7.0 introduced new helpers (`_ps_sq_escape`, `_read_lnk_target_path`, `_normalize_path`, etc.) which themselves carried second-order bugs — this release closes them before they reach production.

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

### Changed (autostart subsystem hardening — Sonnet+Opus audit 2026-05-14)

- **Switched from HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run registry entry to a `.lnk` shortcut in the user's Startup folder** (`%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\Display Off.lnk`). Matches how every other tray app in the workspace manages autostart (MicMute, SyncthingPause, MWBToggle, CapsNumTray, etc.) and is visible/manageable in File Explorer. Legacy HKCU Run entries from v1.6.0 are detected and removed automatically on first toggle (logged as `Removed legacy HKCU Run\\DisplayOff autostart entry (migrated to Startup-folder .lnk)`).
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
- Cross-stack pattern note `memory/reference_tk_callback_silent_under_pythonw.md` captures the Tk-silent-swallow trap for every sibling tray app in the workspace that uses Tk dialogs under pythonw — every one of them is a candidate for the same `report_callback_exception` hook.

## [1.6.0] — 2026-05-14

### UX

- **Double-click the tray icon to blank.** Single-click is a no-op (opens menu / does nothing visible). Ctrl+Alt+F12 hotkey unchanged.
- **No clickable "Turn Off Displays" menu item.** The right-click menu shows a disabled informational label documenting the two paths that work (double-click + hotkey). The clickable menu item was removed after empirical testing on the developer's hardware: the menu-item path ran the identical code chain as double-click and hotkey (verified via `displayoff.log` + `native_blank.log` instrumentation — same `_fire_native_idle_blank` → `blank_via_idle_path` → powercfg writes → idle counter accumulating past threshold per `GetLastInputInfo` polling, with `powercfg /requests` confirming nothing was holding the display awake), but the kernel never acted on the policy change for menu-triggered invocations. Hypothesis: `powercfg /setactive SCHEME_CURRENT` is a lazy refresh that gets optimized away when the active scheme is unchanged, and the kernel only re-reads the live policy when prodded by the right state changes — the two working paths produce some side effect the menu path doesn't. Rather than ship a silently-broken click, the item is now an informational label.
- **Click-timing implementation:** pystray on Windows fires `default=True` menu items on every left-click (single, double, triple) — its API has no separate single-vs-double event. To get true double-click semantics, the tray menu includes a hidden `default=True` item (`visible=False`) that's routed to a click-gap handler. The handler measures the time between successive icon clicks and only fires the blank when two land within 500ms (matching Windows' `GetDoubleClickTime()`). First click records a timestamp and exits silently; second click within the window fires the blank and resets the pair.

### Diagnostics

- **`displayoff.log`** — new file-backed logger so pythonw.exe runs are debuggable. Records every blank trigger source (icon-double-click, menu-turn-off via the now-removed item, hotkey path) and lock-collision drops. Previously every `log.*` call under pythonw went to a NullHandler.
- **`native_blank.py` import-path logging** — when imported by displayoff.py (rather than run as a script) it now attaches a FileHandler to its own logger so log entries still reach `native_blank.log`. Without this, blank invocations from the tray left zero forensic trail.
- **Idle-counter sampling during the sleep window** — `_sleep_with_idle_log` polls `GetLastInputInfo` every 250ms and logs the idle-seconds samples. Made it possible to prove that the menu-item path's failure was NOT an idle-reset issue (samples cleanly accumulate past threshold) but a kernel-policy-refresh issue.

### Hardening (from post-implementation multi-agent review)

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

- **`--native-off` CLI flag** — turns off displays via Windows' own idle-display-off code path instead of `SC_MONITORPOWER`. Temporarily writes `GUID_VIDEO_POWERDOWN_TIMEOUT = 1s` via `PowerWriteACValueIndex` + `PowerSetActiveScheme`, waits ~8s for the kernel to fire its native idle-blank, then restores the original AC/DC timeouts. No `SC_MONITORPOWER` message is sent — uses the exact mechanism wired to **Settings ▸ Power ▸ "Turn off the display after N minutes."** Required on hardware where `SC_MONITORPOWER` triggers a wake-handshake loop (verified on ASUS ROG Strix G614JV with Modern Standby + Intel UHD/RTX 4060 hybrid GPU, where 20× SC_MONITORPOWER events fired in 38s with no input recovery).
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

### Verifier-pair closeout (post-audit hardening)

The above v1.4.0 changes were audited by four parallel verifier agents (concurrency, Win32, functional walkthrough, doc-vs-code gap) before tag. The following hardening followed from their findings:

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
