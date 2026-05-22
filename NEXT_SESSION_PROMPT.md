# DisplayOff v1.7.20 — FINAL Session Prompt (close-out, max productivity)

**This is the LAST displayoff session.** v1.7.19 shipped 2026-05-22. After v1.7.20 lands, displayoff hits maintenance-only mode — no further planned releases. Goal: drain every deferred + out-of-scope item from the v1.7.17 → v1.7.18 → v1.7.19 train in ONE focused session, ship v1.7.20, and walk away with a clean backlog.

## Step 0 — Verify the v1.7.18 → v1.7.19 dance result FIRST (5 min)

This MUST come first. If the dance broke, scope flips from polish-drain to hotfix.

```bash
# On the machine running v1.7.18 (proggy\Tools\displayoff.exe):
# 1. Right-click tray → Settings → Check for updates → Install now
# 2. After it finishes (success or fail), pull the log:
tail -50 "C:/Users/nate/AppData/Roaming/displayoff/displayoff.log"
# 3. Also run:
"C:/Users/nate/proggy/Tools/displayoff.exe" --diagnose-paths  # (won't exist on v1.7.18 install — flag is v1.7.19+)
"C:/Users/nate/proggy/Tools/displayoff.exe" --version          # Confirm post-dance version
```

**Decision tree:**
- ✅ Reports `1.7.19` → dance worked. Continue to Step 1.
- ❌ Reports `1.7.18` + log shows `Rename-dance failed` → v1.7.18's resolver also broke. Pull `path-resolver:` log lines (v1.7.19 source has them but the v1.7.18 binary running on the user does NOT — diagnostic gap). Likely manual install required again. v1.7.20 ships ONLY a hotfix (skip the polish drain). v1.7.21 picks up polish.
- ⚠️  `1.7.19` running but autostart .lnk missing / tray didn't auto-relaunch → child relaunch race lost (item 3 below becomes urgent — promote to v1.7.20).

## Step 1 — The 14-item v1.7.20 polish drain (priority order)

Bundle these into a single release. Each has the file:line ready. Estimated total effort: 2-3 hours of focused work + 1 verifier round.

### A. Resolver hardening (must-fix, security-adjacent)

**1. WindowsApps Store stub false positive in Strategy 0** — `displayoff.py:204-232` block.
   - Currently: `not _path_under_temp(cand)` only excludes `TEMP`, `TMP`, `LOCALAPPDATA\Temp`.
   - Fix: extend `_path_under_temp` to also reject `LOCALAPPDATA\Microsoft\WindowsApps\`. Or add a separate `_path_under_protected(path)` helper used in addition. Naming-wise, `_path_under_protected` is cleaner since `WindowsApps` isn't a temp dir.
   - Apply the same filter to Strategy 1 (line 296) and Strategy 2 (line 327) for symmetry.
   - Cross-check: `os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps")`.

**2. `_download_url_allowed` URL parser hardening** — `displayoff.py` (grep for `def _download_url_allowed`).
   - Currently: `urlsplit` + `startswith` on path. Doesn't normalize `..` traversal in the URL string.
   - Fix: after `urlsplit`, call `os.path.normpath(parsed.path).replace('\\', '/')` and reject if the result starts with `/..` or contains `/..`/. SHA256 still bounds actual exploit, but the parser shouldn't be permissive.

**3. `_migrate_legacy_data` `shutil.move` cross-device atomicity** — `displayoff.py` (grep for `def _migrate_legacy_data`).
   - Hits only when `%APPDATA%` is on a different volume from the install dir (rare; e.g., portable install on USB).
   - Fix: replace `shutil.move(src, dst)` with `shutil.copy2(src, dst); hashlib.sha256(<both files>).hexdigest()` verify, then `os.remove(src)` only on hash match. Race-free.

### B. UX + observability (small but high-leverage)

**4. `--diagnose-paths` exit code semantics** — `displayoff.py:4442-4452` block.
   - Currently: always `return` (exit 0).
   - Fix: `sys.exit(1 if _is_frozen() and not _EXE_PATH else 0)`. A script polling resolver health then has a useful signal.
   - Update CHANGELOG: this changes the exit-code contract.

**5. 300 ms parent-`os._exit` vs child `_acquire_single_instance` race in `_execute_rename_dance`** — `displayoff.py` (grep for `time.sleep(0.3)` or `time.sleep(_CHILD_HANDOFF_SETTLE`).
   - Currently: parent writes state file, spawns child with `--after-update`, sleeps 0.3s, calls `os._exit(0)`. If child loses the mutex race (rare but happens on slow systems), symptom is "no tray after update" with no log entry.
   - Fix: named event `Local\DisplayOff_UpdateChildReady`. Parent creates the event after writing state, sleeps with `WaitForSingleObject(event_handle, 5000)` (5s max). Child opens the event after acquiring mutex and `SetEvent`s. Parent exits on success or timeout. Non-trivial but well-bounded — see how `_signal_other_to_quit` does the named-event handshake for reference.

**6. `_themed_dialog` `dlg.minsize(w, h)` sticky floor** — `displayoff.py` (grep for `def _themed_dialog`).
   - Currently: one-shot `geometry()`.
   - Fix: add `dlg.minsize(w, h)` right after the geometry call. Survives Tk re-solves.

### C. Build + release hygiene (workflow + .bat fixes)

**7. `release.yml` permissions tightening** — `.github/workflows/release.yml`.
   - Currently: `permissions: contents: write` at workflow root.
   - Fix: move `permissions: contents: read` to workflow root, then add `permissions: contents: write` ONLY on the `softprops/action-gh-release` step.

**8. `release.yml` post-upload redirect-host smoke test** — same file.
   - Add a step AFTER the upload that does `curl -sI -L https://github.com/itsnateai/displayoff/releases/download/<tag>/displayoff.exe | grep -i ^location:` and assert the final location matches `release-assets.githubusercontent.com`. Fail the workflow if GitHub silently switches CDN hosts and our allowlist hasn't caught up.

**9. `objects-origin.githubusercontent.com` defensive allowlist add** — `displayoff.py` (grep for the existing allowlist set near `_download_url_allowed`).
   - Forward-compat for the same CDN migration risk #8 addresses at the CI layer.

**10. `_UPDATE_MIN_EXE_SIZE = 1_000_000` → `40_000_000`** — `displayoff.py` (grep for `_UPDATE_MIN_EXE_SIZE`).
   - Real .exe is ~55 MB. 1 MB floor only catches HTML error pages; 40 MB catches mis-shipped stubs.

**11. `build-exe.bat` Nuitka pin guard** — `build-exe.bat`, top of script.
   - Add preflight: `python -m nuitka --version | findstr "^4\.1\.1" || (echo Nuitka 4.1.1 required && exit /b 1)`. CI is pinned via `pip install nuitka==4.1.1` in release.yml; local builds aren't.

**12. `build-exe.bat` timeline entries for v1.7.16..v1.7.19** — `build-exe.bat`, the REM block around line 41-42.
   - Add three entries (one per release that shipped since 2026-05-21) confirming `--onefile-no-compression` still required + Nuitka still pinned to 4.1.1.

### D. Cosmetic (only-if-time)

**13. `_DwmSetWindowAttribute` re-bound on every `_apply_dark_titlebar` call** — `displayoff.py` (grep for `_apply_dark_titlebar`).
   - Convention violation per file rule "bindings live in main block". Move the `ctypes.WinDLL("dwmapi")` + `_DwmSetWindowAttribute.argtypes/restype` setup to the top-of-file win32 block. Wrap in `try/except OSError` since dwmapi load can fail on extremely old Windows.

**14. `tray_promoter.py:121` docstring fix** — `tray_promoter.py` line 121.
   - Change `current_exe_path=sys.executable` to `current_exe_path=_EXE_PATH or sys.executable`. Real call site is correct; docstring will mislead future template-copiers.

## Step 2 — Workflow (copy-paste ready)

```bash
cd X:/_Projects/displayoff

# Verify clean starting state
git status   # should be clean post-v1.7.19 push
git log -1   # should show v1.7.19 release commit

# Items A1-A3 (resolver hardening): edit displayoff.py, add _path_under_protected helper
# Items B4-B6 (UX + observability): edit displayoff.py
# Items C7-C8 (release.yml): edit .github/workflows/release.yml
# Items C9-C12 (.bat + URL allowlist + size floor): edit displayoff.py + build-exe.bat
# Items D13-D14 (cosmetic): edit displayoff.py + tray_promoter.py

# Bump version
# - displayoff.py line 38: __version__ = "1.7.20"
# - build-exe.bat line 32: set VERSION=1.7.20

# Build + smoke
cmd.exe /c "cd /d X:\_Projects\displayoff && build-exe.bat"
./build/displayoff.exe --version          # expect "displayoff 1.7.20"
./build/displayoff.exe --diagnose-paths   # confirm exit-code change works
echo $?  # in bash, expect 0 when _EXE_PATH valid; 1 when frozen+None

# Write CHANGELOG.md v1.7.20 entry covering all 14 items grouped by section (A/B/C/D)
# Write release-notes.md (short — "polish drain + final maintenance release")

# 6-agent normal-stakes verifier round (3 topics × Sonnet+Opus, in PARALLEL via single message with 6 Agent calls)
# Topics: Diff-clean / Gap-audit / Code-review (T1/T2/T3)
# Apply convergent CRITICAL/HIGH fixes mid-round; re-verify only if any REJECT.

# Commit + push
git add displayoff.py build-exe.bat .github/workflows/release.yml CHANGELOG.md release-notes.md tray_promoter.py NEXT_SESSION_PROMPT.md
git commit -m "release(v1.7.20): final polish drain + maintenance close-out

14-item drain: Strategy 0 WindowsApps filter, URL parser hardening,
cross-device migration safety, --diagnose-paths exit code, dance child
relaunch race, themed dialog sticky minsize, release.yml permissions +
CDN redirect smoke, defensive allowlist add, exe size floor, Nuitka
pin guard, build timeline refresh, DwmSetWindowAttribute rebinding,
tray_promoter docstring.

v1.7.20 is the final planned release. Maintenance-only mode after."

git push origin master

# Tag + push tag → CI fires
gh release create v1.7.20 --draft --title "v1.7.20 — final polish drain" --notes-file release-notes.md
git tag v1.7.20 -m "v1.7.20"
git push origin v1.7.20

# Watch CI: gh run watch
# Once assets up + release auto-promoted → manually install on proggy\Tools\:
#   1. Quit v1.7.19 tray
#   2. Copy build/displayoff.exe → C:\Users\nate\proggy\Tools\displayoff.exe
#   3. Launch, verify --version shows 1.7.20
```

## Step 3 — Maintenance-mode handoff (after v1.7.20 ships)

**Once v1.7.20 is live and verified:**

1. **Update `MEMORY.md`**: mark displayoff as `maintenance-only` in the Tray Apps Index entry. Move the `ACTIVE` references out.
2. **Update workspace `CLAUDE.md` if displayoff has any active-project mentions** — should not, but spot-check.
3. **Close out the v1.7.19 + v1.7.20 known gaps section** in CHANGELOG.md — confirm none are outstanding.
4. **Delete `proggy\Tools\displayoff.exe.v1.7.16.bak` + `.SHA256SUMS.txt.v1.7.16.bak`** safety backups (we're 4 releases past now).
5. **Delete this `NEXT_SESSION_PROMPT.md`** — no further sessions expected.
6. **Final memory entry**: `project_displayoff_maintenance_mode_2026_05_22.md` documenting "v1.7.13 path-resolution bug fixed across v1.7.17/v1.7.18/v1.7.19/v1.7.20; rename-dance proven; no further planned work".

## Context preservation (as of v1.7.19 ship)

- **Active workspace**: `X:/_Projects/displayoff/`
- **Source `__version__`**: `1.7.19`
- **Master HEAD**: v1.7.19 release commit (see `git log -1`)
- **Releases live**:
  - v1.7.17: SHA `eab51ae3f77d7c36c3a7c2000da4347a4df9d2e634e6e4edc29a37087339faaa`
  - v1.7.18: SHA `2d12296637b41121b0a89b2008fba7b8a087af85dd9888af4a02a8435b5b900f`
  - v1.7.19: SHA (read from `gh release view v1.7.19 --json assets`)
- **Canonical install**: `C:\Users\nate\proggy\Tools\displayoff.exe` (was v1.7.18 at last check)
- **Local backups**: `displayoff.exe.v1.7.16.bak` + `.SHA256SUMS.txt.v1.7.16.bak` next to current install (delete after v1.7.20)
- **All AKS hooks active**. Workspace push policy: blanket push permission for displayoff (non-`_.claude/` repo); commits NEVER carry Claude attribution.
- **Sandbox rule deleted 2026-05-22**. Not a ship gate.

## What's NOT in scope for v1.7.20

- New features. This is a polish + hardening drain only.
- Architectural changes to the resolver. v1.7.19's Strategy 0 design holds.
- Test suite addition. The empirical-test discipline (`--diagnose-paths` + `path-resolver:` log) is the test harness.
- Cross-platform port. Windows-only forever.

## What's NOT in scope for v1.7.20 (but documented forever)

If a future failure mode emerges that's NOT in the v1.7.20 fix set, the failure will be:
1. **Observable** via `path-resolver:` log lines or `--diagnose-paths` output.
2. **Recoverable** via manual install from the releases page (the SHA256SUMS.txt manifest is canonical).
3. **Reproducible** via the resolver candidates dict (sys.executable / sys.argv[0] / NUITKA_ONEFILE_PARENT / __compiled__.original_argv0 / __compiled__.containing_dir).

Future sessions should NOT re-open displayoff unless one of those three things breaks. The maintenance bar is "if a user reports it AND it's not a known v1.7.20+ gap, hotfix it. Otherwise, don't touch."
