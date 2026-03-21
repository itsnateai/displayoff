# CLAUDE.md — Display Off

## Overview
Tiny system tray utility that turns off all monitors without putting the PC to sleep. Click the tray icon or press Ctrl+Alt+F12 to sleep displays instantly. Move mouse or press any key to wake.

## Tech Stack
- **Python 3.14** — core logic
- **ctypes + ctypes.wintypes** — Win32 `SendMessageW` for `SC_MONITORPOWER` (with proper 64-bit type signatures)
- **pystray + Pillow** — system tray icon (loads `displayoff.ico` if present, falls back to programmatic icon)
- **pynput** — global hotkey listener (optional)
- **logging** — structured log output via `logging.getLogger("displayoff")`

## Build & Run
```bash
pip install -r requirements.txt
python displayoff.py              # Tray mode
python displayoff.py --off        # Immediate off, no tray
python displayoff.py --version    # Print version
pythonw displayoff.py             # Tray mode, no console
```

## Key Files
- `displayoff.py` — Single-file app (tray + hotkey + Win32 call)
- `displayoff.ico` — Multi-size icon (16–256px) for crisp tray rendering
- `requirements.txt` — Dependencies
- `README.md` — User-facing documentation

## How It Works
- Sends `WM_SYSCOMMAND` with `SC_MONITORPOWER = 2` to `HWND_BROADCAST`
- 300ms delay after trigger so the mouse click doesn't immediately wake
- pynput listener for Ctrl+Alt+F12 runs in background thread (accepts any left/right modifier combo)
- Tray icon click triggers the same function
- `threading.Lock` prevents concurrent/duplicate turn-off triggers
- `current_keys` set is capped at 20 entries to prevent unbounded growth from missed release events
- `ctypes.windll` is guarded behind `sys.platform == "win32"` so the module can be imported on non-Windows

## Status
- **Version:** 1.1.0
- **State:** Complete
