r"""Win11 tray-icon auto-promote (Python port).

Direct port of `_.claude/_templates/snippets/csharp/tray-icon-promoter.md`,
v3 (the canonical version used by MicMute, SyncthingPause, EQSwitch, etc).
The Python adaptation has one extra requirement compared to the C# version:
**Python tray apps share `pythonw.exe` as their ExecutablePath**, so Phase 1
must match on `(ExecutablePath, InitialTooltip)` instead of `ExecutablePath`
alone — otherwise we'd promote every Python tray app the user has.

On Windows 11 22H2+, new tray icons default to hidden-in-overflow until the
user manually flips "Show icon in taskbar" under
Settings → Personalization → Taskbar → Other system tray icons. For a tray-
only app like displayoff that delivers no value while hidden, that's a
painful first-run experience. This module flips the visibility flag
automatically on first launch, respects users who deliberately hid the
icon (IsPromoted=0 stays 0), and survives Explorer restarts.

## Two-phase identification

**Phase 1 — Match by (ExecutablePath, InitialTooltip).** Normal case on
reruns and the typical first-run path: Explorer writes the full schema
on NIM_ADD with the tooltip we passed via NIF_TIP, we find our subkey by
the (path, tooltip) tuple, flip IsPromoted if missing.

**Phase 2 — Claim sole-new-orphan.** Edge case observed when the subkey
was externally deleted mid-session and Explorer's in-memory cache held
the old mapping: next NIM_ADD writes only IconSnapshot. No
ExecutablePath, no way for Phase 1 to find us. Solution: capture the set
of existing subkey names BEFORE the icon registers. After NIM_ADD, look
for subkeys that (a) appeared since baseline and (b) have IconSnapshot
but no ExecutablePath — these are orphans. If exactly **one** such
orphan exists, it's ours — write both ExecutablePath, InitialTooltip,
and IsPromoted=1. If **multiple** orphans exist (Win-login race with
other tray apps), wait for the next tick — Explorer will fill in the
other apps' ExecutablePath, leaving ours as the sole remaining orphan.

## Respecting user intent

We never override an explicit `IsPromoted=0` — that's the user having
deliberately hidden us, and we keep it that way.

## Schema (undocumented, stable Win11 22H2 → 25H2)

```
HKCU\Control Panel\NotifyIconSettings\<18-20-digit-hash>
    IsPromoted     REG_DWORD  1=visible / 0=hidden / missing=hidden (default)
    ExecutablePath REG_SZ     Full path to exe (pythonw.exe for Python apps)
    InitialTooltip REG_SZ     Tooltip text at NIM_ADD time
    UID            REG_DWORD  Matches NOTIFYICONDATA.uID
    IconSnapshot   REG_BINARY Explorer's cached PNG of the rendered icon
```

The 18-20-digit subkey name is an unsigned 64-bit hash of
`(ExecutablePath, guidItem)` — we don't compute it; we enumerate.

All registry interaction is wrapped in try/except so a schema change in a
future Windows build silently no-ops instead of crashing.

Canonical template: `_.claude/_templates/snippets/python/tray-icon-promoter.md`
"""
import logging
import os
import sys
import threading
import time

if sys.platform == "win32":
    import winreg
else:
    winreg = None

log = logging.getLogger("tray_promoter")

_KEY_PATH = r"Control Panel\NotifyIconSettings"
_MIN_WIN11_BUILD = 22000

_DEFAULT_POLL_INTERVAL_SECS = 0.5
_DEFAULT_MAX_WAIT_SECS = 10.0


def _on_win11():
    """True if we're on Win11 22H2+ (the build that introduced NotifyIconSettings)."""
    if sys.platform != "win32":
        return False
    try:
        return sys.getwindowsversion().build >= _MIN_WIN11_BUILD
    except Exception:
        return False


def _safe_read(sub, name, expected_type=None):
    """winreg.QueryValueEx wrapper that returns None on missing/wrong-type."""
    try:
        v, _ = winreg.QueryValueEx(sub, name)
    except (FileNotFoundError, OSError):
        return None
    if expected_type is not None and not isinstance(v, expected_type):
        return None
    return v


# ── Stale-entry sweep ──────────────────────────────────────────────────────

def sweep_stale_entries(our_exe_name, current_exe_path):
    """Delete NotifyIconSettings subkeys whose ExecutablePath points to a
    file that no longer exists, scoped to entries whose path basename
    matches our exe basename. Targets the "N entries in tray-overflow
    Settings" cruft that .NET single-file publish + WinGet versioned
    install dirs leave behind across releases.

    **NOT INVOKED FROM DISPLAYOFF.** Displayoff is pystray-backed (exe
    basename = pythonw.exe / python.exe), and this function explicitly
    no-ops for those basenames because "pythonw.exe" is too broad to
    scope safely — would match every other Python tray app the user has.
    The function ships in this module as a **template-portable helper**
    for future Python tray projects that bundle to a stable named .exe
    (e.g., via PyInstaller onefile with --name); those projects should
    invoke `sweep_stale_entries(our_exe_name="myapp.exe", current_exe_path=sys.executable)`
    once at startup before `capture_baseline()`.

    Conservative — only touches entries that:
      (a) have ExecutablePath populated (skips orphans / sparse subkeys),
      (b) have basename matching `our_exe_name` (case-insensitive),
      (c) point to a file that does NOT exist on disk,
      (d) are NOT the currently-running exe path (defensive).

    Run ONCE at startup, BEFORE capture_baseline. Do NOT run from any
    Explorer-restart handler — that fires mid-session while Explorer is
    actively mutating the registry.

    Returns the count of entries removed (0 on no-op or failure).
    """
    if not _on_win11() or not our_exe_name:
        return 0
    if our_exe_name.lower() in ("pythonw.exe", "python.exe"):
        # Too generic to scope safely — would match other Python tray apps.
        return 0
    try:
        current_normalized = os.path.normcase(os.path.abspath(current_exe_path or ""))
    except OSError:
        current_normalized = (current_exe_path or "").lower()

    swept = 0
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY_PATH, 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as root:
            # Snapshot subkey names BEFORE iteration — DeleteKey mid-enumeration
            # would corrupt the enumerator.
            sub_names = []
            i = 0
            while True:
                try:
                    sub_names.append(winreg.EnumKey(root, i))
                except OSError:
                    break
                i += 1

            for sub_name in sub_names:
                try:
                    with winreg.OpenKey(root, sub_name, 0, winreg.KEY_READ) as sub:
                        path = _safe_read(sub, "ExecutablePath", str)
                    if not path:
                        continue
                    if os.path.basename(path).lower() != our_exe_name.lower():
                        continue
                    try:
                        path_normalized = os.path.normcase(os.path.abspath(path))
                    except OSError:
                        path_normalized = path.lower()
                    if path_normalized == current_normalized:
                        continue
                    if os.path.exists(path):
                        continue
                    winreg.DeleteKey(root, sub_name)
                    log.info("tray_promoter.sweep: removed %s -> %s", sub_name, path)
                    swept += 1
                except OSError as e:
                    log.warning("tray_promoter.sweep: subkey %s: %s", sub_name, e)
    except OSError as e:
        log.warning("tray_promoter.sweep: %s", e)

    if swept > 0:
        log.info("tray_promoter.sweep: removed %d stale entr%s for %s",
                 swept, "y" if swept == 1 else "ies", our_exe_name)
    return swept


# ── Baseline capture (Phase 2 orphan-detection prerequisite) ───────────────

def capture_baseline():
    """Snapshot the set of NotifyIconSettings subkey names that exist
    BEFORE our tray icon registers. Anything that appears later is a
    candidate for Phase-2 orphan matching.

    Returns a frozenset on success, None on failure. Callers should treat
    None as "skip Phase 2" rather than "no subkeys existed," otherwise
    every pre-existing orphan would be misclaimed."""
    if not _on_win11():
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY_PATH, 0,
                            winreg.KEY_READ) as root:
            names = []
            i = 0
            while True:
                try:
                    names.append(winreg.EnumKey(root, i))
                except OSError:
                    break
                i += 1
            return frozenset(names)
    except OSError as e:
        log.warning("tray_promoter.capture_baseline: %s", e)
        return None


# ── Single-tick promote attempt ────────────────────────────────────────────

def try_promote(exe_path, tooltip, baseline):
    """Ensure the (exe_path, tooltip) tray icon's IsPromoted is set to 1.

    Returns True once we've identified our subkey (write or no-op) so the
    caller's retry loop can stop. Returns False while we're still waiting
    for Explorer to create or populate it — caller should retry.

    Python adaptation: Phase 1 matches on (ExecutablePath, InitialTooltip)
    together, because pythonw.exe is shared by many tray apps. Matching
    on path alone would promote every Python tray, including unrelated
    ones."""
    if not _on_win11() or not exe_path:
        return False
    if not tooltip:
        log.warning("tray_promoter.try_promote: tooltip is required for Python apps")
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY_PATH, 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as root:
            identified = False
            orphan_candidates = []
            i = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, sub_name, 0,
                                        winreg.KEY_READ | winreg.KEY_WRITE) as sub:
                        path = _safe_read(sub, "ExecutablePath", str)
                        existing_tooltip = _safe_read(sub, "InitialTooltip", str)

                        # Phase 1 — match by (path, tooltip).
                        if path:
                            if path.lower() != exe_path.lower() or existing_tooltip != tooltip:
                                continue
                            identified = True
                            current = _safe_read(sub, "IsPromoted", int)
                            if current == 0:
                                log.info("tray_promoter: %s IsPromoted=0 — respecting user's choice",
                                         sub_name)
                                continue
                            if current == 1:
                                continue
                            winreg.SetValueEx(sub, "IsPromoted", 0,
                                              winreg.REG_DWORD, 1)
                            log.info("tray_promoter: promoted %s for (%s, %r)",
                                     sub_name, path, tooltip)
                            continue

                        # Phase 2 candidate — orphan (IconSnapshot but no
                        # ExecutablePath) that appeared AFTER our NIM_ADD.
                        if baseline is None:
                            continue
                        if sub_name in baseline:
                            continue
                        if _safe_read(sub, "IconSnapshot", bytes) is None:
                            continue
                        orphan_candidates.append(sub_name)
                except OSError as e:
                    log.warning("tray_promoter: subkey %s: %s", sub_name, e)

            # Phase 2 commit — only claim when exactly one orphan appeared.
            if not identified and len(orphan_candidates) == 1:
                sub_name = orphan_candidates[0]
                try:
                    with winreg.OpenKey(root, sub_name, 0,
                                        winreg.KEY_READ | winreg.KEY_WRITE) as sub:
                        current = _safe_read(sub, "IsPromoted", int)
                        if current == 0:
                            log.info("tray_promoter: orphan %s IsPromoted=0 — respecting user's choice",
                                     sub_name)
                            return True
                        winreg.SetValueEx(sub, "ExecutablePath", 0,
                                          winreg.REG_SZ, exe_path)
                        winreg.SetValueEx(sub, "InitialTooltip", 0,
                                          winreg.REG_SZ, tooltip)
                        winreg.SetValueEx(sub, "IsPromoted", 0,
                                          winreg.REG_DWORD, 1)
                        log.info("tray_promoter: claimed orphan %s -> wrote ExecPath + Tooltip + IsPromoted=1 for (%s, %r)",
                                 sub_name, exe_path, tooltip)
                        return True
                except OSError as e:
                    log.warning("tray_promoter: claim orphan %s: %s", sub_name, e)
            elif len(orphan_candidates) > 1:
                log.info("tray_promoter: %d new orphan subkeys — deferring to next tick",
                         len(orphan_candidates))

            return identified
    except OSError as e:
        # Registry access denied, hive locked, schema moved — anything.
        # This is UX polish, never a crash surface.
        log.warning("tray_promoter: %s", e)
        return False


# ── Background promote thread (the usual public entry-point) ───────────────

def promote_in_background(exe_path, tooltip,
                          baseline=None,
                          poll_interval=_DEFAULT_POLL_INTERVAL_SECS,
                          max_wait_secs=None,
                          backoff_after_secs=60.0,
                          backoff_interval=30.0):
    """Spawn a daemon thread that polls try_promote until it returns True
    or `max_wait_secs` elapses (None = forever, until the process exits).

    Why default to forever: on Win11 with pystray-based apps that share
    `pythonw.exe`, the OS catalogues the NotifyIconSettings entry lazily —
    often not until the user opens the tray-overflow flyout for the first
    time, which can be MINUTES OR HOURS after tray launch. A short poll
    window times out before that happens, and the user is stuck with a
    hidden icon for the rest of the session. Polling forever (with
    backoff) keeps the promoter alive so it catches the catalog event
    whenever it happens.

    Backoff schedule: poll every `poll_interval` (default 0.5s) for the
    first `backoff_after_secs` (default 60s) — that's the "user is
    actively setting things up, they might open the flyout right now"
    window. After that, poll every `backoff_interval` (default 30s)
    indefinitely. Cost: ~2 registry enumerations per minute, negligible.

    If `baseline` is None, captures one internally — but ideally the
    caller captures it BEFORE registering the tray icon and passes it in,
    so Phase 2 can detect orphans that appeared during/after NIM_ADD.

    Returns the Thread object so the caller can `.join()` if they want
    deterministic shutdown (most apps just let the daemon die with the
    process)."""
    if baseline is None:
        baseline = capture_baseline()

    def _poll():
        start = time.monotonic()
        attempts = 0
        while True:
            attempts += 1
            try:
                if try_promote(exe_path, tooltip, baseline):
                    log.info("tray_promoter: promotion complete after %d attempts / %.1fs",
                             attempts, time.monotonic() - start)
                    return
            except Exception as e:
                log.warning("tray_promoter.promote_in_background: %s", e)

            elapsed = time.monotonic() - start
            if max_wait_secs is not None and elapsed >= max_wait_secs:
                log.info("tray_promoter: timed out after %.1fs / %d attempts — entry for %r not catalogued by Windows. "
                         "Open the tray overflow flyout once or toggle Settings ▸ Other system tray icons to seed the registry entry.",
                         elapsed, attempts, tooltip)
                return

            # Backoff: fast polling for the first minute, then drop to a
            # much longer interval. Cheap enough to run forever.
            interval = poll_interval if elapsed < backoff_after_secs else backoff_interval
            time.sleep(interval)

    t = threading.Thread(target=_poll, daemon=True, name="tray-promoter")
    t.start()
    return t
