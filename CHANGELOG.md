# Changelog — Display Off

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
