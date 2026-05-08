# Display Off

Tiny system tray utility that turns off all monitors without putting the PC to sleep.

Click the tray icon or press a configurable hotkey (default **Ctrl+Alt+F12**) to sleep displays instantly. Move the mouse or press any key to wake.

## Features

- **System tray** — runs quietly in the background
- **Configurable hotkey** — change via Settings GUI (right-click tray icon → Settings)
- **Lock-on-blank** — optional, locks the workstation before powering off displays
- **Auto-blank when idle** — optional, fires after N minutes of inactivity
- **Autostart toggle** — register/unregister Display Off in Windows startup with one click
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
python displayoff.py --off        # Turn off displays immediately, then exit (honors lock-on-off config)
python displayoff.py --lock-and-off   # Lock workstation, then turn off displays
python displayoff.py --no-lock-off    # Turn off displays without locking (override config)
python displayoff.py --start-off  # Turn off, then start tray
python displayoff.py --quit-other # Signal a running tray instance to quit cleanly
python displayoff.py --reset-config   # Delete the config file
python displayoff.py --version    # Print version
pythonw displayoff.py             # Start in tray without a console window
```

## Configuration

Right-click the tray icon → **Settings** to open the settings window:

- **Hotkey** — click the field, then press your desired combination. Esc cancels recording.
- **Lock workstation when blanking** — when checked, Display Off will press Win+L before powering off the screens.
- **Run at Windows startup** — when checked, Display Off is registered in `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` to launch on logon (uses `pythonw.exe` so there's no console flash).
- **Auto-blank after N minutes idle** — when set above 0, Display Off polls Win32 `GetLastInputInfo` every 15 seconds and fires once when you've been idle for the threshold. Re-arms after any user activity. Set to 0 to disable.

Click **Save** to apply and close, **Apply** to persist without closing, or **Cancel** to close the window. Cancel discards any in-dialog edits not yet Saved or Applied; changes that have already been Applied stay persisted on disk.

Settings are stored in `displayoff_config.json` next to the script. Autostart is stored in the registry.

## How It Works

Sends `WM_SYSCOMMAND` with `SC_MONITORPOWER = 2` to the desktop window via `SendMessageTimeoutW`. Targets a single window instead of broadcasting to all top-level windows, which avoids GPU driver crashes on resume.

The global hotkey listener (pynput) runs in a background thread. If pynput is not installed, the app still works via the tray icon — the hotkey is simply disabled.

When the **Lock workstation when blanking** option is enabled, Display Off calls `LockWorkStation` before sending the monitor-power message, with a brief settle delay so the secure-desktop transition has time to render.

Inside an RDP / Terminal Services session, `SC_MONITORPOWER` is skipped (the virtual desktop has no physical monitors to power off) and the action is logged.

## Caveats

- **Hotkey may be silently unavailable when an elevated window has focus.** Windows UIPI prevents low-privilege keyboard hooks (like pynput's) from receiving input destined for elevated processes — Task Manager, an admin-elevated terminal, or a UAC consent dialog. The tray icon still works in that case.
- **Single instance** is per-user. Each Windows user can run their own copy in their own session (Fast User Switching is supported).

## Dependencies

| Package | Purpose |
|---------|---------|
| [pystray](https://pypi.org/project/pystray/) | System tray icon and menu |
| [Pillow](https://pypi.org/project/Pillow/) | Icon image handling |
| [pynput](https://pypi.org/project/pynput/) | Global hotkey listener (optional) |

## License

MIT
