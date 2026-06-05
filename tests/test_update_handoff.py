"""Regression guard for the folder-swap update handoff race (v1.7.26).

Root cause (live-log-confirmed 2026-06-04, %APPDATA%\\displayoff\\displayoff.log):
the `--after-update-folder-swap` child signaled the parent via the child-ready
event and then IMMEDIATELY raced ahead to (a) rename the install dir and
(b) acquire the single-instance mutex — while the parent was STILL ALIVE. The
v1.7.20 handshake makes the parent *block until the child signals*, which
guarantees the parent is still running at exactly the moment the child needs it
gone. Consequences observed in the log, all within 1ms:
    - rename displayoff -> displayoff.old  => WinError 32 (running .exe locks
      the dir) => "skipping swap"
    - the parent still owns Local\\DisplayOff_SingleInstance => the child's
      single, no-retry CreateMutexW returns ERROR_ALREADY_EXISTS =>
      "Another instance is already running -- exiting"
Net: BOTH processes died, tray never came back.

Fix: the child waits for the parent process (PID from the relaunch state file)
to fully terminate BEFORE the swap and BEFORE _acquire_single_instance. Once the
parent's process object is signalled, its image is unmapped (dir unlocked) and
the kernel has reaped the mutex (mutex free). A scoped retry on the post-update
mutex acquire is belt-and-suspenders for the can't-open-parent edge.

These tests pin the wait helper against REAL processes (no mocks) and the
back-compatible retry signature. Windows-only.
"""
import inspect
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import displayoff as do  # noqa: E402


def _spawn_sleeper(seconds):
    """A throwaway child that just sleeps -> a real live PID to wait on.

    CREATE_NO_WINDOW so the test run doesn't flash console windows.
    """
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds})"],
        creationflags=flags,
    )


def test_wait_returns_true_after_parent_exits():
    """Returns True because the process DIED, well before the timeout."""
    p = _spawn_sleeper(1)
    t0 = time.monotonic()
    ok = do._wait_for_parent_exit(p.pid, timeout_ms=8000)
    elapsed = time.monotonic() - t0
    assert ok is True
    assert elapsed < 7.0, f"returned via timeout, not process death ({elapsed:.2f}s)"
    assert p.poll() is not None, "process should really be dead"


def test_wait_times_out_while_parent_alive():
    """Returns False (and actually waits ~timeout) while the parent lives on."""
    p = _spawn_sleeper(10)
    try:
        t0 = time.monotonic()
        ok = do._wait_for_parent_exit(p.pid, timeout_ms=500)
        elapsed = time.monotonic() - t0
        assert ok is False, "should time out while parent is alive"
        assert 0.3 < elapsed < 3.0, f"should wait ~the timeout, waited {elapsed:.2f}s"
    finally:
        p.kill()
        p.wait()


def test_wait_true_for_already_dead_pid():
    """Parent already reaped: OpenProcess fails -> nothing to wait for -> True."""
    p = _spawn_sleeper(0.1)
    p.wait()
    time.sleep(0.3)
    assert do._wait_for_parent_exit(p.pid, timeout_ms=2000) is True


def test_wait_tolerates_garbage_pid():
    """None / 0 / non-numeric never raise; treated as 'nothing to wait for'."""
    assert do._wait_for_parent_exit(None) is True
    assert do._wait_for_parent_exit(0) is True
    assert do._wait_for_parent_exit("not-a-pid") is True


def test_acquire_accepts_retry_kwarg_back_compat():
    """The post-update path passes a retry budget; default 0.0 keeps the
    historical single-attempt behavior for every normal launch."""
    sig = inspect.signature(do._acquire_single_instance)
    assert "retry_until_s" in sig.parameters
    assert sig.parameters["retry_until_s"].default == 0.0


if __name__ == "__main__":
    # Allow `python tests/test_update_handoff.py` without pytest.
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
            except Exception as e:  # noqa: BLE001
                failures += 1
                print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)
