# CLAUDE.md — Display Off

## Overview
Tiny system tray utility that turns off all monitors without putting the PC to sleep. Click the tray icon or press Ctrl+Alt+F12 to sleep displays instantly. Move mouse or press any key to wake.

## Tech Stack
- **Python 3.14** — core logic
- **ctypes** — Win32 `SendMessageW` for `SC_MONITORPOWER`
- **pystray + Pillow** — system tray icon
- **pynput** — global hotkey listener (optional)

## Build & Run
```bash
pip install -r requirements.txt
python displayoff.py              # Tray mode
python displayoff.py --off        # Immediate off, no tray
pythonw displayoff.py             # Tray mode, no console
```

## Key Files
- `displayoff.py` — Single-file app (tray + hotkey + Win32 call)
- `requirements.txt` — Dependencies

## How It Works
- Sends `WM_SYSCOMMAND` with `SC_MONITORPOWER = 2` to `HWND_BROADCAST`
- 300ms delay after trigger so the mouse click doesn't immediately wake
- pynput listener for Ctrl+Alt+F12 runs in background thread
- Tray icon click triggers the same function

## Status
- **Version:** 1.0.0
- **State:** Complete
