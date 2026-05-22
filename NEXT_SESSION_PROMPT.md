# DisplayOff v1.7.18 — Next-Session Prompt

v1.7.17 shipped 2026-05-22 (tag `v1.7.17`, commit `ff7d160`, public). It fixes the latent v1.7.13–v1.7.16 path-resolution bug under Nuitka onefile via `_resolve_on_disk_exe_path()` (layered `NUITKA_ONEFILE_PARENT` + `QueryFullProcessImageNameW` chain). Source for v1.7.18 already carries the verifier-round hardening (commit `416fb45`).

## Read first

- `C:/Users/nate/.claude/projects/X---Projects/memory/project_displayoff_v1717_shipped.md` — full session telemetry from v1.7.17.
- `X:/_Projects/displayoff/CHANGELOG.md` v1.7.17 entry — describes what shipped and what's still in the backlog.
- `X:/_Projects/displayoff/displayoff.py` lines 105–306 (`_path_under_temp` + `_resolve_on_disk_exe_path` — hardened post-tag for v1.7.18 baseline), 844–854 (`_autostart_target_pythonw` raise-defense).
- `X:/_Projects/displayoff/displayoff.log` (and `%APPDATA%\displayoff\displayoff.log`) — look for `_resolve_on_disk_exe_path candidates:` lines to see which strategy wins on this hardware.

## v1.7.18 scope — in priority order

### 1. HIGH — tag + push v1.7.18 to exercise the inaugural in-the-wild rename-dance

The post-tag hardening from the v1.7.17 8-agent verifier round is already in source on master (`416fb45`). v1.7.17 binary on GitHub is unchanged and works empirically (Strategy 1 always wins under real Nuitka onefile usage). The right next step is to tag v1.7.18, push, let CI build + upload, and **this** is the inaugural in-the-wild rename-dance exercise: v1.7.17 → v1.7.18 update flow.

What's already committed for v1.7.18 baseline (commit `416fb45`):

- New `_path_under_temp(path)` helper — realpath + normcase + check across TEMP/TMP/LOCALAPPDATA\Temp env vars.
- Strategy 1 now rejects results that point inside any TEMP-like dir, fail `os.path.isfile`, or don't end in `.exe`. (T2-Opus convergent.)
- Strategy 2 adds `os.path.isfile(argv0)` + multi-TEMP-env check. (T2-Sonnet CRITICAL ×2.)
- Strategy 3 returns `None` rather than the broken `sys.executable` — downstream consumers already guard `if _EXE_PATH and ...`, so None forces the safer "skip cleanly" path. (T3-Opus HIGH.)
- `_autostart_target_pythonw`: `assert` → `if/raise RuntimeError` — survives `python -O`, satisfies workspace rule 12 "fail loud". (T3-Opus HIGH.)
- `release-notes.md` scrubbed of `proggy\Tools\` private path. Live GitHub release notes also updated via `gh release edit v1.7.17 --notes-file release-notes.md`. (T3-Sonnet.)

### 2. MEDIUM — drain the v1.7.17 deferred backlog into v1.7.18

Pulled from the v1.7.17 CHANGELOG and verifier-round findings. None of these are blocking; bundling them together gives v1.7.18 a meaningful changelog rather than "post-tag hardening only":

- **`_UPDATE_MIN_EXE_SIZE = 1_000_000` → tighter floor.** Real Nuitka onefile build is ~52 MB. A floor of ~40 MB catches mis-shipped stub binaries; current 1 MB only catches HTML error pages. Trivial constant change. (T3-Sonnet MEDIUM, T3-Opus MEDIUM-3 convergent.)
- **`release.yml` permissions tightening.** Move `contents: write` from the workflow level down to just the `softprops/action-gh-release` step (`contents: read` at workflow root). One-line change. (v1.7.17 deferred backlog.)
- **`release.yml` post-upload redirect-host smoke test.** Add a `curl -sI -L` check on `https://github.com/itsnateai/displayoff/releases/download/<tag>/displayoff.exe` and assert the final redirect lands on `release-assets.githubusercontent.com`. Proactive future-CDN-change detection. (v1.7.17 deferred backlog.)
- **`objects-origin.githubusercontent.com` defensive allowlist add.** Forward-compat for future GitHub CDN host migrations. (v1.7.17 deferred backlog.)
- **`_themed_dialog` `dlg.minsize(w, h)` sticky-floor.** Currently the floor is one-shot via `geometry()`. `minsize()` survives Tk geometry re-solves. (v1.7.17 deferred backlog.)
- **`build-exe.bat` Nuitka pin guard.** Add `python -m nuitka --version | findstr "^4\.1\.1"` preflight; fail-fast if a different Nuitka is installed locally. CI is already pinned via `pip install nuitka==4.1.1` in `release.yml`. (T3-Opus INFO.)
- **`tray_promoter.py` docstring example fix.** Line 121 docstring still shows `current_exe_path=sys.executable` as the example. The real call site is correct, but the docstring will mislead future template-copiers. Change to `current_exe_path=_EXE_PATH or sys.executable`. (T4-Sonnet LOW.)

### 3. LOW — non-blocking, can defer to v1.7.19+ if v1.7.18 gets crowded

- **300 ms parent-`os._exit` vs child `_acquire_single_instance` race in the rename-dance child relaunch.** Window is sub-second; if child loses, symptom is "no tray after update" with no log entry. Mitigation idea: switch to a named event (`SetEvent` from parent after child handshake) rather than fixed `time.sleep(0.3)`. Worth doing but non-trivial. (T3-Sonnet MEDIUM, T3-Opus, v1.7.17 deferred backlog convergent.)
- **`_download_url_allowed` URL parser hardening.** `urlsplit` doesn't normalize `..`; `startswith` on path can be tricked by path traversal in the URL string. SHA256 is the integrity boundary so any actual exploit is bounded, but tighter parsing closes the false-positive surface. (T2-Opus CRITICAL but bounded by SHA.)
- **`_migrate_legacy_data` `shutil.move` not atomic cross-device.** Pre-existing code, hits if `%APPDATA%` is on a different volume than the install dir (rare). Add a hash-verify-then-delete pattern. (T2-Opus.)
- **`_DwmSetWindowAttribute` re-bound on every `_apply_dark_titlebar` call.** Convention violation (file rule: bindings live in the main block). Cosmetic. (T3-Sonnet LOW.)

## Workflow

1. Read the v1.7.17 memory file + the empirical `_resolve_on_disk_exe_path candidates:` log line.
2. Decide v1.7.18 scope: minimum is "ship the hardening that's in source"; ideal is "hardening + 3–5 deferred backlog items".
3. For each backlog item picked, edit + verify per the file's discipline (`assert` → `raise`, explicit `argtypes`/`restype`, etc.).
4. Bump `__version__` to `1.7.18` in `displayoff.py` (currently `1.7.17`) and `set VERSION=1.7.18` in `build-exe.bat`.
5. Honest CHANGELOG entry covering both the post-tag hardening shipped between v1.7.17 and v1.7.18 (already in source — describe what's in `416fb45`) AND any new backlog items closed.
6. Write a `release-notes.md` for v1.7.18 — short. The headline is "first dance exercise post-v1.7.17"; emphasize that v1.7.17 users SHOULD be able to use Settings → Check for updates → Install now successfully this time.
7. Build locally (`build-exe.bat`), confirm `displayoff.exe --version` prints `1.7.18`, confirm `displayoff.log` shows the resolver picking Strategy 1.
8. 6-agent normal-stakes verifier round (3 topics × Sonnet+Opus) before tagging — the high-stakes 8-agent round can be reserved for if Strategy 1 fails empirically. **Apply convergent CRITICAL/HIGH fixes mid-round; re-verify if anything REJECTs.**
9. `gh release create v1.7.18 --draft --title "..." --notes-file release-notes.md` (no files; CI uploads).
10. `git tag v1.7.18 -m "v1.7.18" && git push origin master && git push origin v1.7.18` → CI fires.
11. Watch CI run; confirm both `displayoff.exe` + `SHA256SUMS.txt` upload + draft auto-promotes to public.
12. **Now the real test:** click "Settings → Check for updates → Install now" from the v1.7.17 install. The dance should download, SHA-verify, rename `displayoff.exe` → `displayoff.exe.old`, write the new bytes, spawn `displayoff.exe --after-update`, and the v1.7.18 tray icon should appear within ~2 seconds. **If anything goes wrong here, the path-resolution fix has a latent flaw and v1.7.19 is necessary.** Capture `displayoff.log` for proof.

## Context preservation

- Active workspace: `X:/_Projects/displayoff/`
- Current `__version__` in source: `1.7.17` (already-shipped binary)
- Master HEAD: commit `416fb45` (post-tag hardening, NOT in any released binary yet)
- Canonical install: `C:\Users\nate\proggy\Tools\displayoff.exe` — v1.7.17 once manually installed; this is where the "Install now" test fires against.
- Public release: https://github.com/itsnateai/displayoff/releases/tag/v1.7.17 — SHA `eab51ae3f77d7c36c3a7c2000da4347a4df9d2e634e6e4edc29a37087339faaa`.
- All AKS hooks active (pre-edit vault context, post-commit /bug-found, completion-checkpoint v4.1 with stakes-aware verifier dispatch).
- Workspace push policy: blanket push permission granted for displayoff (non-`_.claude/` repo); commits OK to be unattributed.
- **Sandbox rule deleted 2026-05-22.** Don't ask Nate about sandbox-testing; it's no longer a ship gate. The harness reference (`memory/reference_windows_sandbox_testing.md`) stays for optional use only.
- Three things v1.7.17 source carries that the v1.7.17 binary does NOT:
  - `_path_under_temp` helper (multi-TEMP-env coverage)
  - Strategy 1 TEMP-rejection + isfile + .exe filters
  - Strategy 2 isfile + multi-TEMP-env filters
  - Strategy 3 returns None (was `sys.executable`)
  - `_autostart_target_pythonw` `assert` → `raise`
  - `release-notes.md` `proggy\Tools\` scrub (also live on GitHub already)

## Workspace template work (do NOT re-do)

Commit `ec3c0c9` (local-only per `_.claude/**` push-blocking rule) already updated:
- `_.claude/_templates/checklists/release/pre-release.md` — sandbox-mandate lifted
- `_.claude/_templates/snippets/python/tray-icon-promoter.md` — Nuitka onefile trap documented, `_EXE_PATH or sys.executable` pattern shown
- `_.claude/_templates/snippets/csharp/tray-icon-promoter.md` — sandbox mandate lifted
- `_.claude/_templates/snippets/csharp/tray-app.md` — "non-negotiable" → "optional escalation"
- `_.claude/_templates/snippets/powershell/screenshot-capture.md` — sandbox-default-flow lifted
- `_.claude/_templates/troubleshooting/windows/sandbox-silent-logoncommand.md` — workflow rule reference updated

No further template work needed for v1.7.18 unless the dance test surfaces new gaps.
