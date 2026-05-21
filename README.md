# Display Off

[![GitHub Release](https://img.shields.io/github/v/release/itsnateai/displayoff)](https://github.com/itsnateai/displayoff/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue)](https://www.python.org/)
[![Windows](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D6)](https://github.com/itsnateai/displayoff)
[![GitHub Downloads](https://img.shields.io/github/downloads/itsnateai/displayoff/total)](https://github.com/itsnateai/displayoff/releases)

Tiny system tray utility that turns off all monitors without putting the PC to sleep.

**Double-click** the tray icon or press **Ctrl+Alt+F12** to blank all displays. Move the mouse or press any key to wake.

## Why this exists

The classic `SC_MONITORPOWER` mechanism — used by NirCmd, AutoHotkey scripts, PowerToys, and every PowerShell one-liner out there — **breaks on Modern Standby + hybrid-GPU laptops**, where it triggers a wake-handshake loop the user can't recover from. Display Off works around this by hooking into Windows' native idle-display-off code path (the one wired to *Settings ▸ Power ▸ "Turn off the display after N minutes"*) instead of sending `SC_MONITORPOWER`. The legacy mechanism is still available as an opt-in for users on hardware where it works (and where the legacy path is slightly faster).

## Quickstart

```bash
pip install -r requirements.txt
python displayoff.py
```

Then double-click the tray icon, or press **Ctrl+Alt+F12**.

Requires **Python 3.8+** and **Windows**.

## Features

- **Double-click tray icon or `Ctrl+Alt+F12`** to blank all displays — hotkey reconfigurable via Settings
- **Two blank paths** — native idle-display-off (works on Modern Standby + hybrid-GPU) or legacy `SC_MONITORPOWER` (faster, but not on all hardware)
- **Optional lock-on-blank** — Win+L before powering off the screens
- **Optional auto-blank-when-idle** — fires once after N minutes of inactivity, re-arms on activity
- **Autostart toggle** — one-click register/unregister at Windows startup
- **No admin required, no telemetry, no automatic phone-home** — manual update-check only

## Usage

```bash
python displayoff.py              # Start in system tray
python displayoff.py --off        # Turn off displays immediately, then exit (honors lock-on-off + path config)
python displayoff.py --native-off # Force the native idle-display-off path (regardless of config)
python displayoff.py --legacy-off # Force the legacy SC_MONITORPOWER path (regardless of config)
python displayoff.py --lock-and-off   # Lock workstation, then turn off displays
python displayoff.py --no-lock-off    # Turn off displays without locking (override config)
python displayoff.py --start-off  # Turn off, then start tray
python displayoff.py --quit-other # Signal a running tray instance to quit cleanly
python displayoff.py --reset-config   # Delete the config file
python displayoff.py --version    # Print version
pythonw displayoff.py             # Start in tray without a console window
```

## Configuration

Right-click the tray icon → **Settings**:

| Field | Behavior |
|---|---|
| **Hotkey** | Click the field, then press your combination. Esc cancels recording. |
| **Lock workstation when blanking** | Locks via Win+L before powering off the screens. |
| **Run at Windows startup** | Creates `Display Off.lnk` in `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\` (uses `pythonw.exe` so there's no console flash). Legacy `HKCU\...\Run` entries auto-cleaned on first toggle. |
| **Auto-blank after N minutes idle** | Polls `GetLastInputInfo` every 15s, fires once when idle ≥ threshold. Set to 0 to disable. |

**Save** = apply and close. **Apply** = persist and stay open. **Cancel** = close, discard in-dialog edits (already-applied changes stay persisted).

Settings live in `displayoff_config.json` next to the script.

### Choosing the blank mechanism

One additional key isn't exposed in the GUI:

```json
{
  "use_legacy_sc_monitorpower": false
}
```

- `false` (default) — every blank routes through the **native idle-display-off path**. Safe on every Windows version since Win95; required on Modern Standby + hybrid-GPU hardware.
- `true` — every blank routes through the **legacy `SC_MONITORPOWER`** path. Faster (~0.5s vs ~5s) but **may cycle to reboot-required on Modern Standby + hybrid-GPU laptops**. Only flip on if you've confirmed the legacy path works on your hardware.

Or one-shot via `--native-off` / `--legacy-off` CLI flags.

## How It Works

### Default — native idle-display-off path

`turn_off_monitors()` is a dispatcher: based on `cfg['use_legacy_sc_monitorpower']` (or `force_path=` for CLI flags), it routes to `_fire_native_idle_blank()` or `_fire_sc_monitorpower()`.

The native path lives in [`native_blank.py`](./native_blank.py):

1. Read the active power scheme + current AC/DC display-off timeouts.
2. Write a **sentinel file** (atomically, via `.tmp` + `os.replace` + `fsync`) recording the saved values, so a crash mid-flight doesn't leave you with a 1-second display-off timeout.
3. Write 1-second AC and DC timeouts via `powercfg /setacvalueindex SCHEME_CURRENT SUB_VIDEO VIDEOIDLE 1` (+ `/setdcvalueindex` for battery), then `/setactive SCHEME_CURRENT` to apply.
4. Sleep ~5 seconds. Windows itself fires its native display-off code as the kernel's idle counter crosses the 1-second threshold.
5. Restore the original timeouts. Verify the restore via a follow-up read; clear the sentinel only on affirmative match.

**No `SC_MONITORPOWER` message is ever sent.** The native idle-display-off code path has been working reliably on every Windows version since Win95 and is the same one OEM drivers expect to see.

All `powercfg.exe` invocations run under `subprocess.run` with `creationflags=CREATE_NO_WINDOW` + `STARTUPINFO(SW_HIDE)` so the child processes don't flash console windows under `pythonw.exe`.

The 5-second sleep includes a 0.5-second pre-blank settle so the triggering click or keypress doesn't leak into the idle-counter window. An in-process lock for that ~5.5 seconds drops duplicate triggers (logged to `displayoff.log`).

### Optional — legacy `SC_MONITORPOWER`

When opted in, `_fire_sc_monitorpower` sends `WM_SYSCOMMAND` with `SC_MONITORPOWER = 2` to `GetDesktopWindow()` via `SendMessageTimeoutW`. Single-window target instead of `HWND_BROADCAST` — avoids GPU driver crashes on resume that broadcast historically caused.

This is the mechanism every monitor-off tool out there uses (NirCmd, AutoHotkey, asheroto's PowerShell gist). Works on most hardware. Doesn't work on Modern Standby + hybrid-GPU.

### Lock + RDP guards

When **Lock workstation when blanking** is enabled (or `--lock-and-off`), Display Off calls `LockWorkStation` before the blank with a brief settle delay for the secure-desktop transition. Applies to both blank paths.

Inside an RDP / Terminal Services session, both paths are skipped (no physical monitors to power off) and the action is logged.

### Win11 tray-icon auto-promote

`tray_promoter.py` writes the undocumented `IsPromoted=1` value in `HKCU\Control Panel\NotifyIconSettings\<hash>` so the icon isn't hidden in Win11's overflow flyout on first run. Respects users who deliberately hide the icon (`IsPromoted=0` stays 0). See [Caveats](#caveats) for the cataloging quirk.

### Sentinel-based crash recovery

If the native blank path is killed mid-write (hard reboot, process kill, Task Manager), the next launch restores the original timeouts from the on-disk sentinel before doing anything else. v1.7.6+ writes atomically so a partial-write kill doesn't leave a corrupt sentinel.

### Listener watchdog

30-second-poll auto-restart of the global hotkey listener if its thread dies (e.g. after a session lock, RDP transition, or fast-user-switch).

### Update check (cached)

The manual **Check for Updates** button (Settings dialog) hits `api.github.com/repos/itsnateai/displayoff/releases/latest`. GitHub's unauthenticated API limit is 60 req/hour per IP — shared with `gh`, GitHub Desktop, VS Code extensions, etc. v1.7.6+ caches the response for 6 hours and shows a clear "rate-limited" message instead of a generic network error when the cap is hit.

## Caveats

- **Production blank is ~5 seconds** on the native path, vs. ~0.5 seconds on the legacy path. Acceptable for the "click and walk away" use case; if you want faster blanking and your hardware doesn't trip the legacy bug, flip `use_legacy_sc_monitorpower: true` in config.
- **First-run tray icon may land in Win11's overflow flyout** rather than the main taskbar tray. Win11 catalogs `pythonw.exe`-shared icons lazily; on first run, click the up-arrow (`^`) chevron once. The promoter ensures `IsPromoted=1` stays set across every restart afterward. Alternative: *Settings → Personalization → Taskbar → Other system tray icons → Display Off → On*.
- **The right-click menu has no "Turn Off Displays" item.** Use double-click or the hotkey. Empirically, menu-item-triggered invocations ran the identical code chain but the kernel didn't act on the policy change (best hypothesis: `powercfg /setactive SCHEME_CURRENT` is a lazy refresh that gets optimized away when the active scheme is unchanged).
- **Hotkey may be silently unavailable when an elevated window has focus.** Windows UIPI prevents low-privilege keyboard hooks (pynput's) from receiving input destined for elevated processes — Task Manager, an admin-elevated terminal, a UAC consent dialog. The tray icon still works.
- **Single instance is per-user.** Each Windows user can run their own copy in their own session (Fast User Switching supported).

## Logs

`pythonw.exe`-mode runs log to two files next to the script:

- **`displayoff.log`** — tray-app events: hotkey registration, click triggers, lock-collision drops, errors.
- **`native_blank.log`** — native idle-blank events: scheme read, sentinel writes, timeout writes, sleep with idle-counter samples, restore verification.

Both use `RotatingFileHandler` (1 MB cap, 3 backup files) — each tops out at ~4 MB before the oldest rolls off.

## Dependencies

| Package | Pinned | License | Purpose |
|---------|--------|---------|---------|
| [pystray](https://pypi.org/project/pystray/) | 0.19.5 | LGPL-3.0 | System tray icon and menu |
| [Pillow](https://pypi.org/project/Pillow/) | 12.2.0 | MIT-CMU | Icon image handling |
| [pynput](https://pypi.org/project/pynput/) | 1.8.1 | LGPL-3.0 | Global hotkey listener |

## License

MIT — see [LICENSE](LICENSE).

## Links

- Found a bug? [Open an issue](https://github.com/itsnateai/displayoff/issues)
- Release history → [CHANGELOG.md](CHANGELOG.md) · [Releases](https://github.com/itsnateai/displayoff/releases)
