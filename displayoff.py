"""Display Off — Force all monitors to sleep without putting the PC to sleep.

Sits in the system tray. Click the tray icon or press the global hotkey
(Ctrl+Alt+F12 by default) to turn off all displays instantly. Move the
mouse or press any key to wake them.

Requirements:
    pip install pystray Pillow pynput

Usage:
    python displayoff.py              # Start in tray
    python displayoff.py --off        # Turn off immediately (no tray)
    pythonw displayoff.py             # Start in tray, no console window
"""
import ctypes
import sys
import threading
import time

# Win32 constants
SC_MONITORPOWER = 0xF170
HWND_BROADCAST = 0xFFFF
WM_SYSCOMMAND = 0x0112
MONITOR_OFF = 2
MONITOR_ON = -1

SendMessageW = ctypes.windll.user32.SendMessageW


def turn_off_monitors():
    """Send SC_MONITORPOWER to turn off all displays."""
    # Small delay so the mouse click / hotkey release doesn't immediately wake
    time.sleep(0.3)
    SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_OFF)


def _create_icon_image():
    """Create a small moon/monitor icon programmatically."""
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Dark circle background
    draw.ellipse([2, 2, size - 2, size - 2], fill=(15, 15, 30, 255))

    # Monitor shape
    draw.rectangle([14, 16, 50, 40], outline=(100, 100, 200, 255), width=2)
    # Stand
    draw.rectangle([28, 42, 36, 48], fill=(100, 100, 200, 255))
    draw.rectangle([22, 48, 42, 50], fill=(100, 100, 200, 255))

    # Moon crescent (sleep indicator)
    draw.ellipse([30, 20, 46, 36], fill=(255, 200, 50, 200))
    draw.ellipse([34, 18, 50, 34], fill=(15, 15, 30, 255))

    return img


def _start_hotkey_listener():
    """Listen for global hotkey (Ctrl+Alt+F12) in background thread."""
    try:
        from pynput import keyboard

        current_keys = set()
        HOTKEY = {keyboard.Key.ctrl_l, keyboard.Key.alt_l, keyboard.Key.f12}
        HOTKEY_R = {keyboard.Key.ctrl_r, keyboard.Key.alt_r, keyboard.Key.f12}

        def on_press(key):
            current_keys.add(key)
            if HOTKEY.issubset(current_keys) or HOTKEY_R.issubset(current_keys):
                threading.Thread(target=turn_off_monitors, daemon=True).start()

        def on_release(key):
            current_keys.discard(key)

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.daemon = True
        listener.start()
        print("[displayoff] Global hotkey registered: Ctrl+Alt+F12")
    except ImportError:
        print("[displayoff] pynput not installed — hotkey disabled. Install with: pip install pynput")
    except Exception as e:
        print(f"[displayoff] Hotkey registration failed: {e}")


def run_tray():
    """Run as a system tray application."""
    import pystray
    from pystray import MenuItem, Menu

    icon_image = _create_icon_image()

    def on_turn_off(icon, item):
        threading.Thread(target=turn_off_monitors, daemon=True).start()

    def on_quit(icon, item):
        icon.stop()

    menu = Menu(
        MenuItem("Display Off", None, enabled=False),
        Menu.SEPARATOR,
        MenuItem("Turn Off Displays", on_turn_off, default=True),
        MenuItem("Hotkey: Ctrl+Alt+F12", None, enabled=False),
        Menu.SEPARATOR,
        MenuItem("Quit", on_quit),
    )

    icon = pystray.Icon(
        name="displayoff",
        icon=icon_image,
        title="Display Off — Click to sleep monitors",
        menu=menu,
    )

    # Start hotkey listener
    _start_hotkey_listener()

    print("[displayoff] Running in system tray. Click icon or press Ctrl+Alt+F12 to turn off displays.")
    icon.run()


def main():
    if "--off" in sys.argv:
        print("Turning off displays...")
        turn_off_monitors()
        return

    try:
        run_tray()
    except ImportError:
        print("pystray not installed. Install with: pip install pystray Pillow")
        print("Running in --off mode instead...")
        turn_off_monitors()


if __name__ == "__main__":
    main()
