"""Display Off — Force all monitors to sleep without putting the PC to sleep.

Sits in the system tray. Click the tray icon or press the global hotkey
(Ctrl+Alt+F12 by default) to turn off all displays instantly. Move the
mouse or press any key to wake them.

Requirements:
    pip install pystray Pillow pynput

Usage:
    python displayoff.py              # Start in tray
    python displayoff.py --off        # Turn off immediately (no tray)
    python displayoff.py --version    # Print version
    pythonw displayoff.py             # Start in tray, no console window
"""
import ctypes
import logging
import os
import sys
import threading
import time

__version__ = "1.1.0"

log = logging.getLogger("displayoff")
logging.basicConfig(
    level=logging.INFO,
    format="[%(name)s] %(message)s",
)

_ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "displayoff.ico")

# Win32 constants
SC_MONITORPOWER = 0xF170
HWND_BROADCAST = 0xFFFF
WM_SYSCOMMAND = 0x0112
MONITOR_OFF = 2

# Set up SendMessageW with correct type signatures for 64-bit safety.
# Without argtypes, ctypes defaults to c_int (4 bytes) which truncates
# pointer-sized values (HWND, WPARAM, LPARAM) on 64-bit Windows.
if sys.platform == "win32":
    import ctypes.wintypes

    SendMessageW = ctypes.windll.user32.SendMessageW
    SendMessageW.argtypes = [
        ctypes.wintypes.HWND,
        ctypes.wintypes.UINT,
        ctypes.wintypes.WPARAM,
        ctypes.wintypes.LPARAM,
    ]
    SendMessageW.restype = ctypes.wintypes.LPARAM
else:
    SendMessageW = None

# Guard against concurrent/repeated triggers — only one turn-off at a time
_turn_off_lock = threading.Lock()


def turn_off_monitors():
    """Send SC_MONITORPOWER to turn off all displays."""
    if SendMessageW is None:
        log.warning("Not on Windows — monitor power control unavailable.")
        return

    if not _turn_off_lock.acquire(blocking=False):
        return  # Already in progress, skip duplicate

    try:
        # Small delay so the mouse click / hotkey release doesn't immediately wake
        time.sleep(0.3)
        SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_OFF)
    finally:
        _turn_off_lock.release()


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
        TRIGGER_KEY = keyboard.Key.f12
        CTRL_KEYS = {keyboard.Key.ctrl_l, keyboard.Key.ctrl_r}
        ALT_KEYS = {keyboard.Key.alt_l, keyboard.Key.alt_r}

        def _hotkey_active():
            """Check if any Ctrl + any Alt + F12 are held."""
            return (
                TRIGGER_KEY in current_keys
                and bool(CTRL_KEYS & current_keys)
                and bool(ALT_KEYS & current_keys)
            )

        def on_press(key):
            current_keys.add(key)
            if _hotkey_active() and not _turn_off_lock.locked():
                threading.Thread(target=turn_off_monitors, daemon=True).start()

        def on_release(key):
            current_keys.discard(key)
            # Cap set size to prevent unbounded growth from missed release events
            if len(current_keys) > 20:
                current_keys.clear()

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.daemon = True
        listener.start()
        log.info("Global hotkey registered: Ctrl+Alt+F12")
    except ImportError:
        log.warning("pynput not installed — hotkey disabled. Install with: pip install pynput")
    except Exception as e:
        log.error("Hotkey registration failed: %s", e)


def run_tray():
    """Run as a system tray application."""
    import pystray
    from pystray import MenuItem, Menu

    if os.path.isfile(_ICON_PATH):
        from PIL import Image
        icon_image = Image.open(_ICON_PATH)
    else:
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

    log.info("Running in system tray. Click icon or press Ctrl+Alt+F12 to turn off displays.")
    icon.run()


def main():
    if "--version" in sys.argv:
        print(f"displayoff {__version__}")
        return

    if "--off" in sys.argv:
        log.info("Turning off displays...")
        turn_off_monitors()
        return

    if "--start-off" in sys.argv:
        log.info("Turning off displays, then starting tray...")
        turn_off_monitors()

    try:
        run_tray()
    except ImportError:
        log.warning("pystray not installed. Install with: pip install pystray Pillow")
        log.info("Running in --off mode instead...")
        turn_off_monitors()


if __name__ == "__main__":
    main()
