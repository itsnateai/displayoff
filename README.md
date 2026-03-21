# Display Off

Tiny system tray utility that turns off all monitors without putting the PC to sleep.

Click the tray icon or press **Ctrl+Alt+F12** to sleep displays instantly. Move the mouse or press any key to wake.

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

## How It Works

Sends `WM_SYSCOMMAND` with `SC_MONITORPOWER = 2` to `HWND_BROADCAST` via the Win32 API. A 300ms delay after the trigger prevents the mouse click or key release from immediately waking the displays.

The global hotkey listener (pynput) runs in a background thread. If pynput is not installed, the app still works via the tray icon — the hotkey is simply disabled.

## Dependencies

| Package | Purpose |
|---------|---------|
| [pystray](https://pypi.org/project/pystray/) | System tray icon and menu |
| [Pillow](https://pypi.org/project/Pillow/) | Icon image handling |
| [pynput](https://pypi.org/project/pynput/) | Global hotkey listener (optional) |

## License

MIT
