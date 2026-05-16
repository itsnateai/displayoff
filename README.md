# Display Off

Tiny system tray utility that turns off all monitors without putting the PC to sleep.

**Double-click** the tray icon or press a configurable hotkey (default **Ctrl+Alt+F12**) to blank all displays. Move the mouse or press any key to wake.

## Features

- **System tray** — runs quietly in the background; double-click to blank, right-click for menu
- **Two blank paths** — chooses the right mechanism for your hardware:
  - **Native idle-display-off** (default in v1.6.0+) — temporarily writes a 1-second display-off timeout via the same Win32 power-policy API the Windows *Settings ▸ Power ▸ "Turn off the display after N minutes"* dropdown uses. Required on Modern Standby laptops + hybrid-GPU hardware where the legacy `SC_MONITORPOWER` mechanism cycles to reboot-required.
  - **Legacy `SC_MONITORPOWER`** (opt-in via config) — the classic mechanism used by NirCmd, AutoHotkey, PowerToys et al. Slightly faster (~0.5s vs ~5s); only works on hardware that doesn't trip the cycle bug.
- **Configurable hotkey** — change via Settings GUI (right-click tray icon → Settings)
- **Lock-on-blank** — optional, locks the workstation before powering off displays
- **Auto-blank when idle** — optional, fires after N minutes of inactivity
- **Autostart toggle** — register/unregister Display Off in Windows startup with one click
- **Win11 tray-icon auto-promote** — uses the same `IsPromoted=1` registry pattern as the developer's other tray apps (MicMute, SyncthingPause, etc.) to skip the "icon hidden in overflow on first run" experience. See [Caveats](#caveats) for the Win11 cataloging quirk specific to `pythonw.exe`-based apps.
- **Sentinel-based crash recovery** — if the native blank path is killed mid-write (hard reboot, process kill, etc.), the next launch restores the original timeouts from a sentinel file before doing anything else
- **Listener watchdog** — 30-second-poll auto-restart of the global hotkey listener if its thread dies (e.g. after a session lock, RDP transition, or fast-user-switch)
- **Check for Updates** — manual via tray menu (no automatic phone-home)
- **No admin required** — uses standard Win32 API
- **Lightweight** — single Python file, minimal dependencies

## Install

```bash
pip install -r requirements.txt
```

Requires **Python 3.14+** and **Windows**.

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

### Triggering a blank from a running tray

- **Double-click** the tray icon (single-click is a no-op — avoids accidental fires)
- Press **Ctrl+Alt+F12** (configurable via Settings)

The right-click menu shows the active hotkey and the available shortcuts as informational labels; there's no clickable "Turn Off Displays" menu item because empirical testing on Modern Standby hardware found that menu-item-triggered invocations executed the identical underlying code chain but the kernel did not act on the policy change. Double-click and the hotkey reliably fire on the same hardware where the menu item silently fails — so the menu item was removed rather than ship a silently-broken click.

## Configuration

Right-click the tray icon → **Settings** to open the settings window:

- **Hotkey** — click the field, then press your desired combination. Esc cancels recording.
- **Lock workstation when blanking** — when checked, Display Off will press Win+L before powering off the screens.
- **Run at Windows startup** — when checked, Display Off creates a `.lnk` shortcut in `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Display Off.lnk` so Windows launches it on logon (uses `pythonw.exe` so there's no console flash). v1.7.0+ uses the Startup folder; older installs that used the `HKCU\...\Run` registry key are auto-cleaned on first toggle.
- **Auto-blank after N minutes idle** — when set above 0, Display Off polls Win32 `GetLastInputInfo` every 15 seconds and fires once when you've been idle for the threshold. Re-arms after any user activity. Set to 0 to disable.

Click **Save** to apply and close, **Apply** to persist without closing, or **Cancel** to close the window. Cancel discards any in-dialog edits not yet Saved or Applied; changes that have already been Applied stay persisted on disk.

Settings are stored in `displayoff_config.json` next to the script. Autostart is stored as a `.lnk` shortcut in the user's Startup folder (see above).

### Choosing the blank mechanism

The `displayoff_config.json` file has one additional key not exposed in the GUI:

```json
{
  "use_legacy_sc_monitorpower": false
}
```

- `false` (default) — every blank trigger routes through the **native idle-display-off path** (`PowerWriteACValueIndex` + `PowerSetActiveScheme` with a 1-second timeout, then restore). Safe on every Windows version since Win95; required on hardware where the legacy mechanism cycles.
- `true` — every blank trigger routes through the **legacy `SC_MONITORPOWER`** path (`SendMessageTimeoutW(WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_OFF)`). Slightly faster blank (~0.5s vs ~5s) but **may cycle to reboot-required on Modern Standby + hybrid-GPU laptops** (verified failure on ASUS ROG Strix G614JV, 2026-05-14). Only flip this on if you've confirmed the legacy path works on your hardware.

You can also force a specific path one-shot via `--native-off` / `--legacy-off` CLI flags without touching the config.

## How It Works

### Default — native idle-display-off path

`turn_off_monitors()` is a dispatcher: depending on `cfg['use_legacy_sc_monitorpower']` (or an explicit `force_path=` parameter for the CLI flags), it routes to `_fire_native_idle_blank()` or `_fire_sc_monitorpower()`.

The native path lives in [`native_blank.py`](./native_blank.py) and does this dance:

1. Read the active power scheme + current AC/DC display-off timeouts (typically 10 min / 3 min).
2. Write a **sentinel file** to disk recording the saved values, so a hard crash mid-flight doesn't leave you stuck with a 1-second display-off timeout.
3. Write 1-second AC and DC timeouts via `powercfg /setacvalueindex SCHEME_CURRENT SUB_VIDEO VIDEOIDLE 1` (and `/setdcvalueindex` for battery), then `/setactive SCHEME_CURRENT` to apply.
4. Sleep ~5 seconds. Windows itself fires its native display-off code as the kernel's idle counter crosses the 1-second threshold (same path that fires after your normal 10-minute idle).
5. Restore the original AC/DC timeouts via the same `powercfg` calls. Verify the restore succeeded via a follow-up read; only clear the sentinel on affirmative match.

**No `SC_MONITORPOWER` message is ever sent.** That's the whole point — the legacy mechanism breaks on certain hardware (the developer's ROG Strix G614JV in particular: Modern Standby S0ix + Intel UHD/NVIDIA RTX 4060 Optimus hybrid). The native idle-display-off code path has been working reliably on every Windows version since Win95 and is the same one OEM drivers expect to see.

Both `powercfg.exe` invocations and the rest of the work happen under `subprocess.run` with `creationflags=CREATE_NO_WINDOW` + `STARTUPINFO(SW_HIDE)` so the ~5 child processes per blank don't flash console windows under `pythonw.exe`.

The 5-second sleep includes a 0.5-second pre-blank settle so the click or keypress that triggered the blank doesn't leak into the idle-counter window. The dispatcher holds an in-process lock for that ~5.5 seconds; duplicate triggers in that window are explicitly logged to `displayoff.log` and dropped (single-fire guard).

### Optional — legacy `SC_MONITORPOWER`

When `use_legacy_sc_monitorpower: true` is set in config (or `--legacy-off` is used), `_fire_sc_monitorpower` sends `WM_SYSCOMMAND` with `SC_MONITORPOWER = 2` to the desktop window via `SendMessageTimeoutW`. Targets a single window instead of broadcasting to all top-level windows, which avoids GPU driver crashes on resume that the broadcast approach historically caused.

This is the mechanism every monitor-off tool out there uses (NirCmd, AutoHotkey scripts, asheroto's PowerShell gist, etc.). Works on most hardware. Doesn't work on the specific Modern Standby + hybrid-GPU combo this app exists to help.

### Lock + RDP guards

When the **Lock workstation when blanking** option is enabled (or `--lock-and-off` is used), Display Off calls `LockWorkStation` before the blank, with a brief settle delay so the secure-desktop transition has time to render. Applies to both blank paths.

Inside an RDP / Terminal Services session, both paths are skipped (the virtual desktop has no physical monitors to power off) and the action is logged.

## Caveats

- **First-run tray icon may land in Win11's overflow flyout** rather than the main taskbar tray. This is Win11's default-hide behavior for new tray icons. The bundled `tray_promoter.py` writes `IsPromoted=1` in `HKCU\Control Panel\NotifyIconSettings\<hash>` to flip this — but Win11 catalogs `pythonw.exe`-shared tray icons *lazily* (often only after the user opens the overflow flyout once or visits *Settings → Personalization → Taskbar → Other system tray icons*). On first run, click the up-arrow (`^`) chevron in your taskbar tray once. From then on, the promoter ensures `IsPromoted=1` stays set across every restart. Alternative: in *Settings → Personalization → Taskbar → Other system tray icons*, find **Display Off** and flip the toggle to On manually — same end state.
- **The right-click menu has no "Turn Off Displays" item.** Use double-click or the hotkey. The menu shows a disabled informational label documenting this. Empirically the menu-item path executed the identical code chain as double-click but the kernel never acted on the policy change; best hypothesis is that `powercfg /setactive SCHEME_CURRENT` is a lazy refresh that gets optimized away when the active scheme is unchanged.
- **Hotkey may be silently unavailable when an elevated window has focus.** Windows UIPI prevents low-privilege keyboard hooks (like pynput's) from receiving input destined for elevated processes — Task Manager, an admin-elevated terminal, or a UAC consent dialog. The tray icon still works in that case.
- **Single instance** is per-user. Each Windows user can run their own copy in their own session (Fast User Switching is supported).
- **Production blank is ~5 seconds** on the native path, vs. ~0.5 seconds on the legacy path. Acceptable for the "click and walk away" use case; if you want faster blanking and your hardware doesn't trip the legacy bug, flip `use_legacy_sc_monitorpower: true` in config.

## Logs

For diagnostic purposes, `pythonw.exe`-mode runs (the default for the autostart shortcut) log to two files next to the script:

- **`displayoff.log`** — tray-app events: hotkey registration, click triggers, lock-collision drops, errors.
- **`native_blank.log`** — native idle-blank events: scheme read, sentinel writes, timeout writes, sleep with idle-counter samples, restore verification.

Both files use `RotatingFileHandler` with a 1 MB cap and 3 backup files (v1.7.2+) — so each tops out at ~4 MB total before the oldest backup rolls off. No manual cleanup needed.

## Dependencies

| Package | Purpose |
|---------|---------|
| [pystray](https://pypi.org/project/pystray/) | System tray icon and menu |
| [Pillow](https://pypi.org/project/Pillow/) | Icon image handling |
| [pynput](https://pypi.org/project/pynput/) | Global hotkey listener (optional) |

## License

MIT
