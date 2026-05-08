"""Display Off — Force all monitors to sleep without putting the PC to sleep.

Sits in the system tray. Click the tray icon or press the global hotkey
(Ctrl+Alt+F12 by default) to turn off all displays instantly. Move the
mouse or press any key to wake them.

Requirements:
    pip install pystray Pillow pynput

Usage:
    python displayoff.py              # Start in tray
    python displayoff.py --off        # Turn off immediately (honors lock-on-off config)
    python displayoff.py --lock-and-off   # Lock workstation, then turn off
    python displayoff.py --no-lock-off    # Turn off without locking (override config)
    python displayoff.py --start-off  # Turn off, then start tray
    python displayoff.py --quit-other # Signal a running tray instance to quit (no-op if none)
    python displayoff.py --reset-config   # Delete the config file
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
import webbrowser

try:
    import winreg  # Windows-only; used for autostart toggle
except ImportError:
    winreg = None

__version__ = "1.4.0"

log = logging.getLogger("displayoff")

# ── Paths ──────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ICON_PATH = os.path.join(_HERE, "displayoff.ico")
_CONFIG_PATH = os.path.join(_HERE, "displayoff_config.json")

# ── Single-instance guard ──────────────────────────────────────────────────
# Local\ scope = per-session. Each Windows user gets their own instance,
# Fast User Switching works correctly. Global\ would block other users.
_MUTEX_NAME = "Local\\DisplayOff_SingleInstance"
_mutex_handle = None

# ── Win32 constants ────────────────────────────────────────────────────────
SC_MONITORPOWER = 0xF170
WM_SYSCOMMAND = 0x0112
MONITOR_OFF = 2
SMTO_ABORTIFHUNG = 0x0002
SM_REMOTESESSION = 0x1000  # GetSystemMetrics index for "is this an RDP session?"
ERROR_ALREADY_EXISTS = 183

# ── Tunables ───────────────────────────────────────────────────────────────
_TRIGGER_SETTLE_SECS = 0.5      # Delay before powering off so the trigger keypress doesn't immediately wake.
_LOCK_SETTLE_SECS = 0.3         # Delay between LockWorkStation and SC_MONITORPOWER.
_SEND_TIMEOUT_MS = 5000         # SendMessageTimeoutW abort-if-hung timeout.
_KEY_TRACKER_OVERFLOW_CAP = 20  # Cap on tracked simultaneously-pressed keys (defense vs missed releases).

# ── Win32 bindings (Windows-only) ──────────────────────────────────────────
# Every call site must use the bound names from this block — never raw
# `ctypes.windll.*` lookups, which default to c_int restype and silently
# truncate pointer-sized values (HANDLE, HWND) on 64-bit Windows.
if sys.platform == "win32":
    import ctypes.wintypes

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _shell32 = ctypes.windll.shell32

    # ── user32 ──
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

    GetSystemMetrics = _user32.GetSystemMetrics
    GetSystemMetrics.argtypes = [ctypes.c_int]
    GetSystemMetrics.restype = ctypes.c_int

    LockWorkStation = _user32.LockWorkStation
    LockWorkStation.argtypes = []
    LockWorkStation.restype = ctypes.wintypes.BOOL

    GetLastInputInfo = _user32.GetLastInputInfo
    GetLastInputInfo.argtypes = [ctypes.c_void_p]  # POINTER(LASTINPUTINFO)
    GetLastInputInfo.restype = ctypes.wintypes.BOOL

    # ── kernel32 ──
    # HANDLE is pointer-sized; restype must be HANDLE (not int) on 64-bit.
    CreateMutexW = _kernel32.CreateMutexW
    CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR]
    CreateMutexW.restype = ctypes.wintypes.HANDLE

    CreateEventW = _kernel32.CreateEventW
    CreateEventW.argtypes = [ctypes.c_void_p, ctypes.wintypes.BOOL,
                             ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR]
    CreateEventW.restype = ctypes.wintypes.HANDLE

    OpenEventW = _kernel32.OpenEventW
    OpenEventW.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR]
    OpenEventW.restype = ctypes.wintypes.HANDLE

    SetEvent = _kernel32.SetEvent
    SetEvent.argtypes = [ctypes.wintypes.HANDLE]
    SetEvent.restype = ctypes.wintypes.BOOL

    WaitForSingleObject = _kernel32.WaitForSingleObject
    WaitForSingleObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
    WaitForSingleObject.restype = ctypes.wintypes.DWORD

    CloseHandle = _kernel32.CloseHandle
    CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    CloseHandle.restype = ctypes.wintypes.BOOL

    GetLastError = _kernel32.GetLastError
    GetLastError.argtypes = []
    GetLastError.restype = ctypes.wintypes.DWORD

    # DWORD restype matters: defaults to signed c_int which goes negative
    # after ~24.8 days of uptime, breaking idle-time arithmetic silently.
    GetTickCount = _kernel32.GetTickCount
    GetTickCount.argtypes = []
    GetTickCount.restype = ctypes.wintypes.DWORD

    # ── shell32 ──
    IsUserAnAdmin = _shell32.IsUserAnAdmin
    IsUserAnAdmin.argtypes = []
    IsUserAnAdmin.restype = ctypes.wintypes.BOOL
else:
    SendMessageTimeoutW = None
    GetForegroundWindow = None
    GetDesktopWindow = None
    GetSystemMetrics = None
    LockWorkStation = None
    GetLastInputInfo = None
    CreateMutexW = None
    CreateEventW = None
    OpenEventW = None
    SetEvent = None
    WaitForSingleObject = None
    CloseHandle = None
    GetLastError = None
    GetTickCount = None
    IsUserAnAdmin = None

# Win32 wait-result sentinels
_WAIT_OBJECT_0 = 0x00000000
_WAIT_ABANDONED = 0x00000080
_WAIT_TIMEOUT = 0x00000102
_WAIT_FAILED = 0xFFFFFFFF
_INFINITE = 0xFFFFFFFF
_EVENT_MODIFY_STATE = 0x0002


# ── Cross-thread state ─────────────────────────────────────────────────────
_turn_off_lock = threading.Lock()  # one turn-off in flight at a time
_dialog_lock = threading.Lock()    # serializes the check-and-claim of _dialog_active
_dialog_active = False             # True while any Tk window (Settings/About) is open;
                                   # read by the hotkey listener — CPython bool reads are atomic.


# ── Single-instance ────────────────────────────────────────────────────────

def _acquire_single_instance():
    """Acquire a named mutex to prevent multiple instances.

    Returns True if this is the only instance, False if another is running.
    The kernel reaps the mutex when the owning process exits; no explicit
    release is needed.
    """
    global _mutex_handle
    if sys.platform != "win32":
        return True
    _mutex_handle = CreateMutexW(None, True, _MUTEX_NAME)
    last_error = GetLastError()
    if last_error == ERROR_ALREADY_EXISTS:
        CloseHandle(_mutex_handle)
        _mutex_handle = None
        return False
    return True


# ── Config ─────────────────────────────────────────────────────────────────

_DEFAULT_CONFIG = {
    "hotkey": {
        "modifiers": ["ctrl", "alt"],
        "key": "f12",
    },
    "lock_on_off": False,
    "idle_blank_minutes": 0,  # 0 disables idle-trigger blanking.
}


def load_config():
    """Load config from JSON, falling back to defaults.

    Forward-compatible: missing top-level keys are filled from defaults so
    older configs continue to work after schema additions.
    """
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(_DEFAULT_CONFIG)
    if not isinstance(cfg, dict) or "hotkey" not in cfg:
        return dict(_DEFAULT_CONFIG)
    hk = cfg["hotkey"]
    if not isinstance(hk, dict) or "modifiers" not in hk or "key" not in hk:
        return dict(_DEFAULT_CONFIG)
    for k, v in _DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


def save_config(cfg):
    """Save config to JSON atomically via write-temp-then-rename.

    Atomic so concurrent readers (the idle watcher's `cfg_provider()` ticking
    every 15 s) never observe a half-written file. `os.replace` is atomic on
    NTFS for same-volume renames. Caller must still handle OSError.
    """
    tmp_path = _CONFIG_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp_path, _CONFIG_PATH)


def hotkey_display_name(cfg):
    """Return a human-readable hotkey string like 'Ctrl+Alt+F12'."""
    hk = cfg["hotkey"]
    parts = [m.capitalize() for m in hk["modifiers"]] + [hk["key"].upper()]
    return "+".join(parts)


# ── Autostart (HKCU\Software\Microsoft\Windows\CurrentVersion\Run) ─────────

_RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE_NAME = "DisplayOff"


def _autostart_command():
    """Build the command Windows runs at logon. Prefer pythonw.exe (no console flash)."""
    script = os.path.abspath(__file__)
    py = sys.executable
    if py.lower().endswith("python.exe"):
        pyw = py[:-len("python.exe")] + "pythonw.exe"
        if os.path.isfile(pyw):
            py = pyw
    return f'"{py}" "{script}"'


def autostart_enabled():
    """Return True if Display Off is registered in HKCU Run."""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH) as key:
            winreg.QueryValueEx(key, _RUN_VALUE_NAME)
        return True
    except (FileNotFoundError, OSError):
        return False


def set_autostart(enabled):
    """Register or unregister Display Off for autostart. Raises OSError on failure."""
    if winreg is None:
        raise OSError("winreg unavailable on this platform")
    if enabled:
        cmd = _autostart_command()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, _RUN_VALUE_NAME, 0, winreg.REG_SZ, cmd)
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, _RUN_VALUE_NAME)
        except FileNotFoundError:
            pass


# ── Hotkey listener (restartable) ──────────────────────────────────────────

_active_listener = None
_listener_lock = threading.Lock()
_MODIFIER_MAP = None  # Lazy-loaded when pynput is available
_MODIFIER_MAP_LOCK = threading.Lock()  # Guards the lazy init under nogil/free-threaded Python.


def _get_modifier_map():
    """Lazily build the pynput modifier-name lookup. Uses double-checked locking so
    the first concurrent caller wins under any threading model (GIL or nogil)."""
    global _MODIFIER_MAP
    if _MODIFIER_MAP is not None:
        return _MODIFIER_MAP
    with _MODIFIER_MAP_LOCK:
        if _MODIFIER_MAP is not None:
            return _MODIFIER_MAP
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
    try:
        return getattr(keyboard.Key, key_name.lower())
    except AttributeError:
        pass
    if len(key_name) == 1:
        return keyboard.KeyCode.from_char(key_name.lower())
    return None


def start_hotkey_listener(cfg=None, force=True):
    """Start (or restart) the global hotkey listener based on current config.

    If force=False, skip the restart when an existing listener is already
    healthy — used by the watchdog so it doesn't churn a working listener.
    """
    global _active_listener

    with _listener_lock:
        # Idempotent path: bail if listener is alive and caller didn't force.
        if not force and _active_listener is not None:
            try:
                if _active_listener.is_alive():
                    return
            except Exception:
                pass

        # Stop existing listener and wait for its thread to die so events
        # from the old hook can't double-fire alongside the new one.
        if _active_listener is not None:
            _active_listener.stop()
            try:
                _active_listener.join(timeout=0.2)
            except Exception:
                pass
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
            # Defense vs missed-release events: cap fires on the path that grows the set.
            if len(current_keys) > _KEY_TRACKER_OVERFLOW_CAP:
                current_keys.clear()
                return
            if _hotkey_active() and not _turn_off_lock.locked() and not _dialog_active:
                threading.Thread(target=turn_off_monitors, daemon=True).start()

        def on_release(key):
            current_keys.discard(key)

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.daemon = True
        listener.start()
        _active_listener = listener
        log.info("Global hotkey registered: %s", hotkey_display_name(cfg))


# ── Core actions ───────────────────────────────────────────────────────────

def is_remote_session():
    """True when running inside an RDP / Terminal Services session.

    SC_MONITORPOWER inside an RDP session targets the virtual desktop,
    which has no physical monitors — calling it does nothing useful and
    leaves the user wondering why nothing happened.
    """
    if GetSystemMetrics is None:
        return False
    try:
        return bool(GetSystemMetrics(SM_REMOTESESSION))
    except Exception:
        return False


def lock_workstation():
    """Best-effort Win+L. Returns True on success."""
    if LockWorkStation is None:
        return False
    try:
        return bool(LockWorkStation())
    except Exception as e:
        log.warning("LockWorkStation failed: %s", e)
        return False


def turn_off_monitors(lock_first=None):
    """Send SC_MONITORPOWER to turn off all displays.

    Sends to the desktop window (not HWND_BROADCAST) — the kernel powers off
    all monitors regardless of which window receives the message. Broadcasting
    floods every top-level window with WM_SYSCOMMAND, which can crash GPU
    drivers on resume when queued messages fight the wake process.

    Uses SendMessageTimeoutW with SMTO_ABORTIFHUNG so we never hang if the
    target window is unresponsive.

    lock_first: True/False overrides config; None honors config['lock_on_off'].
    """
    if SendMessageTimeoutW is None:
        log.warning("Not on Windows — monitor power control unavailable.")
        return

    if is_remote_session():
        log.info("Skipping monitor power-off — running inside RDP session.")
        return

    if not _turn_off_lock.acquire(blocking=False):
        return  # Already in progress, skip duplicate

    try:
        if lock_first is None:
            lock_first = bool(load_config().get("lock_on_off", False))

        if lock_first:
            if lock_workstation():
                # Let the lock screen render before blanking, otherwise the
                # secure desktop transition itself can wake the displays.
                time.sleep(_LOCK_SETTLE_SECS)

        # Wait for input events to settle so the click/keypress that triggered
        # this doesn't immediately wake the monitors back up.
        time.sleep(_TRIGGER_SETTLE_SECS)

        result = ctypes.wintypes.DWORD(0)
        hwnd = GetDesktopWindow()
        SendMessageTimeoutW(
            hwnd, WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_OFF,
            SMTO_ABORTIFHUNG, _SEND_TIMEOUT_MS, ctypes.byref(result),
        )
    finally:
        _turn_off_lock.release()


# ── Background watchers (listener liveness + idle trigger) ────────────────

def _start_listener_watchdog(interval_secs=30):
    """Periodically nudge the hotkey listener; restart only if dead.

    pynput's low-level Win32 hook can be silently detached after Win+L /
    fast-user-switch / RDP connect. Polling is the cheapest defense — no
    Win32 message pump required. The actual liveness check + conditional
    restart happens atomically inside start_hotkey_listener(force=False)
    under _listener_lock, so there's no stale-snapshot race window.
    """
    def _watch():
        while True:
            time.sleep(interval_secs)
            try:
                start_hotkey_listener(force=False)
            except Exception as e:
                log.warning("Listener watchdog error: %s", e)
    t = threading.Thread(target=_watch, daemon=True, name="displayoff-watchdog")
    t.start()


class _LASTINPUTINFO(ctypes.Structure):
    """Win32 LASTINPUTINFO struct. Both fields are 32-bit unsigned regardless of arch."""
    _fields_ = [("cbSize", ctypes.c_uint),
                ("dwTime", ctypes.c_uint)]


def _idle_seconds():
    """Return seconds since the user's last input event (mouse/keyboard).

    Uses Win32 GetLastInputInfo + GetTickCount with explicit DWORD restype so the
    subtraction stays unsigned (signed c_int defaults break this after ~24.8 days
    of uptime). Returns 0 on non-Windows or on failure.
    """
    if sys.platform != "win32" or GetLastInputInfo is None:
        return 0
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not GetLastInputInfo(ctypes.byref(info)):
            return 0
        # Mask to 32 bits so wraparound math works correctly across the ~49.7-day
        # GetTickCount rollover boundary.
        elapsed = (GetTickCount() - info.dwTime) & 0xFFFFFFFF
        return elapsed / 1000.0
    except Exception:
        return 0


def _start_idle_watcher(cfg_provider, poll_secs=15):
    """Auto-blank the displays when the user has been idle ≥ idle_blank_minutes.

    Only fires once per idle window — the user must move/type to re-arm it.
    Threshold of 0 (the default) disables the feature; the watcher still runs
    cheaply but skips firing.
    """
    def _watch():
        fired = False
        while True:
            time.sleep(poll_secs)
            try:
                cfg = cfg_provider()
                threshold_min = int(cfg.get("idle_blank_minutes", 0) or 0)
                threshold = threshold_min * 60
                if threshold <= 0:
                    fired = False
                    continue
                idle = _idle_seconds()
                if idle < threshold:
                    fired = False
                    continue
                if fired:
                    continue
                fired = True
                log.info("Idle %.0fs ≥ %ds threshold — blanking displays.", idle, threshold)
                threading.Thread(target=turn_off_monitors, daemon=True).start()
            except Exception as e:
                log.warning("Idle watcher error: %s", e)
    t = threading.Thread(target=_watch, daemon=True, name="displayoff-idle")
    t.start()


# ── Privilege detection (UIPI hint) ───────────────────────────────────────

def _is_elevated():
    """True if running with admin token. Used only to inform the user that the
    hotkey may be silently dead under elevated foreground windows (UIPI)."""
    if sys.platform != "win32" or IsUserAnAdmin is None:
        return True
    try:
        return bool(IsUserAnAdmin())
    except Exception:
        return False


# ── Cross-instance "quit" signal (named event IPC) ────────────────────────

_QUIT_EVENT_NAME = r"Local\DisplayOff_QuitSignal"


def _signal_other_to_quit():
    """Open the named quit event and signal it. Returns one of:
        "signaled"  — found a running instance and signaled it
        "missing"   — no running instance (event doesn't exist)
        "error"     — found instance but SetEvent failed
    """
    if sys.platform != "win32" or OpenEventW is None:
        return "missing"
    h = OpenEventW(_EVENT_MODIFY_STATE, False, _QUIT_EVENT_NAME)
    if not h:
        return "missing"
    try:
        if SetEvent(h):
            return "signaled"
        log.warning("SetEvent failed (err=%d) — instance found but could not be signaled.",
                    GetLastError())
        return "error"
    finally:
        CloseHandle(h)


def _create_quit_event():
    """Create the named quit event so other instances can signal us. Returns
    a Win32 handle (caller doesn't have to close it — kernel reaps on exit)."""
    if sys.platform != "win32" or CreateEventW is None:
        return None
    # Manual-reset event so we can wait on it in a worker thread.
    return CreateEventW(None, True, False, _QUIT_EVENT_NAME)


def _watch_quit_event(handle, on_signaled):
    """Block on the event in a daemon thread; call on_signaled when set."""
    def _wait():
        try:
            result = WaitForSingleObject(handle, _INFINITE)
            if result == _WAIT_OBJECT_0 or result == _WAIT_ABANDONED:
                log.info("Received --quit-other signal — stopping.")
                try:
                    on_signaled()
                except Exception as e:
                    log.warning("Quit handler raised: %s", e)
            elif result == _WAIT_FAILED:
                log.warning("WaitForSingleObject failed (err=%d) — quit watcher exiting.",
                            GetLastError())
            else:
                log.warning("Unexpected WaitForSingleObject result %#x — quit watcher exiting.",
                            result)
        except Exception as e:
            log.warning("Quit-event watcher error: %s", e)
    t = threading.Thread(target=_wait, daemon=True, name="displayoff-quitwatch")
    t.start()


# ── Update check (manual, via tray menu) ──────────────────────────────────

_RELEASES_API = "https://api.github.com/repos/itsnateai/displayoff/releases/latest"


def _version_tuple(v):
    """Parse 'v1.4.0' / '1.4.0' / '1.4' / '1.4.0-beta1' into a (major, minor, patch)
    tuple for comparison. Stops at the first non-digit per component, so build/pre-release
    tags don't pollute the numeric comparison.
    """
    if not v:
        return (0, 0, 0)
    parts = str(v).lstrip("vV").split(".")
    out = []
    for p in parts:
        digits = ""
        for c in p:
            if c.isdigit():
                digits += c
            else:
                break
        out.append(int(digits) if digits else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def check_for_updates(timeout=5):
    """Query GitHub releases for the latest version.

    Returns (has_update: bool, latest: str|None, html_url: str|None, error: str|None).
    Network failures return (False, None, None, '<error>').
    """
    import urllib.request, urllib.error
    req = urllib.request.Request(
        _RELEASES_API,
        headers={
            "User-Agent": f"DisplayOff/{__version__}",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as e:
        return False, None, None, str(e)
    latest = data.get("tag_name") or data.get("name") or ""
    html_url = data.get("html_url", "")
    if not latest:
        return False, None, None, "no tag in response"
    has_update = _version_tuple(latest) > _version_tuple(__version__)
    return has_update, latest.lstrip("vV"), html_url, None


# ── UI helpers ─────────────────────────────────────────────────────────────

def _set_dpi_awareness():
    """Best-effort: declare per-monitor V2 DPI awareness for crisp Tk dialogs.

    Cascades V2 → Per-Monitor → System Aware → silent fallback so we cope
    with older Win10 builds that lack the newer entry points.
    """
    if sys.platform != "win32":
        return
    try:
        # DPI_AWARENESS_CONTEXT is a pseudo-handle (pointer-sized); pass via
        # c_void_p so -4 sign-extends correctly on 64-bit Windows.
        fn = ctypes.windll.user32.SetProcessDpiAwarenessContext
        fn.argtypes = [ctypes.c_void_p]
        fn.restype = ctypes.wintypes.BOOL
        fn(ctypes.c_void_p(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def _create_icon_image():
    """Fallback icon used only when displayoff.ico is missing (e.g. bare clone)."""
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


# ── Settings dialog ────────────────────────────────────────────────────────

# Row layout (lets you add new rows without re-threading indices through the impl):
#   row 0 — header label                row 4 — lock-on-blank checkbox
#   row 1 — separator                   row 5 — autostart checkbox
#   row 2 — hotkey label + field        row 6 — auto-blank-when-idle spinbox
#   row 3 — hotkey hint                 row 7 — footer (GitHub / Apply / Save / Cancel)
# To add a new option row, pick the next unused row, drop in a `_build_*` call
# in `_open_settings_impl`, and bump the footer row.


def _build_header(root, row, pad):
    """Title + horizontal separator. Spans (row, row+1)."""
    import tkinter as tk
    from tkinter import ttk

    header = tk.Label(root, text=f"Display Off v{__version__}",
                      font=("Segoe UI", 13, "bold"), bg="#f0f0f0", anchor="w")
    header.grid(row=row, column=0, columnspan=3, sticky="w", padx=pad, pady=(pad, 2))

    sep = ttk.Separator(root, orient="horizontal")
    sep.grid(row=row + 1, column=0, columnspan=3, sticky="ew", padx=pad, pady=(0, 12))


def _build_hotkey_row(root, row, pad, cfg, captured, recording):
    """Hotkey label + click-to-record field + hint. Spans (row, row+1).

    The recording state machine is fully encapsulated here:
      - Click the field → pynput listener records the next combo
      - Esc cancels (leaves captured unchanged)
      - Modifier-only press leaves captured unchanged (final_key never set)
    """
    import tkinter as tk

    hotkey_lbl = tk.Label(root, text="Hotkey:", font=("Segoe UI", 10),
                          bg="#f0f0f0", anchor="e")
    hotkey_lbl.grid(row=row, column=0, sticky="e", padx=(pad, 8), pady=4)

    display_var = tk.StringVar(value=hotkey_display_name(cfg))

    hotkey_display = tk.Label(root, textvariable=display_var, font=("Segoe UI", 11),
                              relief="sunken", bg="white", anchor="center", width=28,
                              pady=6, cursor="hand2")
    hotkey_display.grid(row=row, column=1, columnspan=2, sticky="ew",
                        padx=(0, pad), pady=4)

    hint = tk.Label(root, text="Click the field above, press your hotkey (Esc cancels)",
                    font=("Segoe UI", 8), fg="#888888", bg="#f0f0f0")
    hint.grid(row=row + 1, column=1, columnspan=2, sticky="w", pady=(0, 10))

    def start_recording(event=None):
        if recording["active"]:
            return
        recording["active"] = True
        display_var.set("Press your hotkey...")
        hotkey_display.config(bg="#fff8e0", relief="solid")

        from pynput import keyboard as kb

        pressed_mods = set()
        final_key = [None]
        mod_names_map = {"ctrl_l": "ctrl", "ctrl_r": "ctrl",
                         "alt_l": "alt", "alt_r": "alt",
                         "shift_l": "shift", "shift_r": "shift"}

        def on_press(key):
            # Esc cancels — leaves final_key None so capture is discarded.
            if isinstance(key, kb.Key) and key == kb.Key.esc:
                return
            name = key.name if isinstance(key, kb.Key) else None
            if name in mod_names_map:
                pressed_mods.add(mod_names_map[name])
            else:
                final_key[0] = key

        def on_release(key):
            if isinstance(key, kb.Key) and key == kb.Key.esc:
                listener.stop()
                return
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


def _build_options_section(root, row, pad, lock_var, autostart_var, idle_var):
    """Lock-on-blank + autostart checkboxes + idle-trigger spinbox.
    Spans (row, row+2).

    To add a fourth option, give it the next row index and bump the footer
    in _open_settings_impl.
    """
    import tkinter as tk

    lock_chk = tk.Checkbutton(root, text="Lock workstation when blanking",
                              variable=lock_var, font=("Segoe UI", 10),
                              bg="#f0f0f0", anchor="w")
    lock_chk.grid(row=row, column=0, columnspan=3, sticky="w", padx=pad, pady=2)

    autostart_chk = tk.Checkbutton(root, text="Run at Windows startup",
                                   variable=autostart_var, font=("Segoe UI", 10),
                                   bg="#f0f0f0", anchor="w")
    autostart_chk.grid(row=row + 1, column=0, columnspan=3, sticky="w", padx=pad, pady=2)

    idle_frame = tk.Frame(root, bg="#f0f0f0")
    idle_frame.grid(row=row + 2, column=0, columnspan=3, sticky="w", padx=pad, pady=(6, 2))
    tk.Label(idle_frame, text="Auto-blank after",
             font=("Segoe UI", 10), bg="#f0f0f0").pack(side="left")
    tk.Spinbox(idle_frame, from_=0, to=999, width=5, textvariable=idle_var,
               font=("Segoe UI", 10)).pack(side="left", padx=(8, 8))
    tk.Label(idle_frame, text="minutes idle  (0 = off)",
             font=("Segoe UI", 10), bg="#f0f0f0").pack(side="left")


def _build_footer(root, row, pad, on_save, on_cancel, on_apply=None):
    """GitHub link + Save / Apply (optional) / Cancel buttons."""
    import tkinter as tk

    footer = tk.Frame(root, bg="#f0f0f0")
    footer.grid(row=row, column=0, columnspan=3, sticky="ew", padx=pad, pady=(16, pad))

    tk.Button(footer, text="GitHub",
              command=lambda: webbrowser.open("https://github.com/itsnateai/displayoff"),
              font=("Segoe UI", 9), width=8).pack(side="left")
    tk.Button(footer, text="Cancel", command=on_cancel,
              font=("Segoe UI", 9), width=8).pack(side="right", padx=(4, 0))
    tk.Button(footer, text="Save", command=on_save,
              font=("Segoe UI", 9), width=8).pack(side="right", padx=(0, 4))
    if on_apply is not None:
        tk.Button(footer, text="Apply", command=on_apply,
                  font=("Segoe UI", 9), width=8).pack(side="right", padx=(0, 4))


def _release_dialog_slot():
    """Clear the dialog-active flag under the dialog lock. Pairs with _claim_dialog
    in run_tray. Mandatory under-lock release so on_press's lock-free read of
    _dialog_active sees a consistent value under nogil/free-threaded Python."""
    global _dialog_active
    with _dialog_lock:
        _dialog_active = False


def _open_settings(tray_icon, on_saved=None):
    """Public entry — wraps the impl in try/finally so _dialog_active is always cleared."""
    try:
        _open_settings_impl(tray_icon, on_saved)
    except Exception as e:
        log.exception("Settings dialog crashed: %s", e)
    finally:
        _release_dialog_slot()


def _open_settings_impl(tray_icon, on_saved):
    """Build and run the settings dialog. Wires the row builders into a Tk root."""
    import tkinter as tk
    from tkinter import messagebox

    cfg = load_config()
    captured = {"modifiers": list(cfg["hotkey"]["modifiers"]), "key": cfg["hotkey"]["key"]}
    recording = {"active": False}

    _set_dpi_awareness()

    root = tk.Tk()
    root.title("Display Off — Settings")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    root.configure(bg="#f0f0f0")

    PAD = 20
    w, h = 460, 380
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")

    # Tk vars must be created after the root exists.
    lock_var = tk.BooleanVar(value=bool(cfg.get("lock_on_off", False)))
    autostart_var = tk.BooleanVar(value=autostart_enabled())
    idle_var = tk.IntVar(value=int(cfg.get("idle_blank_minutes", 0) or 0))

    # Build sections — row indices live here, so adding a row is a one-line change.
    _build_header(root, row=0, pad=PAD)
    _build_hotkey_row(root, row=2, pad=PAD, cfg=cfg, captured=captured, recording=recording)
    _build_options_section(root, row=4, pad=PAD,
                           lock_var=lock_var, autostart_var=autostart_var,
                           idle_var=idle_var)

    root.columnconfigure(1, weight=1)

    def _apply_settings():
        """Validate + persist + apply. Returns True on success, False if dialog should stay open."""
        if not captured.get("key"):
            messagebox.showerror(
                "Display Off",
                "Hotkey must include at least one non-modifier key.",
                parent=root,
            )
            return False
        try:
            idle_minutes = max(0, int(idle_var.get() or 0))
        except (TypeError, ValueError):
            messagebox.showerror(
                "Display Off",
                "Idle-blank minutes must be a non-negative number.",
                parent=root,
            )
            return False
        cfg["hotkey"] = dict(captured)
        cfg["lock_on_off"] = bool(lock_var.get())
        cfg["idle_blank_minutes"] = idle_minutes
        try:
            save_config(cfg)
        except OSError as e:
            messagebox.showerror(
                "Display Off",
                f"Could not save settings:\n{e}",
                parent=root,
            )
            return False
        # Autostart is a separate side effect; don't fail the save on its failure.
        try:
            if bool(autostart_var.get()) != autostart_enabled():
                set_autostart(bool(autostart_var.get()))
        except OSError as e:
            messagebox.showerror(
                "Display Off",
                f"Settings saved, but autostart toggle failed:\n{e}",
                parent=root,
            )
        start_hotkey_listener(cfg)
        if on_saved:
            on_saved(cfg)
        return True

    def on_cancel():
        root.destroy()

    def on_save():
        if _apply_settings():
            root.destroy()

    def on_apply():
        _apply_settings()  # stays open regardless

    _build_footer(root, row=7, pad=PAD,
                  on_save=on_save, on_cancel=on_cancel, on_apply=on_apply)
    root.protocol("WM_DELETE_WINDOW", on_cancel)
    root.mainloop()


# ── About dialog ───────────────────────────────────────────────────────────

def _show_about():
    """Open a simple About messagebox in its own Tk root."""
    try:
        import tkinter as tk
        from tkinter import messagebox
        _set_dpi_awareness()
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        cfg = load_config()
        idle_min = int(cfg.get("idle_blank_minutes", 0) or 0)
        idle_line = f"{idle_min} min" if idle_min > 0 else "off"
        messagebox.showinfo(
            "About Display Off",
            f"Display Off v{__version__}\n\n"
            "Tiny tray utility to power off all monitors\n"
            "without putting the PC to sleep.\n\n"
            f"Hotkey: {hotkey_display_name(cfg)}\n"
            f"Lock on blank: {'on' if cfg.get('lock_on_off') else 'off'}\n"
            f"Auto-blank when idle: {idle_line}\n"
            f"Autostart: {'on' if autostart_enabled() else 'off'}\n\n"
            "https://github.com/itsnateai/displayoff",
            parent=root,
        )
        root.destroy()
    except Exception as e:
        log.exception("About dialog crashed: %s", e)
    finally:
        _release_dialog_slot()


def _run_update_check():
    """Hit GitHub releases API and show a result dialog. Same Tk-root discipline as About."""
    try:
        has_update, latest, html_url, err = check_for_updates()
        import tkinter as tk
        from tkinter import messagebox
        _set_dpi_awareness()
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        if err:
            messagebox.showwarning(
                "Display Off",
                f"Could not check for updates.\n\n{err}\n\n"
                "Verify your internet connection and try again.",
                parent=root,
            )
        elif has_update:
            if messagebox.askyesno(
                "Display Off — Update available",
                f"A newer version is available.\n\n"
                f"Current: v{__version__}\n"
                f"Latest:  v{latest}\n\n"
                "Open the release page in your browser?",
                parent=root,
            ):
                webbrowser.open(html_url or "https://github.com/itsnateai/displayoff/releases")
        else:
            messagebox.showinfo(
                "Display Off — Up to date",
                f"You're on the latest release.\n\n"
                f"Current: v{__version__}\n"
                f"Latest:  v{latest}",
                parent=root,
            )
        root.destroy()
    except Exception as e:
        log.exception("Update check dialog crashed: %s", e)
    finally:
        _release_dialog_slot()


# ── Tray ───────────────────────────────────────────────────────────────────

def run_tray():
    """Run as a system tray application."""
    import pystray
    from pystray import MenuItem, Menu

    if os.path.isfile(_ICON_PATH):
        from PIL import Image
        icon_image = Image.open(_ICON_PATH)
    else:
        log.warning("displayoff.ico not found — using programmatic fallback icon.")
        icon_image = _create_icon_image()

    cfg = load_config()
    hotkey_name = [hotkey_display_name(cfg)]  # mutable so menu callback can update
    first_run = not os.path.exists(_CONFIG_PATH)

    def _claim_dialog():
        """Atomically claim the dialog slot. Returns True if the caller may open a Tk window."""
        global _dialog_active
        with _dialog_lock:
            if _dialog_active:
                return False
            _dialog_active = True
            return True

    def on_turn_off(icon, item):
        threading.Thread(target=turn_off_monitors, daemon=True).start()

    def on_settings(icon, item):
        if not _claim_dialog():
            return

        def on_saved(new_cfg):
            hotkey_name[0] = hotkey_display_name(new_cfg)
            icon.update_menu()

        threading.Thread(target=_open_settings, args=(icon, on_saved), daemon=True).start()

    def on_about(icon, item):
        if not _claim_dialog():
            return
        threading.Thread(target=_show_about, daemon=True).start()

    def on_check_updates(icon, item):
        if not _claim_dialog():
            return
        threading.Thread(target=_run_update_check, daemon=True).start()

    def on_quit(icon, item):
        icon.stop()

    menu = Menu(
        MenuItem(f"Display Off v{__version__}", None, enabled=False),
        Menu.SEPARATOR,
        MenuItem("Turn Off Displays", on_turn_off),
        MenuItem(lambda item: f"Hotkey: {hotkey_name[0]}", None, enabled=False),
        Menu.SEPARATOR,
        MenuItem("Settings...", on_settings),
        MenuItem("Check for Updates...", on_check_updates),
        MenuItem("About...", on_about),
        MenuItem("Quit", on_quit),
    )

    icon = pystray.Icon(
        name="displayoff",
        icon=icon_image,
        title="Display Off — Click to sleep monitors",
        menu=menu,
    )

    start_hotkey_listener(cfg)
    _start_listener_watchdog()
    # Idle watcher always reads fresh config so toggling via Settings takes effect immediately.
    _start_idle_watcher(load_config)

    # Cross-instance quit signal — lets `--quit-other` from a second invocation stop us cleanly.
    quit_handle = _create_quit_event()
    if quit_handle:
        _watch_quit_event(quit_handle, lambda: icon.stop())

    log.info("Running in system tray. Click icon or press %s to turn off displays.",
             hotkey_name[0])

    if first_run:
        # One-time welcome notification + persist defaults so this won't fire again.
        # Don't clobber: check the file again after the notification fires, since the
        # user could have opened Settings and saved their own config in the meantime.
        def _welcome():
            time.sleep(1.0)  # let the tray icon attach before notifying
            try:
                icon.notify(
                    f"Press {hotkey_name[0]} to blank all displays.",
                    "Display Off is running",
                )
            except Exception as e:
                log.warning("Could not show first-run notification: %s", e)
            if not os.path.exists(_CONFIG_PATH):
                try:
                    save_config(cfg)
                except OSError as e:
                    log.warning("Could not write initial config: %s", e)
        threading.Thread(target=_welcome, daemon=True).start()

    icon.run()


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="[%(name)s] %(message)s",
    )

    if "--version" in sys.argv:
        print(f"displayoff {__version__}")
        return

    if "--quit-other" in sys.argv:
        # Doesn't need single-instance ownership — it's signaling somebody else.
        result = _signal_other_to_quit()
        if result == "signaled":
            log.info("Signaled running instance to quit.")
        elif result == "missing":
            log.info("No running instance found.")
        else:
            log.error("Found a running instance but could not signal it.")
        return

    if "--reset-config" in sys.argv:
        if os.path.exists(_CONFIG_PATH):
            try:
                os.remove(_CONFIG_PATH)
                log.info("Config reset (%s removed).", _CONFIG_PATH)
            except OSError as e:
                log.error("Could not reset config: %s", e)
        else:
            log.info("No config file to reset.")
        return

    if "--off" in sys.argv:
        log.info("Turning off displays...")
        turn_off_monitors()
        return

    if "--lock-and-off" in sys.argv:
        log.info("Locking and turning off displays...")
        turn_off_monitors(lock_first=True)
        return

    if "--no-lock-off" in sys.argv:
        log.info("Turning off displays (no lock)...")
        turn_off_monitors(lock_first=False)
        return

    # Tray modes need single-instance protection
    if not _acquire_single_instance():
        log.info("Another instance is already running — exiting.")
        return

    # UIPI hint: under standard user, low-level keyboard hook can't see input
    # to elevated windows. Inform once so users aren't mystified when the
    # hotkey appears dead while Task Manager / an admin terminal has focus.
    if not _is_elevated():
        log.info("Running unelevated — hotkey may not fire while an elevated window has focus (UIPI).")

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
