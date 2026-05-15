"""Display Off — Force all monitors to sleep without putting the PC to sleep.

Sits in the system tray. Click the tray icon or press the global hotkey
(Ctrl+Alt+F12 by default) to turn off all displays instantly. Move the
mouse or press any key to wake them.

Requirements:
    pip install pystray Pillow pynput

Usage:
    python displayoff.py              # Start in tray
    python displayoff.py --off        # Turn off immediately (honors lock-on-off config + path config)
    python displayoff.py --native-off # Force the native idle-blank path (regardless of config)
    python displayoff.py --legacy-off # Force the legacy SC_MONITORPOWER path (regardless of config)
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
import subprocess
import sys
import threading
import time
import webbrowser

try:
    import winreg  # Windows-only; used for autostart toggle
except ImportError:
    winreg = None

__version__ = "1.7.2"

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
_LOCK_SETTLE_SECS = 0.3         # Delay between LockWorkStation and the blank.
_SEND_TIMEOUT_MS = 5000         # SendMessageTimeoutW abort-if-hung timeout.
_KEY_TRACKER_OVERFLOW_CAP = 20  # Cap on tracked simultaneously-pressed keys (defense vs missed releases).
_DOUBLE_CLICK_WINDOW_SECS = 0.5 # Treat two icon clicks within this window as a double-click.
                                # Matches Windows' default GetDoubleClickTime() of ~500ms. The hidden
                                # default-action menu item is fired by pystray on every left-click on
                                # Windows (not just double-click), so we time the gap ourselves.
_NATIVE_PROD_SLEEP_SECS = 5.0   # How long the native idle-blank path holds the 1s timeout in production.
                                # Bumped from 2.5s after empirical "menu click → no blank" reports: when the user
                                # navigates the right-click menu, the mouse moves continuously and the kernel's
                                # idle counter keeps resetting. By the time we'd restore at 2.5s the counter has
                                # never reached the 1s threshold. 5s lets idle accumulate even with ~3s of post-
                                # click motion. Lock-collision cost is logged explicitly so the user can see
                                # when rapid double-trigger drops a second click.
_NATIVE_PROD_SETTLE_SECS = 0.5  # Pause before writing the 1s timeout. Same idea as the legacy SC_MONITORPOWER
                                # path: gives the user's mouse time to come to rest before we arm the trap.

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

    Treats a CreateMutexW failure (NULL handle) as "no single-instance guard"
    rather than silently letting two trays coexist — log loudly and refuse to
    proceed. Conditions that can cause this: low system resources, sandbox
    restrictions, ACL changes on the Local\\ namespace.
    """
    global _mutex_handle
    if sys.platform != "win32":
        return True
    _mutex_handle = CreateMutexW(None, True, _MUTEX_NAME)
    last_error = GetLastError()
    if not _mutex_handle:
        # CreateMutexW failed entirely — no guard at all. Treat as "another
        # instance might be running" rather than risk launching a second tray.
        log.error("CreateMutexW failed (lastError=%d) — cannot acquire single-instance guard; "
                  "refusing to start to avoid duplicate trays.", last_error)
        return False
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
    # Path selector. Native idle-blank (default in v1.6.0+) hooks into Windows'
    # built-in display-off-after-N-minutes mechanism — works on every Windows
    # version and on hardware where SC_MONITORPOWER cycles (Modern Standby +
    # hybrid GPU laptops). Set true to force the legacy SC_MONITORPOWER path
    # used in v1.0-1.5.
    "use_legacy_sc_monitorpower": False,
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


# ── Autostart (Startup-folder .lnk, matching the rest of the tray-app set) ─
# v1.7.0+ uses the user's Startup folder (a .lnk next to MicMute.lnk,
# SyncthingPause.lnk, CapsNumTray.lnk, MWBToggle.lnk, etc.) instead of the
# HKCU Run registry key. Same effect at logon, but the .lnk is visible /
# manageable in File Explorer, which matches how Nate manages every other
# tray app in the workspace. The legacy `HKCU\...\Run\DisplayOff` registry
# entry is detected for backward compat and cleaned up on next toggle.

_RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE_NAME = "DisplayOff"

# PowerShell subprocess timeout for shortcut create/read. 30s tolerates slow
# first-launch PS JIT, group-policy evaluation, and AV file-creation hooks.
# Was 10s — bumped after v1.7.0 audit flagged cold-boot Win11 24H2 first-PS
# launch can exceed 10s, raising subprocess.TimeoutExpired (which is NOT
# OSError — would have escaped the v1.6.0 `except OSError` guard silently).
_PS_AUTOSTART_TIMEOUT_SECS = 30

# APPDATA is set on every supported Windows configuration (interactive logon,
# service-account, even safe-mode). If it's somehow missing we explicitly
# refuse to build a startup-folder path rather than silently joining onto an
# empty string, which would produce a CWD-relative path that `os.path.exists`
# would happily check against random files in the working directory and that
# `_create_startup_lnk` would silently write outside the user's actual
# Startup folder. Functions below raise OSError with a clear message if this
# is unset.
_APPDATA_DIR = os.environ.get("APPDATA", "")
_STARTUP_DIR = (
    os.path.join(_APPDATA_DIR, r"Microsoft\Windows\Start Menu\Programs\Startup")
    if _APPDATA_DIR else ""
)
_STARTUP_LNK_NAME = "Display Off.lnk"
_STARTUP_LNK_PATH = (
    os.path.join(_STARTUP_DIR, _STARTUP_LNK_NAME) if _STARTUP_DIR else ""
)


def _require_appdata():
    """Raise OSError with a clear message if APPDATA isn't set. Called by every
    autostart function that touches the Startup folder so the user sees a
    real error instead of silent writes to a CWD-relative path."""
    if not _APPDATA_DIR or not _STARTUP_LNK_PATH:
        raise OSError(
            "APPDATA environment variable is not set — cannot resolve the "
            "Windows Startup folder. This is unexpected; restart with a "
            "fully-initialized user environment, or run via the .lnk that "
            "Display Off creates in your Startup folder (which Explorer "
            "launches with APPDATA populated)."
        )


def _autostart_target_pythonw():
    """Resolve the `pythonw.exe` path that should launch us at logon. Prefers
    `pythonw.exe` over `python.exe` so there's no console flash."""
    py = sys.executable
    if py.lower().endswith("python.exe"):
        pyw = py[:-len("python.exe")] + "pythonw.exe"
        if os.path.isfile(pyw):
            return pyw
    return py


def _ps_sq_escape(s):
    """Escape a string for embedding inside a PowerShell single-quoted literal.
    PS single-quotes are literal-preserving for everything EXCEPT the single
    quote itself, which is escaped by doubling it. Windows paths can legally
    contain `'` (e.g., `C:\\Users\\O'Brien\\...`), so without this every
    interpolated path is a one-character injection vector waiting to happen."""
    return s.replace("'", "''")


def _ps_dq_escape(s):
    """Escape a string for embedding inside a PowerShell double-quoted literal
    (or any context where `"` would close the surrounding quote). PS double-
    quoted literals escape `"` by doubling it (or via backtick — we use the
    portable doubling form). Windows paths can legally contain `"` (rare but
    NTFS-legal), and the `.Arguments` field in our PS script wraps the script
    path inside inner double-quotes so the .lnk records the arg with quotes
    around it — without this escape, a path with `"` would break out of the
    inner DQ context and could corrupt or inject into the surrounding PS SQ."""
    return s.replace('"', '""')


def _ps_run(ps_script, *, timeout=_PS_AUTOSTART_TIMEOUT_SECS):
    """Run a PowerShell one-liner with hidden window + no profile. Returns
    `CompletedProcess`. Raises OSError on `FileNotFoundError` (powershell
    missing from PATH — PSCore-stripped systems) or `TimeoutExpired` (slow
    profile load / AV scan / GPO eval). Both of those are NOT subclasses of
    OSError in Python's hierarchy, so without this wrapping they would have
    escaped the v1.7.0 broadened `except Exception` guard in `_apply_settings`
    only to land in a generic "unknown error" dialog. Translating to OSError
    keeps the documented contract truthful."""
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    try:
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            startupinfo=si,
        )
    except FileNotFoundError as e:
        raise OSError(
            "powershell.exe not found on PATH — required for Startup-folder "
            "shortcut management. Ensure Windows PowerShell 5.1 is installed "
            f"(default on Win10/11). Underlying error: {e}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise OSError(
            f"PowerShell timed out after {timeout}s while managing the Startup "
            f"shortcut. Possible causes: slow first-launch JIT, AV real-time "
            f"scanning, Group Policy script-block-logging delay, or a stuck "
            f"profile load. Underlying error: {e}"
        ) from e


def _create_startup_lnk():
    """Create (or overwrite) the `Display Off.lnk` shortcut in the user's
    Startup folder. Uses PowerShell + `WScript.Shell` COM. Idempotent —
    re-running refreshes the .lnk to point at the current `pythonw.exe`,
    which is how we recover from a Python upgrade that invalidated the
    previous shortcut's target.

    Raises OSError on PowerShell failure, timeout, missing PATH, or post-
    write verify-back failure (file not on disk after rc=0 — AV quarantine
    or locked-down profile)."""
    if sys.platform != "win32":
        raise OSError("startup-folder shortcut is Windows-only")
    _require_appdata()
    script = os.path.abspath(__file__)
    py = _autostart_target_pythonw()
    working_dir = os.path.dirname(script)
    icon_path = os.path.join(working_dir, "displayoff.ico")

    # Escape EVERY interpolated value for the PS single-quoted-literal context.
    # PS single-quotes preserve backslashes but treat `'` as terminator — paths
    # with apostrophes (legal NTFS: `C:\Users\O'Brien\...`) would otherwise
    # break the script or inject arbitrary PS. `_ps_sq_escape` doubles every
    # `'` per PS literal rules. The Arguments field's content also gets
    # escaped because the inner double-quotes wrap a value that goes into a
    # *different* PS string parser (verified by Sonnet+Opus audit 2026-05-14).
    lnk_q = _ps_sq_escape(_STARTUP_LNK_PATH)
    py_q = _ps_sq_escape(py)
    # script_q is embedded inside `'"{script_q}"'` — the OUTER context is PS
    # single-quote (so `'` must be doubled), the INNER content is wrapped in
    # double-quotes that the .lnk records literally (so any `"` in the path
    # must also be doubled to survive PS DQ-parser semantics). Apply BOTH
    # escapes; order matters only in that we treat them as independent
    # character-class substitutions, which is what these helpers do.
    script_q = _ps_dq_escape(_ps_sq_escape(script))
    wd_q = _ps_sq_escape(working_dir)
    icon_q = _ps_sq_escape(icon_path)
    ps_script = (
        f"$sh = New-Object -ComObject WScript.Shell; "
        f"$lnk = $sh.CreateShortcut('{lnk_q}'); "
        f"$lnk.TargetPath = '{py_q}'; "
        f"$lnk.Arguments = '\"{script_q}\"'; "
        f"$lnk.WorkingDirectory = '{wd_q}'; "
        f"$lnk.IconLocation = '{icon_q},0'; "
        f"$lnk.WindowStyle = 7; "
        f"$lnk.Description = 'Display Off - tray app autostart'; "
        f"$lnk.Save()"
    )

    log.info("Creating startup shortcut: target=%s args=%s lnk=%s", py, script, _STARTUP_LNK_PATH)
    proc = _ps_run(ps_script)
    if proc.returncode != 0:
        raise OSError(f"Could not create startup shortcut (PowerShell rc={proc.returncode}): "
                      f"{proc.stderr.strip() or proc.stdout.strip()}")
    # Verify-back: PS rc=0 doesn't guarantee the file landed on disk (AV
    # quarantine, COM Save silent no-op on locked-down profiles, etc.). Same
    # pattern as our GH-release post-publish verify-back: "reported success"
    # is not the same as "exists on disk". If the file isn't there, raise so
    # the caller surfaces an error instead of silently leaving autostart broken.
    if not os.path.exists(_STARTUP_LNK_PATH):
        raise OSError(
            f"PowerShell reported success (rc=0) but {_STARTUP_LNK_PATH} does not exist. "
            f"Possible causes: antivirus quarantine, restricted profile, or "
            f"PowerShell execution policy. stdout={proc.stdout.strip()!r} "
            f"stderr={proc.stderr.strip()!r}"
        )
    if proc.stderr.strip():
        # rc=0 but stderr has content: usually deprecation warnings or profile
        # noise. Log so future regressions in the PS environment are noticed.
        log.debug("PowerShell rc=0 but stderr present: %s", proc.stderr.strip()[:300])
    log.info("Startup shortcut created: %s (%d bytes)",
             _STARTUP_LNK_PATH, os.path.getsize(_STARTUP_LNK_PATH))


def _remove_startup_lnk():
    """Remove the Display Off.lnk shortcut from the user's Startup folder.
    TOCTOU-safe — handles `FileNotFoundError` gracefully if the file is
    removed by another process between our existence check and the unlink.
    Includes a post-removal verify-back to catch the rare case where
    `os.remove` reports success but the file persists (sync-software
    replication, OneDrive, AV restore-from-quarantine)."""
    _require_appdata()
    try:
        os.remove(_STARTUP_LNK_PATH)
        log.info("Removed startup shortcut: %s", _STARTUP_LNK_PATH)
    except FileNotFoundError:
        # Already gone — TOCTOU race or manual delete. Treat as success.
        log.info("Remove startup shortcut: already absent at %s", _STARTUP_LNK_PATH)
        return
    except OSError as e:
        log.warning("Could not remove startup shortcut: %s", e)
        raise
    # Verify-back symmetric to _create_startup_lnk's. Catches the case where
    # `os.remove` returns but sync-software (OneDrive / Syncthing) or AV
    # restore-from-quarantine puts the .lnk back. User explicitly disabled
    # autostart, so a re-appearing .lnk is a real bug they need to know about.
    if os.path.exists(_STARTUP_LNK_PATH):
        raise OSError(
            f"Removed {_STARTUP_LNK_PATH} but it reappeared on disk — likely "
            f"OneDrive/Syncthing replication or AV restore-from-quarantine. "
            f"Disable Startup-folder sync, or remove the source copy."
        )


def _legacy_run_key_present():
    """True if the legacy HKCU\\...\\Run\\DisplayOff entry exists, False if it
    doesn't, raises OSError on PermissionError so callers can distinguish
    "definitely absent" from "I can't tell" (locked hive / Group Policy).
    Used both for backward-compat detection in `autostart_enabled` and for
    cleanup when we migrate a user from the registry path to the .lnk path.

    NOTE: `autostart_enabled()` and `set_autostart()` catch this so a locked
    hive doesn't break those paths — the read is best-effort there."""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH) as key:
            winreg.QueryValueEx(key, _RUN_VALUE_NAME)
        return True
    except FileNotFoundError:
        # Key or value definitely doesn't exist — clean "absent" answer.
        return False
    except PermissionError as e:
        # Locked hive / Group Policy. Don't pretend it's absent.
        log.warning("HKCU Run key read failed (PermissionError): %s — migration "
                    "cleanup cannot run; legacy entry may still fire at logon", e)
        raise
    except OSError as e:
        # Generic registry error — also can't tell.
        log.warning("HKCU Run key read failed (%s: %s)", type(e).__name__, e)
        raise


def _delete_legacy_run_key():
    """Remove the legacy HKCU\\...\\Run\\DisplayOff entry if present.
    No-op if not present. Logs (does NOT raise) on permission/registry
    errors — cleanup is best-effort and the caller (`set_autostart`)
    treats this as a non-fatal side-effect."""
    if winreg is None:
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _RUN_VALUE_NAME)
        log.info("Removed legacy HKCU Run\\%s autostart entry (migrated to Startup-folder .lnk)",
                 _RUN_VALUE_NAME)
    except FileNotFoundError:
        # Already absent — clean no-op.
        pass
    except PermissionError as e:
        log.warning("Could not delete legacy HKCU Run\\%s — PermissionError "
                    "(locked hive or Group Policy): %s. Legacy entry may still "
                    "fire at logon alongside the new .lnk.", _RUN_VALUE_NAME, e)
    except OSError as e:
        log.warning("Could not delete legacy HKCU Run\\%s (%s): %s. Legacy entry "
                    "may still fire at logon alongside the new .lnk.",
                    _RUN_VALUE_NAME, type(e).__name__, e)


def _read_lnk_target_path():
    """Read the `TargetPath` field of the existing startup .lnk via the same
    `WScript.Shell` COM API used to create it. Returns the resolved path
    string, or None on any failure (file missing, PS missing, COM error).
    Used by `autostart_enabled()` to detect a stale .lnk pointing to a
    Python install that no longer exists or has moved.

    NOT load-bearing — if this returns None we treat the .lnk as unverifiable
    and let the caller decide; the typical caller path re-writes the .lnk
    via `_create_startup_lnk` which is idempotent."""
    if sys.platform != "win32" or not _STARTUP_LNK_PATH or not os.path.exists(_STARTUP_LNK_PATH):
        return None
    lnk_q = _ps_sq_escape(_STARTUP_LNK_PATH)
    # `$OutputEncoding = [Console]::OutputEncoding = ...UTF8` makes PS emit
    # output without a BOM on Win10/11. Without this, `Write-Output` under
    # `pythonw.exe` (no console) often prepends `﻿` to the first line,
    # which makes the path comparison in `autostart_enabled()` fail forever
    # → user sees "stale shortcut" log spam on every Settings open and the
    # .lnk gets re-created every Save. Caught by Sonnet+Opus R2 audit
    # 2026-05-14. Also strip BOM defensively in case PS env overrides the
    # encoding directive.
    ps_script = (
        f"$OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        f"$sh = New-Object -ComObject WScript.Shell; "
        f"$lnk = $sh.CreateShortcut('{lnk_q}'); "
        f"Write-Output $lnk.TargetPath"
    )
    try:
        # Use the shared module timeout — was 10s hardcoded, which could fire
        # on cold-boot Win11 where PS JIT exceeds 10s and silently flip the
        # stale-detection to "couldn't read, assume valid" (false positive on
        # the "still enabled?" path). Now consistent with create/remove.
        proc = _ps_run(ps_script)
    except OSError as e:
        log.debug("Could not read .lnk target path: %s", e)
        return None
    if proc.returncode != 0:
        log.debug("PS read of .lnk target failed (rc=%d): %s",
                  proc.returncode, proc.stderr.strip() or proc.stdout.strip())
        return None
    # Strip BOM + whitespace. `.lstrip('﻿')` is a no-op when absent.
    target = proc.stdout.strip().lstrip("﻿").strip()
    return target or None


def autostart_enabled():
    """True if Display Off is currently configured to autostart at logon
    AND the configuration points at a still-valid target.

    Checks (in order):
      1. Startup-folder .lnk exists AND its TargetPath matches our current
         `_autostart_target_pythonw()` — a stale .lnk pointing at a moved
         or uninstalled Python is treated as "not enabled" so the next
         Save re-creates it correctly.
      2. Legacy HKCU\\...\\Run\\DisplayOff entry (v1.6.0 and earlier) —
         either being present is "enabled" for migration purposes.

    Returns False if APPDATA isn't set (so the Settings dialog can still
    open even in an unusual environment — the toggle attempt will produce
    a clear error rather than silently checking against an empty path)."""
    if not _STARTUP_LNK_PATH:
        return False
    if os.path.exists(_STARTUP_LNK_PATH):
        # Validate the target matches our current Python install. If it
        # doesn't, the .lnk is stale (e.g., Python upgraded from 3.13 to
        # 3.14 and the old path doesn't exist anymore) — don't claim
        # "enabled" when the .lnk wouldn't actually launch us at logon.
        target = _read_lnk_target_path()
        if target is None:
            # Couldn't read it — assume valid and let the user reconcile
            # via a manual Save toggle if it turns out to be broken.
            return True
        expected = _autostart_target_pythonw()
        # Use `realpath` not `abspath` — `realpath` resolves NTFS junctions,
        # symlinks, and 8.3 short names (`C:\PROGRA~1\...` ↔ `C:\Program
        # Files\...`). `WScript.Shell` sometimes returns the long form,
        # sometimes the short form; without resolution the comparison
        # spuriously returns False on systems with junction-redirected user
        # profiles (Enterprise folder redirection) or on installs that
        # happen to store Python under a path with a space. `normcase`
        # then lower-cases for case-insensitive NTFS matching.
        if _normalize_path(target) == _normalize_path(expected):
            return True
        log.info("Stale startup shortcut: target=%r but current Python is %r — "
                 "treating as 'not enabled' so next Save re-creates it.",
                 target, expected)
        return False
    try:
        return _legacy_run_key_present()
    except OSError:
        # Can't read the legacy key (locked hive / Group Policy). Assume
        # not enabled — at worst the user re-toggles to force a refresh.
        return False


def _normalize_path(path):
    """Canonicalize a Windows path for equality comparison: resolve junctions
    / symlinks / 8.3 short names via `realpath`, then `normcase` for
    case-insensitive matching. Falls back to `abspath` + `normcase` if
    `realpath` raises (e.g., target doesn't exist — comparing two
    not-yet-resolved paths is still useful for the "are these the same
    path string?" question)."""
    if not path:
        return ""
    try:
        return os.path.normcase(os.path.realpath(path))
    except OSError:
        return os.path.normcase(os.path.abspath(path))


def set_autostart(enabled):
    """Enable or disable autostart. Writes to the Startup folder as a .lnk
    and cleans up any legacy HKCU Run entry from prior versions.

    Raises OSError on .lnk creation/removal failure (including PowerShell
    missing from PATH, PS timeout, post-write verify-back failure, and
    sync-software-restored-removed-file). Legacy registry cleanup is
    best-effort and never raises (warnings go to displayoff.log)."""
    # Build a string description for the log line — keep `_legacy_run_key_present`'s
    # return contract a clean bool|raise and confine the "unreadable" sentinel
    # to log presentation only. (Caught by Opus R2 audit: prior version
    # rebound the same variable name to both bool and str, a future-refactor
    # footgun where `if legacy_state:` would treat a locked hive as "present".)
    try:
        legacy_desc = "present" if _legacy_run_key_present() else "absent"
    except OSError:
        legacy_desc = "unreadable"
    log.info("set_autostart(%s) — current state: lnk=%s legacy=%s",
             enabled, os.path.exists(_STARTUP_LNK_PATH) if _STARTUP_LNK_PATH else "no-appdata",
             legacy_desc)
    if enabled:
        _create_startup_lnk()
        # If user had the legacy registry entry from v1.6.0, clean it up so
        # autostart fires from exactly one place. Best-effort.
        _delete_legacy_run_key()
    else:
        _remove_startup_lnk()
        # Also clear any legacy entry, even when "disabling" — the user
        # clicked off, they want autostart off, period. Best-effort.
        _delete_legacy_run_key()


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


def turn_off_monitors(lock_first=None, force_path=None):
    """Blank all displays without putting the PC to sleep.

    Dispatches to one of two underlying mechanisms based on
    `cfg['use_legacy_sc_monitorpower']`:

      - **Native idle-blank** (default in v1.6.0+) — temporarily writes a
        1-second display-off timeout into the active power scheme via
        PowerWriteACValueIndex + PowerSetActiveScheme. Windows itself fires
        its native idle-display-off code as the idle counter crosses the
        threshold. No SC_MONITORPOWER message is sent. Required on hardware
        where SC_MONITORPOWER triggers a wake-handshake loop (Modern Standby
        + hybrid GPU laptops).

      - **Legacy SC_MONITORPOWER** (opt-in) — original v1.0–v1.5 mechanism.
        Sends WM_SYSCOMMAND + SC_MONITORPOWER + MONITOR_OFF to the desktop
        window. Works on most Windows hardware; cycles on some.

    Either path respects the same single-instance lock, RDP early-return,
    and lock-first option.

    lock_first: True/False overrides config; None honors config['lock_on_off'].
    force_path: None (honor config), "native", or "legacy". Used by --native-off
                and --legacy-off CLI flags to bypass config for explicit one-shot
                invocations without mutating displayoff_config.json.
    """
    if sys.platform != "win32":
        log.warning("Not on Windows — monitor power control unavailable.")
        return

    if is_remote_session():
        log.info("Skipping monitor power-off — running inside RDP session.")
        return

    if not _turn_off_lock.acquire(blocking=False):
        # Previous blank still in flight; silently dropping this trigger would
        # confuse the user ("nothing happened when I clicked!"). Log so we can
        # see the collision in displayoff.log.
        log.info("blank already in progress — dropping duplicate trigger (force_path=%s)",
                 force_path)
        return

    try:
        cfg = load_config()
        if lock_first is None:
            lock_first = bool(cfg.get("lock_on_off", False))

        if lock_first:
            if lock_workstation():
                # Let the lock screen render before blanking, otherwise the
                # secure desktop transition itself can wake the displays.
                time.sleep(_LOCK_SETTLE_SECS)

        if force_path == "native":
            _fire_native_idle_blank()
        elif force_path == "legacy":
            _fire_sc_monitorpower()
        elif cfg.get("use_legacy_sc_monitorpower", False):
            _fire_sc_monitorpower()
        else:
            _fire_native_idle_blank()
    finally:
        _turn_off_lock.release()


def _fire_sc_monitorpower():
    """Legacy v1.0–v1.5 mechanism: WM_SYSCOMMAND + SC_MONITORPOWER + MONITOR_OFF
    to GetDesktopWindow(). Targets the desktop (not HWND_BROADCAST) because
    broadcasting flooded every top-level window with WM_SYSCOMMAND and crashed
    GPU drivers on resume in older Windows builds. SendMessageTimeoutW with
    SMTO_ABORTIFHUNG so we never hang on a frozen target."""
    if SendMessageTimeoutW is None:
        return
    # Wait for the trigger event to settle so the click/keypress that fired
    # this doesn't immediately wake the monitors back up.
    time.sleep(_TRIGGER_SETTLE_SECS)
    result = ctypes.wintypes.DWORD(0)
    hwnd = GetDesktopWindow()
    SendMessageTimeoutW(
        hwnd, WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_OFF,
        SMTO_ABORTIFHUNG, _SEND_TIMEOUT_MS, ctypes.byref(result),
    )


def _fire_native_idle_blank():
    """Default v1.6.0+ mechanism: hook into Windows' native idle-display-off
    path by temporarily writing a 1-second display-off timeout. The mechanism
    + sentinel-based crash safety live in `native_blank.py`.

    On import failure we REFUSE TO BLANK rather than fall back to
    SC_MONITORPOWER. The entire reason v1.6.0 exists is that SC_MONITORPOWER
    cycles the display on Modern Standby + hybrid-GPU hardware to the point
    of requiring a reboot. Silently falling back to that path on a broken
    install would re-introduce exactly the bug v1.6.0 was built to fix. Users
    who actually want SC_MONITORPOWER on their (working) hardware set
    `use_legacy_sc_monitorpower: true` in config — and reach this code via
    `_fire_sc_monitorpower()` directly, not via this fallback path."""
    try:
        from native_blank import blank_via_idle_path
    except ImportError as e:
        log.error("native_blank.py missing or broken (%s) — REFUSING to fall back to "
                  "SC_MONITORPOWER (would re-trigger the bug v1.6.0 fixed). "
                  "Reinstall displayoff or restore native_blank.py.", e)
        return
    # Settle pause so the click/keypress that triggered us doesn't leak into
    # the idle window and prevent the kernel from crossing the 1s threshold.
    # Especially load-bearing for the right-click → menu path where the mouse
    # is still moving when this function fires.
    if _NATIVE_PROD_SETTLE_SECS > 0:
        time.sleep(_NATIVE_PROD_SETTLE_SECS)
    try:
        ok = blank_via_idle_path(sleep_seconds=_NATIVE_PROD_SLEEP_SECS)
    except Exception as e:
        log.exception("native idle-blank raised — no blank fired: %s", e)
        return
    if not ok:
        log.error("native idle-blank left a sentinel on disk — see native_blank.log")


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


# ── Dark theme palette + helpers ──────────────────────────────────────────
# Colors picked to match Win11 dark mode chrome (Settings, File Explorer,
# context menus). Centralized constants so a future tweak is one-line.
_THEME_BG           = "#1f1f1f"  # Main window background
_THEME_BG_SUNKEN    = "#2d2d2d"  # Input fields, sunken hotkey display
_THEME_BG_RECORD    = "#4a3f1c"  # Hotkey display while recording (dark amber)
_THEME_FG           = "#e6e6e6"  # Primary text
_THEME_FG_HINT      = "#8a8a8a"  # Secondary / hint text
_THEME_SEP          = "#3a3a3a"  # Separator line
_THEME_BTN_BG       = "#2d2d2d"
_THEME_BTN_FG       = "#e6e6e6"
_THEME_BTN_ACTIVE_BG = "#3d3d3d"
_THEME_BTN_ACTIVE_FG = "#ffffff"


def _apply_dark_titlebar(root):
    """Apply Win11 immersive dark mode to the title bar of a Tk Toplevel.

    Uses `DwmSetWindowAttribute(DWMWA_USE_IMMERSIVE_DARK_MODE = 20)` which
    became stable in Win10 build 19041 (2004). Without this call the window
    body is themed (we set bg manually) but the title bar stays light-gray —
    classic broken-dark-theme look.

    `root.winfo_id()` returns the HWND of Tk's internal frame, not the
    top-level window — we need the parent. No-op on non-Windows or older
    Win10/11 builds where the attribute isn't supported."""
    if sys.platform != "win32":
        return
    try:
        # update() forces window creation if it hasn't fully realized yet —
        # winfo_id is only valid post-realize.
        root.update_idletasks()
        hwnd_inner = root.winfo_id()
        # The actual top-level HWND is the inner widget's parent in Tk.
        hwnd = ctypes.windll.user32.GetParent(hwnd_inner) or hwnd_inner
        dwmapi = ctypes.windll.dwmapi
        # DWMWA_USE_IMMERSIVE_DARK_MODE: 20 on Win10 2004+ / Win11.
        # Earlier Win10 builds (1903–1909) used 19 — try 20 first, fall back
        # to 19 only if 20 returns nonzero (failure).
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        value = ctypes.c_int(1)
        result = dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(value), ctypes.sizeof(value))
        if result != 0:
            # Try the legacy attribute index used on early Win10 1903-1909.
            dwmapi.DwmSetWindowAttribute(hwnd, 19,
                                         ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError) as e:
        log.warning("Could not apply dark title bar: %s", e)


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
                      font=("Segoe UI", 13, "bold"),
                      bg=_THEME_BG, fg=_THEME_FG, anchor="w")
    header.grid(row=row, column=0, columnspan=3, sticky="w", padx=pad, pady=(pad, 2))

    # ttk.Separator doesn't accept `bg=` directly — use a configured ttk Style.
    style = ttk.Style(root)
    style.configure("Dark.TSeparator", background=_THEME_SEP)
    sep = ttk.Separator(root, orient="horizontal", style="Dark.TSeparator")
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
                          bg=_THEME_BG, fg=_THEME_FG, anchor="e")
    hotkey_lbl.grid(row=row, column=0, sticky="e", padx=(pad, 8), pady=4)

    display_var = tk.StringVar(value=hotkey_display_name(cfg))

    hotkey_display = tk.Label(root, textvariable=display_var, font=("Segoe UI", 11),
                              relief="sunken", bg=_THEME_BG_SUNKEN, fg=_THEME_FG,
                              anchor="center", width=28, pady=6, cursor="hand2",
                              highlightthickness=1, highlightbackground=_THEME_SEP)
    hotkey_display.grid(row=row, column=1, columnspan=2, sticky="ew",
                        padx=(0, pad), pady=4)

    hint = tk.Label(root, text="Click the field above, press your hotkey (Esc cancels)",
                    font=("Segoe UI", 8), fg=_THEME_FG_HINT, bg=_THEME_BG)
    hint.grid(row=row + 1, column=1, columnspan=2, sticky="w", pady=(0, 10))

    def start_recording(event=None):
        if recording["active"]:
            return
        recording["active"] = True
        display_var.set("Press your hotkey...")
        hotkey_display.config(bg=_THEME_BG_RECORD, relief="solid")

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
            hotkey_display.config(bg=_THEME_BG_SUNKEN, relief="sunken")
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

    # Common checkbutton kwargs — selectcolor is the indicator box itself,
    # activebackground is the row's hover state.
    _chk_kw = dict(
        font=("Segoe UI", 10),
        bg=_THEME_BG, fg=_THEME_FG,
        selectcolor=_THEME_BG_SUNKEN,
        activebackground=_THEME_BG, activeforeground=_THEME_FG,
        anchor="w",
    )
    lock_chk = tk.Checkbutton(root, text="Lock workstation when blanking",
                              variable=lock_var, **_chk_kw)
    lock_chk.grid(row=row, column=0, columnspan=3, sticky="w", padx=pad, pady=2)

    autostart_chk = tk.Checkbutton(root, text="Run at Windows startup",
                                   variable=autostart_var, **_chk_kw)
    autostart_chk.grid(row=row + 1, column=0, columnspan=3, sticky="w", padx=pad, pady=2)

    idle_frame = tk.Frame(root, bg=_THEME_BG)
    idle_frame.grid(row=row + 2, column=0, columnspan=3, sticky="w", padx=pad, pady=(6, 2))
    tk.Label(idle_frame, text="Auto-blank after",
             font=("Segoe UI", 10), bg=_THEME_BG, fg=_THEME_FG).pack(side="left")
    tk.Spinbox(idle_frame, from_=0, to=999, width=5, textvariable=idle_var,
               font=("Segoe UI", 10),
               bg=_THEME_BG_SUNKEN, fg=_THEME_FG,
               insertbackground=_THEME_FG,
               buttonbackground=_THEME_BTN_BG,
               highlightthickness=1, highlightbackground=_THEME_SEP,
               relief="flat").pack(side="left", padx=(8, 8))
    tk.Label(idle_frame, text="minutes idle  (0 = off)",
             font=("Segoe UI", 10), bg=_THEME_BG, fg=_THEME_FG).pack(side="left")


def _build_footer(root, row, pad, on_save, on_cancel, on_apply=None,
                  on_about=None, on_check_updates=None):
    """Footer button row.

    Left side  : [GitHub] [About] [Updates]   ← info / action buttons
    Right side : [Apply] [Save] [Cancel]      ← dialog-result buttons

    `on_about` and `on_check_updates` are optional callbacks. When supplied,
    they render as buttons that open child dialogs of the Settings root.
    Added in v1.7.0 — previously these lived in the tray right-click menu."""
    import tkinter as tk

    footer = tk.Frame(root, bg=_THEME_BG)
    footer.grid(row=row, column=0, columnspan=3, sticky="ew", padx=pad, pady=(16, pad))

    _btn_kw = dict(
        font=("Segoe UI", 9), width=8,
        bg=_THEME_BTN_BG, fg=_THEME_BTN_FG,
        activebackground=_THEME_BTN_ACTIVE_BG,
        activeforeground=_THEME_BTN_ACTIVE_FG,
        relief="flat", borderwidth=1,
        highlightthickness=1, highlightbackground=_THEME_SEP,
    )

    tk.Button(footer, text="GitHub",
              command=lambda: webbrowser.open("https://github.com/itsnateai/displayoff"),
              **_btn_kw).pack(side="left")
    if on_about is not None:
        tk.Button(footer, text="About", command=on_about,
                  **_btn_kw).pack(side="left", padx=(4, 0))
    if on_check_updates is not None:
        tk.Button(footer, text="Updates", command=on_check_updates,
                  **_btn_kw).pack(side="left", padx=(4, 0))
    tk.Button(footer, text="Cancel", command=on_cancel,
              **_btn_kw).pack(side="right", padx=(4, 0))
    tk.Button(footer, text="Save", command=on_save,
              **_btn_kw).pack(side="right", padx=(0, 4))
    if on_apply is not None:
        tk.Button(footer, text="Apply", command=on_apply,
                  **_btn_kw).pack(side="right", padx=(0, 4))


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
    # Route Tk callback exceptions through our logger BEFORE any other
    # callback wiring. Tk's default `report_callback_exception` writes the
    # traceback to sys.stderr, which under `pythonw.exe` has no console —
    # so any uncaught exception in a button command (Save / Apply / About
    # / Updates), a key-bind, an `after()` callback, or a hotkey-recording
    # event would silently evaporate. This single line ensures every
    # swallowed exception lands in `displayoff.log` instead of /dev/null.
    # (Defense #2 from `memory/reference_tk_callback_silent_under_pythonw.md`.)
    def _log_tk_callback_exc(exc_type, exc_val, exc_tb):
        log.error("Tk callback exception (Settings dialog)",
                  exc_info=(exc_type, exc_val, exc_tb))
    root.report_callback_exception = _log_tk_callback_exc
    # Hide the window IMMEDIATELY so the user never sees the default
    # light-mode Tk chrome flash before our dark theme + DWM dark title bar
    # apply. We re-show (deiconify) only after every widget is built, the
    # geometry is set, and the title bar has been re-painted dark.
    root.withdraw()
    root.title("Display Off — Settings")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    root.configure(bg=_THEME_BG)
    # Dark title bar (Win11 via DWM immersive dark mode). No-op on older OS.
    _apply_dark_titlebar(root)

    PAD = 20
    w = 460
    # Height is computed AFTER widgets are built (see below) so the window
    # always matches its content. Was previously hardcoded to 380, which left
    # ~90px of dead space below the footer after the v1.4.0 row decomposition.

    # Tk vars must be created after the root exists.
    # Cache the initial autostart state so we don't spawn a fresh PS
    # subprocess (~1-3s cold-boot) on every Save's change-detection. Refreshed
    # only after a successful toggle. The dialog lifetime is short; if the
    # autostart state changes externally during the dialog (vanishingly rare)
    # the user's next Save reconciles via `_create_startup_lnk`'s idempotency.
    autostart_state = {"enabled": autostart_enabled()}
    lock_var = tk.BooleanVar(value=bool(cfg.get("lock_on_off", False)))
    autostart_var = tk.BooleanVar(value=autostart_state["enabled"])
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
        # Autostart is a separate side effect — config is already persisted
        # whether or not this succeeds. Catch broadly: Tk's default
        # `report_callback_exception` writes to stderr, which is /dev/null
        # under pythonw.exe, so a NameError / TimeoutExpired / etc. would
        # otherwise vanish silently and the user would see "Save did nothing"
        # with no error dialog and no log entry.
        autostart_ok = True
        desired_autostart = bool(autostart_var.get())
        try:
            # Compare against the cached state captured at dialog open —
            # avoids re-spawning a PS subprocess just to answer "did the
            # checkbox change?". `set_autostart` itself rechecks on-disk
            # state for the actual write decision.
            if desired_autostart != autostart_state["enabled"]:
                set_autostart(desired_autostart)
                autostart_state["enabled"] = desired_autostart
        except Exception as e:
            log.exception("Autostart toggle failed")
            messagebox.showerror(
                "Display Off",
                f"Autostart toggle failed:\n{type(e).__name__}: {e}\n\n"
                f"Your other settings were saved. Adjust and click Save "
                f"again to retry — the dialog stays open.",
                parent=root,
            )
            # Refresh the checkbox to the actual on-disk state so the user
            # sees the real state (not what they thought they'd set) and
            # the cached state matches reality for the next change-check.
            # Wrapped — Tk var destroyed mid-error would otherwise raise
            # TclError which would route through report_callback_exception
            # (now hooked) — harmless but noisy.
            try:
                actual = autostart_enabled()
                autostart_var.set(actual)
                autostart_state["enabled"] = actual
            except Exception:
                pass
            autostart_ok = False
        start_hotkey_listener(cfg)
        if on_saved:
            on_saved(cfg)
        # Keep dialog open on autostart failure so the user has a chance to
        # retry without re-navigating from the tray menu. The hotkey/idle/
        # lock settings are already persisted (the autostart try-block runs
        # AFTER save_config), so the user doesn't lose those edits.
        return autostart_ok

    def on_cancel():
        root.destroy()

    def on_save():
        if _apply_settings():
            root.destroy()

    def on_apply():
        _apply_settings()  # stays open regardless

    # About and Updates buttons render as child dialogs of the Settings Tk
    # root. Settings already holds the dialog-slot, so these don't need
    # separate slot machinery.
    def on_about_btn():
        _show_about(root)

    def on_updates_btn():
        _run_update_check(root)

    _build_footer(root, row=7, pad=PAD,
                  on_save=on_save, on_cancel=on_cancel, on_apply=on_apply,
                  on_about=on_about_btn, on_check_updates=on_updates_btn)

    # Size the window to its actual content. Must happen after every widget
    # has been added so winfo_reqheight reports the right value. Center on
    # screen using the computed height.
    root.update_idletasks()
    h = root.winfo_reqheight()
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")

    # NOW show — everything is themed, sized, positioned. Re-asserting the
    # dark title bar after deiconify is belt-and-suspenders: some Win11
    # builds re-paint the title bar with default chrome when a previously-
    # withdrawn window is first shown.
    root.deiconify()
    _apply_dark_titlebar(root)

    root.protocol("WM_DELETE_WINDOW", on_cancel)
    root.mainloop()


# ── Dark-mode native menus (Win10 1903+) ──────────────────────────────────

def _enable_dark_mode_menus():
    """Force this process's native Win32 context menus (including pystray's
    `TrackPopupMenu`-based tray right-click menu) to render in dark mode.

    Uses uxtheme's undocumented `SetPreferredAppMode` (ordinal 135) and
    `FlushMenuThemes` (ordinal 136). These are private/undocumented APIs but
    have been stable since Windows 10 1903 and are exactly what Explorer
    itself uses for its own context menus. Microsoft has not deprecated
    them in any release through Win11 25H2.

    Modes:
      0 = Default (follow system setting)
      1 = AllowDark   (let app opt-in per-window via DwmSetWindowAttribute)
      2 = ForceDark   (force every window/menu in this process to dark)
      3 = ForceLight  (force every window/menu in this process to light)
      4 = Max         (sentinel — do not use)

    If the OS is already in dark mode, this is a no-op. If the OS is light
    but the user wants the tray menu themed to match the app's dark icon,
    ForceDark gets that result without affecting the rest of Windows.

    No-op on non-Windows or if the uxtheme ordinals don't resolve (defensive
    against future Windows builds that might rename them)."""
    if sys.platform != "win32":
        return
    try:
        uxtheme = ctypes.windll.uxtheme
        # Resolve by ordinal — these functions are name-less exports.
        SetPreferredAppMode = uxtheme[135]
        SetPreferredAppMode.argtypes = [ctypes.c_int]
        SetPreferredAppMode.restype = ctypes.c_int
        FlushMenuThemes = uxtheme[136]
        FlushMenuThemes.argtypes = []
        FlushMenuThemes.restype = ctypes.c_int

        SetPreferredAppMode(2)  # ForceDark
        FlushMenuThemes()
        log.info("Dark-mode menus enabled (uxtheme SetPreferredAppMode = ForceDark)")
    except (AttributeError, OSError) as e:
        log.warning("Could not enable dark-mode menus (uxtheme ordinal lookup failed): %s", e)


# ── About + Update-check dialogs (called from Settings) ──────────────────
# As of v1.7.0 these are invoked exclusively from buttons inside the Settings
# dialog — not from the tray right-click menu. That means they render as
# CHILD dialogs of the Settings Tk root rather than spawning their own
# top-level root, and the parent's dialog-slot (already held by Settings)
# covers them too — no separate slot mgmt needed.

def _show_about(parent_root):
    """Open a modeless About window as a child of `parent_root`.

    Modeless = no `grab_set`, no `transient`, no `-topmost`. The user can
    click away to other windows while About stays visible, and can dismiss
    it whenever — same affordance as a typical About dialog in Office, VS
    Code, etc. Replaces the prior `messagebox.showinfo` which was
    application-modal and stole focus until dismissed."""
    try:
        import tkinter as tk
        cfg = load_config()
        idle_min = int(cfg.get("idle_blank_minutes", 0) or 0)
        idle_line = f"{idle_min} min" if idle_min > 0 else "off"

        about = tk.Toplevel(parent_root)
        about.title("About Display Off")
        about.configure(bg=_THEME_BG)
        about.resizable(False, False)
        # Deliberately NOT transient(parent_root) and NOT -topmost — those
        # would make the window stay above its parent and steal focus on
        # parent activation. We want fully independent z-order.
        _apply_dark_titlebar(about)

        body_text = (
            f"Display Off v{__version__}\n\n"
            "Tiny tray utility to power off all monitors\n"
            "without putting the PC to sleep.\n\n"
            f"Hotkey: {hotkey_display_name(cfg)}\n"
            f"Lock on blank: {'on' if cfg.get('lock_on_off') else 'off'}\n"
            f"Auto-blank when idle: {idle_line}\n"
            f"Autostart: {'on' if autostart_enabled() else 'off'}"
        )
        body = tk.Label(about, text=body_text, justify="left",
                        font=("Segoe UI", 10),
                        bg=_THEME_BG, fg=_THEME_FG,
                        padx=20, pady=15)
        body.pack()

        # Clickable GitHub link styled as a label with hand cursor.
        link = tk.Label(about, text="https://github.com/itsnateai/displayoff",
                        font=("Segoe UI", 9, "underline"),
                        bg=_THEME_BG, fg="#4ec9ff", cursor="hand2",
                        padx=20, pady=(0, 10))
        link.pack()
        link.bind("<Button-1>",
                  lambda _: webbrowser.open("https://github.com/itsnateai/displayoff"))

        btn_frame = tk.Frame(about, bg=_THEME_BG)
        btn_frame.pack(pady=(0, 15))
        close_btn = tk.Button(btn_frame, text="Close", command=about.destroy,
                              font=("Segoe UI", 9), width=10,
                              bg=_THEME_BTN_BG, fg=_THEME_BTN_FG,
                              activebackground=_THEME_BTN_ACTIVE_BG,
                              activeforeground=_THEME_BTN_ACTIVE_FG,
                              relief="flat", borderwidth=1,
                              highlightthickness=1, highlightbackground=_THEME_SEP)
        close_btn.pack()
        # Enter / Escape both close the window.
        about.bind("<Return>", lambda _: about.destroy())
        about.bind("<Escape>", lambda _: about.destroy())

        # Center on screen.
        about.update_idletasks()
        w, h = about.winfo_reqwidth(), about.winfo_reqheight()
        x = (about.winfo_screenwidth() - w) // 2
        y = (about.winfo_screenheight() - h) // 2
        about.geometry(f"+{x}+{y}")
        close_btn.focus_set()
    except Exception as e:
        log.exception("About dialog crashed: %s", e)


def _run_update_check(parent_root):
    """Hit the GitHub releases API and show a result dialog as a child of
    `parent_root`. Synchronous — the network call (~3s typical, 5s timeout)
    blocks the Tk event loop. Acceptable for a Settings-driven user action."""
    try:
        from tkinter import messagebox
        has_update, latest, html_url, err = check_for_updates()
        if err:
            err_text = str(err)
            # GitHub returns 404 for both "private repo with no auth" and
            # "no releases tagged yet" — they look identical to an
            # unauthenticated caller. Spell out both possibilities instead
            # of the generic "check your internet" message.
            if "404" in err_text or "Not Found" in err_text:
                msg = (
                    "Could not check for updates — GitHub returned 404 (Not Found).\n\n"
                    "This usually means one of:\n"
                    "  • The repository (itsnateai/displayoff) is private and the\n"
                    "    update check has no authentication token.\n"
                    "  • The repository exists but has no published releases yet —\n"
                    "    update-check needs at least one tagged release to compare against.\n"
                    "  • The repository or owner name has changed.\n\n"
                    "Manage releases at:\n"
                    "https://github.com/itsnateai/displayoff/releases\n\n"
                    f"Raw error: {err_text}"
                )
            elif "timeout" in err_text.lower() or "timed out" in err_text.lower():
                msg = (
                    "Could not check for updates — request timed out.\n\n"
                    "Verify your internet connection and try again. GitHub may\n"
                    "also be experiencing an outage — check https://www.githubstatus.com/"
                )
            else:
                msg = (
                    f"Could not check for updates.\n\n{err_text}\n\n"
                    "Verify your internet connection and try again."
                )
            messagebox.showwarning("Display Off", msg, parent=parent_root)
        elif has_update:
            if messagebox.askyesno(
                "Display Off — Update available",
                f"A newer version is available.\n\n"
                f"Current: v{__version__}\n"
                f"Latest:  v{latest}\n\n"
                "Open the release page in your browser?",
                parent=parent_root,
            ):
                webbrowser.open(html_url or "https://github.com/itsnateai/displayoff/releases")
        else:
            messagebox.showinfo(
                "Display Off — Up to date",
                f"You're on the latest release.\n\n"
                f"Current: v{__version__}\n"
                f"Latest:  v{latest}",
                parent=parent_root,
            )
    except Exception as e:
        log.exception("Update check dialog crashed: %s", e)


# ── Tray ───────────────────────────────────────────────────────────────────

def run_tray():
    """Run as a system tray application."""
    import pystray
    from pystray import MenuItem, Menu

    # Eagerly recover from any stale native-blank sentinel BEFORE doing
    # anything else. If a prior run was killed mid-blank (BSOD, power
    # loss, Task Manager kill), the display-off timeout is still 1s and
    # the user is one idle-second away from a constant-blanking loop.
    # The blank path itself runs this on every fire, but if the user
    # launches displayoff and doesn't immediately blank, the broken state
    # persists for the whole session. Calling here closes that window.
    # Safe no-op when no sentinel is on disk.
    try:
        from native_blank import recover_stale_sentinel
        recover_stale_sentinel()
    except ImportError as e:
        log.warning("native_blank not available at startup (%s) — skipping stale-sentinel recovery", e)

    # Force dark-mode native menus for this process. Must happen BEFORE
    # pystray creates the tray icon so the menu rendering picks up the
    # theme on its first display. Safe no-op on non-Windows or if the
    # private uxtheme ordinals ever change.
    _enable_dark_mode_menus()

    # Capture the NotifyIconSettings subkey baseline BEFORE pystray registers
    # the icon (Shell_NotifyIcon NIM_ADD). Any subkey that appears after this
    # snapshot is a Phase-2 orphan-claim candidate. Captured here, used by
    # the background promoter below. Safe on non-Win11 (returns None).
    #
    # tray_promoter is UX polish — never a crash surface. If the module is
    # missing or fails to import, log it and proceed with no promotion (the
    # icon will land in Win11's overflow flyout instead of the main tray).
    try:
        from tray_promoter import capture_baseline, promote_in_background
        tray_baseline = capture_baseline()
        _promote_tray = True
    except ImportError as e:
        log.warning("tray_promoter not available (%s) — tray icon will land in Win11 overflow until manually promoted", e)
        tray_baseline = None
        _promote_tray = False

    icon_image = None
    if os.path.isfile(_ICON_PATH):
        from PIL import Image
        try:
            with Image.open(_ICON_PATH) as _im:
                icon_image = _im.copy()
        except Exception as e:
            # Truncated / 0-byte / corrupt .ico (Syncthing partial, OneDrive
            # placeholder, AV quarantine-restore mid-read). Lazy Image.open
            # would have deferred this to pystray; .copy() forces eager load
            # so we catch it here and fall through to the programmatic icon.
            log.warning("displayoff.ico unreadable (%s) — using programmatic fallback icon.", e)
    if icon_image is None:
        if not os.path.isfile(_ICON_PATH):
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

    def _spawn_blank_thread(reason):
        """All paths that should fire a blank go through here. Logs the reason
        so we can see in displayoff.log which UI action triggered it (or which
        action *didn't* fire when the user reports "nothing happened")."""
        log.info("blank-trigger: %s — spawning turn_off_monitors thread", reason)
        try:
            t = threading.Thread(target=turn_off_monitors, daemon=True,
                                 name=f"displayoff-blank-{reason}")
            t.start()
        except Exception as e:
            log.exception("failed to spawn blank thread (%s): %s", reason, e)

    def on_turn_off(icon, item):
        _spawn_blank_thread("menu-turn-off")

    # Hidden default-action item for left-click on the tray icon. pystray on
    # Windows fires this on EVERY left-click (single, double, triple) — there
    # is no separate single/double-click event in its API — so we measure the
    # gap between clicks ourselves and only fire the blank when two clicks
    # land within _DOUBLE_CLICK_WINDOW_SECS of each other. The first click of
    # a pair stores a timestamp and exits without blanking.
    last_icon_click = [0.0]
    icon_click_lock = threading.Lock()

    def on_icon_default_click(icon, item):
        with icon_click_lock:
            now = time.monotonic()
            gap = now - last_icon_click[0]
            log.info("icon-click: now=%.3f last=%.3f gap=%.3fs (window=%.1fs)",
                     now, last_icon_click[0], gap, _DOUBLE_CLICK_WINDOW_SECS)
            if last_icon_click[0] > 0 and gap <= _DOUBLE_CLICK_WINDOW_SECS:
                last_icon_click[0] = 0.0  # consume the pair so a 3rd click doesn't combo
                _spawn_blank_thread("icon-double-click")
            else:
                last_icon_click[0] = now

    def on_settings(icon, item):
        if not _claim_dialog():
            return

        def on_saved(new_cfg):
            hotkey_name[0] = hotkey_display_name(new_cfg)
            icon.update_menu()

        threading.Thread(target=_open_settings, args=(icon, on_saved), daemon=True).start()

    def on_quit(icon, item):
        icon.stop()

    # Why no clickable "Turn Off Displays" menu item:
    #
    # In v1.6.0+ the blank routes through the Win32 native idle-display-off
    # path (PowerWriteACValueIndex + PowerSetActiveScheme writing a 1-second
    # timeout). On this developer's hardware (ASUS ROG Strix G614JV, Modern
    # Standby + Intel UHD/RTX 4060 hybrid), the menu-item path fired the
    # underlying call chain perfectly — idle counter accumulated past the
    # threshold cleanly per GetLastInputInfo polling, nothing held the
    # display awake per `powercfg /requests` — but the kernel did not act on
    # the policy change. Double-click on the tray icon and the Ctrl+Alt+F12
    # hotkey, which run the IDENTICAL code path, do trigger the blank
    # reliably. The most plausible hypothesis is that `/setactive` is a
    # lazy refresh that gets optimized away when the active scheme is
    # unchanged, and the kernel only re-reads the policy when prodded by
    # the right combination of state changes (the two working paths
    # produce some side effect the menu path doesn't). Rather than ship a
    # menu item that silently fails, we replace it with a disabled label
    # documenting the two paths that work.
    menu = Menu(
        MenuItem(f"Display Off v{__version__}", None, enabled=False),
        Menu.SEPARATOR,
        MenuItem("Blank displays:", None, enabled=False),
        MenuItem("  • Double-click this icon", None, enabled=False),
        MenuItem(lambda item: f"  • {hotkey_name[0]}", None, enabled=False),
        Menu.SEPARATOR,
        MenuItem("Settings...", on_settings),
        MenuItem("Quit", on_quit),
        # Hidden item — not shown in the right-click menu, but `default=True`
        # makes pystray route every left-click on the icon to this handler so
        # we can apply double-click detection.
        # NOTE: About and Check-for-Updates were moved out of this menu and
        # into the Settings dialog footer in v1.7.0 — the right-click menu is
        # now minimal (Settings + Quit), with the rest of the info/action
        # buttons living inside Settings.
        MenuItem("__icon_default__", on_icon_default_click, default=True, visible=False),
    )

    # Tooltip is also the registry-key identity for Win11's tray-icon
    # NotifyIconSettings — changing it invalidates any prior IsPromoted=1
    # setting. Keep stable.
    tray_tooltip = "Display Off"
    icon = pystray.Icon(
        name="displayoff",
        icon=icon_image,
        title=tray_tooltip,
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

    log.info("Running in system tray. Double-click icon or press %s to turn off displays.",
             hotkey_name[0])

    # Win11 hides new tray icons in the overflow flyout by default. Auto-promote
    # via the shared tray_promoter module (canonical pattern at
    # _.claude/_templates/snippets/python/tray-icon-promoter.md). pystray uses
    # pythonw.exe as the executable path, so the promoter matches on
    # (ExecutablePath, tooltip) rather than path alone to distinguish from
    # other Python tray apps the user might have. Guarded above against
    # ImportError so a missing/broken tray_promoter module never crashes
    # the tray (UX polish, never a crash surface).
    if _promote_tray:
        # max_wait_secs=None → poll for the full tray lifetime. Win11
        # catalogs pystray icons lazily (often not until the user opens
        # the tray overflow flyout for the first time), which can happen
        # hours after launch. The promoter uses backoff (0.5s for the
        # first minute, then 30s thereafter) so the CPU cost is negligible.
        promote_in_background(
            exe_path=sys.executable,
            tooltip=tray_tooltip,
            baseline=tray_baseline,
            max_wait_secs=None,
        )

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
    # File logging so pythonw.exe runs are debuggable. Without this, every
    # log.* call below goes to a NullHandler and we have zero visibility.
    # RotatingFileHandler (v1.7.0+) prevents unbounded growth — a tray app
    # logs every icon click, blank-trigger, listener-watchdog tick, and
    # idle-watcher sample. Without rotation the log would grow ~MB/day on
    # an active workstation. 1MB × 3 backups = ~4MB total budget.
    from logging.handlers import RotatingFileHandler
    _displayoff_log = os.path.join(_HERE, "displayoff.log")
    _file_handler = RotatingFileHandler(
        _displayoff_log, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(name)s] %(message)s",
        handlers=[_file_handler, logging.StreamHandler()],
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

    if "--native-off" in sys.argv:
        # Force the native idle-blank path regardless of config. Useful when
        # the user has `use_legacy_sc_monitorpower: true` but wants to fire
        # the native path explicitly (or vice-versa as `--legacy-off`).
        log.info("Turning off displays via native idle path (forced)...")
        turn_off_monitors(force_path="native")
        return

    if "--legacy-off" in sys.argv:
        log.info("Turning off displays via legacy SC_MONITORPOWER path (forced)...")
        turn_off_monitors(force_path="legacy")
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
