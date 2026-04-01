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
import json
import logging
import os
import sys
import threading
import time

__version__ = "1.2.1"

log = logging.getLogger("displayoff")
logging.basicConfig(
    level=logging.INFO,
    format="[%(name)s] %(message)s",
)

_ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "displayoff.ico")

# Win32 constants
SC_MONITORPOWER = 0xF170
WM_SYSCOMMAND = 0x0112
MONITOR_OFF = 2

# Set up Win32 functions with correct type signatures for 64-bit safety.
# Without argtypes, ctypes defaults to c_int (4 bytes) which truncates
# pointer-sized values (HWND, WPARAM, LPARAM) on 64-bit Windows.
if sys.platform == "win32":
    import ctypes.wintypes

    _user32 = ctypes.windll.user32

    SendMessageTimeoutW = _user32.SendMessageTimeoutW
    SendMessageTimeoutW.argtypes = [
        ctypes.wintypes.HWND,
        ctypes.wintypes.UINT,
        ctypes.wintypes.WPARAM,
        ctypes.wintypes.LPARAM,
        ctypes.wintypes.UINT,   # fuFlags
        ctypes.wintypes.UINT,   # uTimeout (ms)
        ctypes.POINTER(ctypes.wintypes.DWORD),  # lpdwResult
    ]
    SendMessageTimeoutW.restype = ctypes.wintypes.LPARAM

    GetForegroundWindow = _user32.GetForegroundWindow
    GetForegroundWindow.argtypes = []
    GetForegroundWindow.restype = ctypes.wintypes.HWND

    GetDesktopWindow = _user32.GetDesktopWindow
    GetDesktopWindow.argtypes = []
    GetDesktopWindow.restype = ctypes.wintypes.HWND

    SMTO_ABORTIFHUNG = 0x0002
else:
    SendMessageTimeoutW = None

# Guard against concurrent/repeated triggers — only one turn-off at a time
_turn_off_lock = threading.Lock()

# Suppress hotkey while settings window is open
_settings_open = False

# ── Config ──────────────────────────────────────────────────────────────────

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "displayoff_config.json")

_DEFAULT_CONFIG = {
    "hotkey": {
        "modifiers": ["ctrl", "alt"],
        "key": "f12",
    }
}


def load_config():
    """Load config from JSON, falling back to defaults."""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        # Ensure required keys exist
        if "hotkey" not in cfg or "modifiers" not in cfg["hotkey"] or "key" not in cfg["hotkey"]:
            return dict(_DEFAULT_CONFIG)
        return cfg
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_DEFAULT_CONFIG)


def save_config(cfg):
    """Save config to JSON."""
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def hotkey_display_name(cfg=None):
    """Return a human-readable hotkey string like 'Ctrl+Alt+F12'."""
    if cfg is None:
        cfg = load_config()
    hk = cfg["hotkey"]
    parts = [m.capitalize() for m in hk["modifiers"]] + [hk["key"].upper()]
    return "+".join(parts)


# ── Hotkey listener (restartable) ───────────────────────────────────────────

_active_listener = None
_listener_lock = threading.Lock()

# Maps config modifier names to pynput Key sets
_MODIFIER_MAP = None  # Lazy-loaded when pynput is available


def _get_modifier_map():
    global _MODIFIER_MAP
    if _MODIFIER_MAP is None:
        from pynput import keyboard
        _MODIFIER_MAP = {
            "ctrl": {keyboard.Key.ctrl_l, keyboard.Key.ctrl_r},
            "alt": {keyboard.Key.alt_l, keyboard.Key.alt_r},
            "shift": {keyboard.Key.shift_l, keyboard.Key.shift_r},
        }
    return _MODIFIER_MAP


def _resolve_key(key_name):
    """Convert a config key name like 'f12' or 'a' to a pynput Key/KeyCode."""
    from pynput import keyboard
    # Try special keys first (F1-F24, etc.)
    try:
        return getattr(keyboard.Key, key_name.lower())
    except AttributeError:
        pass
    # Single character key
    if len(key_name) == 1:
        return keyboard.KeyCode.from_char(key_name.lower())
    return None


def start_hotkey_listener(cfg=None):
    """Start (or restart) the global hotkey listener based on current config."""
    global _active_listener

    with _listener_lock:
        # Stop existing listener
        if _active_listener is not None:
            _active_listener.stop()
            _active_listener = None

        try:
            from pynput import keyboard
        except ImportError:
            log.warning("pynput not installed — hotkey disabled.")
            return

        if cfg is None:
            cfg = load_config()

        hk = cfg["hotkey"]
        mod_map = _get_modifier_map()

        required_modifiers = []
        for m in hk["modifiers"]:
            if m in mod_map:
                required_modifiers.append(mod_map[m])

        trigger_key = _resolve_key(hk["key"])
        if trigger_key is None:
            log.error("Unknown hotkey: %s", hk["key"])
            return

        current_keys = set()

        def _hotkey_active():
            if trigger_key not in current_keys:
                return False
            return all(bool(mod_set & current_keys) for mod_set in required_modifiers)

        def on_press(key):
            current_keys.add(key)
            if _hotkey_active() and not _turn_off_lock.locked() and not _settings_open:
                threading.Thread(target=turn_off_monitors, daemon=True).start()

        def on_release(key):
            current_keys.discard(key)
            if len(current_keys) > 20:
                current_keys.clear()

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.daemon = True
        listener.start()
        _active_listener = listener
        name = hotkey_display_name(cfg)
        log.info("Global hotkey registered: %s", name)


def turn_off_monitors():
    """Send SC_MONITORPOWER to turn off all displays.

    Sends to the desktop window (not HWND_BROADCAST) — the kernel powers off
    all monitors regardless of which window receives the message. Broadcasting
    floods every top-level window with WM_SYSCOMMAND, which can crash GPU
    drivers on resume when queued messages fight the wake process.

    Uses SendMessageTimeoutW with SMTO_ABORTIFHUNG so we never hang if the
    target window is unresponsive.
    """
    if SendMessageTimeoutW is None:
        log.warning("Not on Windows — monitor power control unavailable.")
        return

    if not _turn_off_lock.acquire(blocking=False):
        return  # Already in progress, skip duplicate

    try:
        # Wait for input events to settle so the click/keypress that triggered
        # this doesn't immediately wake the monitors back up
        time.sleep(0.5)
        result = ctypes.wintypes.DWORD(0)
        hwnd = GetDesktopWindow()
        SendMessageTimeoutW(
            hwnd, WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_OFF,
            SMTO_ABORTIFHUNG, 5000, ctypes.byref(result),
        )
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


def _pynput_key_to_name(key):
    """Convert a pynput key object to a config-friendly name."""
    from pynput import keyboard
    if isinstance(key, keyboard.Key):
        return key.name  # e.g. "f12", "space", "tab"
    if isinstance(key, keyboard.KeyCode) and key.char:
        return key.char.lower()
    if isinstance(key, keyboard.KeyCode) and key.vk:
        return f"vk{key.vk}"
    return None


def _open_settings(tray_icon, on_saved=None):
    """Open a tkinter settings window for hotkey configuration."""
    global _settings_open
    import tkinter as tk
    from tkinter import ttk

    _settings_open = True
    cfg = load_config()
    captured = {"modifiers": list(cfg["hotkey"]["modifiers"]), "key": cfg["hotkey"]["key"]}

    root = tk.Tk()
    root.title("Display Off — Settings")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    root.configure(bg="#f0f0f0")

    PAD = 20  # consistent outer margin

    # Center on screen
    w, h = 400, 220
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")

    # ── Header ──
    header = tk.Label(root, text=f"Display Off v{__version__}",
                      font=("Segoe UI", 13, "bold"), bg="#f0f0f0", anchor="w")
    header.grid(row=0, column=0, columnspan=3, sticky="w", padx=PAD, pady=(PAD, 2))

    sep = ttk.Separator(root, orient="horizontal")
    sep.grid(row=1, column=0, columnspan=3, sticky="ew", padx=PAD, pady=(0, 12))

    # ── Hotkey row ──
    # Click the field to start listening, press your combo, done.
    hotkey_lbl = tk.Label(root, text="Hotkey:", font=("Segoe UI", 10), bg="#f0f0f0", anchor="e")
    hotkey_lbl.grid(row=2, column=0, sticky="e", padx=(PAD, 8), pady=4)

    display_var = tk.StringVar(value=hotkey_display_name(cfg))
    recording = {"active": False}

    hotkey_display = tk.Label(root, textvariable=display_var, font=("Segoe UI", 11),
                              relief="sunken", bg="white", anchor="center", width=28,
                              pady=6, cursor="hand2")
    hotkey_display.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, PAD), pady=4)

    hint = tk.Label(root, text="Click the field above, then press your hotkey",
                    font=("Segoe UI", 8), fg="#888888", bg="#f0f0f0")
    hint.grid(row=3, column=1, columnspan=2, sticky="w", pady=(0, 4))

    def start_recording(event=None):
        if recording["active"]:
            return
        recording["active"] = True
        display_var.set("Press your hotkey...")
        hotkey_display.config(bg="#fff8e0", relief="solid")

        from pynput import keyboard as kb

        pressed_mods = set()
        final_key = [None]
        mod_names_map = {"ctrl_l": "ctrl", "ctrl_r": "ctrl", "alt_l": "alt", "alt_r": "alt",
                         "shift_l": "shift", "shift_r": "shift"}

        def on_press(key):
            name = None
            if isinstance(key, kb.Key):
                name = key.name
            if name in mod_names_map:
                pressed_mods.add(mod_names_map[name])
            else:
                final_key[0] = key

        def on_release(key):
            if final_key[0] is not None:
                listener.stop()

        listener = kb.Listener(on_press=on_press, on_release=on_release)
        listener.daemon = True
        listener.start()

        def poll_capture():
            if listener.running:
                root.after(50, poll_capture)
                return
            key_name = _pynput_key_to_name(final_key[0])
            if key_name:
                captured["modifiers"] = sorted(pressed_mods) if pressed_mods else []
                captured["key"] = key_name
                display_var.set(hotkey_display_name({"hotkey": captured}))
            else:
                display_var.set(hotkey_display_name(cfg))
            hotkey_display.config(bg="white", relief="sunken")
            recording["active"] = False

        root.after(50, poll_capture)

    hotkey_display.bind("<Button-1>", start_recording)

    # Let the hotkey display stretch
    root.columnconfigure(1, weight=1)

    # ── Footer: GitHub link + buttons ──
    footer = tk.Frame(root, bg="#f0f0f0")
    footer.grid(row=4, column=0, columnspan=3, sticky="ew", padx=PAD, pady=(16, PAD))

    tk.Button(footer, text="GitHub", command=lambda: os.startfile("https://github.com/itsnateai/displayoff"),
              font=("Segoe UI", 9), width=8).pack(side="left")

    def _close_settings():
        global _settings_open
        _settings_open = False

    def on_cancel():
        _close_settings()
        root.destroy()

    def on_save():
        cfg["hotkey"] = dict(captured)
        save_config(cfg)
        start_hotkey_listener(cfg)
        if on_saved:
            on_saved(cfg)
        _close_settings()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_cancel)

    tk.Button(footer, text="Cancel", command=on_cancel, font=("Segoe UI", 9),
              width=8).pack(side="right", padx=(4, 0))
    tk.Button(footer, text="Save", command=on_save, font=("Segoe UI", 9),
              width=8).pack(side="right", padx=(0, 4))

    root.mainloop()


def run_tray():
    """Run as a system tray application."""
    import pystray
    from pystray import MenuItem, Menu

    if os.path.isfile(_ICON_PATH):
        from PIL import Image
        icon_image = Image.open(_ICON_PATH)
    else:
        icon_image = _create_icon_image()

    cfg = load_config()
    hotkey_name = [hotkey_display_name(cfg)]  # mutable so menu callback can update

    def on_turn_off(icon, item):
        threading.Thread(target=turn_off_monitors, daemon=True).start()

    def on_settings(icon, item):
        def on_saved(new_cfg):
            hotkey_name[0] = hotkey_display_name(new_cfg)
            icon.update_menu()
        threading.Thread(target=_open_settings, args=(icon, on_saved), daemon=True).start()

    def on_quit(icon, item):
        icon.stop()

    menu = Menu(
        MenuItem(f"Display Off v{__version__}", None, enabled=False),
        Menu.SEPARATOR,
        MenuItem("Turn Off Displays", on_turn_off),
        MenuItem(lambda item: f"Hotkey: {hotkey_name[0]}", None, enabled=False),
        Menu.SEPARATOR,
        MenuItem("Settings...", on_settings),
        MenuItem("Quit", on_quit),
    )

    icon = pystray.Icon(
        name="displayoff",
        icon=icon_image,
        title="Display Off — Click to sleep monitors",
        menu=menu,
    )

    start_hotkey_listener(cfg)

    hk = hotkey_name[0]
    log.info("Running in system tray. Click icon or press %s to turn off displays.", hk)
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
