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
    python displayoff.py --diagnose-paths # Print path-resolver candidates + winning strategy (v1.7.19+)
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

__version__ = "1.7.22"

log = logging.getLogger("displayoff")


# ── Freeze-mode detection ─────────────────────────────────────────────────
# v1.7.22 ships a Nuitka --standalone bundle (build/displayoff/displayoff.exe
# + ~150 runtime DLLs alongside it, packaged for distribution as
# displayoff-vX.Y.Z.zip). v1.7.13–v1.7.21 used --onefile, but that mode's
# %TEMP%\onefile_<pid>_<rand>\ extraction pattern matched Defender's
# Trojan:Win32/Bearfoos.A!ml heuristic on every launch — the path/strategy
# logic below dates from the --onefile era and still covers it for users
# building locally with the legacy flag. The .py source mode is still a
# valid parallel distribution channel for development.
#
#   - Nuitka --standalone (v1.7.22+ shipped builds): sets `__compiled__`.
#     The .exe lives PERSISTENTLY in <install_dir>/displayoff/, alongside
#     all its bundled DLLs. There is NO temp extraction:
#       - `sys.executable` = the on-disk .exe (path on disk, in the bundle dir).
#       - `__file__`       = the bundle dir's compiled module path (under
#         <install_dir>/displayoff/).
#       - `sys.argv[0]`    = path the bundle was invoked with (typically the
#         on-disk .exe; argv can still be lied about by an upstream parent).
#       - `os.environ["NUITKA_ONEFILE_PARENT"]` = UNSET (no bootstrap parent).
#       - `__compiled__.original_argv0` = the launch path. Same Nuitka attr
#         as under --onefile; populated identically.
#     The strategies below still work — Strategy 0 (__compiled__.original_argv0)
#     and Strategy 2 (sys.argv[0]) both point at the on-disk .exe directly,
#     and the `_path_under_temp` filter is a no-op under standalone.
#
#   - Nuitka --onefile (legacy, v1.7.13–v1.7.21 + anyone still building
#     locally with the old flag): also sets the `__compiled__` module attr.
#     The on-disk displayoff.exe is the BOOTSTRAP process; it extracts the
#     real CPython interpreter + compiled modules to %TEMP%\onefile_<pid>_…
#     then CreateProcessW-spawns a child python.exe inside that temp dir.
#     The Python code we're reading runs in the CHILD, where:
#       - `sys.executable` = the temp-extracted python.exe (NOT the on-disk
#         .exe — `sys.executable` here is the interpreter actually executing
#         this code, which is the freshly-extracted CPython under TEMP).
#       - `__file__`       = the temp extraction dir's compiled module path.
#       - `sys.argv[0]`    = path the bootstrap was invoked with (typically
#         the on-disk .exe, but argv can be lied about by an upstream parent).
#       - `os.environ["NUITKA_ONEFILE_PARENT"]` = bootstrap process PID. The
#         bootstrap IS the on-disk .exe → QueryFullProcessImageNameW on this
#         PID yields the kernel-tracked image path = ground truth.
#       - `__compiled__.original_argv0` may exist on newer Nuitkas; we treat
#         it as advisory candidate, not authoritative.
#
#     v1.7.17 fixes a latent bug present since v1.7.13's freeze pass: the
#     comment block previously claimed "sys.executable is the on-disk .exe"
#     (matching PyInstaller's behavior). Empirically false on Nuitka 4.1.1
#     — displayoff.log captured `'C:\Users\nate\AppData\Local\Temp\onefile_
#     42604_561348_DreZIYVFd8M\python.exe'` as `sys.executable`'s value.
#     `_resolve_on_disk_exe_path()` below is the corrected resolver.
#
#   - PyInstaller onefile (defensive — we don't ship this, but the helper
#     stays correct if someone builds with PyInstaller for testing): sets
#     `sys.frozen = True` and exposes `sys._MEIPASS` (temp extract dir);
#     under PyInstaller `sys.executable` IS the on-disk .exe, so the
#     resolver's sys.argv[0] strategy catches that case too.
#   - .py source: neither sentinel is set, `sys.executable` is the Python
#     interpreter (python.exe / pythonw.exe), `__file__` is the script.
#     The resolver returns None under source mode and the rename-dance
#     is skipped entirely.
#
# Two distinct directories under freeze:
#   _HERE        — bundle's temp extraction dir (where displayoff.ico and
#                  imported modules land at launch; transient, per-run).
#                  Pystray's Image.open(_ICON_PATH) reads from here.
#   _INSTALL_DIR — on-disk dir containing the .exe itself (persistent
#                  across launches). The autostart .lnk's WorkingDirectory
#                  and the rename-dance update flow both target this.
#
# Both collapse to the script dir under .py source. The split only matters
# when frozen, but the resolver works in both modes so call sites stay
# single-code-path.
def _is_frozen():
    return getattr(sys, "frozen", False) or "__compiled__" in globals()


# Buffer for data-dir migration messages emitted before basicConfig wires
# up the file handler. main() flushes this list once the logger is live,
# with prefix `data-dir migration:`. Module-level so `_ensure_data_dir`
# and `_migrate_legacy_data` can both append. v1.7.19 split: the path
# resolver no longer writes here — see `_RESOLVER_LOG` below, which has
# its own flush prefix and fires on EVERY startup (not gated on whether
# migration actually ran).
_MIGRATION_LOG: list[str] = []

# Buffer for path-resolver candidates + winning-strategy lines, kept
# separate from _MIGRATION_LOG so the log prefix reflects what was actually
# logged (`path-resolver:` not `data-dir migration:`). Flushed on EVERY
# startup, not gated on whether migration ran — v1.7.18 leaked diagnostic
# data on pid 18996 because its data-dir was already migrated, so the
# resolver outcome stayed in-memory only. v1.7.19 separates the concerns:
# migration log = one-shot upgrade events; resolver log = every-launch
# observability into which strategy won and why.
_RESOLVER_LOG: list[str] = []


def _path_under_temp(path):
    """True if `path` lives under any of Windows' canonical temp-dir env
    vars: TEMP, TMP, LOCALAPPDATA\\Temp. v1.7.17 T2-Sonnet+T2-Opus
    hardening: a single `%TEMP%` check is fragile because the env can be
    cleared in restricted accounts or sandboxed services, and short-name
    (8.3) vs long-name resolution can also defeat a string prefix match.
    This helper resolves both sides via `os.path.realpath` + `normcase`
    so junctions, symlinks, and 8.3 names all compare equal."""
    if not path:
        return False
    try:
        canonical = os.path.normcase(os.path.realpath(path))
    except OSError:
        canonical = os.path.normcase(os.path.abspath(path))
    temp_candidates = []
    for env_var in ("TEMP", "TMP"):
        v = os.environ.get(env_var)
        if v:
            temp_candidates.append(v)
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        temp_candidates.append(os.path.join(local_appdata, "Temp"))
    for raw in temp_candidates:
        try:
            t = os.path.normcase(os.path.realpath(raw))
        except OSError:
            t = os.path.normcase(os.path.abspath(raw))
        # Append a separator so a path like `C:\Temp_Backup\foo.exe` doesn't
        # match `C:\Temp\` as a substring.
        if not t.endswith(os.sep):
            t = t + os.sep
        if canonical.startswith(t):
            return True
    return False


def _path_under_protected(path):
    """True if `path` lives under a Windows-protected location the resolver
    must never target. Covers:
      - `%LOCALAPPDATA%\\Microsoft\\WindowsApps\\` (Store reparse-point
        stubs; v1.7.20 original closure)
      - `%SystemRoot%\\System32\\` and `%SystemRoot%\\SysWOW64\\` (kernel-
        owned binaries; v1.7.20 verifier-convergent T2-Sonnet + T2-Opus)
      - `%ProgramFiles%`, `%ProgramFiles(x86)%` (admin-installed apps;
        ACLs would block writes anyway but rejecting up front prevents
        partway-through-the-dance failures)
      - `%ProgramData%` (machine-wide app data; same rationale)
    A malicious argv[0] pointing at any of these would force the rename-
    dance to attempt `os.rename(current, old)` on a path the current user
    can't write — failing mid-step with a confusing ACL error. Rejecting
    up front gives a clean "manual install required" log path instead.
    Symmetric resolution (realpath + normcase) with `_path_under_temp`
    so junctions/symlinks/8.3 short-names compare equal."""
    if not path:
        return False
    try:
        canonical = os.path.normcase(os.path.realpath(path))
    except OSError:
        canonical = os.path.normcase(os.path.abspath(path))
    protected_candidates = []
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        protected_candidates.append(
            os.path.join(local_appdata, "Microsoft", "WindowsApps")
        )
    # v1.7.20 verifier-convergent expansion: kernel + admin-installed dirs.
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if system_root:
        protected_candidates.append(os.path.join(system_root, "System32"))
        protected_candidates.append(os.path.join(system_root, "SysWOW64"))
    for env_var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        v = os.environ.get(env_var)
        if v:
            protected_candidates.append(v)
    for raw in protected_candidates:
        try:
            p = os.path.normcase(os.path.realpath(raw))
        except OSError:
            p = os.path.normcase(os.path.abspath(raw))
        if not p.endswith(os.sep):
            p = p + os.sep
        if canonical.startswith(p):
            return True
    return False


def _resolve_on_disk_exe_path():
    """Resolve the on-disk displayoff.exe path under freeze. None under .py.

    See freeze-mode comment block above for why sys.executable is wrong here.
    Layered fallback chain (v1.7.19):

      0. `__compiled__.original_argv0` — Nuitka-authoritative origin path
         recorded by the compiled module. Available even after the bootstrap
         parent exits, which was v1.7.17/v1.7.18 Strategy 1's failure mode
         under certain launch chains (pid 18996 incident, 2026-05-22). No
         cross-process query required.
      1. `NUITKA_ONEFILE_PARENT` + `QueryFullProcessImageNameW(parent_pid)`
         — bootstrap IS the on-disk .exe, kernel-tracked image path = truth.
         Requires the bootstrap parent to still be alive.
      2. `os.path.abspath(sys.argv[0])` if it ends in .exe and isn't under
         %TEMP%. Per Nuitka docs sys.argv[0] is the original onefile binary.
      3. Return None (v1.7.18+ behavior — fail loud instead of returning the
         broken sys.executable). Downstream consumers (rename-dance,
         autostart .lnk, tray_promoter) all guard `_EXE_PATH and ...` and
         skip when None — user can manually install instead of the dance
         corrupting their TEMP dir.

    Strategies 0, 1, and 2 all gate on the same hardening triple: `.exe`
    extension, `os.path.isfile`, not under any TEMP-like dir (see
    `_path_under_temp`).

    Diagnostic logging buffers every candidate and the winning-strategy
    line into `_RESOLVER_LOG`. `main()` emits it with `path-resolver:`
    prefix on EVERY startup, regardless of whether data-dir migration ran
    — v1.7.18 left this gated on `_MIGRATION_LOG` which silently dropped
    diagnostics on already-migrated processes (pid 18996 had zero resolver
    output despite hitting Strategy 3). The `--diagnose-paths` CLI flag
    prints the same data to stdout for ad-hoc triage."""
    if not _is_frozen():
        return None

    candidates = {
        "sys.executable": os.path.abspath(sys.executable) if sys.executable else None,
        "sys.argv[0]": os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else None,
        "NUITKA_ONEFILE_PARENT": os.environ.get("NUITKA_ONEFILE_PARENT"),
        "__compiled__.original_argv0": getattr(
            globals().get("__compiled__"), "original_argv0", None
        ),
        "__compiled__.containing_dir": getattr(
            globals().get("__compiled__"), "containing_dir", None
        ),
    }
    _RESOLVER_LOG.append(
        "candidates: " + ", ".join(
            f"{k}={v!r}" for k, v in candidates.items()
        )
    )

    # Strategy 0: __compiled__.original_argv0 — Nuitka-authoritative origin
    # path. Tried before Strategy 1 because it doesn't depend on a live
    # parent process (Strategy 1's failure mode when the bootstrap exits or
    # an exotic launch chain reroutes the parent). All every-pid logs to
    # date show original_argv0 pointing at the correct on-disk .exe path,
    # even when sys.executable was the TEMP bootstrap python.exe. Filtered
    # through the same hardening triple as Strategies 1 and 2 so a
    # malformed value (relative path, deleted .exe, TEMP path) can't
    # silently mis-target the rename-dance.
    #
    # Trust-boundary note: `__compiled__.original_argv0` is the cmdline
    # argv[0] Nuitka recorded at process launch. An attacker who can
    # `CreateProcessW` displayoff.exe with `lpCommandLine` whose first
    # token is `C:\Users\victim\some-other.exe` could redirect this
    # resolver at that path. Same trust posture as Strategy 1's
    # parent-PID query (see below): the attacker already has local
    # code-exec as the current user when they spawn us with a controlled
    # cmdline, so it's not a privilege escalation. The downstream
    # rename-dance writes only after SHA256 verification against the
    # release manifest, so they can't inject arbitrary bytes — but the
    # bytes they DO inject (a real `displayoff.exe` build) will be
    # written to the spoofed path. Mitigations: the hardening triple
    # (`.exe`, isfile, not-temp, not-protected) excludes the obvious
    # paths an attacker would point at (TEMP scratch files, missing
    # paths) AND the WindowsApps Store-stub directory closed in v1.7.20
    # via `_path_under_protected`. The Store stubs are reparse points
    # under `%LOCALAPPDATA%\Microsoft\WindowsApps\` — writing a `.exe`
    # there is forbidden by the Store ACL, but a malicious argv[0] could
    # still steer the resolver at one (and rename-dance would then fail
    # noisily mid-step rather than silently). Rejecting up front is
    # cleaner.
    compiled = globals().get("__compiled__")
    if compiled is not None:
        orig_argv0 = getattr(compiled, "original_argv0", None)
        if isinstance(orig_argv0, str) and orig_argv0:
            cand = os.path.abspath(orig_argv0)
            if (cand.lower().endswith(".exe")
                    and os.path.isfile(cand)
                    and not _path_under_temp(cand)
                    and not _path_under_protected(cand)):
                _RESOLVER_LOG.append(
                    f"Strategy 0 (__compiled__.original_argv0) -> {cand!r}"
                )
                return cand
            else:
                _RESOLVER_LOG.append(
                    f"Strategy 0 rejected: original_argv0={cand!r} "
                    f"(temp/protected/missing/non-.exe) — falling back"
                )

    # Strategy 1: NUITKA_ONEFILE_PARENT → QueryFullProcessImageNameW.
    # Self-contained ctypes inside the helper because the file's main win32
    # bindings block is defined later (this helper runs at module import
    # before that block executes). argtypes/restype set explicitly per the
    # file's pointer-width discipline (HANDLE is pointer-sized; default
    # c_int restype would truncate on 64-bit).
    #
    # Trust-boundary note: `NUITKA_ONEFILE_PARENT` is parent-controlled
    # env. An attacker who can `CreateProcessW` displayoff.exe with a
    # spoofed env block could redirect this resolver to an arbitrary PID's
    # image. The attacker already has local code-exec as the current user
    # at that point (they spawned us with a controlled env), so this is
    # not a privilege escalation — the trust boundary was broken upstream.
    # The strategy 1 result is filtered through `.endswith(".exe")` as a
    # weak sanity check; downstream consumers (rename-dance, autostart
    # .lnk) write only inside the resolved path's directory, which the
    # spawning user already controls.
    #
    # PID-reuse race: Nuitka's bootstrap parent stays alive until the
    # child exits in the standard onefile model, so the parent_pid is
    # still bound to the actual bootstrap process at module-import time.
    # In that standard case the race window is effectively zero.
    # HOWEVER, v1.7.18's pid 18996 incident empirically falsified the
    # "standard case" assumption for at least one launch chain — Strategy
    # 1 evidently failed (no resolver candidates wrote, _EXE_PATH landed
    # on Strategy 3's TEMP\python.exe). The exact mechanism stays
    # uncertain pending the next `--diagnose-paths` capture, but possible
    # explanations include: detached bootstrap, OpenProcess denied by AV,
    # or `QueryFullProcessImageNameW` returning a stale value. v1.7.19's
    # Strategy 0 (preferred above) sidesteps all three by reading
    # `__compiled__.original_argv0` from the compiled module's own
    # metadata — no cross-process query needed. Strategy 1 stays as a
    # fallback in case `__compiled__` ever evolves in a way that drops
    # `original_argv0`.
    parent_pid_str = os.environ.get("NUITKA_ONEFILE_PARENT")
    if sys.platform == "win32" and parent_pid_str:
        try:
            parent_pid = int(parent_pid_str)
        except ValueError:
            parent_pid = 0
        if parent_pid:
            try:
                import ctypes.wintypes as _wt
                _k = ctypes.WinDLL("kernel32", use_last_error=True)
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                _OpenProcess = _k.OpenProcess
                _OpenProcess.argtypes = [_wt.DWORD, _wt.BOOL, _wt.DWORD]
                _OpenProcess.restype = _wt.HANDLE
                _QueryImg = _k.QueryFullProcessImageNameW
                _QueryImg.argtypes = [
                    _wt.HANDLE, _wt.DWORD, _wt.LPWSTR, ctypes.POINTER(_wt.DWORD)
                ]
                _QueryImg.restype = _wt.BOOL
                _Close = _k.CloseHandle
                _Close.argtypes = [_wt.HANDLE]
                _Close.restype = _wt.BOOL
                handle = _OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, False, parent_pid
                )
                if handle:
                    try:
                        # 32768 chars = the Win32 \\?\-prefixed long-path
                        # ceiling. QueryFullProcessImageNameW returns the
                        # path without \\?\ prefix (dwFlags=0), so the
                        # actual ceiling is MAX_PATH-style ~260, but we
                        # over-allocate for safety against future kernel
                        # changes — it's a one-time module-init alloc.
                        buf = ctypes.create_unicode_buffer(32768)
                        size = _wt.DWORD(len(buf))
                        if _QueryImg(handle, 0, buf, ctypes.byref(size)):
                            resolved = buf.value
                            # v1.7.17 T2-Opus hardening: reject Strategy 1
                            # results that point inside any TEMP-like dir.
                            # If a future Nuitka version spawns the bootstrap
                            # via a chain where the parent process is itself
                            # the extracted temp python.exe, Strategy 1 would
                            # otherwise re-introduce the v1.7.13 bug.
                            if (resolved
                                    and resolved.lower().endswith(".exe")
                                    and os.path.isfile(resolved)
                                    and not _path_under_temp(resolved)
                                    and not _path_under_protected(resolved)):
                                _RESOLVER_LOG.append(
                                    f"Strategy 1 (NUITKA_ONEFILE_PARENT "
                                    f"pid={parent_pid}) -> {resolved!r}"
                                )
                                return os.path.abspath(resolved)
                            elif resolved:
                                _RESOLVER_LOG.append(
                                    f"Strategy 1 rejected: parent "
                                    f"pid={parent_pid} -> {resolved!r} "
                                    f"(temp/protected/missing/non-.exe) — falling back"
                                )
                    finally:
                        _Close(handle)
                else:
                    _RESOLVER_LOG.append(
                        f"Strategy 1 OpenProcess failed for "
                        f"parent_pid={parent_pid} (err="
                        f"{ctypes.get_last_error()}) — falling back"
                    )
            except Exception as e:
                _RESOLVER_LOG.append(
                    f"Strategy 1 errored ({e!r}) — falling back"
                )

    # Strategy 2: sys.argv[0] if it's outside TEMP + WindowsApps, ends
    # with .exe, AND exists on disk. v1.7.17 T2-Sonnet hardening:
    # existence check prevents a synthetic argv[0] (relative path,
    # network path, deleted .exe) from silently propagating to the
    # rename-dance / .lnk / tray_promoter. v1.7.20: also filters via
    # `_path_under_protected` so a Store-stub-spoofed argv[0] doesn't
    # land here.
    argv0 = candidates.get("sys.argv[0]")
    if (argv0
            and argv0.lower().endswith(".exe")
            and os.path.isfile(argv0)
            and not _path_under_temp(argv0)
            and not _path_under_protected(argv0)):
        _RESOLVER_LOG.append(
            f"Strategy 2 (sys.argv[0]) -> {argv0!r}"
        )
        return argv0
    elif argv0:
        # v1.7.20 verifier T1-Sonnet N2: log Strategy 2 rejection for
        # observability symmetry with Strategy 0 and Strategy 1, which
        # both already log their rejection reasons. Without this line,
        # a Strategy 2 rejection silently falls through to Strategy 3
        # (which logs "all strategies failed") and the user can't tell
        # WHY Strategy 2 didn't win from the log alone.
        _RESOLVER_LOG.append(
            f"Strategy 2 rejected: sys.argv[0]={argv0!r} "
            f"(temp/protected/missing/non-.exe) — falling back"
        )

    # Strategy 3: all three primary strategies failed — Nuitka likely
    # changed its environment contract, or the .exe file got moved/deleted
    # between launch and resolver invocation. Return None rather than the
    # broken sys.executable value: downstream consumers
    # (`_execute_rename_dance`, `_autostart_target`,
    # `_recover_from_failed_update`) already guard on `_EXE_PATH and ...`,
    # so returning None makes those code paths skip cleanly instead of
    # mis-targeting. v1.7.17 T3-Opus hardening — the whole v1.7.13–v1.7.16
    # incident proves "WARNING + wrong path" is worse than "no path"; the
    # user can manually re-install instead of silently corrupting their
    # install. v1.7.19: same behavior, additional Strategy 0 (Nuitka
    # original_argv0) layered on top to catch v1.7.18's specific failure
    # mode where Strategy 1's parent-PID query returned a stale path.
    exe = candidates.get("sys.executable")
    _RESOLVER_LOG.append(
        f"WARNING — all strategies failed; sys.executable={exe!r} is "
        f"likely the wrong path. Returning None so rename-dance / "
        f"autostart .lnk / tray_promoter skip cleanly (downstream guards "
        f"check `_EXE_PATH and ...`). Please run `displayoff.exe "
        f"--diagnose-paths` and report the output to "
        f"github.com/itsnateai/displayoff/issues."
    )
    return None


# ── Paths ──────────────────────────────────────────────────────────────────
# See the freeze-mode block above for why _HERE and _INSTALL_DIR can
# differ under one-file freezers.
# _DATA_DIR = per-user state (%APPDATA%\displayoff\). Holds config, logs,
# crash-recovery sentinel — anything the running process writes. Split since
# v1.7.9 so a shared install (e.g. one clone under Program Files used by two
# Windows accounts) doesn't leak one user's idle-pattern history, log file,
# or in-progress sentinel into another user's session. Matches the per-user
# %APPDATA% discipline already used for the Startup-folder .lnk (see the
# autostart section below — both use the same `APPDATA` env var, so when the
# fallback to _HERE fires here it also fires for autostart).
_HERE = os.path.dirname(os.path.abspath(__file__))
_EXE_PATH = _resolve_on_disk_exe_path()
_INSTALL_DIR = (os.path.dirname(_EXE_PATH) if _EXE_PATH
                else os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = (os.path.join(os.environ.get("APPDATA", ""), "displayoff")
             if os.environ.get("APPDATA") else _HERE)
_ICON_PATH = os.path.join(_HERE, "displayoff.ico")
_CONFIG_PATH = os.path.join(_DATA_DIR, "displayoff_config.json")

# One-shot gate (v1.7.12): _migrate_legacy_data is idempotent by design (each
# file check is `src exists AND dst missing`), but the existence-check loop
# still runs every call — wasted ~5 stat syscalls per blank fire on a fully-
# migrated install. This flag short-circuits the whole loop after first
# successful pass. Resets only on process restart.
_MIGRATED = False


def _ensure_data_dir():
    """Idempotent. Creates _DATA_DIR if APPDATA-based; no-op when falling
    back to _HERE. Best-effort: a failure here surfaces as a real error
    when the first config/log write tries to open a file in the missing
    directory, so we don't need to bail out — just buffer the warning.

    Called at module load + again from main() before the migration shim,
    so a directory removed between launches re-appears."""
    if _DATA_DIR == _HERE:
        return
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
    except OSError as e:
        _MIGRATION_LOG.append(
            f"could not create data dir {_DATA_DIR!r}: {e}"
        )


_ensure_data_dir()


def _migrate_legacy_data():
    """One-shot migration of state files from the v1.7.8 _HERE layout to
    the v1.7.9+ %APPDATA%\\displayoff\\ layout.

    Idempotent: a file is moved only if it exists in _HERE AND does not
    already exist in _DATA_DIR, so a partially-completed migration resumes
    safely on the next launch.

    No-op when _DATA_DIR == _HERE (the APPDATA fallback) — both ends of
    the move are the same path.

    Files moved: displayoff_config.json, displayoff.log (+ rotated
    .1/.2/.3 from RotatingFileHandler), native_blank.log (+ rotated),
    .native_blank_in_progress.json. The icon stays in _HERE as a code
    asset.

    Called from main() AFTER _ensure_data_dir() but BEFORE basicConfig
    so the freshly-attached log handler reads the migrated file rather
    than re-creating an empty one at the new path while the old log
    sits orphaned in _HERE.

    v1.7.12: gated on the module-level _MIGRATED flag so repeated calls
    in the same process (e.g. main() then native_blank's lazy migrate
    when imported for blank_via_idle_path) short-circuit the existence-
    check loop entirely after the first pass."""
    global _MIGRATED
    if _MIGRATED:
        return
    if _DATA_DIR == _HERE:
        _MIGRATED = True
        return
    import shutil
    legacy_names = [
        "displayoff_config.json",
        "displayoff.log",
        "displayoff.log.1", "displayoff.log.2", "displayoff.log.3",
        "native_blank.log",
        "native_blank.log.1", "native_blank.log.2", "native_blank.log.3",
        ".native_blank_in_progress.json",
    ]
    # v1.7.13: track per-file recoverable failures so we DON'T set the
    # short-circuit flag when a retry could succeed. v1.7.12 unconditionally
    # set _MIGRATED = True after the loop — meaning a file locked by AV
    # mid-launch (the AV scanner momentarily holding the source file open)
    # would be permanently skipped for the lifetime of the process even
    # though a retry 30 seconds later would have succeeded. Surfaced by T2
    # Sonnet+Opus round 5 verifiers (convergent).
    #
    # "Benign races" (dst already exists post-failure — concurrent launcher
    # won) DON'T count as recoverable: the work is done, just by someone
    # else. Only true OSErrors with no destination materialization stop the
    # flag from being set.
    had_recoverable_failure = False
    for name in legacy_names:
        src = os.path.join(_HERE, name)
        dst = os.path.join(_DATA_DIR, name)
        if not os.path.exists(src):
            continue
        if os.path.exists(dst):
            # Already migrated on a previous launch (or destination was
            # created fresh under _DATA_DIR before any legacy file could be
            # moved into it). Leave the legacy copy alone — touching it
            # risks clobbering the canonical destination.
            _MIGRATION_LOG.append(
                f"legacy {src!r} ignored — destination {dst!r} already present"
            )
            continue
        try:
            # v1.7.20: cross-device-atomic two-step (hash src → copy2 →
            # SHA256 verify dst against pre-copy src hash → unlink src)
            # instead of `shutil.move`. `shutil.move` collapses to
            # `os.rename` when src/dst are on the same volume (atomic on
            # NTFS) but falls back to a non-atomic copy+unlink on a
            # cross-device move — `%APPDATA%` and the install dir CAN live
            # on different volumes (portable install on USB, NTFS-junctioned
            # roaming-profile mount). Under that fallback, a crash between
            # copy and unlink would leave the bytes in BOTH locations and
            # we'd re-do the move on next launch, copying the destination
            # over itself. Worse, a partial copy would land on disk and the
            # `os.path.exists(dst)` check on the next attempt would skip
            # the file forever. The hash-verify gate makes the second pass
            # detect partial copies and re-do them cleanly.
            #
            # v1.7.20 verifier-convergent H2 + T3-Sonnet I1 correction:
            # hash src BEFORE copy2, not after. The post-fix-verifier T3-
            # Sonnet noted the original "RotatingFileHandler active in
            # this process" rationale is factually wrong at first launch
            # — `_migrate_legacy_data` runs BEFORE `basicConfig`, so no
            # handler is attached yet when the loop fires. The hash-
            # before-copy ordering still matters for two real scenarios:
            #   (a) concurrent reader/writer in ANOTHER process (e.g., a
            #       native_blank.py invocation running parallel to the
            #       tray's startup writes to displayoff.log via its own
            #       handler) — pre-copy hash captures a stable snapshot.
            #   (b) future refactor that moves the call site to AFTER
            #       basicConfig — keeps the H2 defense intact under that
            #       refactor without the retry-loop foot-gun.
            # SHA256 verification on dst still catches truncated writes
            # / disk errors / NTFS junction surprises that show up as
            # bytes-on-disk-differ-from-bytes-just-written.
            src_hash = _sha256_file(src)
            shutil.copy2(src, dst)
            if src_hash != _sha256_file(dst):
                # Partial copy or mid-transfer disk error. Pull dst back
                # off disk so the next attempt re-tries cleanly. Don't
                # touch src — it's still the canonical copy.
                try:
                    os.remove(dst)
                except OSError as cleanup_err:
                    _MIGRATION_LOG.append(
                        f"PARTIAL COPY DETECTED for {src!r} -> {dst!r} "
                        f"AND cleanup of dst failed: {cleanup_err}. "
                        f"Manual recovery: delete {dst!r} and relaunch."
                    )
                    had_recoverable_failure = True
                    continue
                _MIGRATION_LOG.append(
                    f"partial copy detected {src!r} -> {dst!r}; "
                    f"removed corrupted dst, will retry on next launch"
                )
                had_recoverable_failure = True
                continue
            os.remove(src)
            _MIGRATION_LOG.append(f"migrated {src!r} -> {dst!r}")
        except OSError as e:
            # Common failure: shared install in Program Files where _HERE is
            # read-only for the current user. The destination at _DATA_DIR
            # is still fresh, so first launch just writes new state there.
            # Also covers the TOCTOU race where a concurrent launch (e.g. a
            # standalone `native_blank.py --read` running parallel to the
            # tray's own startup) won the move between our existence-check
            # and shutil.move call — re-check dst, and treat post-failure
            # existence as benign race-loss rather than a corrupted state.
            if os.path.exists(dst):
                _MIGRATION_LOG.append(
                    f"benign race: {src!r} -> {dst!r} (dst materialized "
                    f"during our move; concurrent launch won): {e}"
                )
            else:
                _MIGRATION_LOG.append(
                    f"could not migrate {src!r} -> {dst!r}: {e}"
                )
                had_recoverable_failure = True
    # Mark the migration complete only when no recoverable failures
    # occurred — that way a transient lock (AV scan, OneDrive in-progress
    # sync) doesn't permanently strand a file in _HERE. A future invocation
    # of _migrate_legacy_data() re-runs the loop, which re-discovers the
    # un-moved file and retries the shutil.move. Permanent failures (e.g.
    # _HERE is read-only) still pay the existence-check cost per call, but
    # those are rare and the per-call cost is small (~5 stat syscalls).
    if not had_recoverable_failure:
        _MIGRATED = True

# ── Single-instance guard ──────────────────────────────────────────────────
# Local\ scope = per-session. Each Windows user gets their own instance,
# Fast User Switching works correctly. Global\ would block other users.
_MUTEX_NAME = "Local\\DisplayOff_SingleInstance"
_mutex_handle = None

# v1.7.20: rename-dance child-ready handshake. The parent
# `_execute_rename_dance()` CreateEventWs this name BEFORE spawning the
# `--after-update` child, then `_run_rename_dance_flow._worker` waits on
# it (5s timeout) before calling `os._exit(0)`. The child's `main()`
# `--after-update` branch OpenEventWs the name as one of its first acts
# and SetEvents to signal "Python interpreter is alive, I can take over
# the tray slot now". Replaces v1.7.13's fixed `time.sleep(0.3)` which
# was a guess at how long the child needed — too short on slow systems
# (icon disappears, child loses mutex race), too long on fast ones
# (laggy "Install now" UX). Manual-reset event, initial-state=False.
# Falls back to the 0.3s sleep if CreateEventW returns NULL.
_UPDATE_CHILD_READY_EVENT_NAME = "Local\\DisplayOff_UpdateChildReady"
_update_child_ready_handle = None

# ── Win32 constants ────────────────────────────────────────────────────────
SC_MONITORPOWER = 0xF170
WM_SYSCOMMAND = 0x0112
MONITOR_OFF = 2
SMTO_ABORTIFHUNG = 0x0002
SM_REMOTESESSION = 0x1000  # GetSystemMetrics index for "is this an RDP session?"
ERROR_ALREADY_EXISTS = 183

# CallNtPowerInformation level for the aggregate EXECUTION_STATE bitmask.
# Documented value is 16 (POWER_INFORMATION_LEVEL::SystemExecutionState).
_SYSTEM_EXECUTION_STATE = 16
# EXECUTION_STATE bit flags (winnt.h). Only ES_DISPLAY_REQUIRED would suppress
# the native idle-blank — ES_SYSTEM_REQUIRED alone (e.g. PT Awake's "keep
# awake without screen-on") lets the display blank normally, so we don't warn
# on it. ES_CONTINUOUS is the marker bit; it's always set whenever any other
# bit is set and isn't itself a wake-lock.
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002
_ES_CONTINUOUS = 0x80000000

# ── Tunables ───────────────────────────────────────────────────────────────
_TRIGGER_SETTLE_SECS = 0.5      # Delay before powering off so the trigger keypress doesn't immediately wake.
_LOCK_SETTLE_SECS = 0.3         # Delay between LockWorkStation and the blank.
_SEND_TIMEOUT_MS = 5000         # SendMessageTimeoutW abort-if-hung timeout.
_KEY_TRACKER_OVERFLOW_CAP = 20  # Cap on tracked simultaneously-pressed keys (defense vs missed releases).
_DOUBLE_CLICK_WINDOW_SECS = 0.5 # Treat two icon clicks within this window as a double-click.
                                # Matches Windows' default GetDoubleClickTime() of ~500ms. The hidden
                                # default-action menu item is fired by pystray on every left-click on
                                # Windows (not just double-click), so we time the gap ourselves.
_IDLE_REFIRE_COOLDOWN_SECS = 60 # Minimum gap between idle-watcher fires. Prevents rapid mouse-jitter
                                # loops from re-firing the blank in quick succession (the wider `fired`
                                # flag handles the common case, this is the belt-and-suspenders pair).
_NATIVE_PROD_SLEEP_SECS = 5.0   # How long the native idle-blank path holds the 1s timeout in production.
                                # Bumped from 2.5s after empirical "menu click → no blank" reports: when the user
                                # navigates the right-click menu, the mouse moves continuously and the kernel's
                                # idle counter keeps resetting. By the time we'd restore at 2.5s the counter has
                                # never reached the 1s threshold. 5s lets idle accumulate even with ~3s of post-
                                # click motion. Lock-collision cost is logged explicitly so the user can see
                                # when rapid double-trigger drops a second click.
_NATIVE_PROD_SETTLE_SECS = 0.5  # Pause before writing the 1s timeout. Same idea as the legacy SC_MONITORPOWER
                                # path: gives the user's mouse time to come to rest before we arm the trap.
_TRAY_SETTLE_SECS = 1.0         # Pause before icon.notify() in tray-startup workers (first-run welcome
                                # notification and frozen-first-launch promotion ping). Lets pystray's
                                # NIM_ADD register the icon before we ask Explorer to render NIF_INFO; a
                                # toast before NIM_ADD lands gets silently dropped. Extracted from the
                                # two inline time.sleep(1.0) call sites so a future timing change stays
                                # coupled (v1.7.15 — T3-Sonnet LOW from v1.7.14 verifier round).

# ── Win32 bindings (Windows-only) ──────────────────────────────────────────
# Every call site must use the bound names from this block — never raw
# `ctypes.windll.*` lookups, which default to c_int restype and silently
# truncate pointer-sized values (HANDLE, HWND) on 64-bit Windows.
if sys.platform == "win32":
    import ctypes.wintypes

    # use_last_error=True on every binding: ctypes captures the Win32
    # LastError into a thread-local IMMEDIATELY after the call returns,
    # before the GIL release or any other ctypes call can clobber it.
    # Without this, `GetLastError()` from a separate binding can race with
    # Python's own kernel calls between CreateMutexW(...) and the error
    # check, returning a misleading value. Read the saved value via
    # `ctypes.get_last_error()` at the call site. Applied uniformly so any
    # future call site can rely on get_last_error regardless of which DLL.
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)

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

    # HWND is pointer-sized; default c_int restype truncates on 64-bit.
    GetParent = _user32.GetParent
    GetParent.argtypes = [ctypes.wintypes.HWND]
    GetParent.restype = ctypes.wintypes.HWND

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

    # NOTE: there is NO bound `GetLastError = _kernel32.GetLastError` here.
    # `_kernel32` was created with `use_last_error=True`, which means ctypes
    # captures the Win32 LastError into a thread-local immediately after each
    # call. Read it via `ctypes.get_last_error()`. Calling a bound
    # `GetLastError()` via ctypes would itself reset that thread-local with
    # the GetLastError syscall's own (zero) result — silently poisoning the
    # saved value. Don't add the binding back; use `ctypes.get_last_error()`.

    # DWORD restype matters: defaults to signed c_int which goes negative
    # after ~24.8 days of uptime, breaking idle-time arithmetic silently.
    GetTickCount = _kernel32.GetTickCount
    GetTickCount.argtypes = []
    GetTickCount.restype = ctypes.wintypes.DWORD

    # ── shell32 ──
    IsUserAnAdmin = _shell32.IsUserAnAdmin
    IsUserAnAdmin.argtypes = []
    IsUserAnAdmin.restype = ctypes.wintypes.BOOL

    # ── dwmapi (immersive dark titlebar) ──
    # v1.7.20: hoisted from inside `_apply_dark_titlebar()` so the WinDLL
    # load + argtypes/restype mutation happens ONCE at module load instead
    # of every dialog open. Workspace convention: "All bindings live in
    # the `if sys.platform == "win32":` block at the top of the file with
    # explicit argtypes/restype" — `_apply_dark_titlebar` was an exception
    # carried since v1.7.0 because dwmapi.dll is missing on pre-Win10 1607
    # builds; wrap the load in try/OSError so the function gracefully
    # no-ops on those (rather than crashing module import).
    try:
        _dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    except OSError:
        _dwmapi = None
    if _dwmapi is not None:
        try:
            DwmSetWindowAttribute = _dwmapi.DwmSetWindowAttribute
            DwmSetWindowAttribute.argtypes = [
                ctypes.wintypes.HWND, ctypes.wintypes.DWORD,
                ctypes.c_void_p, ctypes.wintypes.DWORD,
            ]
            DwmSetWindowAttribute.restype = ctypes.HRESULT
        except AttributeError:
            DwmSetWindowAttribute = None
    else:
        DwmSetWindowAttribute = None

    # ── Foreground-window elevation probing (UIPI miss-detection) ──
    # Used by the foreground-elevation watcher (v1.7.8+) to log when an
    # elevated window has focus and our global hotkey is being silently
    # suppressed by UIPI. All four entry-points are lazy — bound here but
    # only invoked from the watcher.
    GetWindowThreadProcessId = _user32.GetWindowThreadProcessId
    GetWindowThreadProcessId.argtypes = [ctypes.wintypes.HWND,
                                          ctypes.POINTER(ctypes.wintypes.DWORD)]
    GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD

    OpenProcess = _kernel32.OpenProcess
    OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL,
                            ctypes.wintypes.DWORD]
    OpenProcess.restype = ctypes.wintypes.HANDLE

    # shcore.dll only exists on Win8.1+. Try-import so the module still loads
    # on Win7. _set_dpi_awareness checks for None before calling, and falls
    # through to the user32 SetProcessDPIAware path (which always exists).
    try:
        _shcore = ctypes.WinDLL("shcore", use_last_error=True)
    except OSError:
        _shcore = None

    # uxtheme.dll provides ordinal-only exports for dark-mode native menus
    # (SetPreferredAppMode = ordinal 135, FlushMenuThemes = ordinal 136).
    # These are private/undocumented Win10 1903+ APIs but have been stable
    # through Win11 25H2. v1.7.13: converted from `ctypes.windll.uxtheme`
    # raw lookup to a bound WinDLL with use_last_error=True so any future
    # GetLastError consultation reads the intended thread-local rather than
    # an unrelated syscall's stale value. Ordinal lookup (`_uxtheme[135]`)
    # works the same on bound and raw WinDLL objects.
    try:
        _uxtheme = ctypes.WinDLL("uxtheme", use_last_error=True)
    except OSError:
        _uxtheme = None

    # DPI awareness function declarations. v1.7.13: moved here from inside
    # _set_dpi_awareness so the argtypes/restype mutation happens once at
    # module load rather than every call. v1.7.12 set these on the function
    # object inside the function body — idempotent (function called once
    # at startup) but technically a shared-state mutation; the bindings-
    # block pattern exists precisely to keep these immutable. Each tier is
    # guarded with try/AttributeError so missing symbols on older Windows
    # leave the corresponding bound name as None.
    try:
        _SetProcessDpiAwarenessContext = _user32.SetProcessDpiAwarenessContext
        _SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        _SetProcessDpiAwarenessContext.restype = ctypes.wintypes.BOOL
    except AttributeError:
        _SetProcessDpiAwarenessContext = None
    if _shcore is not None:
        try:
            _SetProcessDpiAwareness = _shcore.SetProcessDpiAwareness
            _SetProcessDpiAwareness.argtypes = [ctypes.c_int]
            _SetProcessDpiAwareness.restype = ctypes.c_long
        except AttributeError:
            _SetProcessDpiAwareness = None
    else:
        _SetProcessDpiAwareness = None
    try:
        _SetProcessDPIAware = _user32.SetProcessDPIAware
        _SetProcessDPIAware.argtypes = []
        _SetProcessDPIAware.restype = ctypes.wintypes.BOOL
    except AttributeError:
        _SetProcessDPIAware = None

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    OpenProcessToken = _advapi32.OpenProcessToken
    OpenProcessToken.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD,
                                  ctypes.POINTER(ctypes.wintypes.HANDLE)]
    OpenProcessToken.restype = ctypes.wintypes.BOOL

    GetTokenInformation = _advapi32.GetTokenInformation
    GetTokenInformation.argtypes = [ctypes.wintypes.HANDLE,
                                     ctypes.c_int,    # TOKEN_INFORMATION_CLASS
                                     ctypes.c_void_p,  # TokenInformation
                                     ctypes.wintypes.DWORD,
                                     ctypes.POINTER(ctypes.wintypes.DWORD)]
    GetTokenInformation.restype = ctypes.wintypes.BOOL

    # powrprof.dll — CallNtPowerInformation(SystemExecutionState) returns the
    # kernel's aggregate EXECUTION_STATE bitmask (the OR of every active
    # SetThreadExecutionState call SYSTEM-WIDE, across every session — verified
    # against Microsoft Learn `nf-powerbase-callntpowerinformation`). Used by
    # _check_display_blocked() to detect when PowerToys Awake / video players /
    # presentation apps are holding the display awake via
    # ES_DISPLAY_REQUIRED. Unprivileged — `powercfg /requests` would give us
    # the responsible process names too, but requires admin and displayoff
    # runs under the user's standard token. Try-import so a stripped
    # powrprof.dll (rare; some hardened Win images) leaves the helper as a
    # silent no-op rather than crashing the tray.
    #
    # System-wide scope means: under Fast User Switching, a wake-lock held by
    # User B's PT Awake (while B is switched-away) will appear in User A's
    # blocked-check too, producing a false-positive toast for A. The blank
    # still attempts (the toast is advisory), so this is a UX inaccuracy not
    # a correctness regression — flagged here so future-readers don't expect
    # session-scoping. (Note: displayoff itself runs per-session — the
    # single-instance mutex is `Local\` scope, so each FUS user has their
    # own tray — but the kernel-state read crosses the session boundary
    # because the kernel's SetThreadExecutionState bookkeeping is global.)
    try:
        _powrprof = ctypes.WinDLL("powrprof", use_last_error=True)
        CallNtPowerInformation = _powrprof.CallNtPowerInformation
        CallNtPowerInformation.argtypes = [
            ctypes.c_int,                    # POWER_INFORMATION_LEVEL
            ctypes.c_void_p,                 # InputBuffer
            ctypes.wintypes.ULONG,           # InputBufferLength
            ctypes.c_void_p,                 # OutputBuffer
            ctypes.wintypes.ULONG,           # OutputBufferLength
        ]
        CallNtPowerInformation.restype = ctypes.c_long   # NTSTATUS
    except (OSError, AttributeError):
        CallNtPowerInformation = None
else:
    SendMessageTimeoutW = None
    GetForegroundWindow = None
    GetDesktopWindow = None
    GetSystemMetrics = None
    LockWorkStation = None
    GetLastInputInfo = None
    GetParent = None
    CreateMutexW = None
    CreateEventW = None
    OpenEventW = None
    SetEvent = None
    WaitForSingleObject = None
    CloseHandle = None
    GetTickCount = None
    IsUserAnAdmin = None
    GetWindowThreadProcessId = None
    OpenProcess = None
    OpenProcessToken = None
    GetTokenInformation = None
    CallNtPowerInformation = None
    _uxtheme = None
    _SetProcessDpiAwarenessContext = None
    _SetProcessDpiAwareness = None
    _SetProcessDPIAware = None
    _dwmapi = None
    DwmSetWindowAttribute = None

# Win32 wait-result sentinels
_WAIT_OBJECT_0 = 0x00000000
_WAIT_ABANDONED = 0x00000080
_WAIT_TIMEOUT = 0x00000102
_WAIT_FAILED = 0xFFFFFFFF
_INFINITE = 0xFFFFFFFF
_EVENT_MODIFY_STATE = 0x0002

# Process access + token-info constants for foreground-elevation probing.
# PROCESS_QUERY_LIMITED_INFORMATION (0x1000) is the post-Vista minimum-scope
# access that lets a non-admin OpenProcess a higher-IL process — anything
# stronger fails with ACCESS_DENIED across the UIPI boundary we're trying to
# detect. TOKEN_QUERY (0x0008) is the only token access we need.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TOKEN_QUERY = 0x0008
_TOKEN_ELEVATION = 20  # TOKEN_INFORMATION_CLASS::TokenElevation


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
    last_error = ctypes.get_last_error()
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
    # v1.7.21: when True, show a tray toast on hotkey/icon/idle blank attempts
    # if the kernel reports ES_DISPLAY_REQUIRED (PowerToys Awake "Keep screen
    # on", fullscreen video players, presentation apps). Default ON because
    # the alternative is the silent failure that motivated the feature.
    "warn_on_blocked_blank": True,
    # v1.7.13: one-shot flag set after the first successful tray-promotion
    # notification fires under the frozen .exe build. Win11 22H2+ defaults
    # new tray icons (new ExecutablePath in NotifyIconSettings) to hidden-
    # in-overflow until either (a) the user manually flips them via
    # Settings ▸ Personalization ▸ Taskbar ▸ Other system tray icons, or
    # (b) Explorer catalogs the icon and our tray_promoter writes
    # IsPromoted=1. Explorer's catalog is lazy — it doesn't write the
    # registry entry until the user opens the overflow flyout. Firing
    # `icon.notify(...)` immediately after launch is the one well-known
    # trick that FORCES Explorer to catalog the icon synchronously
    # (because the balloon needs the icon's screen position). Once
    # catalogued, the promoter's background poll finds the new entry and
    # flips IsPromoted to 1. We only need to fire this notification ONCE
    # per .exe install — subsequent launches inherit the IsPromoted=1
    # state. The flag persists in config so a user who installs the .exe,
    # closes it, and relaunches doesn't get repeated post-install
    # notifications.
    "_frozen_promoted_pinged": False,
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


# ── Autostart (Startup-folder .lnk) ─
# v1.7.0+ uses the user's Startup folder (a .lnk in
# %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup) instead of the
# HKCU Run registry key. Same effect at logon, but the .lnk is visible /
# manageable in File Explorer. The legacy `HKCU\...\Run\DisplayOff` registry
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
    `pythonw.exe` over `python.exe` so there's no console flash.

    Source-mode only — never call under freeze. Under Nuitka onefile,
    `sys.executable` is the temp-extracted python.exe under `%TEMP%` and
    would produce a .lnk that breaks the moment the bootstrap exits (the
    temp dir is per-launch, the .lnk would point at a path that vanishes).
    The assert below is defense-in-depth: every other v1.7.17 call site
    that touched `sys.executable` got migrated to the resolver; this one
    is gated by the freeze check at the only caller (`_autostart_target`),
    but a future refactor could plumb it under a frozen branch by mistake.
    The same docstring-trust failure that bit v1.7.13 → v1.7.16 is the
    exact thing we're defending against."""
    # Hard guard (raise, not assert): `assert` compiles to no-op under
    # `python -O`, which would silently revive the v1.7.13 bug class. Raise
    # is unconditional and matches workspace rule 12 ("fail loud"). v1.7.17
    # T3-Opus verifier-round catch.
    if _is_frozen():
        raise RuntimeError(
            "_autostart_target_pythonw must never be called under freeze — "
            "sys.executable is the temp-extracted python.exe, not the on-disk "
            ".exe. Use the resolved on-disk path (_EXE_PATH from "
            "_resolve_on_disk_exe_path) for the .lnk target under freeze."
        )
    py = sys.executable
    if py.lower().endswith("python.exe"):
        pyw = py[:-len("python.exe")] + "pythonw.exe"
        if os.path.isfile(pyw):
            return pyw
    return py


def _autostart_target():
    """Resolve (target, arguments, working_dir, icon_location) for the
    Startup-folder .lnk.

    Returns a 4-tuple suitable for direct interpolation into the PowerShell
    `WScript.Shell` script that creates the shortcut. Two modes:

      - Frozen (v1.7.13+ .exe): `displayoff.exe` is self-contained, takes no
        startup args (run_tray is the default behavior), and carries its own
        icon as embedded Windows resource — IconLocation references the .exe
        with resource index 0. WorkingDirectory is the .exe's install dir.

      - Source (.py): `pythonw.exe` launches the script. Arguments is the
        quoted absolute path to displayoff.py. WorkingDirectory is the script
        dir. IconLocation is the on-disk displayoff.ico bundled with the
        repo.

    Keeping the dispatch in one helper means `_create_startup_lnk` and
    `autostart_enabled` always reconcile against the same expected target —
    a v1.7.12 source-mode .lnk auto-refreshes to point at the new .exe the
    next time the user toggles autostart from the frozen build.
    """
    if _is_frozen():
        exe = _EXE_PATH
        return exe, "", _INSTALL_DIR, f"{exe},0"
    py = _autostart_target_pythonw()
    script = os.path.abspath(__file__)
    working_dir = os.path.dirname(script)
    icon_path = os.path.join(working_dir, "displayoff.ico")
    return py, f'"{script}"', working_dir, f"{icon_path},0"


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
    target, arguments, working_dir, icon_location = _autostart_target()

    # Escape EVERY interpolated value for the PS single-quoted-literal context.
    # PS single-quotes preserve backslashes but treat `'` as terminator — paths
    # with apostrophes (legal NTFS: `C:\Users\O'Brien\...`) would otherwise
    # break the script or inject arbitrary PS. `_ps_sq_escape` doubles every
    # `'` per PS literal rules. The Arguments field's content (under source
    # mode it's `"<script_path>"`) gets a DQ escape FIRST so embedded `"` in
    # the path survive the inner double-quote parser, THEN the SQ escape so
    # embedded `'` survive the outer single-quote parser. Under frozen mode
    # `arguments` is empty, so both escapes pass it through unchanged.
    lnk_q = _ps_sq_escape(_STARTUP_LNK_PATH)
    target_q = _ps_sq_escape(target)
    arguments_q = _ps_sq_escape(_ps_dq_escape(arguments))
    wd_q = _ps_sq_escape(working_dir)
    icon_q = _ps_sq_escape(icon_location)
    ps_script = (
        f"$sh = New-Object -ComObject WScript.Shell; "
        f"$lnk = $sh.CreateShortcut('{lnk_q}'); "
        f"$lnk.TargetPath = '{target_q}'; "
        f"$lnk.Arguments = '{arguments_q}'; "
        f"$lnk.WorkingDirectory = '{wd_q}'; "
        f"$lnk.IconLocation = '{icon_q}'; "
        f"$lnk.WindowStyle = 7; "
        f"$lnk.Description = 'Display Off - tray app autostart'; "
        f"$lnk.Save()"
    )

    log.info("Creating startup shortcut: target=%s args=%s lnk=%s", target, arguments, _STARTUP_LNK_PATH)
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
    # .lnk gets re-created every Save. Caught by code review R2
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
         expected target — a stale .lnk pointing at a moved/uninstalled
         Python interpreter (source-mode .lnk after a Python upgrade) OR a
         pythonw.exe target from a previous source-mode install (v1.7.12 or
         earlier) when we're now running as the frozen .exe (v1.7.13+) is
         treated as "not enabled" so the next Save re-creates it correctly.
      2. Legacy HKCU\\...\\Run\\DisplayOff entry (v1.6.0 and earlier) —
         either being present is "enabled" for migration purposes.

    Returns False if APPDATA isn't set (so the Settings dialog can still
    open even in an unusual environment — the toggle attempt will produce
    a clear error rather than silently checking against an empty path)."""
    if not _STARTUP_LNK_PATH:
        return False
    if os.path.exists(_STARTUP_LNK_PATH):
        # Validate the target matches our current expected launcher. If it
        # doesn't, the .lnk is stale — covers two cases:
        #   - source-mode Python upgraded (3.13 → 3.14) and the old path is
        #     gone, so the .lnk wouldn't actually launch us at logon
        #   - we're the frozen v1.7.13+ .exe but the .lnk still points at
        #     a v1.7.12-era pythonw.exe + script combo; toggle Save will
        #     re-point it
        target = _read_lnk_target_path()
        if target is None:
            # Couldn't read it — assume valid and let the user reconcile
            # via a manual Save toggle if it turns out to be broken.
            return True
        expected, _, _, _ = _autostart_target()
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
        log.info("Stale startup shortcut: target=%r but current launcher is %r — "
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
    # to log presentation only. (Caught by R2 code review: prior version
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
    """Convert a config key name like 'f12', 'a', or 'vk183' to a pynput Key/KeyCode."""
    from pynput import keyboard
    try:
        return getattr(keyboard.Key, key_name.lower())
    except AttributeError:
        pass
    if len(key_name) == 1:
        return keyboard.KeyCode.from_char(key_name.lower())
    # `vkNNN` round-trip: _pynput_key_to_name emits this for KeyCodes with no
    # printable char (media keys, app-defined keys). Without this branch the
    # config would silently disable the hotkey on next launch.
    if key_name.lower().startswith("vk"):
        try:
            return keyboard.KeyCode.from_vk(int(key_name[2:]))
        except (ValueError, AttributeError):
            pass
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


# ── Display-blocked detection (v1.7.21) ───────────────────────────────────
# Module-level tray-icon ref so turn_off_monitors() can fire icon.notify()
# from the hotkey/idle/menu paths without having to thread `icon` through
# every call site. Set once in run_tray() right after pystray.Icon(...) is
# constructed; read with a None check (helper is a no-op until set, which
# means pre-tray paths like --off CLI flags don't try to notify). The
# --start-off CLI path also predates _tray_icon_ref being assigned (it
# fires turn_off_monitors() BEFORE run_tray()) — that's an intentional
# trade-off so autostart-blank stays fast.
_tray_icon_ref = None

# Toast rate-limit: the idle watcher retries the blank every
# _IDLE_REFIRE_COOLDOWN_SECS while the user stays idle and PT Awake stays
# on; without this gate, the user would get a tray toast every minute.
# State-transition logic: we ALWAYS toast on a fresh blocked→not-blocked-
# →blocked transition (so disabling PT Awake and re-enabling it re-warns
# immediately), and we suppress back-to-back blocked detections within
# _WARN_COOLDOWN_SECS (so idle-watcher refires + rapid-fire hotkey presses
# don't spam). The blank attempt itself still fires every call — the
# rate-limit only gates the notification, not the action.
#
# Accepted UX trade-off: a user with idle-blank set AND PT Awake
# intentionally on (e.g. watching a long video) gets one toast every 5
# minutes for the duration of the video. The escape hatch is the Settings
# checkbox "Warn when something is keeping the display awake" — they can
# disable the warn without disabling the idle-blank. Better than v1.7.20's
# silent failure for the "forgot PT Awake was on" case, which is the
# bug-fix this whole rate-limit exists to surface.
_WARN_COOLDOWN_SECS = 300.0
_last_warn_ts = 0.0
_last_warn_was_blocked = False
_warn_lock = threading.Lock()  # protects _last_warn_ts / _last_warn_was_blocked RMW


def _check_display_blocked():
    """Return (blocked, reason) tuple.

    blocked: True iff the kernel's aggregate EXECUTION_STATE has
    ES_DISPLAY_REQUIRED set — meaning some process called
    SetThreadExecutionState(ES_DISPLAY_REQUIRED) and the native idle-blank
    will be suppressed by the kernel even though we'll write the 1s timeout
    cleanly. PowerToys Awake's "Keep screen on" toggle, video players in
    fullscreen, and presentation apps all do this.

    reason: short user-facing string naming the most common culprit on hit.
    Empty string when not blocked.

    Fails quiet: any binding/syscall failure returns (False, "") so a broken
    powrprof.dll never prevents a blank from firing. This is a UX hint, not
    a correctness gate.

    Trade-off note: `powercfg /requests` would give us the responsible
    process names, but it requires admin and displayoff runs unelevated.
    `CallNtPowerInformation(SystemExecutionState)` is the unprivileged
    equivalent for the SetThreadExecutionState side of the API; processes
    using only PoCreatePowerRequest won't show up here. Covers the
    user-reported case (PT Awake) which is what matters for this version.
    """
    if CallNtPowerInformation is None:
        return False, ""
    state = ctypes.wintypes.ULONG(0)
    try:
        status = CallNtPowerInformation(
            _SYSTEM_EXECUTION_STATE, None, 0,
            ctypes.byref(state), ctypes.sizeof(state),
        )
    except OSError as e:
        log.debug("CallNtPowerInformation raised: %s", e)
        return False, ""
    # STATUS_SUCCESS == 0. Anything else (STATUS_ACCESS_DENIED on locked-down
    # systems, STATUS_INVALID_PARAMETER on stripped power profiles, etc.) =
    # fail-quiet.
    if status != 0:
        log.debug("CallNtPowerInformation returned NTSTATUS 0x%08x", status & 0xFFFFFFFF)
        return False, ""
    if state.value & _ES_DISPLAY_REQUIRED:
        return True, "an app is keeping the display awake (e.g. PowerToys Awake)"
    return False, ""


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

        # v1.7.21: surface display-blocking wake-locks BEFORE the blank attempt
        # so the user gets a tray toast naming the likely culprit instead of
        # "I pressed Ctrl+Alt+F12 and nothing happened". Still attempt the
        # blank afterward — the check is advisory, not a suppression gate (the
        # legacy SC_MONITORPOWER path actually bypasses ES_DISPLAY_REQUIRED on
        # some hardware, so refusing to fire would be over-eager). Only
        # relevant to the native path; SC_MONITORPOWER ignores the wake-lock,
        # so we skip the toast there to avoid scaring the user about a state
        # that won't affect them. Failures (missing icon ref, notify
        # exception) are swallowed — this is a hint, not a contract.
        if cfg.get("warn_on_blocked_blank", True):
            using_native = (force_path == "native") or (
                force_path is None and not cfg.get("use_legacy_sc_monitorpower", False)
            )
            if using_native and _tray_icon_ref is not None:
                blocked, reason = _check_display_blocked()
                # Always log the kernel-state read so the diagnostic trail
                # stays complete regardless of toast-rate-limit decisions.
                if blocked:
                    log.info("Blank may be suppressed: %s", reason)
                # State-transition rate limit: toast immediately on
                # not-blocked → blocked, suppress back-to-back blocked
                # within _WARN_COOLDOWN_SECS. Reset state on any
                # not-blocked read so the next blocked detection toasts.
                global _last_warn_ts, _last_warn_was_blocked
                now = time.monotonic()
                show_toast = False
                # Snapshot the elapsed-since-last value INSIDE the lock so
                # the debug log line below reads a consistent number even
                # if another thread updates _last_warn_ts after we release.
                suppress_elapsed = 0.0
                with _warn_lock:
                    if not blocked:
                        _last_warn_was_blocked = False
                    elif not _last_warn_was_blocked:
                        # Fresh transition into blocked state — always toast.
                        _last_warn_ts = now
                        _last_warn_was_blocked = True
                        show_toast = True
                    elif now - _last_warn_ts >= _WARN_COOLDOWN_SECS:
                        # Still blocked but cooldown elapsed — toast again
                        # as a periodic reminder.
                        _last_warn_ts = now
                        show_toast = True
                    else:
                        suppress_elapsed = now - _last_warn_ts
                if show_toast:
                    try:
                        _tray_icon_ref.notify(
                            f"Blank may be blocked — {reason}. "
                            f"Disable it and try again.",
                            "Display Off",
                        )
                    except Exception as e:
                        log.debug("icon.notify for blocked-blank warning failed: %s", e)
                elif blocked:
                    log.debug("Blocked-blank toast suppressed (rate-limit, %.1fs since last)",
                              suppress_elapsed)

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

    Re-fires every `_IDLE_REFIRE_COOLDOWN_SECS` while the user remains idle
    past threshold — covers the silent-failure case where our blank attempt
    runs but the kernel's idle counter is reset by another process during the
    5s policy window (PowerToys Awake, presentation tools, certain peripheral
    drivers — all reset GetLastInputInfo on a timer and would otherwise leave
    the monitor lit forever). Threshold of 0 (the default) disables the
    feature; the watcher still runs cheaply but skips firing.
    """
    def _watch():
        fired = False
        last_fire = 0.0
        last_heartbeat = 0.0
        heartbeat_secs = 300  # log "still alive" every 5 min so a silently
                              # dead watcher thread is easy to spot in the log
        while True:
            time.sleep(poll_secs)
            try:
                cfg = cfg_provider()
                threshold_min = int(cfg.get("idle_blank_minutes", 0) or 0)
                threshold = threshold_min * 60
                if threshold <= 0:
                    # Feature disabled — reset both gates so a re-enable
                    # within _IDLE_REFIRE_COOLDOWN_SECS of a prior fire
                    # isn't blocked by stale cooldown state.
                    fired = False
                    last_fire = 0.0
                    continue
                idle = _idle_seconds()
                now = time.monotonic()

                # Heartbeat: log idle/threshold state every ~5 min so the log
                # has a paper trail when "auto-blank didn't fire" reports
                # come in. INFO level (not DEBUG) because pythonw.exe sessions
                # may not ship a DEBUG-level handler.
                if now - last_heartbeat >= heartbeat_secs:
                    log.info("Idle watcher heartbeat: idle=%.0fs threshold=%ds fired=%s",
                             idle, threshold, fired)
                    last_heartbeat = now

                # Reset `fired` whenever the user is active. This MUST run
                # before the cooldown gate — otherwise during the cooldown
                # window `continue` short-circuits past this reset and
                # `fired` stays True until the next idle-drop.
                if idle < threshold:
                    fired = False
                    continue
                # If we already fired this idle window AND the cooldown
                # hasn't expired, skip — covers the rapid-jitter case where
                # the user's idle dips below threshold for one poll then
                # climbs back.
                if fired and (now - last_fire) < _IDLE_REFIRE_COOLDOWN_SECS:
                    continue
                # User is past threshold and either (a) we haven't fired yet
                # this idle window, or (b) we did fire but cooldown expired
                # AND user is STILL idle — meaning the previous blank
                # evidently didn't take effect. Retry.
                if fired:
                    log.info("Idle %.0fs ≥ %ds threshold but previous blank didn't "
                             "stick — retrying (kernel may have been overridden by a "
                             "stay-awake tool or driver event).", idle, threshold)
                fired = True
                last_fire = now
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


def _foreground_is_elevated():
    """Probe the current foreground window's process for elevation.

    Returns True if the foreground window is owned by a higher-integrity
    process than us (i.e., our global hotkey can't reach it because of
    UIPI), False otherwise — including the "we can't tell" case (the
    foreground process is also elevated to a peer level, the desktop, or
    OpenProcess was denied). Treating ambiguity as False avoids logging
    false positives every poll cycle.

    Closed exclusively over win32 bindings; safe to call from any thread.
    All handles closed via try/finally — leaking a process handle every
    30s would dwarf the rest of the tray's footprint inside a day.
    """
    if (sys.platform != "win32" or
            GetForegroundWindow is None or
            GetWindowThreadProcessId is None or
            OpenProcess is None or
            OpenProcessToken is None or
            GetTokenInformation is None):
        return False
    hwnd = GetForegroundWindow()
    if not hwnd:
        return False
    pid = ctypes.wintypes.DWORD(0)
    GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return False
    h_proc = OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h_proc:
        # ACCESS_DENIED across UIPI is itself a STRONG hint that the
        # foreground IS more elevated than us. ERROR_ACCESS_DENIED == 5.
        # GetLastError() must be read via ctypes.get_last_error() because
        # kernel32 was loaded with use_last_error=True.
        return ctypes.get_last_error() == 5
    try:
        h_tok = ctypes.wintypes.HANDLE(0)
        if not OpenProcessToken(h_proc, _TOKEN_QUERY, ctypes.byref(h_tok)):
            return False
        try:
            # TOKEN_ELEVATION is a single-DWORD struct (TokenIsElevated).
            elevation = ctypes.wintypes.DWORD(0)
            ret_len = ctypes.wintypes.DWORD(0)
            ok = GetTokenInformation(h_tok, _TOKEN_ELEVATION,
                                     ctypes.byref(elevation),
                                     ctypes.sizeof(elevation),
                                     ctypes.byref(ret_len))
            if not ok:
                return False
            return bool(elevation.value)
        finally:
            CloseHandle(h_tok)
    finally:
        CloseHandle(h_proc)


# Rate-limited per-miss log state. The watcher logs at most once per
# _UIPI_LOG_INTERVAL_SECS when elevated foreground is detected so that
# (a) users see a fresh nudge when they're actually in the problematic
# state — not just one INFO buried in startup — and (b) the log doesn't
# fill with one line every poll cycle.
_UIPI_POLL_INTERVAL_SECS = 30.0
_UIPI_LOG_INTERVAL_SECS = 60.0
_uipi_last_logged = [0.0]  # mutable closure capture


def _start_foreground_elevation_watcher():
    """Daemon thread that polls foreground-window elevation every 30s.

    When an elevated foreground is observed, logs a per-miss hint at INFO
    rate-limited to once per 60s. Replaces the one-shot startup INFO log
    that users routinely missed — now the message lands when they're
    actually in the affected state and re-fires after each minute of
    continued exposure so it isn't lost in scrolled-back logs.

    No-op when WE are elevated (UIPI doesn't bite us) or when running
    elevated on a single-user system without an unelevated peer to fall
    back to.
    """
    if sys.platform != "win32" or _is_elevated():
        return None

    def _watch():
        while True:
            try:
                if _foreground_is_elevated():
                    now = time.monotonic()
                    if now - _uipi_last_logged[0] >= _UIPI_LOG_INTERVAL_SECS:
                        log.info(
                            "Foreground window is elevated — global hotkey may be "
                            "silently suppressed by UIPI until you switch focus to a "
                            "non-elevated window. Double-clicking the tray icon still works."
                        )
                        _uipi_last_logged[0] = now
            except Exception as e:
                # The probe is best-effort; if it throws we don't want the
                # watcher to die — that would silently disable the warning
                # signal users rely on.
                log.debug("UIPI watcher probe raised: %s", e)
            time.sleep(_UIPI_POLL_INTERVAL_SECS)

    t = threading.Thread(target=_watch, daemon=True, name="displayoff-uipi-watch")
    t.start()
    return t


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
                    ctypes.get_last_error())
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
                            ctypes.get_last_error())
            else:
                log.warning("Unexpected WaitForSingleObject result %#x — quit watcher exiting.",
                            result)
        except Exception as e:
            log.warning("Quit-event watcher error: %s", e)
    t = threading.Thread(target=_wait, daemon=True, name="displayoff-quitwatch")
    t.start()


# ── Update check (manual, via tray menu) ──────────────────────────────────

_GITHUB_REPO = "itsnateai/displayoff"
_GITHUB_REPO_URL = f"https://github.com/{_GITHUB_REPO}"
_GITHUB_RELEASES_URL = f"{_GITHUB_REPO_URL}/releases"
_RELEASES_API = f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest"

# ── Update host allowlist (HARDCODED — never config-driven) ────────────────
# Validated as a prefix match (case-insensitive) against any URL the
# rename-dance updater is about to fetch. Never read from config or env: a
# config-driven allowlist becomes admin-elevation bait — poison the config,
# poison the updater. Both entries cover the GitHub releases delivery path:
#   - github.com/itsnateai/<repo>/releases/download/<tag>/<name>
#     is the canonical URL the API returns in assets[].browser_download_url
#     and what users see in their browser.
#   - objects.githubusercontent.com/...?token=...
#     is where the github.com URL above redirects (S3-backed asset host with
#     a short-lived signed token). urllib follows that redirect by default,
#     so even though we never see this host in the JSON response we still
#     have to allowlist it for the download fetch to succeed.
_ALLOWED_UPDATE_HOSTS = (
    "https://github.com/itsnateai/",
    # GitHub migrated the release-asset CDN from
    # objects.githubusercontent.com → release-assets.githubusercontent.com
    # over 2025. The current canonical redirect target (verified
    # 2026-05-21 against a freshly-uploaded v1.7.15 asset) is
    # release-assets.githubusercontent.com — the JWT inside the signed
    # URL even names it explicitly via the `aud` claim. The legacy host
    # stays in the list for any older release whose asset URLs were
    # baked before the migration (defensive — both may coexist for some
    # time and the SHA256 verification is the actual integrity boundary).
    "https://release-assets.githubusercontent.com/",
    "https://objects.githubusercontent.com/",
)

# Release-asset filenames the folder-swap updater expects. Static across
# versions so v1.7.22 can recognize a v1.7.23 release without per-version
# updates. v1.7.22 switched from a `.exe` asset to a `.zip` containing the
# Nuitka --standalone bundle — the updater matches by `.zip` suffix in the
# release's assets dict, not by exact filename, since the version-stamped
# zip name (`displayoff-v1.7.23.zip`) changes every release. SHA256SUMS.txt
# stays a stable name across versions; it's parsed by zip-filename key.
# v1.7.22 verifier-round R2 convergent fix (T2 Opus G4 + T3 Opus): the
# canonical install dir name had been hardcoded as the literal "displayoff"
# in 5 places (_extract_zip_bundle, _recover_from_failed_update's
# half-swap detector + artifact list, --after-update-folder-swap's path
# discovery). Hoisting to a single constant means a future rebrand or
# install-dir-naming-convention change is a one-line edit, not a grep-
# and-pray exercise.
_CANONICAL_INSTALL_NAME = "displayoff"
_UPDATE_ZIP_SUFFIX = ".zip"
_UPDATE_MANIFEST_NAME = "SHA256SUMS.txt"

# Filenames for the folder-swap dance intermediates. These live as siblings
# of _INSTALL_DIR (i.e., under _INSTALL_DIR's parent), NOT inside _INSTALL_DIR.
# Sibling placement is load-bearing: the dance needs to atomically rename
# _INSTALL_DIR to <something>.old AND rename <something>.new to _INSTALL_DIR.
# Both renames operate on parent-directory entries, so the staging dirs HAVE
# to live in that parent. If they lived inside _INSTALL_DIR, renaming
# _INSTALL_DIR would carry the staging dirs along for the ride.
#
#   <_INSTALL_DIR>.new.zip  — freshly-downloaded zip (deleted after extract)
#   <_INSTALL_DIR>.new      — extracted bundle staging dir
#                            (becomes _INSTALL_DIR after the swap)
#   <_INSTALL_DIR>.old      — pre-swap backup of the previous _INSTALL_DIR
#                            (best-effort deleted by --after-update-folder-swap)
_UPDATE_NEW_ZIP_SUFFIX = ".new.zip"
_UPDATE_NEW_DIR_SUFFIX = ".new"
_UPDATE_NEW_STAGING_SUFFIX = ".new.staging"
_UPDATE_OLD_DIR_SUFFIX = ".old"

# Relaunch-mode persistence: the dance writes this file before spawning the
# --after-update-folder-swap child. The child reads + deletes it as its
# first act. Lives in _DATA_DIR (%APPDATA%\displayoff\) so it survives the
# folder swap, which renames _INSTALL_DIR mid-flight.
_UPDATE_RELAUNCH_FILENAME = "_update_relaunch.json"
_UPDATE_RELAUNCH_PATH = os.path.join(_DATA_DIR, _UPDATE_RELAUNCH_FILENAME)

# Minimum size for a valid downloaded zip. Anything smaller is a truncated
# transfer or, more dangerously, a 200-OK HTML error page (some CDNs serve
# a "404" body with HTTP 200 — without a size floor, that HTML would land
# on disk renamed as the .zip). v1.7.22 standalone bundle is ~52 MB raw,
# zip with deflate compression is typically ~25-35 MB. 15 MB floor catches
# truncated transfers, mis-shipped stub builds, and HTML-disguised-as-zip,
# while allowing for compression-ratio variance across Nuitka rebuilds and
# Python-version drift.
_UPDATE_MIN_ZIP_SIZE = 15_000_000

# Cache the last successful /releases/latest response to avoid burning
# GitHub's 60-req/hr unauthenticated rate limit on repeated manual clicks.
# That quota is shared per-IP with `gh`, GitHub Desktop, VS Code extension
# update checks, and any other tool hitting the API from the same network.
# A user behind a corporate NAT can find the quota near-empty before they
# ever click Check-for-Updates; caching turns "click N times in a session"
# from "N API calls" into "1 API call + N-1 cache hits".
_UPDATE_CHECK_CACHE_TTL = 6 * 3600  # 6 hours
_update_check_cache = {"timestamp": 0.0, "result": None}
_update_check_cache_lock = threading.Lock()

# v1.7.15 (T2 C2 follow-up from v1.7.14 verifier round): in-memory dedupe for
# the frozen-first-launch promotion ping. If %APPDATA% is read-only or AV
# holds the config file consistently, save_config raises OSError and the
# `_frozen_promoted_pinged` flag never persists to disk — so the next launch
# re-fires the toast. v1.7.14 deferred this with a `log.warning(... "Harmless
# beyond the extra toast.")` line and accepted cross-launch spam in the rare
# RO-APPDATA case. This module-level boolean catches the SAME-session case:
# nothing in the current process re-enters `_frozen_promote_ping` (it's a
# one-shot startup worker), but defensive — if a future code path ever
# re-invokes the worker (Explorer-restart handler, mid-session promote retry,
# etc.) the bool prevents a second toast within one process invocation. The
# acceptable degradation under RO-APPDATA is still "one toast per launch",
# never "two toasts per launch".
# v1.7.17: lock-guarded gate. Previously a bare bool, flagged by the
# workspace's "no GIL-only assumptions" rule. The lock protects the
# claim-then-fire-then-release pattern: a caller claims the gate up front,
# fires the toast, and releases on notify failure (so a retry on next
# launch is possible). Two simultaneous _frozen_promote_ping invocations
# (today only spawned once per launch, but defended against future
# refactors) cannot both win the claim.
_PING_GATE_LOCK = threading.Lock()
_PING_FIRED_THIS_PROCESS = False


def _try_claim_ping_gate():
    """Atomic test-and-set. Returns True iff this caller wins the slot to
    fire the frozen-first-launch promotion ping. Subsequent callers in the
    same process get False until `_release_ping_gate` is called (which only
    happens on notify failure)."""
    global _PING_FIRED_THIS_PROCESS
    with _PING_GATE_LOCK:
        if _PING_FIRED_THIS_PROCESS:
            return False
        _PING_FIRED_THIS_PROCESS = True
        return True


def _release_ping_gate():
    """Release the gate so a later caller can retry. Only called when the
    icon.notify call raised — the toast didn't actually fire, so the gate
    must not stay claimed."""
    global _PING_FIRED_THIS_PROCESS
    with _PING_GATE_LOCK:
        _PING_FIRED_THIS_PROCESS = False


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


def check_for_updates(timeout=5, force=False):
    """Query GitHub releases for the latest version.

    Returns (has_update, latest, html_url, error, assets):
      - has_update: bool — True if a newer release exists
      - latest:    str|None — version string with the leading "v" stripped
      - html_url:  str|None — GitHub release page URL (browser fallback)
      - error:     str|None — populated on network/parse failure
      - assets:    dict[str, str] — {asset_name: browser_download_url} for
                   every published asset on the latest release. Empty dict
                   on source-only or pre-asset releases. Consumed by the
                   rename-dance to locate `displayoff.exe` +
                   `SHA256SUMS.txt`. Always validate the URLs against
                   `_ALLOWED_UPDATE_HOSTS` before fetching.

    Network/parse failures return (False, None, None, '<error>', {}).

    Successful results are cached for `_UPDATE_CHECK_CACHE_TTL` seconds —
    repeated clicks within that window hit the cache instead of GitHub's
    API. Errors are NOT cached so a transient outage doesn't poison future
    checks. Pass `force=True` to bypass the cache (not currently wired to
    any UI affordance — internal hook).

    Schema change vs. v1.7.12: the 5th element (assets) was added in
    v1.7.13. The cache also stores 5-tuples now, so a v1.7.12 cache loaded
    by a v1.7.13 process would tuple-unpack-fail — but the cache lives only
    in-process memory (not on disk), so a version bump means a cold cache
    on first launch. No persistence migration needed.
    """
    import urllib.request, urllib.error

    now = time.monotonic()
    if not force:
        with _update_check_cache_lock:
            cached = _update_check_cache["result"]
            age = now - _update_check_cache["timestamp"]
            if cached is not None and age < _UPDATE_CHECK_CACHE_TTL:
                return cached

    req = urllib.request.Request(
        _RELEASES_API,
        headers={
            # Generic UA — previously embedded the running version
            # ("DisplayOff/{__version__}") which let any passive observer on
            # the network path (corporate proxy, ISP, GitHub log) fingerprint
            # the exact installed build. GitHub only requires *some* UA on
            # API requests; the value is otherwise unused.
            "User-Agent": "displayoff-updater",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as e:
        return False, None, None, str(e), {}
    latest = data.get("tag_name") or data.get("name") or ""
    html_url = data.get("html_url", "")
    if not latest:
        return False, None, None, "no tag in response", {}
    # Build the assets map: {name: browser_download_url}. The API returns a
    # list of dicts, one per uploaded artifact. Filter for entries that have
    # both a name AND a download URL — partially-failed asset uploads can
    # leave entries with one but not the other in the API response.
    assets = {
        a["name"]: a["browser_download_url"]
        for a in (data.get("assets") or [])
        if a.get("name") and a.get("browser_download_url")
    }
    has_update = _version_tuple(latest) > _version_tuple(__version__)
    result = (has_update, latest.lstrip("vV"), html_url, None, assets)

    with _update_check_cache_lock:
        _update_check_cache["timestamp"] = now
        _update_check_cache["result"] = result

    return result


# ── Folder-swap updater (v1.7.22+) ──────────────────────────────────────────
# Replaces v1.7.13's single-file rename-dance (which atomically swapped
# `displayoff.exe` ↔ `displayoff.exe.old` via os.rename) when running as the
# frozen --standalone bundle. The standalone bundle is a folder of ~150
# DLLs; mid-flight individual-file rename of DLLs that are memory-mapped
# by the running process can't work, so the unit-of-swap moved from a
# single file to the whole bundle directory.
#
# All staging artifacts live as SIBLINGS of the canonical install dir
# (i.e., under `<install_parent>/`, NOT inside `<install_dir>/`). Sibling
# placement is load-bearing because the dance renames the install dir
# itself.
#
# Caller responsibility (the Settings "Install now" worker):
#   1. Hit GitHub releases API for latest tag + assets list
#      (check_for_updates) — already exists pre-dance
#   2. Identify the `*.zip` asset for the new version + fetch
#      SHA256SUMS.txt; parse the SHA256 keyed by the zip's filename
#      (_fetch_release_manifest_sha256)
#
# _execute_rename_dance handles:
#   3. Download zip to `<install_parent>/displayoff.new.zip`
#      (_download_to_path)
#   4. SHA256-verify the zip against the manifest digest; on mismatch,
#      delete the zip and return "sha256_mismatch"
#   5. Extract the zip's top-level `displayoff/` folder into
#      `<install_parent>/displayoff.new/` (_extract_zip_bundle)
#   6. Delete the downloaded zip (no longer needed)
#   7. Write _UPDATE_RELAUNCH_PATH with the new-version string + the
#      original install dir path (so the child knows what to rename)
#   8. Spawn `<install_parent>/displayoff.new/displayoff.exe
#      --after-update-folder-swap` detached, then caller waits for child
#      signal + os._exit(0)
#
# Step 9 (in the new --after-update-folder-swap process, running from
# the `.new/` dir):
#   - Signals the parent via named event (parent then exits, releasing
#     the single-instance mutex)
#   - Reads + deletes _UPDATE_RELAUNCH_PATH (forensics)
#   - os.rename(`<install_parent>/displayoff`,
#               `<install_parent>/displayoff.old`)    — old install backed up
#   - os.rename(`<install_parent>/displayoff.new`,
#               `<install_parent>/displayoff`)        — we become canonical
#   - Re-resolves `_EXE_PATH` and `_INSTALL_DIR` post-rename via
#     GetModuleFileNameW(NULL) so downstream callers (autostart .lnk,
#     forensics) see the canonical paths
#   - shutil.rmtree(`<install_parent>/displayoff.old`) — best-effort
#     cleanup (AV may briefly hold locks; left for _recover_from_failed_update
#     on next launch if it fails)
#   - Continues to the normal tray-start path
#
# Recovery (called at the top of main(), independent of the dance):
#   - Stale `displayoff.new.zip` (interrupted download) → delete
#   - Stale `displayoff.new/` (extracted but never swapped) → delete
#   - Stale `displayoff.new.staging/` (interrupted extraction) → delete
#   - Stale `displayoff.old/` (post-swap leftover from crashed cleanup)
#     → delete recursively (best-effort)
#   - Stale _UPDATE_RELAUNCH_PATH without --after-update-folder-swap →
#     log + delete


def _download_url_allowed(url):
    """Validate URL against the update allowlist. v1.7.13 verifier round
    (T2-Sonnet + T3-Opus convergent) hardened this from a flat
    `startswith(host)` check to a parsed (scheme, netloc, path) match:

      - scheme MUST be `https` (no http downgrade, no `file://`,
        no `javascript:`)
      - host `github.com` ONLY for paths under `/itsnateai/displayoff/`
        (NOT all itsnateai repos — a future `itsnateai/other` release
        could otherwise impersonate displayoff)
      - host `release-assets.githubusercontent.com` for any path
        (GitHub's current Azure-Blob-backed asset CDN — migrated from
        objects.githubusercontent.com over 2025. The SHA256 verification
        is the actual integrity boundary; the host check just keeps the
        redirect chain inside known GitHub infra.)
      - host `objects.githubusercontent.com` for any path (the legacy
        asset CDN — kept for any older release whose asset URLs were
        baked before the migration. Defensive — both may coexist for
        some time.)
      - host `objects-origin.githubusercontent.com` for any path
        (v1.7.20 forward-compat defense — Microsoft's storage layer
        occasionally serves the origin host directly in long redirect
        chains. If GitHub flips us at it again the way they did when
        the release-assets.* CDN landed, the dance keeps working.)

    Case-insensitive on scheme + netloc (RFC 3986 §3.1/3.2.2 — both are
    case-insensitive); path is exact-prefix per HTTP semantics. v1.7.20
    also normalizes the path via `os.path.normpath` and rejects any
    URL whose normalized path starts with `/..` or contains `/../` —
    SHA256 is still the integrity boundary, but rejecting malformed
    traversal up front keeps the allowlist's prefix check honest (a
    `https://github.com/itsnateai/displayoff/../other-repo/...` URL
    would otherwise pass the github.com branch's startswith check).

    v1.7.16 hotfix: added `release-assets.githubusercontent.com` after
    the v1.7.14 → v1.7.15 in-the-wild rename-dance attempt failed with
    `urlopen error redirect target not in allowlist:
    'https://release-assets.githubusercontent.com/...'`. The dance was
    silently broken since v1.7.13 because nobody had exercised it
    end-to-end until v1.7.15 shipped. This is the load-bearing case for
    why a real-world live test belongs in the release gate — the
    8-agent code review couldn't catch a GitHub-end CDN-domain change.

    Returns False for empty/None/malformed URLs.
    """
    if not url or not isinstance(url, str):
        return False
    import urllib.parse
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if parts.scheme.lower() != "https":
        return False
    # v1.7.20 traversal rejection (per verifier-round convergent finding from
    # T1-Sonnet + T1-Opus + T3-Opus, plus post-fix T2-Sonnet + T2-Opus
    # percent-encoding gap):
    #
    #   Layer 0: percent-decode the URL path. `urlsplit` does NOT decode
    #            percent-encoded characters, so `%2e%2e` would pass the raw-
    #            segment `..` check and the normpath check (normpath also
    #            doesn't decode). `urllib.parse.unquote` decodes `%2e` → `.`
    #            and other RFC 3986 percent-encoded sequences before our
    #            traversal-detection runs.
    #   Layer 1: reject any DECODED path containing a literal `..` segment
    #            (split on forward slash AFTER backslash-rewrite). Catches
    #            both literal `..` and decoded `%2e%2e`.
    #   Layer 2: do the `github.com` prefix check against the NORMALIZED
    #            decoded path. Layer 1 should already reject anything that
    #            would change the prefix; Layer 2 is belt-and-suspenders.
    #
    # SHA256 verification is still the integrity boundary; this defense is
    # belt-and-suspenders so the host-allowlist's documented promise actually
    # holds. After Layer 0 + Layer 1, the `startswith("/..")` /
    # `"/../" in normalized_path` checks at Layer 2's threshold can never
    # fire (normpath collapses `..` segments away). They stay as a defensive
    # tripwire — a future refactor that removes Layer 1 without adding an
    # equivalent guard would still get caught by the dead Layer 2 check.
    # Loop until idempotent so double-encoded `%252e%252e` (decodes to
    # `%2e%2e`, decodes again to `..`) doesn't bypass the segment check.
    # T2-Opus R2 + T3-Opus R2 convergent finding. Bounded by max_passes so
    # a pathological infinite-decode input (which urllib's unquote
    # shouldn't produce, but defensive) terminates.
    decoded_path = parts.path
    for _ in range(5):
        once = urllib.parse.unquote(decoded_path)
        if once == decoded_path:
            break
        decoded_path = once
    raw_segments = decoded_path.replace("\\", "/").split("/")
    if ".." in raw_segments:
        return False
    normalized_path = os.path.normpath(decoded_path).replace("\\", "/")
    if normalized_path.startswith("/..") or "/../" in normalized_path:
        return False
    netloc_low = parts.netloc.lower()
    if netloc_low == "github.com":
        # Trailing-slash tolerance: `os.path.normpath` strips the trailing
        # `/`, so a legitimate URL ending exactly at the repo root would
        # over-reject. Padding `normalized_path` with `/` before the check
        # keeps `/itsnateai/displayoff` (root, no slash) matching, while
        # rejecting `/itsnateai/displayoffother/...` (no separator between
        # `displayoff` and `other` after the pad — `(orig + "/")` is
        # `/itsnateai/displayoffother/.../` which doesn't startswith the
        # prefix-with-slash).
        return (normalized_path + "/").startswith("/itsnateai/displayoff/")
    if netloc_low == "release-assets.githubusercontent.com":
        return True
    if netloc_low == "objects.githubusercontent.com":
        return True
    if netloc_low == "objects-origin.githubusercontent.com":
        return True
    return False


def _sha256_file(path, chunk_size=1 << 20):
    """Stream-compute SHA256 of a file. 1 MB chunks keep peak memory bounded
    for the ~15-25 MB .exe download — `f.read()` of the whole thing would
    spike to ~25 MB momentarily, which matters under low-memory conditions
    (the user clicked "Install now" because their machine is sluggish)."""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _parse_sha256_manifest(text, target_name):
    """Extract the 64-hex SHA256 for `target_name` from a `sha256sum`-format
    manifest. Returns lowercase hex on success, or None if `target_name`
    is absent / malformed.

    Format per line (GNU coreutils sha256sum -b):
        <64_hex>  *<filename>   binary mode (Windows-typical)
        <64_hex>  <filename>    text mode

    Tolerates blank lines, `#` comments, and trailing whitespace.
    """
    target = target_name.strip()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        hex_part, name_part = parts
        # Validate hex digest shape before string-comparison. Bare "len ==
        # 64" wouldn't catch a 64-char string with non-hex chars; explicit
        # int parse confirms it's a real digest.
        if len(hex_part) != 64:
            continue
        try:
            int(hex_part, 16)
        except ValueError:
            continue
        name_part = name_part.lstrip("*").strip()
        if name_part == target:
            return hex_part.lower()
    return None


def _build_allowlist_opener():
    """urllib opener that re-validates EVERY redirect hop against the host
    allowlist. v1.7.13 verifier round (T3-Opus H1, T2-Sonnet C2, T2-Opus
    convergent): the default `urllib.request.urlopen` follows redirects
    with no re-check — a compromised github.com asset row could 302 to
    arbitrary attacker-controlled hosts. SHA256 verification still catches
    tampered bytes, but the redirect itself leaks the request fingerprint
    (IP / User-Agent / request-time) to the attacker domain via the
    Location-header GET. Override `redirect_request` so disallowed hops
    raise URLError, which urlopen surfaces as a plain failure.
    """
    import urllib.request, urllib.error

    class _AllowlistedRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not _download_url_allowed(newurl):
                raise urllib.error.URLError(
                    f"redirect target not in allowlist: {newurl!r}"
                )
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(_AllowlistedRedirectHandler())


def _fetch_release_manifest_sha256(manifest_url, target_name, timeout=15):
    """Fetch and parse SHA256SUMS.txt for `target_name`. Returns
    (sha256_hex, None) on success or (None, error) on failure.

    Validates the URL against `_ALLOWED_UPDATE_HOSTS`. Caps the read at
    16 KiB so a malicious 200-OK response with a huge body can't OOM us
    (a real manifest is < 200 bytes per asset entry).

    Uses the allowlist-validating opener (`_build_allowlist_opener`) so
    every redirect hop is re-checked — the default urllib behavior would
    follow github.com → objects.gh.com → anywhere silently.
    """
    import urllib.error
    if not _download_url_allowed(manifest_url):
        return None, f"manifest URL host not allowed: {manifest_url!r}"
    try:
        opener = _build_allowlist_opener()
        req = opener.open(manifest_url, timeout=timeout)  # noqa: S310 — allowlist-validated
        try:
            data = req.read(16 * 1024)
        finally:
            req.close()
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return None, str(e)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, f"manifest is not UTF-8: {e}"
    sha = _parse_sha256_manifest(text, target_name)
    if sha is None:
        return None, f"no SHA256 entry for {target_name!r} in manifest"
    return sha, None


def _download_to_path(url, dest_path, timeout=60, min_size=None):
    """Download `url` to `dest_path`. Returns (ok, error). Validates URL
    against `_ALLOWED_UPDATE_HOSTS` and a minimum-size floor (defaults to
    `_UPDATE_MIN_ZIP_SIZE` for the standalone-bundle zip).

    Truncated downloads + URLs that 200-OK with an HTML error page (some
    CDNs do this) both get caught by the size check, which deletes the
    partial file before returning failure. Files smaller than the floor
    are deleted so the folder-swap dance never accidentally promotes a
    junk download.

    Uses `_build_allowlist_opener` so every redirect hop is re-validated
    against the allowlist (github.com → release-assets.githubusercontent.com
    is the expected path; anything else raises URLError from the
    redirect_request override and surfaces as a download failure).
    """
    import urllib.request, urllib.error
    if not _download_url_allowed(url):
        return False, f"download URL host not allowed: {url!r}"
    if min_size is None:
        min_size = _UPDATE_MIN_ZIP_SIZE
    try:
        opener = _build_allowlist_opener()
        req = urllib.request.Request(url, headers={"User-Agent": "displayoff-updater"})
        bytes_written = 0
        with opener.open(req, timeout=timeout) as resp:  # noqa: S310 — allowlist-validated
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    bytes_written += len(chunk)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        # Cleanup partial write — a half-downloaded zip would survive to
        # the next launch's recovery pass anyway, but explicit removal here
        # closes the window between failure and recovery.
        try:
            if os.path.exists(dest_path):
                os.remove(dest_path)
        except OSError:
            pass
        return False, str(e)

    if bytes_written < min_size:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        return False, (f"download truncated or unexpected response body: "
                       f"{bytes_written} bytes (expected >= {min_size})")
    return True, None


def _extract_zip_bundle(zip_path, install_parent, log=log):
    """Extract `zip_path` into `install_parent`, expecting a top-level
    `displayoff/` directory inside the zip. Stages extraction through a
    scratch directory so a partial extract on failure doesn't pollute the
    canonical `<install_parent>/displayoff.new` target.

    Returns (ok, error). On success the bundle is at
    `<install_parent>/displayoff.new/displayoff.exe` (+ siblings).

    Defense layers:
      - Reject zip entries with absolute paths, drive letters, or `..`
        segments before extracting (Zip Slip protection).
      - Reject any extracted layout that doesn't have a top-level
        `displayoff/` directory containing `displayoff.exe`.
      - Clean any pre-existing `displayoff.new.staging/` or
        `displayoff.new/` from a prior crashed dance before extracting.
    """
    import shutil
    import zipfile

    staging = os.path.join(install_parent,
                           _CANONICAL_INSTALL_NAME + _UPDATE_NEW_STAGING_SUFFIX)
    new_dir = os.path.join(install_parent,
                           _CANONICAL_INSTALL_NAME + _UPDATE_NEW_DIR_SUFFIX)

    # Pre-clean any stale artifacts from a crashed prior attempt.
    for path in (staging, new_dir):
        if os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)
            if os.path.exists(path):
                return False, f"cannot remove stale {path!r} (locked?)"

    try:
        os.makedirs(staging, exist_ok=True)
    except OSError as e:
        return False, f"could not create staging dir {staging!r}: {e}"

    try:
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                name = info.filename
                normalized = name.replace("\\", "/")
                # Zip Slip: reject absolute paths, drive letters, traversal.
                if (normalized.startswith("/")
                        or (len(normalized) >= 2 and normalized[1] == ":")
                        or ".." in normalized.split("/")):
                    return False, (f"zip contains suspicious path "
                                   f"(possible Zip Slip): {name!r}")
            z.extractall(staging)
    except (zipfile.BadZipFile, OSError) as e:
        shutil.rmtree(staging, ignore_errors=True)
        return False, f"zip extraction failed: {e}"

    # Expect a top-level `displayoff/` directory containing `displayoff.exe`.
    # v1.7.22 verifier-round convergent fix (T3 Sonnet + T3 Opus): the
    # original case-sensitive `os.path.isdir(staging/displayoff)` would
    # spuriously pass on NTFS when the zip's top-level entry was actually
    # cased differently (e.g. `Displayoff/displayoff.exe`) because NTFS
    # lookups are case-insensitive but Windows then keeps whatever case
    # the zip used on-disk. The subsequent `os.rename(inner, new_dir)`
    # would leave the .new dir with a non-canonical case, breaking later
    # `endswith(.new)` checks and the autostart .lnk target. Walk the
    # staging dir and find a single top-level entry that case-insensitively
    # equals "displayoff", rejecting any zip whose top-level is the wrong
    # name OR contains multiple unrelated top-level entries.
    try:
        top_entries = os.listdir(staging)
    except OSError as e:
        shutil.rmtree(staging, ignore_errors=True)
        return False, f"could not list staging dir {staging!r}: {e}"
    # Case-fold compare via `.casefold()` (not `.lower()`) so a zip whose
    # top-level uses a non-ASCII case (e.g. Turkish locale's `İ` → `i̇`
    # mismatch under `.lower()`) is still matched correctly. R2 T3 Opus
    # flag — vanishingly unlikely in practice but defends the documented
    # case-insensitive contract.
    _expected = _CANONICAL_INSTALL_NAME.casefold()
    matching = [
        e for e in top_entries
        if os.path.isdir(os.path.join(staging, e))
        and e.casefold() == _expected
    ]
    if len(matching) != 1:
        shutil.rmtree(staging, ignore_errors=True)
        return False, (f"zip does not contain a single top-level 'displayoff/' "
                       f"directory (found {len(matching)} matching entries "
                       f"out of {len(top_entries)} top-level items) — "
                       "release packaging may be broken.")
    inner_name = matching[0]
    inner = os.path.join(staging, inner_name)
    inner_exe = os.path.join(inner, "displayoff.exe")
    if not os.path.isfile(inner_exe):
        shutil.rmtree(staging, ignore_errors=True)
        return False, ("top-level 'displayoff/' directory does not contain "
                       "displayoff.exe — release packaging may be broken.")

    # Promote the inner dir to the canonical .new/ location, then clean
    # the now-empty staging tree.
    try:
        os.rename(inner, new_dir)
    except OSError as e:
        shutil.rmtree(staging, ignore_errors=True)
        return False, f"could not move extracted bundle to {new_dir!r}: {e}"
    shutil.rmtree(staging, ignore_errors=True)

    log.info("Extracted %s -> %s (%d files)",
             zip_path, new_dir,
             sum(1 for _ in _walk_files(new_dir)))
    return True, None


def _walk_files(root):
    """Yield every file path under `root`. Helper for sizing/logging — kept
    standalone so callers can pass it to sum()/len() without lambdas."""
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            yield os.path.join(dirpath, name)


def _re_resolve_exe_path_post_swap():
    """After the --after-update-folder-swap rename, the cached `_EXE_PATH`
    and `_INSTALL_DIR` (resolved at module import) point at the pre-swap
    `<install_parent>/displayoff.new/displayoff.exe` location — which no
    longer exists because we just renamed `displayoff.new/` → `displayoff/`.

    Re-resolve via `GetModuleFileNameW(NULL)` which Windows updates to
    track the process's current image path after directory rename. Updates
    the module globals so downstream callers (autostart .lnk target,
    forensics log lines, future update attempts) see the canonical path.

    No-op on non-Windows or if the GetModuleFileNameW call fails — the
    caller should still continue; degradation is "stale `_EXE_PATH`
    values used for autostart .lnk target" rather than crash.
    """
    global _EXE_PATH, _INSTALL_DIR
    if sys.platform != "win32":
        return
    try:
        from ctypes import wintypes as _wt
        _k = ctypes.WinDLL("kernel32", use_last_error=True)
        _GetModuleFileNameW = _k.GetModuleFileNameW
        _GetModuleFileNameW.argtypes = [_wt.HMODULE, _wt.LPWSTR, _wt.DWORD]
        _GetModuleFileNameW.restype = _wt.DWORD
        # Buffer needs to be > 32767 (the long-path ceiling under
        # `\\?\`-prefixed paths). On overflow GetModuleFileNameW returns
        # the buffer length (NOT null-terminated) — we treat that case as
        # a failure (path truncation could land us at a path that exists
        # but isn't ours).
        buf_size = 32768
        buf = ctypes.create_unicode_buffer(buf_size)
        n = _GetModuleFileNameW(None, buf, buf_size)
    except (OSError, AttributeError) as e:
        log.warning("Could not re-resolve _EXE_PATH post-swap "
                    "(GetModuleFileNameW setup failed: %s); leaving "
                    "cached value %r. Autostart .lnk re-toggle may "
                    "produce a stale target until next launch.", e, _EXE_PATH)
        return

    # v1.7.22 verifier-round convergent fix (T2 Sonnet + T2 Opus): log
    # loudly on every silent-failure branch instead of falling through
    # quietly. A silent fallthrough leaves `_EXE_PATH` pointing at the
    # pre-swap `.new/displayoff.exe` path that no longer exists; the
    # autostart .lnk re-toggle then writes a broken target with no
    # diagnostic trail.
    if n == 0:
        log.warning("Post-swap _EXE_PATH re-resolve: "
                    "GetModuleFileNameW returned 0 (lastError=%d); "
                    "leaving cached value %r. Autostart .lnk re-toggle "
                    "may produce a stale target until next launch.",
                    ctypes.get_last_error(), _EXE_PATH)
        return
    if n >= buf_size:
        log.warning("Post-swap _EXE_PATH re-resolve: "
                    "GetModuleFileNameW returned %d (== buf_size %d) "
                    "indicating path truncation; leaving cached value "
                    "%r. Autostart .lnk re-toggle may produce a stale "
                    "target until next launch.",
                    n, buf_size, _EXE_PATH)
        return
    if not buf.value:
        log.warning("Post-swap _EXE_PATH re-resolve: "
                    "GetModuleFileNameW returned n=%d but buf is empty; "
                    "leaving cached value %r.", n, _EXE_PATH)
        return
    if not os.path.isfile(buf.value):
        log.warning("Post-swap _EXE_PATH re-resolve: "
                    "GetModuleFileNameW returned %r but os.path.isfile "
                    "is False; leaving cached value %r. Filesystem may "
                    "not have flushed the rename yet, or path uses an "
                    "extended-length prefix Python can't stat.",
                    buf.value, _EXE_PATH)
        return
    new_path = os.path.abspath(buf.value)
    log.info("Re-resolved _EXE_PATH post-folder-swap: %r -> %r",
             _EXE_PATH, new_path)
    _EXE_PATH = new_path
    _INSTALL_DIR = os.path.dirname(new_path)


def _write_update_relaunch_state(new_version, old_install_dir=None,
                                 new_install_dir=None):
    """Persist the relaunch state file. Called at step 7 of the dance, just
    before spawning the --after-update-folder-swap child. Writes JSON:
    `{version, timestamp, exe_path, old_install_dir, new_install_dir, pid}`.
    The child reads + deletes it as one of its first acts.

    `old_install_dir` and `new_install_dir` are load-bearing for v1.7.22's
    folder-swap dance: the child needs to know which sibling dir to
    rename → .old and which (its own .new dir) to rename → canonical.
    Both default to None for backward compatibility with any legacy
    callers (shouldn't exist post-v1.7.22, but defensive)."""
    state = {
        "version": new_version,
        "exe_path": _EXE_PATH or "",
        "old_install_dir": old_install_dir or "",
        "new_install_dir": new_install_dir or "",
        "timestamp": time.time(),
        "pid": os.getpid(),  # forensics — which process wrote this
    }
    # Atomic write to defeat partial-state reads (the child could in
    # principle race the parent's write). _DATA_DIR is on the same volume
    # as %APPDATA% so os.replace is atomic per NTFS semantics.
    tmp = _UPDATE_RELAUNCH_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, _UPDATE_RELAUNCH_PATH)


def _read_and_clear_update_relaunch_state():
    """Read the relaunch-state file written by the previous-version's
    dance, then delete it. Returns the parsed dict (or None if absent /
    corrupted). Called from the --after-update handler in main()."""
    if not os.path.exists(_UPDATE_RELAUNCH_PATH):
        return None
    try:
        with open(_UPDATE_RELAUNCH_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Could not parse update-relaunch state %r: %s",
                    _UPDATE_RELAUNCH_PATH, e)
        state = None
    try:
        os.remove(_UPDATE_RELAUNCH_PATH)
    except OSError as e:
        log.warning("Could not delete update-relaunch state %r: %s",
                    _UPDATE_RELAUNCH_PATH, e)
    return state


def _recover_from_failed_update():
    """Clean up artifacts from a previous folder-swap dance that crashed or
    hung. Called at the top of main() — runs on every launch, cheap when
    there's nothing to do.

    Five independent cleanups:
      1. `<install_parent>/displayoff.new.zip` — partial download
         (untrusted bytes; delete)
      2. `<install_parent>/displayoff.new/` — extracted bundle that never
         made it through the swap step (we're already running from the
         canonical `displayoff/` dir, so the staged copy is dead weight)
      3. `<install_parent>/displayoff.new.staging/` — interrupted zip
         extraction
      4. `<install_parent>/displayoff.old/` — pre-swap backup that
         --after-update-folder-swap didn't get around to deleting (we're
         already running from the new install dir; safe to clean)
      5. Stale `_update_relaunch.json` in _DATA_DIR without a
         corresponding --after-update-folder-swap CLI flag — the
         spawn-child step succeeded but the child crashed before
         consuming the state. Log + delete.

    Also detects the **half-swapped state** (canonical install dir
    missing but both `displayoff.new/` and `displayoff.old/` exist as
    siblings — what you get if the previous run's
    --after-update-folder-swap was killed between rename steps) and
    resumes the swap by completing `.new -> canonical`. This MUST run
    before the artifact cleanup loop — otherwise the loop would delete
    `.new/` and `.old/`, destroying all the user's installed bytes.

    Skipped under .py source mode — the folder-swap dance only applies to
    the frozen --standalone bundle. Under source, `_EXE_PATH` is None and
    there's nothing to clean up alongside.

    Defensive identity check before deleting any sibling dir: never delete
    a sibling that resolves (via realpath) to the current install dir.
    Without this, an unusual install layout (junction loops, an APP that
    sits at install_parent/displayoff and also at
    install_parent/displayoff.new because of an external symlink) could
    delete the running app.
    """
    if not _is_frozen() or not _EXE_PATH or not _INSTALL_DIR:
        return
    install_parent = os.path.dirname(_INSTALL_DIR)
    if not install_parent or install_parent == _INSTALL_DIR:
        # Top-of-volume install (e.g., C:\displayoff\displayoff.exe).
        # `dirname` returns `C:\`; sibling cleanup would scan the drive
        # root, which is hostile. Skip cleanup in this edge case.
        log.info("Skipping update-artifact recovery: install dir %r has no "
                 "usable parent for sibling cleanup.", _INSTALL_DIR)
        return
    try:
        current_real = os.path.realpath(_INSTALL_DIR)
    except OSError as e:
        # v1.7.22 verifier-round convergent fix (T2 Sonnet + T3 Opus): if
        # realpath on our own install dir fails, skip the entire cleanup
        # pass rather than fall back to the unresolved path. Without the
        # fallback's safer-default the identity guard below silently
        # disables — `_safe_realpath` would return raw paths for both
        # `current_real` and the artifact, and the equality check could
        # randomly compare unresolved-to-resolved across calls, exposing
        # the live install dir to deletion via a junction.
        log.warning("Update-artifact recovery: cannot realpath %r (%s); "
                    "skipping cleanup pass to keep the identity guard "
                    "honest.", _INSTALL_DIR, e)
        return

    def _safe_realpath(path):
        """Return realpath(path), or None if realpath fails. v1.7.22
        verifier-fix: signaling failure (None) lets the caller treat the
        guard as failed-closed rather than failed-open (the prior
        implementation returned the raw path on OSError, which silently
        bypassed the identity guard whenever the artifact's parent had
        a permission-denied entry along the realpath chain)."""
        try:
            return os.path.realpath(path)
        except OSError:
            return None

    # v1.7.22 verifier-round convergent fix (T3 Opus I2): detect the
    # half-swapped state where the previous run's --after-update-folder-swap
    # was killed between the first rename (canonical → .old) and the
    # second rename (.new → canonical). Symptom: canonical install dir
    # is missing but both `.new/` and `.old/` exist as siblings. Without
    # this recovery, the autostart .lnk's canonical target doesn't exist
    # AND the cleanup loop below would happily delete the `.new/` and
    # `.old/` artifacts, destroying ALL the user's installed bytes.
    # Resume by completing the second rename — gets the user onto the new
    # version they wanted; .old/ gets cleaned by the loop below.
    canonical_dir = os.path.join(install_parent, _CANONICAL_INSTALL_NAME)
    new_dir = os.path.join(install_parent,
                           _CANONICAL_INSTALL_NAME + _UPDATE_NEW_DIR_SUFFIX)
    old_dir = os.path.join(install_parent,
                           _CANONICAL_INSTALL_NAME + _UPDATE_OLD_DIR_SUFFIX)

    # R2 T2 Opus G4: detect the half-swap state EITHER when canonical
    # doesn't exist at all OR when it exists but is empty. The
    # empty-but-exists case arises when AV partially-quarantined the
    # bundle (deleted all files but left the parent dir) or a partial
    # `rmdir /s` was interrupted by a permission error. Without the
    # empty-dir branch, the resume is skipped and the cleanup loop then
    # deletes `.new/` and `.old/`, leaving the user with an empty
    # canonical and nothing usable — hard brick.
    canonical_missing_or_empty = False
    if not os.path.exists(canonical_dir):
        canonical_missing_or_empty = True
    else:
        try:
            with os.scandir(canonical_dir) as it:
                # Any entry inside means it's a real install, not empty.
                if not any(True for _ in it):
                    canonical_missing_or_empty = True
                    log.warning(
                        "Canonical install dir %r exists but is empty — "
                        "likely a partial-quarantine or interrupted "
                        "remove. Treating as half-swap candidate.",
                        canonical_dir,
                    )
        except OSError as e:
            # Permission denied or similar. Conservative default: do NOT
            # treat as half-swap (we can't prove it's empty), and let
            # the cleanup loop's identity guard handle it.
            log.warning("Cannot scandir canonical %r (%s) — skipping "
                        "half-swap detection.", canonical_dir, e)

    if (canonical_missing_or_empty
            and os.path.isdir(new_dir)
            and os.path.isdir(old_dir)
            and os.path.isfile(os.path.join(new_dir, "displayoff.exe"))):
        log.warning(
            "Detected half-swapped install layout: canonical %r missing/empty, "
            "but %r and %r both exist. Resuming the swap by completing "
            "the .new -> canonical rename.",
            canonical_dir, new_dir, old_dir,
        )
        # R3 verifier-round convergent fix (T2 Opus + T3 Sonnet + T3 Opus):
        # the previous implementation called `shutil.rmtree(canonical_dir)`
        # before `os.rename(new_dir, canonical_dir)`. rmtree is NOT atomic
        # on Windows — if AV holds a lock partway through the recursive
        # delete, rmtree raises OSError with PARTIAL deletion done. The
        # caught exception triggered a return, leaving canonical with
        # partial contents, and the next launch's empty-canonical detector
        # then saw a "non-empty" canonical and skipped the half-swap
        # branch entirely, after which the cleanup loop deleted .new/ +
        # .old/. Net: hard brick from a state that was recoverable.
        #
        # Atomic alternative: rename canonical → a scratch sibling
        # (single-syscall, can't partial-fail), then rename .new →
        # canonical, then best-effort rmtree the scratch sibling
        # (ignore_errors=True so AV locks don't propagate failure into
        # an otherwise-successful swap). Same recurrence-killing pattern
        # the single-file rename-dance used in v1.7.13.
        scratch_dir = canonical_dir + ".tmp-evicted"
        if os.path.exists(canonical_dir):
            # Pre-clean any stale scratch dir from a prior crashed
            # attempt before reusing the name.
            if os.path.exists(scratch_dir):
                try:
                    import shutil
                    shutil.rmtree(scratch_dir, ignore_errors=True)
                except OSError:
                    pass
            try:
                os.rename(canonical_dir, scratch_dir)
            except OSError as e:
                log.error("Could not evict empty canonical %r to %r "
                          "before half-swap resume (%s). MANUAL "
                          "RECOVERY: remove %r, then rename %r to %r. "
                          "Skipping artifact cleanup pass.",
                          canonical_dir, scratch_dir, e,
                          canonical_dir, new_dir, canonical_dir)
                return
        try:
            os.rename(new_dir, canonical_dir)
            log.info("Half-swap resumed: %r -> %r", new_dir, canonical_dir)
            # Best-effort cleanup of the evicted-canonical scratch dir.
            # ignore_errors=True so an AV lock here doesn't undo the
            # swap that just succeeded — the next launch's
            # _recover_from_failed_update artifact loop will retry the
            # scratch_dir delete (it's not on the loop's list yet,
            # though — see the inline cleanup below).
            if os.path.exists(scratch_dir):
                try:
                    import shutil
                    shutil.rmtree(scratch_dir, ignore_errors=True)
                except OSError:
                    pass
            # R2 T3 Sonnet + Opus: replace the prior `_INSTALL_DIR ==
            # new_dir` string comparison with `os.path.samefile` /
            # normpath-normcase equivalence so junction loops, trailing-
            # slash drift, 8.3-short-name variants, or abspath-vs-realpath
            # mismatches don't false-negative. We do an EXIST check
            # first — `os.path.samefile` raises if either path is gone.
            # `new_dir` is gone post-rename (we just renamed it), so the
            # samefile would raise — guard via fallback to the cheap
            # normalized-string compare against the pre-rename `new_dir`.
            install_dir_was_new_dir = False
            try:
                # The pre-rename samefile-equivalent: normalize both to
                # the same canonical form. _INSTALL_DIR is set via
                # os.path.dirname(os.path.abspath(...)) at module import;
                # new_dir is built via os.path.join from the same parent.
                _install_norm = os.path.normcase(
                    os.path.normpath(_INSTALL_DIR or ""))
                _new_norm = os.path.normcase(os.path.normpath(new_dir))
                install_dir_was_new_dir = _install_norm == _new_norm
            except (OSError, AttributeError):
                install_dir_was_new_dir = False

            if install_dir_was_new_dir:
                _re_resolve_exe_path_post_swap()
                # Refresh the install_parent + current_real values we
                # already computed before this rename.
                install_parent = os.path.dirname(_INSTALL_DIR)
                try:
                    current_real = os.path.realpath(_INSTALL_DIR)
                except OSError:
                    log.warning("Post-half-swap recovery: cannot realpath "
                                "%r; skipping artifact cleanup pass.",
                                _INSTALL_DIR)
                    return
        except OSError as e:
            # The half-swap could not be resumed. Bail BEFORE the cleanup
            # loop — proceeding would delete `.old/` (the user's only
            # remaining install bytes) leaving them with nothing usable.
            log.error("Half-swap resume failed (%s). MANUAL RECOVERY: "
                      "rename %r to %r. Skipping artifact cleanup pass.",
                      e, new_dir, canonical_dir)
            return

    artifacts = [
        (os.path.join(install_parent,
                      _CANONICAL_INSTALL_NAME + _UPDATE_NEW_ZIP_SUFFIX), "file"),
        (os.path.join(install_parent,
                      _CANONICAL_INSTALL_NAME + _UPDATE_NEW_DIR_SUFFIX), "dir"),
        (os.path.join(install_parent,
                      _CANONICAL_INSTALL_NAME + _UPDATE_NEW_STAGING_SUFFIX), "dir"),
        (os.path.join(install_parent,
                      _CANONICAL_INSTALL_NAME + _UPDATE_OLD_DIR_SUFFIX), "dir"),
    ]
    # v1.7.22 verifier-round MINOR fix (T1 Opus): the docstring lists 5
    # cleanups but the loop only ran 4. Item 5 — the stale
    # `_update_relaunch.json` left in _DATA_DIR by a crashed dance — used
    # to be cleaned by the v1.7.13 recovery code; restored here so the
    # docstring's "Five independent cleanups" claim isn't a lie.
    # Only cleans the file if NO `--after-update-folder-swap` flag is
    # currently in argv (because in that case, the handler in main()
    # is about to consume the state file via _read_and_clear).
    if ("--after-update-folder-swap" not in sys.argv
            and os.path.exists(_UPDATE_RELAUNCH_PATH)):
        try:
            os.remove(_UPDATE_RELAUNCH_PATH)
            log.info("Cleaned stale update-relaunch state: %s",
                     _UPDATE_RELAUNCH_PATH)
        except OSError as e:
            log.warning("Could not clean stale update-relaunch state "
                        "%r: %s", _UPDATE_RELAUNCH_PATH, e)

    for path, kind in artifacts:
        if not os.path.exists(path):
            continue
        # Identity guard: never delete a sibling that's actually the
        # current install dir under a symlink/junction loop. v1.7.22
        # verifier-fix: failed realpath also skips (safer default — a
        # path we can't resolve is one we can't safely classify).
        if kind == "dir":
            artifact_real = _safe_realpath(path)
            if artifact_real is None:
                log.warning(
                    "Skipping update-artifact cleanup of %r: could not "
                    "realpath (permission denied or broken junction). "
                    "Failed-closed to keep the identity guard honest.",
                    path,
                )
                continue
            if artifact_real == current_real:
                log.warning(
                    "Skipping update-artifact cleanup of %r: realpath "
                    "matches current install dir. Likely a "
                    "junction/symlink loop — user should manually "
                    "inspect the layout.",
                    path,
                )
                continue
        try:
            if kind == "file":
                os.remove(path)
            else:
                import shutil
                shutil.rmtree(path, ignore_errors=False)
            log.info("Cleaned update artifact: %s", path)
        except OSError as e:
            # Most common cause: Windows still holds a file lock on a
            # DLL inside `.old/` because AV scanning the just-renamed
            # bundle hasn't finished. We're called from main() at startup,
            # so the parent process is gone by the time we get here — but
            # AV can hold a lock for a few seconds after a write. Log and
            # move on; the next launch retries.
            log.warning("Could not clean update artifact %r: %s", path, e)


def _execute_rename_dance(zip_url, zip_sha256, new_version, zip_filename):
    """Execute steps 3-8 of the folder-swap dance (steps 1-2 are the
    caller's API + manifest fetch; step 9 is the --after-update-folder-swap
    child). See the outer `── Folder-swap updater ──` comment block above
    for the full 9-step framing.

    Returns (status, detail):
      - ("relaunched", None)           — caller MUST exit immediately
      - ("not_frozen", detail)         — running from .py source; N/A
      - ("download_failed", detail)    — network/404/redirect outside allowlist
      - ("sha256_mismatch", detail)    — download corrupted or tampered
      - ("extract_failed", detail)     — bad zip, Zip Slip rejection, or
                                         could not place displayoff.new/
      - ("rename_failed", detail)      — bundle locked / AV / permissions
                                         (caught when the child tries to
                                         swap — the parent's own work
                                         can't actually reach this status)
      - ("spawn_failed", detail)       — bundle staged but child didn't launch

    URL allowlist re-validation happens here even though `_download_to_path`
    also checks — belt-and-suspenders, especially relevant because this
    function takes the URL as a parameter from the manifest+API flow and
    we want a single audit checkpoint right at the dance entry.
    """
    if not _is_frozen() or not _EXE_PATH or not _INSTALL_DIR:
        return "not_frozen", "folder-swap dance requires the frozen standalone build"
    if not _download_url_allowed(zip_url):
        return "rename_failed", f"download URL host not allowed: {zip_url!r}"
    if not zip_sha256 or len(zip_sha256) != 64:
        return "sha256_mismatch", "no SHA256 available for the new release zip"

    install_parent = os.path.dirname(_INSTALL_DIR)
    if not install_parent or install_parent == _INSTALL_DIR:
        return "rename_failed", (
            f"install dir {_INSTALL_DIR!r} has no usable parent for sibling "
            "staging — cannot run folder-swap dance here. Re-install under "
            "a non-root path (e.g., %LOCALAPPDATA%\\Programs\\displayoff\\)."
        )

    zip_path = os.path.join(install_parent,
                            _CANONICAL_INSTALL_NAME + _UPDATE_NEW_ZIP_SUFFIX)
    new_dir = os.path.join(install_parent,
                           _CANONICAL_INSTALL_NAME + _UPDATE_NEW_DIR_SUFFIX)
    staging_dir = os.path.join(install_parent,
                               _CANONICAL_INSTALL_NAME + _UPDATE_NEW_STAGING_SUFFIX)

    # Pre-clean any stale staging artifacts from a prior attempt.
    # _recover_from_failed_update also runs at startup, so this is
    # belt-and-suspenders — but a same-session retry (user clicked
    # Install after a network glitch) needs cleanup before the new
    # attempt starts.
    import shutil
    for path in (zip_path, new_dir, staging_dir):
        if os.path.exists(path):
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            except OSError as e:
                return "rename_failed", f"cannot remove stale {path!r}: {e}"

    # Steps 3+4: download new zip, then SHA256-verify against the manifest
    # digest the caller already extracted. The two steps share a try-block
    # because a failed verify also wants the zip cleaned up.
    log.info("Update dance: downloading %s -> %s", zip_url, zip_path)
    ok, err = _download_to_path(zip_url, zip_path)
    if not ok:
        return "download_failed", err or "download failed"
    actual_sha = _sha256_file(zip_path)
    if actual_sha.lower() != zip_sha256.lower():
        # DELETE the zip on hash mismatch instead of preserving it (same
        # rationale as v1.7.13's exe-tmp handling — an attacker who
        # controlled a release manifest could otherwise plant arbitrary
        # unverified bytes inside install_parent).
        try:
            os.remove(zip_path)
        except OSError as cleanup_err:
            log.warning("Could not delete zip after sha256 mismatch %r: %s",
                        zip_path, cleanup_err)
        return "sha256_mismatch", (
            f"sha256 mismatch: expected {zip_sha256}, got {actual_sha}; "
            f"corrupted download or tampered release. zip deleted."
        )

    # Step 5: extract zip → install_parent/displayoff.new/. The extract
    # helper does Zip Slip protection + verifies a top-level displayoff/
    # directory containing displayoff.exe before promoting the inner dir
    # to the canonical .new/ location.
    log.info("Update dance: extracting %s -> %s", zip_path, new_dir)
    ok, err = _extract_zip_bundle(zip_path, install_parent, log=log)
    if not ok:
        try:
            os.remove(zip_path)
        except OSError:
            pass
        return "extract_failed", err or "zip extraction failed"

    # Step 6: zip no longer needed once the bundle is extracted; deleting
    # it shrinks the install_parent footprint by ~30 MB. Failure here is
    # non-fatal — _recover_from_failed_update will catch the stale zip
    # on next launch.
    try:
        os.remove(zip_path)
    except OSError as e:
        log.warning("Could not delete downloaded zip %r after extract: %s "
                    "— next launch will retry.", zip_path, e)

    # Step 7: write relaunch state so the child knows what to do. Records
    # the OLD install dir (canonical pre-swap path) so the child can find
    # its target. The state file lives in _DATA_DIR (%APPDATA%) which is
    # outside the folder-swap path, so it survives the rename.
    new_exe_path = os.path.join(new_dir, "displayoff.exe")
    try:
        _write_update_relaunch_state(new_version, old_install_dir=_INSTALL_DIR,
                                     new_install_dir=new_dir)
    except OSError as e:
        # Non-fatal — the child will fall back to deducing old_install_dir
        # from its own sibling layout. Keep going.
        log.warning("Could not write update-relaunch state: %s", e)

    # v1.7.20: create the child-ready handshake event BEFORE spawning the
    # child. Parent will WaitForSingleObject on this handle (in the caller,
    # `_run_rename_dance_flow._worker`) so the parent's `os._exit(0)` waits
    # for the child's signal — replaces the fixed 0.3s sleep that lost the
    # mutex race on slow systems. Failure to create the event is logged
    # but non-fatal: the caller falls back to the 0.3s sleep.
    global _update_child_ready_handle
    if sys.platform == "win32" and CreateEventW is not None:
        # v1.7.20 verifier T3-Sonnet H1: ABA defense. If a previous update
        # attempt in this session left `_update_child_ready_handle` non-None,
        # we'd silently leak that prior handle when CreateEventW assigns a
        # new one. Closing first is idempotent and safe.
        if _update_child_ready_handle:
            try:
                CloseHandle(_update_child_ready_handle)
            except Exception:
                pass
            _update_child_ready_handle = None
        try:
            _update_child_ready_handle = CreateEventW(
                None, True, False, _UPDATE_CHILD_READY_EVENT_NAME
            )
            if not _update_child_ready_handle:
                log.warning(
                    "Could not create update-child-ready event (err=%d); "
                    "falling back to fixed 0.3s sleep before parent exit.",
                    ctypes.get_last_error(),
                )
                _update_child_ready_handle = None
        except OSError as e:
            log.warning(
                "CreateEventW for update-child-ready raised (%s); "
                "falling back to fixed 0.3s sleep before parent exit.", e
            )
            _update_child_ready_handle = None

    # Step 8: spawn child --after-update-folder-swap detached. The child
    # runs from <install_parent>/displayoff.new/displayoff.exe and is the
    # process that performs the actual folder rename — the parent
    # (this process) can't rename its OWN install dir while running from
    # inside it (Windows allows directory rename with open file handles
    # inside, but the parent's CWD might be _INSTALL_DIR and we don't
    # want to gamble on those semantics).
    log.info("Update dance: spawning %s --after-update-folder-swap", new_exe_path)
    try:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        # close_fds=True ensures no inherited file handles keep the parent's
        # log file (or any opened tray pipe) locked into the child.
        # cwd=new_dir so the child starts with its CWD inside the new
        # bundle dir (matches the standalone-launch-from-double-click UX).
        subprocess.Popen(
            [new_exe_path, "--after-update-folder-swap"],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
            cwd=new_dir,
        )
    except OSError as e:
        # Release the handshake event if Popen failed — the caller won't
        # wait on it, and leaving a kernel handle dangling for the
        # lifetime of the process is sloppy. Best-effort cleanup of the
        # staged .new/ dir too — recovery will catch any failures.
        if _update_child_ready_handle:
            try:
                CloseHandle(_update_child_ready_handle)
            except Exception:
                pass
            _update_child_ready_handle = None
        try:
            shutil.rmtree(new_dir, ignore_errors=True)
        except OSError:
            pass
        return "spawn_failed", (
            f"new bundle staged successfully but spawn failed: {e}. "
            "Restart Display Off manually."
        )
    return "relaunched", None


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
    Win10/11 builds where dwmapi.dll or `DwmSetWindowAttribute` is missing
    (v1.7.20: the binding lives at module-level — `DwmSetWindowAttribute`
    is `None` on those builds and the early return catches it cleanly)."""
    if sys.platform != "win32" or DwmSetWindowAttribute is None:
        return
    try:
        # update() forces window creation if it hasn't fully realized yet —
        # winfo_id is only valid post-realize.
        root.update_idletasks()
        hwnd_inner = root.winfo_id()
        # The actual top-level HWND is the inner widget's parent in Tk.
        # GetParent goes through the bound-name block (argtypes/restype set)
        # rather than ctypes.windll.* which defaults to c_int and truncates
        # HWNDs above 2GB on 64-bit. Bound name per workspace constraint:
        # "Never call ctypes.windll.* directly outside the bindings block".
        hwnd = GetParent(hwnd_inner) or hwnd_inner
        # DWMWA_USE_IMMERSIVE_DARK_MODE: 20 on Win10 2004+ / Win11.
        # Earlier Win10 builds (1903–1909) used 19 — try 20 first, fall back
        # to 19 only if 20 returns nonzero (failure).
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        value = ctypes.c_int(1)
        result = DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(value), ctypes.sizeof(value))
        if result != 0:
            # Try the legacy attribute index used on early Win10 1903-1909.
            DwmSetWindowAttribute(hwnd, 19,
                                  ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError) as e:
        log.warning("Could not apply dark title bar: %s", e)


_THEMED_DIALOG_KIND_GLYPHS = {
    "info":    "ℹ︎ ",   # ℹ︎  variation selector to suppress emoji-style fallback
    "warning": "⚠︎ ",   # ⚠︎
    "error":   "❌ ",          # ❌ — only emoji-style for "error", since the cross
                                   # is visually distinct enough without VS15
    "none":    "",
}


def _themed_dialog(parent, title, message, buttons=("OK",), default_idx=0,
                   kind="info"):
    """Dark-themed modal replacement for `tkinter.messagebox.*`.

    `tkinter.messagebox` uses the native Win32 MessageBox primitive, which
    paints stock light-mode chrome regardless of the app's theme — produces
    a jarring white flash next to our dark Settings / About windows. This
    helper builds an equivalent dialog as a `tk.Toplevel` so the same dark
    palette + DWM titlebar trick applies.

    `buttons`: tuple of button labels. `default_idx`: which one fires on
    Enter and gets initial keyboard focus. Returns the clicked label, or
    `None` if the user closed via the X / Esc. For yes/no usage, default
    to "No" (`default_idx=1`) so Enter is a safe non-action.

    `kind`: visual severity hint, one of {"info", "warning", "error",
    "none"}. Prepends a Unicode glyph (ℹ︎ / ⚠︎ / ❌) to the body so users
    can tell at a glance whether the dialog is informational ("update
    available"), a soft warning ("autostart toggle failed but settings
    were saved"), or a hard error ("could not save settings"). Default
    "info" preserves the previous look-and-feel for callers that don't
    care about severity.
    """
    import tkinter as tk
    # Defensive: silently rendering no glyph for a typo'd kind (e.g. "warn"
    # vs "warning", "err" vs "error") hides bugs in call sites. Log + coerce
    # to "info" so the caller still sees a glyph and a human can chase the
    # typo via the log. Surfaced by v1.7.8 T2 Sonnet+Opus verification.
    if kind not in _THEMED_DIALOG_KIND_GLYPHS:
        log.debug("_themed_dialog: unknown kind=%r, coercing to 'info'", kind)
        kind = "info"
    glyph = _THEMED_DIALOG_KIND_GLYPHS[kind]
    display_message = f"{glyph}{message}" if glyph else message

    dlg = tk.Toplevel(parent)
    dlg.withdraw()  # build invisibly to avoid light-mode flash before dark titlebar
    dlg.title(title)
    dlg.configure(bg=_THEME_BG)
    dlg.resizable(False, False)
    dlg.transient(parent)
    dlg.attributes("-topmost", True)
    _apply_dark_titlebar(dlg)

    result = [None]  # mutable closure capture; messages-box-style return

    body = tk.Label(dlg, text=display_message, justify="left", wraplength=460,
                    font=("Segoe UI", 10),
                    bg=_THEME_BG, fg=_THEME_FG,
                    padx=20, pady=15)
    body.pack()

    btn_frame = tk.Frame(dlg, bg=_THEME_BG)
    btn_frame.pack(pady=(0, 15))

    def _make_handler(label):
        def _handler():
            result[0] = label
            dlg.destroy()
        return _handler

    btn_widgets = []
    for label in buttons:
        btn = tk.Button(btn_frame, text=label, command=_make_handler(label),
                        font=("Segoe UI", 9), width=10,
                        bg=_THEME_BTN_BG, fg=_THEME_BTN_FG,
                        activebackground=_THEME_BTN_ACTIVE_BG,
                        activeforeground=_THEME_BTN_ACTIVE_FG,
                        relief="flat", borderwidth=1,
                        highlightthickness=1, highlightbackground=_THEME_SEP)
        btn.pack(side="left", padx=5)
        btn_widgets.append(btn)

    if btn_widgets:
        # Defer focus until after deiconify so the highlight ring renders.
        default_btn = btn_widgets[min(default_idx, len(btn_widgets) - 1)]
        dlg.bind("<Return>", lambda _: default_btn.invoke())
    # Escape and the close button both produce result=None (the
    # "dismissed without choosing" case). For yes/no dialogs callers check
    # `== "Yes"`, so None correctly maps to "user said No / closed".
    dlg.bind("<Escape>", lambda _: dlg.destroy())
    dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)

    # Center on parent (or screen if no parent geometry) before the alpha-mask
    # deiconify pattern (matches Settings + About).
    dlg.update_idletasks()
    # v1.7.16 defense: ensure the dialog is at least wide enough for the
    # button row. v1.7.13-v1.7.15 used wraplength=460 on the body Label,
    # which caps the body's natural width — but a body whose actual text
    # wraps narrower than the button row would let the dialog inherit
    # that narrower width and clip long button labels. (Live update flow
    # in v1.7.15 surfaced this: a 3-button "Install now / Open releases
    # page / Cancel" row clipped the middle button.) Compute the button
    # row's required width and floor the dialog's width to it + chrome
    # margin. winfo_reqwidth includes Tk's padding from pack(padx=...).
    btn_row_w = btn_frame.winfo_reqwidth()
    body_w = body.winfo_reqwidth()
    # v1.7.17: DPI-relative chrome margin. winfo_pixels("0.4i") returns
    # ~38 px at 100% DPI (close to the prior hardcoded 40) and scales
    # correctly at 125%/150%/175%/200% so the button row's right edge isn't
    # clipped under high-DPI scaling. 0.4" rather than 0.3" so the 100%
    # case stays at ~38 px (parity with prior behavior), preserving the
    # v1.7.16 fix that closed the 3-button-row clip without regressing
    # standard-DPI users who had been fine with 40.
    chrome_margin = dlg.winfo_pixels("0.4i")
    min_dialog_w = max(body_w, btn_row_w + chrome_margin)
    w, h = max(dlg.winfo_reqwidth(), min_dialog_w), dlg.winfo_reqheight()
    try:
        px = parent.winfo_rootx() + max((parent.winfo_width() - w) // 2, 0)
        py = parent.winfo_rooty() + max((parent.winfo_height() - h) // 2, 0)
    except (AttributeError, tk.TclError):
        px = (dlg.winfo_screenwidth() - w) // 2
        py = (dlg.winfo_screenheight() - h) // 2
    dlg.geometry(f"{w}x{h}+{px}+{py}")
    # v1.7.20: sticky minimum size. Tk may re-solve geometry on font-cache
    # refresh, DPI change, or grab-set side effects — without minsize, the
    # body Label's wraplength becomes the floor and the button row clips
    # again (same failure mode v1.7.16 fixed once at create-time, but the
    # one-shot fix doesn't survive a re-solve). minsize is the durable
    # form of the same constraint.
    dlg.minsize(w, h)

    dlg.attributes("-alpha", 0)
    dlg.deiconify()
    dlg.update()
    _apply_dark_titlebar(dlg)  # re-assert after deiconify (Win11 repaint quirk)
    dlg.update()
    dlg.attributes("-alpha", 1)

    if btn_widgets:
        btn_widgets[min(default_idx, len(btn_widgets) - 1)].focus_set()

    dlg.grab_set()
    parent.wait_window(dlg)
    return result[0]


# ── UI helpers ─────────────────────────────────────────────────────────────

def _set_dpi_awareness():
    """Best-effort: declare per-monitor V2 DPI awareness for crisp Tk dialogs.

    Cascades V2 → Per-Monitor → System Aware → silent fallback so we cope
    with older Win10 builds that lack the newer entry points.

    v1.7.13: argtypes/restype declarations now live in the module-level
    bindings block (above) — `_SetProcessDpiAwarenessContext`,
    `_SetProcessDpiAwareness`, `_SetProcessDPIAware`. Each is None when the
    corresponding entry point is missing from the running Windows build
    (e.g. SetProcessDpiAwarenessContext is None on pre-Win10-1607).
    Previously this function mutated `fn.argtypes`/`fn.restype` on the
    shared function-object attrs every invocation — idempotent in practice
    but a convention violation flagged by T2 Opus + T3 Sonnet round 5.
    """
    if sys.platform != "win32":
        return
    # Tier 1 — Win10 1607+ per-monitor V2.
    if _SetProcessDpiAwarenessContext is not None:
        try:
            # DPI_AWARENESS_CONTEXT is a pseudo-handle (pointer-sized);
            # pass via c_void_p so -4 sign-extends correctly on 64-bit Windows.
            _SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            return
        except OSError:
            pass
    # Tier 2 — Win8.1+ per-monitor (no V2).
    if _SetProcessDpiAwareness is not None:
        try:
            _SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
            return
        except OSError:
            pass
    # Tier 3 — Vista+ system-aware (every supported Windows).
    if _SetProcessDPIAware is not None:
        try:
            _SetProcessDPIAware()
        except OSError:
            pass


def _create_icon_image():
    """Fallback icon used only when displayoff.ico is missing (e.g. bare clone).

    Mirrors the 64px design baked into displayoff.ico (cyan-rimmed rounded square,
    bright monitor outline, gold crescent moon) so bare clones don't look
    second-class. If you redesign the .ico, keep this in sync — same palette,
    same proportions — so users never see two different icons.

    The moon-bite ellipse uses DARK_BG to carve the crescent out of the gold
    disc; this works because the icon's interior fill is also DARK_BG, so the
    carved pixels match the surrounding background pixel-for-pixel. If you ever
    introduce a different fill inside the monitor frame, the bite will become
    visible — keep DARK_BG load-bearing for both surfaces or restructure the
    carve to use a clipping mask.
    """
    from PIL import Image, ImageDraw

    DARK_BG    = (18, 24, 40, 255)
    RIM        = (130, 200, 255, 255)
    MONITOR_FG = (235, 240, 250, 255)
    MOON_GOLD  = (255, 210, 95, 255)

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded-square silhouette with a bright cyan rim. PIL added
    # rounded_rectangle in 8.2 (2021-04); bare clones on older PIL fall
    # through to a plain rectangle so the tray still starts.
    try:
        draw.rounded_rectangle([1, 1, size - 2, size - 2], radius=9,
                               fill=DARK_BG, outline=RIM, width=3)
        draw.rounded_rectangle([14, 18, 50, 42], radius=3,
                               outline=MONITOR_FG, width=2)
        draw.rounded_rectangle([22, 47, 42, 50], radius=1, fill=MONITOR_FG)
    except AttributeError:
        log.warning("Pillow < 8.2 detected (no rounded_rectangle) — using square fallback. "
                    "Upgrade Pillow for the rounded design: pip install -U Pillow")
        draw.rectangle([1, 1, size - 2, size - 2], fill=DARK_BG, outline=RIM, width=3)
        draw.rectangle([14, 18, 50, 42], outline=MONITOR_FG, width=2)
        draw.rectangle([22, 47, 42, 50], fill=MONITOR_FG)

    # Stand neck (works on any PIL version)
    draw.rectangle([29, 42, 35, 47], fill=MONITOR_FG)

    # Gold crescent moon — see docstring re: DARK_BG color invariant.
    moon_r = 7
    cx, cy = 30, 29
    draw.ellipse([cx - moon_r, cy - moon_r, cx + moon_r, cy + moon_r],
                 fill=MOON_GOLD)
    draw.ellipse([cx - moon_r + 4, cy - moon_r - 2,
                  cx + moon_r + 4, cy + moon_r - 2], fill=DARK_BG)

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


# ── Hotkey safety guard ───────────────────────────────────────────────────
# OS-reserved combos are keyed by "modifier+modifier+key" with modifiers
# sorted alphabetically (matches the canonical form produced below). Single
# modifier-app conflicts only need one entry each.

_RESERVED_HOTKEYS = {
    "alt+f4": "Alt+F4 closes the active window",
    "alt+tab": "Alt+Tab switches between windows",
    "alt+space": "Alt+Space opens the active window's system menu",
    "alt+esc": "Alt+Esc cycles windows in z-order",
    "alt+escape": "Alt+Esc cycles windows in z-order",
    "ctrl+esc": "Ctrl+Esc opens the Start menu",
    "ctrl+escape": "Ctrl+Esc opens the Start menu",
    "alt+ctrl+del": "Ctrl+Alt+Del is the secure-attention sequence",
    "alt+ctrl+delete": "Ctrl+Alt+Del is the secure-attention sequence",
    "alt+ctrl+esc": "Ctrl+Alt+Esc cycles windows",
    "alt+ctrl+escape": "Ctrl+Alt+Esc cycles windows",
    "ctrl+shift+esc": "Ctrl+Shift+Esc opens Task Manager",
    "ctrl+shift+escape": "Ctrl+Shift+Esc opens Task Manager",
}

# Single-Ctrl-modifier combos that would clobber a near-universal app shortcut.
# These warn-but-allow rather than hard-block — some users genuinely don't
# care about Ctrl+P (Print) and want it as their blank-displays hotkey.
_COMMON_APP_HOTKEYS = {
    "ctrl+c": "Copy",
    "ctrl+v": "Paste",
    "ctrl+x": "Cut",
    "ctrl+z": "Undo",
    "ctrl+y": "Redo",
    "ctrl+a": "Select All",
    "ctrl+s": "Save",
    "ctrl+p": "Print",
    "ctrl+f": "Find",
    "ctrl+w": "Close tab/document",
    "ctrl+t": "New tab",
    "ctrl+n": "New",
    "ctrl+o": "Open",
    "ctrl+q": "Quit (some apps)",
}


def _validate_hotkey_safety(captured):
    """Check whether `captured` is a safe choice for a global hotkey.

    Returns:
      None            — safe, register it as-is
      ("block", msg)  — refuse and show msg as an error
      ("warn",  msg)  — show msg as a yes/no confirm; proceed only if user OKs

    Rules:
      1. A non-modifier key is required.
      2. At least one of Ctrl/Alt must be in the modifier set — Shift alone
         with a letter just types uppercase, and a bare key (e.g. F12 / 'a')
         would intercept that key system-wide so the user could never type
         the letter normally or use the F-key in any other app.
      3. OS-reserved combos (Alt+Tab, Alt+F4, Ctrl+Esc, etc.) are blocked
         outright — Windows gets them before pynput, so registering them
         silently fails and the user is left wondering why nothing happens.
      4. Common-app shortcuts (Ctrl+C/V/S/Z/...) warn but allow.
    """
    mods = set(captured.get("modifiers") or [])
    key = (captured.get("key") or "").lower()

    if not key:
        return ("block", "Hotkey must include at least one non-modifier key.")

    if not (mods & {"ctrl", "alt"}):
        return (
            "block",
            "Hotkey must include Ctrl or Alt.\n\n"
            "Without a Ctrl or Alt modifier the hotkey would intercept every "
            "press of that key system-wide — you wouldn't be able to type "
            "the letter normally or use the F-key in any other app.",
        )

    combo = "+".join(sorted(mods) + [key])
    pretty = hotkey_display_name({"hotkey": captured})

    if combo in _RESERVED_HOTKEYS:
        return (
            "block",
            f"{pretty} is reserved by Windows — {_RESERVED_HOTKEYS[combo]}, "
            f"so the OS would intercept it before Display Off ever sees it. "
            f"Pick a different combo.",
        )

    if combo in _COMMON_APP_HOTKEYS:
        return (
            "warn",
            f"{pretty} is widely used as the \"{_COMMON_APP_HOTKEYS[combo]}\" "
            f"shortcut in most apps. Display Off would intercept it system-"
            f"wide, so {_COMMON_APP_HOTKEYS[combo]} would stop working in "
            f"every other app.\n\nUse this hotkey anyway?",
        )

    return None


# ── Settings dialog ────────────────────────────────────────────────────────

# Row layout (lets you add new rows without re-threading indices through the impl):
#   row 0 — header label                row 4 — lock-on-blank checkbox
#   row 1 — separator                   row 5 — autostart checkbox
#   row 2 — hotkey label + field        row 6 — warn-on-blocked-blank checkbox
#   row 3 — hotkey hint                 row 7 — auto-blank-when-idle spinbox
#                                       row 8 — footer (GitHub / Apply / Save / Cancel)
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

        # pynput may legitimately fail to import on a broken / partial install
        # (Pillow/pynput pulled but the platform-specific listener .pyd is
        # missing or has been quarantined by AV). Without this guard we'd
        # leave recording["active"]=True and the hotkey field locked in the
        # "Press your hotkey..." state for the rest of the session — companion
        # to the TclError variant fixed in v1.7.6.
        try:
            from pynput import keyboard as kb
        except ImportError as e:
            log.warning("pynput import failed during hotkey capture (%s) — aborting capture", e)
            display_var.set(hotkey_display_name(cfg))
            hotkey_display.config(bg=_THEME_BG_SUNKEN, relief="sunken")
            recording["active"] = False
            try:
                _themed_dialog(
                    root,
                    "Display Off",
                    "Could not start hotkey capture — pynput failed to load.\n\n"
                    "Reinstall the dependency:\n"
                    "    pip install --upgrade pynput\n\n"
                    f"Details: {e}",
                    kind="error",
                )
            except Exception:
                pass
            return

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

        try:
            listener = kb.Listener(on_press=on_press, on_release=on_release)
            listener.daemon = True
            listener.start()
        except Exception as e:
            # Listener init can fail under broken backends (X11-less containers,
            # WSLg, Wayland on Linux ports) even when the import itself succeeds.
            log.warning("pynput listener start failed during hotkey capture (%s) — aborting capture", e)
            display_var.set(hotkey_display_name(cfg))
            hotkey_display.config(bg=_THEME_BG_SUNKEN, relief="sunken")
            recording["active"] = False
            return

        def poll_capture():
            # If the Settings dialog is destroyed while a capture is in
            # flight (user clicks Cancel mid-recording), `root.after` raises
            # TclError and Tk's report_callback_exception logs it — but the
            # cleanup at the bottom of this function never runs, leaving
            # `recording["active"] = True` AND the pynput listener alive.
            # Catch the TclError, stop the listener, and reset state so the
            # listener doesn't leak across dialog sessions.
            try:
                if listener.running:
                    root.after(50, poll_capture)
                    return
            except tk.TclError:
                listener.stop()
                recording["active"] = False
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


def _build_options_section(root, row, pad, lock_var, autostart_var, idle_var, warn_var):
    """Lock-on-blank + autostart + blocked-blank-warn checkboxes + idle-trigger spinbox.
    Spans (row, row+3).

    To add a fifth option, give it the next row index and bump the footer
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

    warn_chk = tk.Checkbutton(root,
                              text="Warn when something is keeping the display awake",
                              variable=warn_var, **_chk_kw)
    warn_chk.grid(row=row + 2, column=0, columnspan=3, sticky="w", padx=pad, pady=2)

    idle_frame = tk.Frame(root, bg=_THEME_BG)
    idle_frame.grid(row=row + 3, column=0, columnspan=3, sticky="w", padx=pad, pady=(6, 2))
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
              command=lambda: _open_url(_GITHUB_REPO_URL),
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
    warn_var = tk.BooleanVar(value=bool(cfg.get("warn_on_blocked_blank", True)))

    # Build sections — row indices live here, so adding a row is a one-line change.
    _build_header(root, row=0, pad=PAD)
    _build_hotkey_row(root, row=2, pad=PAD, cfg=cfg, captured=captured, recording=recording)
    _build_options_section(root, row=4, pad=PAD,
                           lock_var=lock_var, autostart_var=autostart_var,
                           idle_var=idle_var, warn_var=warn_var)

    root.columnconfigure(1, weight=1)

    def _apply_settings():
        """Validate + persist + apply. Returns True on success, False if dialog should stay open."""
        safety = _validate_hotkey_safety(captured)
        if safety is not None:
            severity, msg = safety
            if severity == "block":
                _themed_dialog(root, "Display Off", msg, kind="error")
                return False
            # severity == "warn" — give the user a chance to proceed anyway.
            if _themed_dialog(root, "Display Off", msg, ("Yes", "No"),
                              default_idx=1, kind="warning") != "Yes":
                return False
        try:
            idle_minutes = max(0, int(idle_var.get() or 0))
        except (TypeError, ValueError):
            _themed_dialog(root, "Display Off",
                           "Idle-blank minutes must be a non-negative number.",
                           kind="warning")
            return False
        cfg["hotkey"] = dict(captured)
        cfg["lock_on_off"] = bool(lock_var.get())
        cfg["idle_blank_minutes"] = idle_minutes
        cfg["warn_on_blocked_blank"] = bool(warn_var.get())
        try:
            save_config(cfg)
        except OSError as e:
            _themed_dialog(root, "Display Off",
                           f"Could not save settings:\n{e}",
                           kind="error")
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
            _themed_dialog(root, "Display Off",
                           f"Autostart toggle failed:\n{type(e).__name__}: {e}\n\n"
                           f"Your other settings were saved. Adjust and click Save "
                           f"again to retry — the dialog stays open.",
                           kind="warning")
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
        # Pass the cached autostart state captured at dialog open so About
        # doesn't re-spawn the 30-second PowerShell subprocess on the Tk
        # event-loop thread (was visibly hanging the About dialog on
        # cold-boot Win11 with AV scanning).
        _show_about(root, autostart_enabled_value=autostart_state["enabled"])

    def on_updates_btn():
        _run_update_check(root)

    # Footer row bumped 7→8 in v1.7.21 because _build_options_section grew
    # from 3 rows (lock/autostart/idle) to 4 (lock/autostart/warn/idle).
    _build_footer(root, row=8, pad=PAD,
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

    # Alpha trick to mask the deiconify → first-paint flash. Without this,
    # Win11 briefly paints the window with default-light chrome on first show
    # of a previously-withdrawn window before our DwmSetWindowAttribute call
    # re-paints it dark — visible as a quick flash. By setting alpha=0 first
    # and only restoring to 1 AFTER the dark titlebar has been re-asserted,
    # all of that chrome-repaint churn happens while the window is invisible.
    root.attributes("-alpha", 0.0)
    root.deiconify()
    # Flush Tk's event queue so the window-shown notification reaches DWM
    # before we re-apply the dark titlebar. Without this update(), some Win11
    # builds queue the default-chrome paint AFTER our attribute write and
    # the flash returns.
    root.update()
    _apply_dark_titlebar(root)
    root.update()
    root.attributes("-alpha", 1.0)

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
    if _uxtheme is None:
        log.warning("Could not enable dark-mode menus: uxtheme.dll not loadable")
        return
    try:
        # v1.7.13: use the bound `_uxtheme` from the module-level bindings
        # block instead of raw `ctypes.windll.uxtheme`. Ordinal indexing
        # (`_uxtheme[135]`) works identically on bound and raw WinDLL
        # objects, but the bound name carries use_last_error=True so any
        # future GetLastError consultation reads this DLL's thread-local
        # rather than picking up a stale value from an unrelated syscall.
        # SetPreferredAppMode (ordinal 135) and FlushMenuThemes (ordinal
        # 136) are name-less exports — Microsoft documents the behavior but
        # not the symbols; ordinal lookup is the only access path.
        SetPreferredAppMode = _uxtheme[135]
        SetPreferredAppMode.argtypes = [ctypes.c_int]
        SetPreferredAppMode.restype = ctypes.c_int
        FlushMenuThemes = _uxtheme[136]
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

def _show_about(parent_root, autostart_enabled_value=None):
    """Open a modeless About window as a child of `parent_root`.

    `autostart_enabled_value` is an optional pre-computed value from the
    caller (e.g., the Settings dialog already caches it). When provided,
    we skip the PowerShell subprocess `autostart_enabled()` would otherwise
    spawn — that subprocess has a 30s timeout and on cold-boot Win11 with
    AV scanning can take 10-30s, blocking the Tk event loop and making
    About appear to hang. Pass the cached value to keep About snappy.

    Modeless = no `grab_set`, no `transient`. The user can click away to
    other windows while About stays visible, and can dismiss it whenever —
    same affordance as a typical About dialog in Office, VS Code, etc."""
    try:
        import tkinter as tk
        cfg = load_config()
        idle_min = int(cfg.get("idle_blank_minutes", 0) or 0)
        idle_line = f"{idle_min} min" if idle_min > 0 else "off"

        about = tk.Toplevel(parent_root)
        # Hide IMMEDIATELY so the user never sees default light-mode Tk chrome
        # flash before the dark theme + DWM dark title bar apply, and so the
        # window doesn't first paint at (0,0) before jumping to the centered
        # position computed below. Re-shown via deiconify() at the end.
        about.withdraw()
        about.title("About Display Off")
        about.configure(bg=_THEME_BG)
        about.resizable(False, False)
        # `-topmost True` is REQUIRED here because the parent Settings window
        # already has -topmost True. Without matching it, About opens at
        # normal Z-order, is immediately covered by the always-on-top Settings
        # window, and from the user's perspective "the About button does
        # nothing." Both windows now stay above other apps; the younger
        # (About) renders above the elder (Settings). Closing About leaves
        # Settings on top, as expected. Set early so deiconify happens in
        # the correct Z-order rather than needing a post-show raise.
        about.attributes("-topmost", True)
        _apply_dark_titlebar(about)

        body_text = (
            f"Display Off v{__version__}\n\n"
            "Tiny tray utility to power off all monitors\n"
            "without putting the PC to sleep.\n\n"
            f"Hotkey: {hotkey_display_name(cfg)}\n"
            f"Lock on blank: {'on' if cfg.get('lock_on_off') else 'off'}\n"
            f"Auto-blank when idle: {idle_line}\n"
            f"Autostart: {'on' if (autostart_enabled_value if autostart_enabled_value is not None else autostart_enabled()) else 'off'}"
        )
        body = tk.Label(about, text=body_text, justify="left",
                        font=("Segoe UI", 10),
                        bg=_THEME_BG, fg=_THEME_FG,
                        padx=20, pady=15)
        body.pack()

        # Clickable GitHub link styled as a label with hand cursor.
        # Note: tuple-form padding (e.g. (0, 10)) is only valid on the geometry
        # manager (pack/grid/place), NOT on the widget constructor — Tk parses
        # the constructor pad-* values as a single screen-distance and raises
        # TclError: bad screen distance "0 10" otherwise. Keep the asymmetric
        # bottom padding on the pack() call below.
        link = tk.Label(about, text="https://github.com/itsnateai/displayoff",
                        font=("Segoe UI", 9, "underline"),
                        bg=_THEME_BG, fg="#4ec9ff", cursor="hand2")
        link.pack(padx=20, pady=(0, 10))
        link.bind("<Button-1>", lambda _: _open_url(_GITHUB_REPO_URL))

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

        # Center on screen before showing — sizing all widgets first means
        # winfo_reqwidth/height returns final dimensions, so the window pops
        # in at its final position rather than at (0,0) then jumping.
        about.update_idletasks()
        w, h = about.winfo_reqwidth(), about.winfo_reqheight()
        x = (about.winfo_screenwidth() - w) // 2
        y = (about.winfo_screenheight() - h) // 2
        about.geometry(f"{w}x{h}+{x}+{y}")

        # Alpha=0 mask while deiconify + dark-titlebar reapply happens — see
        # the matching block in `_open_settings_impl` for the full rationale.
        # Without this, Win11 briefly paints default-light chrome between
        # deiconify and our DWM dark-mode attribute write, visible as a flash.
        about.attributes("-alpha", 0.0)
        about.deiconify()
        about.update()
        _apply_dark_titlebar(about)
        about.update()
        about.attributes("-alpha", 1.0)
        close_btn.focus_set()
    except Exception as e:
        log.exception("About dialog crashed: %s", e)


def _open_url(url):
    """`webbrowser.open` returns False if no handler is registered (locked-down
    profiles, kiosk mode, broken HKCR\\http association). Log it so the user
    isn't left wondering why the GitHub button "did nothing"."""
    if not webbrowser.open(url):
        log.warning("webbrowser.open(%s) returned False — no URL handler registered.", url)


def _find_release_zip_asset(assets):
    """Return (zip_filename, zip_url) for the first `.zip` asset in `assets`,
    or (None, None) if no zip is present. v1.7.22+ asset names are
    version-stamped (`displayoff-v1.7.22.zip`, `displayoff-v1.7.23.zip`,
    ...), so we match by suffix rather than exact filename. If multiple
    zips are uploaded to one release (unusual), prefer the first one
    starting with `displayoff-` to avoid grabbing a stray attachment."""
    candidates = [
        (name, url) for name, url in assets.items()
        if isinstance(name, str) and name.lower().endswith(_UPDATE_ZIP_SUFFIX)
    ]
    if not candidates:
        return None, None
    # Prefer `displayoff-*.zip` if present; otherwise take the first.
    preferred = [c for c in candidates if c[0].lower().startswith("displayoff-")]
    return (preferred or candidates)[0]


def _can_use_rename_dance(assets):
    """True when the v1.7.22+ folder-swap update flow is viable for the
    current launch context. Requires (1) running as the frozen standalone
    bundle, (2) a `*.zip` asset AND `SHA256SUMS.txt` published on the
    release, and (3) both URLs on the hardcoded allowlist. Any miss falls
    back to the v1.7.12 "open release page" flow.
    """
    if not _is_frozen() or not _EXE_PATH:
        return False
    _zip_name, zip_url = _find_release_zip_asset(assets)
    manifest_url = assets.get(_UPDATE_MANIFEST_NAME)
    if not zip_url or not manifest_url:
        return False
    return _download_url_allowed(zip_url) and _download_url_allowed(manifest_url)


def _run_rename_dance_flow(parent_root, assets, latest):
    """Run the rename-dance in a background thread. On success, terminate
    the current process via os._exit(0) after the spawned child .exe has
    had a moment to come up. On failure, marshal back to the Tk thread and
    show a themed error dialog with a "Open releases page" fallback.

    parent_root is the Settings dialog's Tk root — used as the marshalling
    target for after() so the error dialog renders correctly as a child of
    the open Settings window.
    """

    import tkinter as _tk_local

    def _show_error(title, detail):
        try:
            btn = _themed_dialog(
                parent_root,
                "Display Off",
                f"{title}\n\n{detail}\n\n"
                "You can open the release page manually to download v"
                f"{latest} yourself.",
                buttons=("Releases page", "Cancel"),
                default_idx=0,
                kind="error",
            )
            if btn == "Releases page":
                _open_url(_GITHUB_RELEASES_URL)
        except _tk_local.TclError:
            log.error("Update error: %s — %s (parent window closed before "
                      "dialog could render)", title, detail)
        except Exception as e:
            log.exception("Update error dialog crashed: %s", e)

    def _marshal_error(title, detail):
        try:
            parent_root.after(0, lambda: _show_error(title, detail))
        except _tk_local.TclError:
            log.error("Update error (Tk gone): %s — %s", title, detail)
        except Exception as e:
            log.exception("Failed to marshal update error: %s", e)

    def _worker():
        # v1.7.20: `_update_child_ready_handle` is mutated below (zeroed
        # after CloseHandle on the wait path — see N1 verifier finding).
        # Without `global` the assignment would create a local var that
        # shadows the module-level reads above, defeating the cleanup.
        global _update_child_ready_handle
        manifest_url = assets.get(_UPDATE_MANIFEST_NAME)
        zip_filename, zip_url = _find_release_zip_asset(assets)
        log.info("Update dance starting: latest=%s zip=%s zip_url=%s manifest_url=%s",
                 latest, zip_filename, zip_url, manifest_url)

        if not zip_filename or not zip_url:
            _marshal_error("Update failed",
                           "No .zip asset on the latest release — the "
                           "folder-swap updater needs a versioned zip "
                           "(displayoff-vX.Y.Z.zip). The release may be "
                           "mid-upload or assets-less.")
            return

        sha, manifest_err = _fetch_release_manifest_sha256(
            manifest_url, zip_filename
        )
        if manifest_err:
            _marshal_error("Update failed",
                           f"Could not fetch SHA256 manifest: {manifest_err}")
            return

        status, detail = _execute_rename_dance(zip_url, sha, latest, zip_filename)
        if status == "relaunched":
            log.info("Folder-swap dance complete; exiting current process so the "
                     "spawned child (--after-update-folder-swap) can take over.")
            # v1.7.13 verifier round (T3-Sonnet + T3-Opus convergent):
            # logging.shutdown() flushes the RotatingFileHandler's pending
            # writes so the dance's last 3-4 log lines ("downloading",
            # "renaming", "spawning child") survive into displayoff.log.
            # Without this, os._exit skips Python's normal teardown and the
            # buffered writes are lost — the very log lines that matter
            # most for diagnosing an update problem evaporate. Pystray icon
            # teardown still gets skipped (icon.run lives on the main
            # thread, can't be safely stopped from this daemon worker),
            # but the OS reclaims the tray slot via Shell_NotifyIcon when
            # the process dies, and the spawned child registers a fresh
            # icon a few hundred ms later.
            try:
                logging.shutdown()
            except Exception:
                # logging.shutdown() catches its own internal errors per
                # Python docs, but be defensive about a corrupted logging
                # state mid-dance never blocking the relaunch.
                pass
            # v1.7.20: wait for the child to signal "I'm alive" via the
            # named event the parent created in `_execute_rename_dance`.
            # Previously we slept a fixed 0.3s — too short on slow systems
            # (the child's Python interpreter is still bootstrapping, parent
            # exits, child loses the mutex race and exits silently → "no
            # tray after update" with no log entry). 5s is a generous
            # ceiling for the Nuitka bootstrap + interpreter init; if the
            # child genuinely never starts we fall through anyway.
            # Fallback to the legacy 0.3s sleep when CreateEventW failed
            # (no handle was stashed) — at least we don't regress.
            if _update_child_ready_handle is not None:
                wait_result = WaitForSingleObject(
                    _update_child_ready_handle, 5000
                )
                if wait_result == _WAIT_OBJECT_0:
                    log.info("Update dance: child signaled ready in <5s; "
                             "parent exiting now.")
                elif wait_result == _WAIT_TIMEOUT:
                    log.warning(
                        "Update dance: child did not signal ready within "
                        "5s — exiting anyway. The tray may briefly show no "
                        "icon while the child completes startup; this "
                        "should self-heal once the child registers."
                    )
                else:
                    log.warning(
                        "Update dance: WaitForSingleObject returned "
                        "unexpected code 0x%X; exiting anyway.", wait_result
                    )
                try:
                    CloseHandle(_update_child_ready_handle)
                except Exception:
                    pass
                # v1.7.20 verifier T1-Sonnet N1: zero the global so a
                # hypothetical second update attempt in this session (if
                # os._exit didn't follow — defensive, current code does
                # exit) starts with a clean slot. Symmetric with the
                # spawn-failure cleanup path in `_execute_rename_dance`.
                # In practice the `os._exit(0)` below makes this cosmetic,
                # but consistency with the H1 pre-create guard means a
                # future refactor (e.g., replacing os._exit with a clean
                # tray shutdown) won't inherit a stale handle.
                _update_child_ready_handle = None
            else:
                # Brief settle so the OS has a chance to start the child
                # process before the current one releases its tray icon.
                # 300ms is short enough to feel instant.
                time.sleep(0.3)
            # os._exit skips Python's interpreter shutdown — necessary
            # because pystray's icon.run() owns the main thread and a clean
            # exit from a daemon thread isn't straightforward. The spawned
            # child process is already running; this process is dead weight.
            os._exit(0)

        # Failure path
        log.warning("Folder-swap dance failed: status=%s detail=%s", status, detail)
        title = {
            "not_frozen":      "Update not available in source mode",
            "download_failed": "Update download failed",
            "sha256_mismatch": "Update download corrupted",
            "extract_failed":  "Update zip could not be extracted",
            "rename_failed":   "Update could not be staged",
            "spawn_failed":    "Update staged but relaunch failed",
        }.get(status, f"Update failed ({status})")
        _marshal_error(title, detail or "(no detail)")

    threading.Thread(
        target=_worker, daemon=True, name="displayoff-update-dance"
    ).start()


def _run_update_check(parent_root):
    """Hit the GitHub releases API and show a result dialog as a child of
    `parent_root`. Network call runs in a daemon thread so the Tk event loop
    stays responsive; result is marshalled back via `parent_root.after`."""

    def _show_result(has_update, latest, html_url, err, assets):
        import tkinter as _tk_local
        try:
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
                        f"  • The repository ({_GITHUB_REPO}) is private and the\n"
                        "    update check has no authentication token.\n"
                        "  • The repository exists but has no published releases yet —\n"
                        "    update-check needs at least one tagged release to compare against.\n"
                        "  • The repository or owner name has changed.\n\n"
                        "Manage releases at:\n"
                        f"{_GITHUB_RELEASES_URL}\n\n"
                        f"Raw error: {err_text}"
                    )
                elif "403" in err_text or "rate limit" in err_text.lower():
                    msg = (
                        "Could not check for updates — GitHub API rate limit reached.\n\n"
                        "GitHub limits unauthenticated requests to 60 per hour per IP.\n"
                        "Other tools on this network (gh CLI, GitHub Desktop, VS Code\n"
                        "extensions, etc.) share the same quota.\n\n"
                        "Try again in an hour, or check releases directly:\n"
                        f"{_GITHUB_RELEASES_URL}"
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
                _themed_dialog(parent_root, "Display Off", msg, kind="error")
            elif has_update:
                # v1.7.13+ split: when running as the frozen .exe AND the
                # release publishes both displayoff.exe + SHA256SUMS.txt as
                # assets on allowlisted hosts, offer the in-app rename-dance
                # update. Falls back to the v1.7.12 "open release page" flow
                # when running from source, or when the release predates the
                # .exe asset (e.g., v1.7.12 viewed from a v1.7.13 client).
                if _can_use_rename_dance(assets):
                    # v1.7.16: button labels shortened so the three-button
                    # row fits the dialog width at default DPI scaling.
                    # v1.7.13-v1.7.15 used "Open releases page" (18 chars)
                    # which clipped on the live update-flow at 100% scaling
                    # (and worse under 125%+). "Releases page" (13 chars)
                    # renders cleanly. The body prose still refers to it
                    # by the full functional name for accessibility/clarity
                    # — only the button label changed.
                    btn = _themed_dialog(
                        parent_root,
                        "Display Off — Update available",
                        f"A newer version is available.\n\n"
                        f"Current: v{__version__}\n"
                        f"Latest:  v{latest}\n\n"
                        "Install now will download the new build, verify its\n"
                        "SHA256 against the published manifest, replace the\n"
                        "running .exe, and relaunch.\n\n"
                        "Releases page lets you download manually instead.",
                        buttons=("Install now", "Releases page", "Cancel"),
                        default_idx=0,
                        kind="info",
                    )
                    if btn == "Install now":
                        _run_rename_dance_flow(parent_root, assets, latest)
                        # Worker either os._exit's on success or marshals an
                        # error dialog back via parent_root.after — nothing
                        # more to do here.
                        return
                    if btn == "Releases page":
                        if html_url and html_url.startswith("https://github.com/"):
                            _open_url(html_url)
                        else:
                            _open_url(_GITHUB_RELEASES_URL)
                    # btn == "Cancel" (or dialog closed) → no-op
                else:
                    # v1.7.12 flow — source mode or missing .exe asset.
                    if _themed_dialog(
                        parent_root,
                        "Display Off — Update available",
                        f"A newer version is available.\n\n"
                        f"Current: v{__version__}\n"
                        f"Latest:  v{latest}\n\n"
                        "Open the release page in your browser?",
                        buttons=("Yes", "No"),
                        default_idx=0,
                        kind="info",
                    ) == "Yes":
                        # html_url comes from GitHub API response — validate
                        # it before passing to webbrowser.open. A compromised
                        # release or MITM-injected JSON could set html_url
                        # to a `file://` or `javascript:` URI; the OS handler
                        # then opens whatever the attacker wants. Allowlist
                        # https://github.com/ prefix; otherwise fall back to
                        # the hardcoded releases page.
                        if html_url and html_url.startswith("https://github.com/"):
                            _open_url(html_url)
                        else:
                            _open_url(_GITHUB_RELEASES_URL)
            else:
                _themed_dialog(
                    parent_root,
                    "Display Off — Up to date",
                    f"You're on the latest release.\n\n"
                    f"Current: v{__version__}\n"
                    f"Latest:  v{latest}",
                    kind="info",
                )
        except _tk_local.TclError as e:
            # User closed Settings while the API call was in flight — the
            # parent_root is destroyed and any _themed_dialog(parent_root, ...)
            # call raises TclError. Expected, not an error. v1.7.7 logged this
            # at ERROR via log.exception which made the log noisy whenever
            # users closed Settings before the response landed.
            log.debug("Update check dialog skipped (expected when parent window closed mid-request): %s", e)
        except Exception as e:
            log.exception("Update check dialog crashed: %s", e)

    def _worker():
        try:
            result = check_for_updates()
        except Exception as e:
            # v1.7.13: 5-tuple (assets dict added). Empty {} keeps
            # _can_use_rename_dance from claiming the dance is viable.
            result = (False, None, None, e, {})
        # Marshal back to the Tk thread. parent_root.after is thread-safe.
        import tkinter as _tk_local
        try:
            parent_root.after(0, lambda: _show_result(*result))
        except _tk_local.TclError as e:
            # Same "user closed Settings mid-request" race, just from the
            # scheduling side rather than the dialog side. Either path lands
            # on the user closing the parent before we can render.
            log.debug("Update check result not delivered (expected when parent window closed mid-request): %s", e)
        except Exception as e:
            log.exception("Failed to schedule update-check result dialog: %s", e)

    threading.Thread(target=_worker, daemon=True, name="displayoff-update-check").start()


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
        from tray_promoter import capture_baseline, promote_in_background, sweep_stale_entries
        # v1.7.15: clean NotifyIconSettings cruft from prior displayoff.exe
        # builds at different install paths (the rename-dance keeps the same
        # path across upgrades, but a user who moved the .exe between
        # releases — e.g. relocated from a personal folder to Program Files
        # — leaves a stale subkey behind for every prior location). Only
        # invoked under freeze: under .py source the basename is
        # pythonw.exe / python.exe and sweep_stale_entries explicitly
        # no-ops on those names (too broad to scope safely — would match
        # every other Python tray app the user has). Must run BEFORE
        # capture_baseline so removed subkeys aren't in the baseline
        # snapshot.
        if _is_frozen() and _EXE_PATH:
            # Defense-in-depth: sweep_stale_entries wraps all registry I/O
            # in try/except OSError internally and returns 0 on failure,
            # so this catch should never trigger in practice. The narrow
            # outer guard here exists so that a future tray_promoter
            # refactor (e.g. introducing a non-OSError exception type, a
            # Nuitka-frozen import quirk, or a registry-schema change)
            # can never crash the tray-startup path — tray_promoter is UX
            # polish, never a crash surface (line ~3580 comment above).
            try:
                sweep_stale_entries(our_exe_name="displayoff.exe",
                                    current_exe_path=_EXE_PATH)
            except Exception as e:
                log.warning("tray_promoter.sweep_stale_entries raised "
                            "unexpectedly (%s) — skipping sweep, continuing "
                            "with capture_baseline.", e)
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

    def _menu_header_text(item):
        """Dynamic-text callable for the header menu item. Pystray evaluates
        this every time the right-click menu is painted on Windows — which
        gives us our only handle on "right-click happened" since pystray
        doesn't expose a menu-open event. We piggyback the eval and clear
        any half-finished double-click sequence (last_icon_click != 0).

        Why this matters: a user who double-clicks (fires blank), then
        immediately right-clicks to open the menu, then left-clicks twice
        more inside the 500ms double-click window risks the second pair
        being interpreted as a fresh double-click and firing a second blank
        WHILE the context menu is on screen. Resetting the timestamp on
        menu render means the user must start a brand-new click pair
        post-menu before the next blank can fire.

        Pystray left-clicks bypass the menu render path entirely (they go
        straight to the `default=True` hidden item), so this callable does
        NOT interfere with legitimate double-click detection.

        Exception guard: pystray's Win32 backend catches exceptions raised
        from dynamic-property callables and renders an empty label. If we
        let an exception unwind here, the side-effect reset never fires and
        the user sees a broken-looking menu. Wrap the whole body so a
        worst-case bug still returns the header string."""
        try:
            with icon_click_lock:
                if last_icon_click[0] != 0.0:
                    log.info("menu render — resetting pending icon-click state "
                             "(was %.3f, defeats double+right+double race)",
                             last_icon_click[0])
                    last_icon_click[0] = 0.0
        except Exception:
            log.exception("menu header callable raised — ignoring; "
                          "last_icon_click may not have been reset")
        return f"Display Off v{__version__}"

    def on_settings(icon, item):
        if not _claim_dialog():
            return

        def on_saved(new_cfg):
            hotkey_name[0] = hotkey_display_name(new_cfg)
            icon.update_menu()

        threading.Thread(target=_open_settings, args=(icon, on_saved), daemon=True).start()

    def on_quit(icon, item):
        # If a blank fired moments before Quit was clicked (e.g. via the
        # hotkey, double-click, or idle watcher), the worker thread is
        # currently inside `blank_via_idle_path` holding the
        # `_turn_off_lock` and counting down its restore window. Letting
        # icon.run() return immediately would tear down the interpreter
        # mid-restore — the daemon thread gets killed, the powercfg restore
        # never runs, and the user is left with a 1-second VIDEOIDLE
        # timeout until the next launch's sentinel-recovery fires.
        #
        # native_blank registers a per-invocation atexit handler as a
        # belt-and-suspenders restore, but atexit fires *after* main()
        # returns — by then the in-flight thread is already racing the
        # interpreter shutdown. Cleaner to wait briefly for the lock to
        # release (blank finished its own try/finally restore) before
        # exiting. Lock is held for ~5.5s during a native blank, so 6s
        # covers a typical run with a small margin.
        if _turn_off_lock.locked():
            log.info("Quit requested while blank in progress — waiting up to 6s for restore.")
            if _turn_off_lock.acquire(timeout=6.0):
                _turn_off_lock.release()
                log.info("In-flight blank finished restore — proceeding with quit.")
            else:
                log.warning("Blank still in progress after 6s — proceeding with quit; "
                            "native_blank's atexit handler will attempt the restore.")
        icon.stop()

    # Why no clickable "Turn Off Displays" menu item:
    #
    # In v1.6.0+ the blank routes through the Win32 native idle-display-off
    # path (PowerWriteACValueIndex + PowerSetActiveScheme writing a 1-second
    # timeout). On Modern Standby + hybrid-GPU laptop hardware, the
    # menu-item path fired the
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
        # Dynamic text — see _menu_header_text. Side-effect: clears any
        # half-finished icon-click pair on menu render (right-click only,
        # since pystray bypasses the menu paint path for the default-action
        # left-click handler).
        MenuItem(_menu_header_text, None, enabled=False),
        Menu.SEPARATOR,
        MenuItem("Blank displays:", None, enabled=False),
        MenuItem("• Double-click icon", None, enabled=False),
        MenuItem(lambda item: f"• {hotkey_name[0]}", None, enabled=False),
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
    # v1.7.21: stash a module-level ref so turn_off_monitors() can fire the
    # blocked-blank toast from the hotkey/idle paths without threading `icon`
    # through every call site. Set AFTER pystray.Icon(...) returns so the
    # ref is never read pre-construction (helper is None-guarded regardless).
    # Note: assigned BEFORE start_hotkey_listener so there's no race window
    # where a Ctrl+Alt+F12 fires between listener-start and ref-assign.
    global _tray_icon_ref
    _tray_icon_ref = icon

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
    # via the shared tray_promoter module. pystray uses
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
        # v1.7.17: prefer the resolved on-disk .exe under freeze. Under
        # .py source `_EXE_PATH is None` and we fall back to sys.executable
        # (pythonw.exe), which is the correct ExecutablePath for the source
        # mode anyway since pystray uses pythonw to attach the icon.
        promote_in_background(
            exe_path=_EXE_PATH or sys.executable,
            tooltip=tray_tooltip,
            baseline=tray_baseline,
            max_wait_secs=None,
        )

    if first_run:
        # One-time welcome notification + persist defaults so this won't fire again.
        # Don't clobber: check the file again after the notification fires, since the
        # user could have opened Settings and saved their own config in the meantime.
        # v1.7.14: pre-set `_frozen_promoted_pinged` under freeze so the welcome
        # notification's own NIF_INFO balloon doubles as the catalog-forcing ping
        # — without this, a first-run frozen user gets the welcome on launch N
        # and the SEPARATE promotion ping on launch N+1 (two toasts for one
        # install). Surfaced by T3-Opus MEDIUM: the `elif _is_frozen() and ...`
        # branch wouldn't fire on first_run (different elif arm), so the flag
        # stayed False, so launch N+1 re-fired the ping. Pre-setting here closes
        # the gap.
        if _is_frozen():
            cfg["_frozen_promoted_pinged"] = True
        welcome_hotkey = hotkey_name[0]

        def _welcome():
            time.sleep(_TRAY_SETTLE_SECS)  # let the tray icon attach before notifying
            try:
                icon.notify(
                    f"Press {welcome_hotkey} to blank all displays.",
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
    elif _is_frozen() and cfg.get("_frozen_promoted_pinged") is not True:
        # v1.7.13 (initial) + v1.7.14 (hardening): first launch under the
        # frozen .exe build for a user who already has a config from a
        # previous source-mode install. Win11 22H2+ treats the .exe as a
        # brand-new ExecutablePath in NotifyIconSettings (separate from
        # the previous pythonw.exe entry) and defaults it to hidden-in-
        # overflow. Fire a one-shot notification to force Explorer to
        # catalog the icon synchronously — without that catalog,
        # tray_promoter has nothing to set IsPromoted=1 on and the icon
        # stays hidden indefinitely. See _DEFAULT_CONFIG
        # `_frozen_promoted_pinged` comment for the full backstory.
        #
        # v1.7.14 verifier-round hardening (T2 + T3 pair convergent):
        #   - Gate via `is not True` rather than `not cfg.get(...)`: a
        #     hand-edited config with `null` / `0` / `""` was previously
        #     re-firing the toast every launch (truthiness false-match).
        #     Strict identity-check means ONLY a literal Python True
        #     suppresses; everything else re-tries.
        #   - Flag-set moved INSIDE the notify try-block: previously the
        #     flag was set unconditionally after the try, so an exception
        #     during `icon.notify()` (icon not yet registered, Focus
        #     Assist blocking NIF_INFO) burned the one-shot silently and
        #     the icon stayed hidden. Now the flag only flips on
        #     successful notify, so retry happens on next launch.
        #   - Capture `hotkey_name[0]` BEFORE the 1s sleep: hotkey_name is
        #     mutated by Settings → Save (on_saved); without the snapshot,
        #     a user who reconfigured the hotkey during the 1s window
        #     would see a stale label vs the running listener.
        #   - Title is just "Display Off" (no version): consistent with
        #     v1.7.8's UA-header policy of not broadcasting __version__
        #     for fingerprinting reasons. The toast is local but visible
        #     under screen-share.
        #   - Persist via read-modify-write off the on-disk config rather
        #     than overwriting the closure `cfg`: a concurrent
        #     `_apply_settings` Save in the Settings dialog could
        #     otherwise have its just-written changes clobbered by our
        #     stale closure-cfg save.
        ping_hotkey = hotkey_name[0]

        def _frozen_promote_ping():
            # v1.7.15: in-memory dedupe across any future re-invocation
            # within this same process. The persisted `_frozen_promoted_pinged`
            # config flag is the cross-launch gate; this module-level bool
            # is the within-process gate that holds even when the disk
            # write fails (RO-APPDATA, AV lock, locked-down policy).
            #
            # v1.7.17: lock-guarded claim/release pattern (see
            # _try_claim_ping_gate / _release_ping_gate). Claim up front so
            # two simultaneous spawns of this thread (today there's only
            # one, but the rule about no-bare-bool-shared-state applies
            # regardless) can't both pass the gate and both fire the toast.
            # Release on notify failure so next launch retries.
            if not _try_claim_ping_gate():
                log.info("Frozen-first-launch promotion ping already fired this "
                         "process — skipping (in-memory dedupe).")
                return

            # _TRAY_SETTLE_SECS settle matches the first-run welcome
            # notification — gives pystray's NIM_ADD time to register
            # before we fire NIF_INFO. If notify fires before NIM_ADD
            # lands, pystray silently no-ops; we catch the exception
            # below, release the gate, and DON'T persist the disk flag,
            # so next launch retries.
            time.sleep(_TRAY_SETTLE_SECS)
            notify_ok = False
            try:
                icon.notify(
                    f"Display Off is now running as a single-file .exe. "
                    f"Press {ping_hotkey} to blank all displays.",
                    "Display Off",
                )
                log.info("Frozen-first-launch promotion ping fired — Explorer "
                         "should catalog the tray icon now, then tray_promoter "
                         "writes IsPromoted=1.")
                notify_ok = True
            except Exception as e:
                log.warning("Could not fire frozen-first-launch promotion "
                            "notification: %s. Tray icon may stay hidden; user "
                            "can manually toggle Settings ▸ Personalization "
                            "▸ Taskbar ▸ Other system tray icons. The gate is "
                            "released so a retry will fire on next "
                            "launch.", e)

            if not notify_ok:
                _release_ping_gate()
                return

            # Gate stays claimed (already set by _try_claim_ping_gate).
            # Disk write can fail (RO-APPDATA, AV lock) but within-process
            # re-entry is already blocked by the claimed gate. The toast
            # already fired; firing a second one in the same process is
            # the failure mode the gate is preventing.

            # Read-modify-write off the on-disk config to defeat the race
            # against a concurrent Settings → Save: the user's just-edited
            # hotkey/idle/lock values could otherwise be silently
            # clobbered by a stale closure-cfg snapshot.
            try:
                disk_cfg = load_config()
                disk_cfg["_frozen_promoted_pinged"] = True
                save_config(disk_cfg)
                # Mirror into the running closure cfg too so any
                # in-process re-check (none today, but defensive) sees the
                # post-ping state.
                cfg["_frozen_promoted_pinged"] = True
            except OSError as e:
                log.warning("Could not persist _frozen_promoted_pinged flag: "
                            "%s — notification may fire again next launch. "
                            "In-process dedupe still prevents same-session "
                            "re-fire; cross-launch behavior under RO-APPDATA "
                            "is acceptable (rare edge case).", e)
        threading.Thread(target=_frozen_promote_ping, daemon=True).start()

    icon.run()


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    # v1.7.19: --diagnose-paths prints the path-resolver candidates +
    # winning-strategy line to stdout, then exits. Runs BEFORE any
    # data-dir / logging setup so the flag works even when %APPDATA% is
    # unwritable or the log file is locked. The resolver already ran at
    # module-import time (see `_EXE_PATH = _resolve_on_disk_exe_path()`
    # above), so `_EXE_PATH` and `_RESOLVER_LOG` are populated whenever
    # this code runs under freeze.
    if "--diagnose-paths" in sys.argv:
        print(f"displayoff {__version__} --diagnose-paths")
        print(f"frozen: {_is_frozen()}")
        print(f"_EXE_PATH: {_EXE_PATH!r}")
        if _RESOLVER_LOG:
            print("resolver log:")
            for _line in _RESOLVER_LOG:
                print(f"  {_line}")
        else:
            print("resolver log: (empty — likely running from .py source)")
        # v1.7.20: exit non-zero when the resolver failed under freeze
        # (the actual failure case the flag exists to diagnose). A health
        # script polling for "is the install still able to update itself"
        # now has a useful signal: exit-0 = healthy or .py source mode;
        # exit-1 = frozen build with broken path resolution (manual
        # install required). The flag itself ran cleanly either way —
        # the non-zero exit is about the resolver outcome, not the
        # diagnose flag's own success.
        sys.exit(1 if _is_frozen() and not _EXE_PATH else 0)

    # File logging so pythonw.exe runs are debuggable. Without this, every
    # log.* call below goes to a NullHandler and we have zero visibility.
    # RotatingFileHandler (v1.7.0+) prevents unbounded growth — a tray app
    # logs every icon click, blank-trigger, listener-watchdog tick, and
    # idle-watcher sample. Without rotation the log would grow ~MB/day on
    # an active workstation. 1MB × 3 backups = ~4MB total budget.
    from logging.handlers import RotatingFileHandler
    # State migration (v1.7.9): move config/logs/sentinel from _HERE to
    # _DATA_DIR. Must run BEFORE basicConfig so the freshly-attached
    # RotatingFileHandler opens the migrated log file rather than creating
    # a brand-new empty one at the new path while the old log sits orphaned.
    # _ensure_data_dir is already called at module-load time, but we re-run
    # it here in case the data dir was removed between launches (rare, but
    # cheap to defend against).
    _ensure_data_dir()
    _migrate_legacy_data()
    _displayoff_log = os.path.join(_DATA_DIR, "displayoff.log")
    # Under pythonw.exe sys.stderr is None — StreamHandler() defaults to
    # sys.stderr and every emit would call None.write(...), which logging's
    # handleError catches but noisily. Only attach the StreamHandler when
    # there's a real stream behind it.
    _handlers = []
    try:
        _handlers.append(RotatingFileHandler(
            _displayoff_log, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        ))
    except OSError as _e:
        # File-handler init can fail if _DATA_DIR is unwritable (rare —
        # roaming-profile sync mid-conflict, disk full, locked-down policy).
        # Without this guard the OSError unwinds out of main() and a
        # pythonw.exe launch shows nothing — no tray, no error, no log.
        # Degrade to console-only logging so the tray still launches; the
        # error message and any buffered migration breadcrumbs go to stderr
        # if attached (console launches catch it; pythonw silently loses
        # them, but at least the app runs).
        if sys.stderr is not None:
            sys.stderr.write(
                f"displayoff: log file at {_displayoff_log!r} unavailable "
                f"({_e}) — running without file logging\n"
            )
            for _m in _MIGRATION_LOG:
                sys.stderr.write(f"displayoff: data-dir migration: {_m}\n")
            _MIGRATION_LOG.clear()
            for _r in _RESOLVER_LOG:
                sys.stderr.write(f"displayoff: path-resolver: {_r}\n")
            _RESOLVER_LOG.clear()
    if sys.stderr is not None:
        _handlers.append(logging.StreamHandler())
    if not _handlers:
        # pythonw.exe (no stderr) + RotatingFileHandler init failed (no
        # writable _DATA_DIR). `logging.basicConfig(handlers=[])` is a no-op
        # — it skips configuration entirely (the empty list passes the
        # truthiness check that `handlers is None` would have rejected, and
        # `basicConfig` documented behavior is to do nothing once any
        # configuration argument is "effectively unset"). Result: the root
        # logger keeps its bootstrap state (no handlers attached, level
        # WARNING). A subsequent `log.info(...)` then dispatches past the
        # implicit per-logger level filter, finds NO handlers in the chain,
        # and falls through to the module-level `lastResort` handler — which
        # itself filters at WARNING. So INFO calls silently drop with no
        # visible artifact. (NB: this is subtler than "root stays at
        # WARNING" — `basicConfig(level=INFO, handlers=[])` doesn't take the
        # level either; the silence comes from `lastResort`'s WARNING gate.)
        # NullHandler keeps basicConfig in its happy path: the logger gets
        # configured at INFO level, log.info calls find a handler (a no-op
        # one, but a handler), and `lastResort` is never consulted. The
        # tray still launches; the migration breadcrumbs are still lost
        # (no destination exists to hold them), but the rest of the app
        # remains observable to any future logging.* reconfiguration.
        _handlers.append(logging.NullHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(name)s] %(message)s",
        handlers=_handlers,
    )
    # Flush any migration breadcrumbs buffered before the log handler existed.
    # INFO level so a clean upgrade leaves a one-time trail in the new log.
    # Skipped if the stderr-fallback above already drained _MIGRATION_LOG.
    for _msg in _MIGRATION_LOG:
        log.info("data-dir migration: %s", _msg)
    # v1.7.19: parallel flush for path-resolver buffer. Separate prefix so a
    # human reading the log isn't misled into thinking migration ran when
    # only the resolver did. Fires on EVERY startup (resolver runs at
    # module import unconditionally under freeze), unlike _MIGRATION_LOG
    # which is typically empty after the one-time v1.7.9 migration.
    for _msg in _RESOLVER_LOG:
        log.info("path-resolver: %s", _msg)
    # v1.7.11 refinement: only clear the buffer when at least one real handler
    # consumed the messages. In the NullHandler-only degenerate path
    # (pythonw + unwritable _DATA_DIR) the log.info calls above went to
    # /dev/null — wiping the buffer there strands the breadcrumbs with no
    # forensic surface. Keep them so a future About-dialog readout, a
    # `/diagnostics` CLI flag, or an exception handler can surface "we
    # tried to migrate these files and nothing got written" to the user.
    _root_has_real_handler = any(
        not isinstance(_h, logging.NullHandler)
        for _h in logging.getLogger().handlers
    )
    if _root_has_real_handler:
        _MIGRATION_LOG.clear()
        _RESOLVER_LOG.clear()

    # v1.7.22: clean up artifacts from a previous launch's folder-swap dance
    # that may have crashed or been interrupted. Cheap when there's nothing
    # to do; no-op under .py source. Runs BEFORE the --after-update-folder-swap
    # handler so any pre-existing `.new/` / `.new.zip` / `.new.staging/` /
    # `.old/` from a crash get cleaned up first. Important ordering note:
    # the --after-update-folder-swap handler then performs ITS OWN
    # rename of the current install dir → .old + .new → canonical, after
    # which it best-effort deletes .old. If THAT delete fails, the next
    # launch's _recover_from_failed_update catches the leftover.
    _recover_from_failed_update()

    # v1.7.22: --after-update-folder-swap is the relaunch entry point used
    # by the folder-swap dance. The previous-version's _execute_rename_dance()
    # extracted the new bundle into <install_parent>/displayoff.new/, then
    # spawned <new_bundle>/displayoff.exe --after-update-folder-swap. THIS
    # process is that child, running from the .new/ dir. We need to:
    #   1. Signal the parent (so it exits, releasing the install dir lock)
    #   2. Rename old install dir → .old (backup)
    #   3. Rename our own .new dir → canonical install dir name
    #   4. Re-resolve _EXE_PATH / _INSTALL_DIR via GetModuleFileNameW
    #   5. Best-effort delete the .old dir
    #   6. Fall through to normal tray startup
    if "--after-update-folder-swap" in sys.argv:
        # Signal the parent's child-ready handshake event as the very
        # first act — before reading state, before any folder rename,
        # before `_acquire_single_instance`. Signaling early breaks the
        # parent-must-exit-first / child-can't-acquire-mutex deadlock:
        # parent observes the signal, exits (releasing mutex AND
        # releasing any open file handles in the old install dir),
        # then THIS process can safely rename the old install dir.
        if sys.platform == "win32" and OpenEventW is not None:
            try:
                _child_event = OpenEventW(
                    _EVENT_MODIFY_STATE, False,
                    _UPDATE_CHILD_READY_EVENT_NAME
                )
                if _child_event:
                    try:
                        SetEvent(_child_event)
                        log.info("After-update-folder-swap: signaled parent "
                                 "via child-ready event.")
                    finally:
                        CloseHandle(_child_event)
                else:
                    log.info("After-update-folder-swap: child-ready event "
                             "not present (parent may be running an older "
                             "build or the event creation failed).")
            except OSError as e:
                log.warning("After-update-folder-swap: child-ready signal "
                            "raised %s; continuing anyway.", e)

        state = _read_and_clear_update_relaunch_state()
        old_install_dir = ""
        new_install_dir = ""
        if state is not None:
            persisted_version = state.get("version") or "?"
            old_install_dir = state.get("old_install_dir") or ""
            new_install_dir = state.get("new_install_dir") or ""
            if persisted_version != "?" and persisted_version != __version__:
                log.warning(
                    "After-update-folder-swap: state file says target "
                    "version v%s but running binary is v%s — possible "
                    "stale state from a prior failed dance, or wrong "
                    "build at the .new/ path. Forensics below may be "
                    "misleading.", persisted_version, __version__,
                )
            log.info(
                "After-update-folder-swap: relaunched as v%s (parent "
                "pid %s, parent exe %r, old_install=%r, new_install=%r)",
                persisted_version,
                state.get("pid") or "?",
                state.get("exe_path") or "?",
                old_install_dir, new_install_dir,
            )
        else:
            log.info("After-update-folder-swap: launched without relaunch "
                     "state — manual invocation or state file lost. "
                     "Falling back to layout-based discovery.")

        # Layout-based discovery fallback: if the state file is missing or
        # didn't record the install dirs, deduce them from our current
        # location. We MUST be running from <install_parent>/displayoff.new/
        # (the dance only spawns from there); if we're not, something's off
        # and we should bail out of the swap rather than rename random dirs.
        if not new_install_dir:
            new_install_dir = _INSTALL_DIR or ""
        if not old_install_dir and new_install_dir:
            # Strip the .new suffix to get the canonical install dir name.
            if new_install_dir.endswith(_UPDATE_NEW_DIR_SUFFIX):
                old_install_dir = new_install_dir[:-len(_UPDATE_NEW_DIR_SUFFIX)]

        # Sanity-check the discovered paths before doing destructive moves.
        # If either is empty, or new_install_dir != _INSTALL_DIR, OR
        # new_install_dir doesn't end in `.new`, abort the swap and log
        # loudly. The user is left running the v-new build from the .new/
        # dir — functional, but the install layout will need manual cleanup.
        do_swap = bool(
            old_install_dir and new_install_dir
            and new_install_dir == _INSTALL_DIR
            and new_install_dir.endswith(_UPDATE_NEW_DIR_SUFFIX)
            and not old_install_dir.endswith(_UPDATE_NEW_DIR_SUFFIX)
        )

        if do_swap:
            import shutil
            old_backup = old_install_dir + _UPDATE_OLD_DIR_SUFFIX
            log.info("Folder-swap: renaming %s -> %s",
                     old_install_dir, old_backup)
            # If a stale .old/ from a prior crashed dance exists, clean it
            # first; otherwise os.rename will refuse to overwrite.
            if os.path.exists(old_backup):
                try:
                    shutil.rmtree(old_backup)
                except OSError as e:
                    log.warning("Folder-swap: could not remove stale %r: %s "
                                "— skipping swap. Manual cleanup required.",
                                old_backup, e)
                    do_swap = False
            if do_swap and os.path.exists(old_install_dir):
                try:
                    os.rename(old_install_dir, old_backup)
                except OSError as e:
                    log.warning("Folder-swap: cannot rename %r -> %r (%s) "
                                "— skipping swap. Manual cleanup required.",
                                old_install_dir, old_backup, e)
                    do_swap = False
            if do_swap:
                log.info("Folder-swap: renaming %s -> %s",
                         new_install_dir, old_install_dir)
                try:
                    os.rename(new_install_dir, old_install_dir)
                except OSError as e:
                    # We've already moved old_install_dir → .old. Try to
                    # restore so the user isn't left without an install.
                    log.error("Folder-swap: cannot rename %r -> %r (%s); "
                              "restoring %r -> %r so the user isn't "
                              "stranded.", new_install_dir, old_install_dir,
                              e, old_backup, old_install_dir)
                    try:
                        os.rename(old_backup, old_install_dir)
                    except OSError as restore_err:
                        log.error("Folder-swap: restore also failed (%s). "
                                  "MANUAL RECOVERY: rename %r back to %r.",
                                  restore_err, old_backup, old_install_dir)
                    do_swap = False
            if do_swap:
                # Re-resolve _EXE_PATH/_INSTALL_DIR — our underlying .exe
                # is now at old_install_dir/displayoff.exe (NOT at the
                # original new_install_dir path which no longer exists).
                _re_resolve_exe_path_post_swap()
                # Best-effort cleanup of the .old/ backup. AV may briefly
                # hold locks on DLLs inside; failures here get retried at
                # next launch's _recover_from_failed_update pass.
                try:
                    shutil.rmtree(old_backup)
                    log.info("Folder-swap: deleted %s (old install backup).",
                             old_backup)
                except OSError as e:
                    log.warning("Folder-swap: could not delete %r (%s) — "
                                "will retry on next launch.", old_backup, e)
        else:
            log.warning("Folder-swap: skipping swap. old=%r new=%r "
                        "_INSTALL_DIR=%r. Running build is v%s but install "
                        "layout is non-canonical — user should reinstall.",
                        old_install_dir, new_install_dir, _INSTALL_DIR,
                        __version__)

        # Strip --after-update-folder-swap from argv so it doesn't survive
        # into any later argv-scanning code. Idempotent.
        sys.argv = [a for a in sys.argv if a != "--after-update-folder-swap"]

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

    # The --off-family CLI paths blank the display and exit without entering
    # run_tray(), which is where the eager stale-sentinel recovery normally
    # lives. If a prior tray process was killed mid-blank, the on-disk
    # sentinel still names a 1-second display-off timeout to restore; running
    # `--off` in that state without recovery would clobber the saved AC/DC
    # values via a fresh sentinel and trap the user in the 1-second loop.
    # Run recovery up-front so all CLI-blank paths see a clean state.
    _off_flags = {"--off", "--lock-and-off", "--no-lock-off",
                  "--native-off", "--legacy-off", "--start-off"}
    if _off_flags.intersection(sys.argv):
        try:
            from native_blank import recover_stale_sentinel
            recover_stale_sentinel()
        except ImportError as e:
            log.warning("native_blank not available (%s) — skipping stale-sentinel recovery for CLI blank", e)
        except Exception as e:
            log.exception("stale-sentinel recovery raised (%s) — continuing with blank anyway", e)

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
    # hotkey appears dead while Task Manager / an admin terminal has focus,
    # then start a foreground-elevation watcher that logs a per-miss hint
    # (rate-limited to once per minute) when we observe the affected state.
    # The watcher replaces the previous single startup log — users would
    # routinely miss the startup line and assume the hotkey was broken.
    if not _is_elevated():
        log.info("Running unelevated — hotkey may not fire while an elevated window has focus (UIPI).")
        _start_foreground_elevation_watcher()

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
