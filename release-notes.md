Post-tag hardening of the v1.7.17 path resolver fallback paths, plus the inaugural in-the-wild rename-dance exercise. v1.7.17's binary works empirically in the real Nuitka onefile case (Strategy 1 — `NUITKA_ONEFILE_PARENT` + `QueryFullProcessImageNameW` — always wins, verified in `displayoff.log`). The v1.7.17 8-agent verifier round surfaced edge cases in the Strategy 2/3 fallback paths that don't fire under normal use but were soft against synthetic argv[0], unset `%TEMP%`, junctions, and `python -O`. v1.7.18 closes those gaps.

If you're on v1.7.17, **this is the first release that the in-app "Install now" updater can pull**. Click Settings → Check for updates → Install now and v1.7.17 should download, SHA-verify, atomically rename your existing `displayoff.exe` to `displayoff.exe.old`, write v1.7.18 in its place, and relaunch — all in about 5 seconds. This is the inaugural end-to-end exercise of the rename-dance.

## What's fixed

- **`_path_under_temp(path)` helper** — multi-env-var TEMP detection (`TEMP`, `TMP`, `LOCALAPPDATA\Temp`) with `realpath` + `normcase` resolution, robust against 8.3 short-names and junctions.
- **Strategy 1** now rejects results that point inside any TEMP-like dir, fail `os.path.isfile`, or don't end in `.exe` — defense if a future Nuitka spawns the bootstrap via a chain where the parent is itself a temp-extracted `python.exe`.
- **Strategy 2** adds `os.path.isfile(argv0)` + the multi-TEMP-env check so a synthetic `sys.argv[0]` (relative path, deleted .exe, etc.) no longer silently propagates to the rename-dance and autostart `.lnk`.
- **Strategy 3** returns `None` (was `sys.executable`) — downstream consumers guard `if _EXE_PATH and ...` already, so `None` makes them skip cleanly instead of mis-targeting. "WARNING log + wrong path" was worse than "no path" because users don't read logs but DO notice features silently failing.
- **`_autostart_target_pythonw`**: `assert` → `raise`. v1.7.17 added the assert as a defense against source-only invariants leaking under freeze, but `assert` compiles to a no-op under `python -O`, which would silently revive the v1.7.13 `.lnk`-points-at-temp-path bug. v1.7.18 promotes to an unconditional `RuntimeError`.
- **`release-notes.md` private-path scrub** — v1.7.17's notes referenced a personal install path; v1.7.18 (and the live v1.7.17 release page, retroactively edited) no longer mention it.

## Upgrade path

- **v1.7.17 users** — click Settings → Check for updates → Install now. The dance should run end-to-end. If it doesn't, capture `displayoff.log` and report the issue.
- **v1.7.13 / v1.7.14 / v1.7.15 / v1.7.16 users** — manual install is still required (those clients have the path-resolution bug v1.7.17 fixed, so they can't reach this release via the in-app updater). Same steps as the v1.7.17 release notes documented:
  1. Right-click the Display Off tray icon → **Quit**.
  2. Download `displayoff.exe` from this release.
  3. Replace your existing `displayoff.exe` with the new file (same filename, same location).
  4. Launch v1.7.18. Your config + autostart `.lnk` + idle settings carry over unchanged.
- **`.py` source-mode users** — no action needed; the resolver returns `None` under source and the rename-dance is skipped entirely (you get updates by `git pull`).

## What v1.7.18 doesn't yet fix (deferred to v1.7.19+)

- `_UPDATE_MIN_EXE_SIZE` floor (currently 1 MB; real .exe is ~52 MB).
- `release.yml` permissions tightening + post-upload redirect-host smoke test.
- 300 ms parent-`os._exit` vs child-mutex race in the dance child relaunch (sub-second window; if hit, symptom is "no tray after update" with no log entry).
- `_themed_dialog` sticky `minsize()` floor (currently one-shot `geometry()`).

None of these block normal operation; they're hardening items the verifier round flagged that didn't make this release's cut.

Full changelog: see `CHANGELOG.md`.
