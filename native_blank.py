"""Native Windows display-off via the power-policy idle path.

Why this exists: on Nate's ROG Strix G614JV (Modern Standby only, hybrid
Intel UHD + NVIDIA RTX 4060), `SC_MONITORPOWER MONITOR_OFF` triggers a
display on/off cycle that input cannot recover from. The Windows-native
idle-display-off path (the one wired to Settings ▸ Power ▸ "Turn off the
display after N minutes") works correctly on the same hardware.

This script hooks into THAT path: it temporarily writes a 1-second
display-off timeout into the active power scheme, applies it (which fires
Windows' own native display-off code as the idle counter crosses the
threshold), waits long enough for the blank to take effect, then restores
the original timeouts. `SC_MONITORPOWER` is never sent.

Three modes, in order of risk:

    python native_blank.py --read     # read + print current timeouts, no writes
    python native_blank.py --toggle   # write 1s, sleep 0.5s, restore. NO BLANK.
    python native_blank.py --blank    # write 1s, sleep 3s, restore. REAL BLANK.

Safety: the restore lives in a try/finally + atexit + sentinel-file recovery
path, so even a hard kill mid-run leaves a breadcrumb the next launch can use
to put the timeouts back before anything else runs.
"""

import argparse
import atexit
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import subprocess
import sys
import time

# ── Windows: hide child-process consoles ──────────────────────────────────
# Under pythonw.exe (no parent console), every console-mode child like
# powercfg.exe gets a freshly-allocated console window unless we explicitly
# suppress it. Without these flags, a single --native-off invocation shows ~10
# black terminal flashes (one per powercfg call). Worse, the window churn can
# reset Windows' idle-input counter, preventing the native blank from firing.
# CREATE_NO_WINDOW suppresses the console allocation; STARTUPINFO + SW_HIDE is
# the belt-and-suspenders pair that some legacy Windows builds still need.
if sys.platform == "win32":
    _CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _STARTUPINFO.wShowWindow = 0  # SW_HIDE
else:
    _CREATE_NO_WINDOW = 0
    _STARTUPINFO = None


# ── Idle-counter introspection (diagnostic) ────────────────────────────────
# Used to log Windows' idle-input counter every 250ms during the blank sleep
# so we can see WHY the kernel might not be firing the native blank (e.g.,
# something keeps resetting idle to 0 during our window).
import ctypes  # noqa: E402

if sys.platform == "win32":
    from ctypes import wintypes  # noqa: E402

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _GetLastInputInfo = _user32.GetLastInputInfo
    _GetLastInputInfo.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]
    _GetLastInputInfo.restype = wintypes.BOOL
    _GetTickCount = _kernel32.GetTickCount
    _GetTickCount.argtypes = []
    _GetTickCount.restype = wintypes.DWORD

    def _idle_secs():
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not _GetLastInputInfo(ctypes.byref(info)):
            return -1.0
        elapsed = (_GetTickCount() - info.dwTime) & 0xFFFFFFFF
        return elapsed / 1000.0
else:
    def _idle_secs():
        return -1.0


def _sleep_with_idle_log(sleep_seconds, poll_interval=0.25):
    """Drop-in replacement for time.sleep(N) that records GetLastInputInfo
    idle-seconds every poll_interval so we can diagnose why the kernel may
    not be firing the native idle-blank during our window."""
    end = time.monotonic() + sleep_seconds
    samples = []
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            break
        samples.append(_idle_secs())
        time.sleep(min(poll_interval, remaining))
    log.info("idle samples during sleep (every %.2fs): %s",
             poll_interval, ", ".join(f"{s:.2f}" for s in samples))

# ── Paths ──────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_LOG_PATH = os.path.join(_HERE, "native_blank.log")
_SENTINEL_PATH = os.path.join(_HERE, ".native_blank_in_progress.json")

# ── Tunables ──────────────────────────────────────────────────────────────
_BLANK_TIMEOUT_SECONDS = 1   # value we write into AC/DC display-off timeout
_BLANK_SLEEP_SECONDS = 8.0   # how long --blank waits for the kernel to actually blank
_TOGGLE_SLEEP_SECONDS = 0.5  # how long --toggle waits (too short to actually blank)
_BLANK_HANDS_OFF_COUNTDOWN_SECONDS = 6  # pre-blank countdown so the user can stop touching input
_POWERCFG_TIMEOUT_SECONDS = 5

log = logging.getLogger("native_blank")


def _setup_logging():
    """File-backed logging so pythonw.exe runs are still debuggable.

    RotatingFileHandler bounds growth at 1 MB × 3 backups to match
    displayoff.log's policy — a 24/7 tray that fires idle-blank N times/day
    would otherwise grow this file unbounded across the process lifetime.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(message)s",
        handlers=[RotatingFileHandler(_LOG_PATH, maxBytes=1_000_000,
                                      backupCount=3, encoding="utf-8"),
                  logging.StreamHandler()],
    )


def _ensure_module_logger_has_filehandler():
    """When `native_blank` is imported by `displayoff.py` rather than run as a
    script, _setup_logging() is never called. Without a FileHandler our log.*
    calls go to a NullHandler under pythonw.exe — completely invisible. Attach
    a FileHandler directly to our module logger so import-driven runs also
    leave a paper trail in native_blank.log.

    Idempotent: returns early if any FileHandler pointing at our log path
    already exists on this logger.
    """
    for h in log.handlers:
        if isinstance(h, logging.FileHandler) and os.path.abspath(getattr(h, "baseFilename", "")) == os.path.abspath(_LOG_PATH):
            return
    fh = RotatingFileHandler(_LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s [import] %(message)s"))
    log.addHandler(fh)
    log.setLevel(logging.INFO)
    log.propagate = False  # don't double-log via root


# ── powercfg shell-out ────────────────────────────────────────────────────

def _run_powercfg(args, *, check=True):
    """Run powercfg.exe with the given args. Returns (returncode, stdout, stderr).

    powercfg lives in System32 and is on PATH for every Windows install — we
    don't hardcode the path so the script also works under unusual PATH setups.
    """
    cmd = ["powercfg.exe"] + list(args)
    log.debug("→ %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=_POWERCFG_TIMEOUT_SECONDS,
            creationflags=_CREATE_NO_WINDOW,
            startupinfo=_STARTUPINFO,
        )
    except FileNotFoundError:
        raise RuntimeError("powercfg.exe not found — is this Windows?")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"powercfg.exe timed out after {_POWERCFG_TIMEOUT_SECONDS}s: {cmd}")
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"powercfg returned {proc.returncode}\n"
            f"  cmd:    {' '.join(cmd)}\n"
            f"  stdout: {proc.stdout.strip()}\n"
            f"  stderr: {proc.stderr.strip()}"
        )
    return proc.returncode, proc.stdout, proc.stderr


def _read_display_timeouts():
    """Read current AC + DC display-off timeouts (seconds) from active scheme.

    Returns (ac_seconds, dc_seconds, scheme_guid_str). Raises if it can't parse.
    """
    _, out, _ = _run_powercfg(["/getactivescheme"])
    m = re.search(r"Power Scheme GUID:\s+([0-9a-fA-F-]+)", out)
    if not m:
        raise RuntimeError(f"could not parse active scheme guid from:\n{out}")
    scheme = m.group(1)

    _, out, _ = _run_powercfg(["/query", "SCHEME_CURRENT", "SUB_VIDEO", "VIDEOIDLE"])
    ac_m = re.search(r"Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)", out)
    dc_m = re.search(r"Current DC Power Setting Index:\s*0x([0-9a-fA-F]+)", out)
    if not (ac_m and dc_m):
        raise RuntimeError(f"could not parse AC/DC timeouts from:\n{out}")
    ac = int(ac_m.group(1), 16)
    dc = int(dc_m.group(1), 16)
    return ac, dc, scheme


def _write_display_timeouts(ac_seconds, dc_seconds):
    """Write AC + DC display-off timeouts (in seconds) and re-apply active scheme.

    Both writes happen before the /setactive so the kernel sees a consistent
    pair, not a transient where AC was changed but DC wasn't.
    """
    _run_powercfg(["/setacvalueindex", "SCHEME_CURRENT", "SUB_VIDEO", "VIDEOIDLE", str(ac_seconds)])
    _run_powercfg(["/setdcvalueindex", "SCHEME_CURRENT", "SUB_VIDEO", "VIDEOIDLE", str(dc_seconds)])
    _run_powercfg(["/setactive", "SCHEME_CURRENT"])


# ── Sentinel: crash-recovery for the restore ──────────────────────────────

def _write_sentinel(saved_ac, saved_dc):
    """Persist the original timeouts to a sentinel file so a crash mid-run
    doesn't leave the user stuck with a 1-second display timeout."""
    with open(_SENTINEL_PATH, "w", encoding="utf-8") as f:
        json.dump({"ac": saved_ac, "dc": saved_dc, "pid": os.getpid()}, f)


def _clear_sentinel():
    if os.path.exists(_SENTINEL_PATH):
        try:
            os.remove(_SENTINEL_PATH)
        except OSError as e:
            log.warning("could not remove sentinel: %s", e)


def _recover_from_stale_sentinel():
    """If a previous run left a sentinel, the last run probably crashed.
    Restore from the saved values before doing anything else.

    Three failure modes, each handled distinctly:
      - Sentinel unreadable (OSError / corrupt JSON) → DELETE it. Keeping it
        causes every future launch to hit the same wall and bail; the user
        ends up with a permanently broken native-blank path. The sentinel's
        value is zero if we can't read it.
      - Sentinel content invalid (wrong types) → same, DELETE.
      - Restore powercfg call fails → leave sentinel on disk for next-run
        retry, log a manual-recovery command for the user.
    """
    if not os.path.exists(_SENTINEL_PATH):
        return
    try:
        # utf-8-sig reads both BOM-prefixed and BOM-less UTF-8. Our own
        # writer doesn't emit a BOM, but if anyone (PowerShell's `Set-Content
        # -Encoding UTF8`, a hex-editor save, a different tool) ever drops a
        # BOM into the sentinel, we still recover instead of treating it as
        # corrupt.
        with open(_SENTINEL_PATH, "r", encoding="utf-8-sig") as f:
            saved = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.error("sentinel exists but is unreadable (%s) — deleting it. "
                  "If your display-off timeout is wrong, run: "
                  "powercfg /setacvalueindex SCHEME_CURRENT SUB_VIDEO VIDEOIDLE <seconds>",
                  e)
        try:
            os.remove(_SENTINEL_PATH)
        except OSError as rm_e:
            log.error("could not delete corrupt sentinel: %s", rm_e)
        return
    ac, dc = saved.get("ac"), saved.get("dc")
    if not (isinstance(ac, int) and isinstance(dc, int)):
        log.error("sentinel content invalid (%r) — deleting it", saved)
        try:
            os.remove(_SENTINEL_PATH)
        except OSError as rm_e:
            log.error("could not delete invalid sentinel: %s", rm_e)
        return
    log.warning("stale sentinel from PID %s — restoring AC=%ds DC=%ds before continuing",
                saved.get("pid"), ac, dc)
    try:
        _write_display_timeouts(ac, dc)
        _clear_sentinel()
    except Exception as e:
        log.error("recovery powercfg call failed: %s — sentinel left on disk for next-run retry. "
                  "Manual fix: powercfg /setacvalueindex SCHEME_CURRENT SUB_VIDEO VIDEOIDLE %d ; "
                  "powercfg /setdcvalueindex SCHEME_CURRENT SUB_VIDEO VIDEOIDLE %d ; "
                  "powercfg /setactive SCHEME_CURRENT",
                  e, ac, dc)


# ── Core action ───────────────────────────────────────────────────────────

def recover_stale_sentinel():
    """Public entry-point for eager startup recovery.

    Call this from your tray app's startup path BEFORE the tray icon
    registers and BEFORE any blank fires. If a previous run was killed
    mid-blank (BSOD, power loss, Task Manager kill, OOM), the sentinel
    file persists on disk and the user's display-off timeout is still at
    1 second — meaning the screen blanks every 1s of idle until next
    blank-trigger (which calls `_recover_from_stale_sentinel` internally
    before doing its own work).

    By calling this eagerly at app launch, we close that window so the
    user never sees the "why is my screen blanking constantly?" moment
    after an abrupt prior termination.

    Idempotent: no-op if there's no sentinel on disk. Safe to call from
    any thread."""
    _ensure_module_logger_has_filehandler()
    _recover_from_stale_sentinel()


def blank_via_idle_path(sleep_seconds=None, hands_off_countdown=0):
    """Public API used by displayoff.py.

    Temporarily writes a 1s display-off timeout, waits for the Windows kernel
    to fire its native idle-blank, then restores the original timeout. No
    SC_MONITORPOWER message is sent.

    Returns True on clean restore (sentinel removed), False if the sentinel
    is still on disk (caller should investigate).
    """
    _ensure_module_logger_has_filehandler()
    log.info("blank_via_idle_path called from PID %d (import path)", os.getpid())
    if sleep_seconds is None:
        sleep_seconds = _BLANK_SLEEP_SECONDS
    try:
        _recover_from_stale_sentinel()
        native_blank(sleep_seconds, dry_label="display blank", hands_off_countdown=hands_off_countdown)
    except Exception:
        log.exception("blank_via_idle_path raised")
        raise
    return not os.path.exists(_SENTINEL_PATH)


def native_blank(sleep_seconds, dry_label, hands_off_countdown=0):
    """The full sequence: read, write 1s, sleep, restore. dry_label is just for logging."""
    saved_ac, saved_dc, scheme = _read_display_timeouts()
    log.info("active scheme: %s", scheme)
    log.info("saved AC=%ds DC=%ds — will write %ds, sleep %.1fs (%s), restore",
             saved_ac, saved_dc, _BLANK_TIMEOUT_SECONDS, sleep_seconds, dry_label)

    if hands_off_countdown > 0:
        log.warning("hands off keyboard/mouse — blank firing in %ds", hands_off_countdown)
        for remaining in range(hands_off_countdown, 0, -1):
            print(f"  hands off: {remaining}...", flush=True)
            time.sleep(1.0)

    _write_sentinel(saved_ac, saved_dc)

    # Per-invocation atexit handler — must be a named function (not a lambda)
    # so we can `atexit.unregister` it on clean exit. Without this, every
    # blank in a long-running tray (idle-watcher fires can add 50+ per day)
    # accumulates a permanent closure in atexit's list — slow leak.
    def _this_invocation_atexit():
        _attempt_atexit_restore(saved_ac, saved_dc)
    atexit.register(_this_invocation_atexit)

    try:
        _write_display_timeouts(_BLANK_TIMEOUT_SECONDS, _BLANK_TIMEOUT_SECONDS)
        log.info("timeouts set to %ds AC + DC — sleeping %.1fs (idle pre-sleep=%.3fs)",
                 _BLANK_TIMEOUT_SECONDS, sleep_seconds, _idle_secs())
        _sleep_with_idle_log(sleep_seconds)
    finally:
        # Restore + sentinel-clear MUST be resilient: if _write_display_timeouts
        # raises here, the un-wrapped version of this block would skip
        # _clear_sentinel and the sentinel becomes immortal — every future run
        # would hit the same failing restore and bail without clearing it.
        # Wrap defensively, log loudly, but ALWAYS clear the sentinel if we
        # got the values back to expected (verified by post-restore read).
        log.info("restoring AC=%ds DC=%ds", saved_ac, saved_dc)
        restore_ok = False
        try:
            _write_display_timeouts(saved_ac, saved_dc)
            restore_ok = True
        except Exception as e:
            log.error("RESTORE FAILED — display timeout may be stuck at 1s. "
                      "Manual fix: powercfg /setacvalueindex SCHEME_CURRENT SUB_VIDEO VIDEOIDLE %d ; "
                      "powercfg /setdcvalueindex SCHEME_CURRENT SUB_VIDEO VIDEOIDLE %d ; "
                      "powercfg /setactive SCHEME_CURRENT. Error: %s",
                      saved_ac, saved_dc, e)
        # Verification: re-read and check. Clear sentinel ONLY on affirmative
        # match. The earlier "conservatively clear on verification failure"
        # path was a footgun — `restore_ok=True` only proves the powercfg
        # subprocess returned 0, not that the active scheme reflects the
        # values we wrote. If we can't verify and we still clear the sentinel,
        # the user could be silently stuck at AC=1 with no recovery trail on
        # next launch. Better to leave the sentinel on disk and let the next
        # launch's `_recover_from_stale_sentinel` retry the restore than to
        # paper over an unknown state.
        try:
            ac, dc, _ = _read_display_timeouts()
            log.info("post-restore verification: AC=%ds DC=%ds", ac, dc)
            if (ac, dc) == (saved_ac, saved_dc):
                _clear_sentinel()
            else:
                log.error("RESTORE MISMATCH — expected AC=%ds DC=%ds, got AC=%ds DC=%ds. "
                          "Sentinel left on disk for next-run recovery. "
                          "Manual fix: powercfg /setacvalueindex SCHEME_CURRENT SUB_VIDEO VIDEOIDLE %d ; "
                          "powercfg /setdcvalueindex SCHEME_CURRENT SUB_VIDEO VIDEOIDLE %d ; "
                          "powercfg /setactive SCHEME_CURRENT",
                          saved_ac, saved_dc, ac, dc, saved_ac, saved_dc)
        except Exception as e:
            log.error("could not verify restore (%s) — sentinel left on disk for next-run recovery. "
                      "If display-off timeout is wrong, manual fix: "
                      "powercfg /setacvalueindex SCHEME_CURRENT SUB_VIDEO VIDEOIDLE %d ; "
                      "powercfg /setdcvalueindex SCHEME_CURRENT SUB_VIDEO VIDEOIDLE %d ; "
                      "powercfg /setactive SCHEME_CURRENT",
                      e, saved_ac, saved_dc)
        # Unregister this invocation's atexit handler regardless of restore
        # outcome — the in-process try/finally already ran. Leaving it
        # registered would re-fire at process exit and try to restore values
        # that are either already correct (no-op via sentinel-absent check)
        # or stale-and-unrecoverable. Either way, no value in keeping the
        # registration around past this point; unregister to prevent the
        # long-running-tray accumulation leak.
        try:
            atexit.unregister(_this_invocation_atexit)
        except Exception:
            pass


def _attempt_atexit_restore(ac, dc):
    """Last-ditch restore. atexit fires even on sys.exit/uncaught exception,
    though not on os._exit or hard kill — that's what the sentinel covers."""
    if not os.path.exists(_SENTINEL_PATH):
        return  # normal path already restored; sentinel is gone
    log.warning("atexit restoring AC=%ds DC=%ds", ac, dc)
    try:
        _write_display_timeouts(ac, dc)
        _clear_sentinel()
    except Exception as e:
        log.error("atexit restore failed: %s", e)


# ── Entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--read", action="store_true",
                   help="read + print current timeouts. No writes. No risk.")
    g.add_argument("--toggle", action="store_true",
                   help=f"write 1s, sleep {_TOGGLE_SLEEP_SECONDS}s, restore. Plumbing test, no blank.")
    g.add_argument("--blank", action="store_true",
                   help=f"write 1s, sleep {_BLANK_SLEEP_SECONDS}s, restore. REAL display blank.")
    args = parser.parse_args()

    _setup_logging()
    _recover_from_stale_sentinel()

    if args.read:
        ac, dc, scheme = _read_display_timeouts()
        log.info("scheme=%s  AC display-off=%ds (%d min)  DC display-off=%ds (%d min)",
                 scheme, ac, ac // 60, dc, dc // 60)
        return 0

    if args.toggle:
        native_blank(_TOGGLE_SLEEP_SECONDS, dry_label="plumbing test")
        return 0

    if args.blank:
        native_blank(_BLANK_SLEEP_SECONDS, dry_label="real blank",
                     hands_off_countdown=_BLANK_HANDS_OFF_COUNTDOWN_SECONDS)
        return 0

    parser.error("internal: no mode selected")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.warning("interrupted — sentinel-based recovery will fire on next run")
        sys.exit(130)
    except Exception as e:
        log.exception("fatal: %s", e)
        sys.exit(1)
