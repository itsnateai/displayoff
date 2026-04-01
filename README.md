# Display Off

Tiny system tray utility that turns off all monitors without putting the PC to sleep.

Click the tray icon or press a configurable hotkey (default **Ctrl+Alt+F12**) to sleep displays instantly. Move the mouse or press any key to wake.

## Features

- **System tray** — runs quietly in the background
- **Configurable hotkey** — change via Settings GUI (right-click tray icon → Settings)
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
python displayoff.py --off        # Turn off displays immediately, then exit
python displayoff.py --version    # Print version
pythonw displayoff.py             # Start in tray without a console window
```

## Configuration

Right-click the tray icon → **Settings** to open the settings window. Click **Record**, press your desired key combination, then click **Save**. The new hotkey takes effect immediately — no restart needed.

Settings are stored in `displayoff_config.json` next to the script.

## How It Works

Sends `WM_SYSCOMMAND` with `SC_MONITORPOWER = 2` to the desktop window via `SendMessageTimeoutW`. Targets a single window instead of broadcasting to all top-level windows, which avoids GPU driver crashes on resume.

The global hotkey listener (pynput) runs in a background thread. If pynput is not installed, the app still works via the tray icon — the hotkey is simply disabled.

## Dependencies

| Package | Purpose |
|---------|---------|
| [pystray](https://pypi.org/project/pystray/) | System tray icon and menu |
| [Pillow](https://pypi.org/project/Pillow/) | Icon image handling |
| [pynput](https://pypi.org/project/pynput/) | Global hotkey listener (optional) |

## License

MIT
