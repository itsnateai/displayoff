# DisplayOff v1.7.17 — Next-Session Prompt

Continue displayoff hardening. v1.7.15 + v1.7.16 shipped 2026-05-21 (tags `v1.7.15` `8e4b53c` / `v1.7.16` `a991f47`, both public). The session ended with a CRITICAL latent bug discovered that was missed by both the 8-agent verifier round AND the v1.7.13 freeze-pass author.

## Read first

- `C:/Users/nate/.claude/projects/X---Projects/memory/project_displayoff_v1716_shipped.md` — full session telemetry: what shipped, what's still broken, every claim I made that turned out wrong, complete v1.7.17 backlog.
- `X:/_Projects/displayoff/CHANGELOG.md` v1.7.15 + v1.7.16 entries.
- `X:/_Projects/displayoff/displayoff.py` lines 84-90 (`_EXE_PATH` / `_INSTALL_DIR` constants), 670-700 (`_autostart_target`), 3625-3680 (sweep + promote_in_background call site).
- `X:/_Projects/displayoff/displayoff.log` — look for "current launcher is 'C:\Users\nate\AppData\Local\Temp\onefile_..._python.exe'" entries; that's the empirical proof of the bug.

## v1.7.17 scope — IN PRIORITY ORDER

### 1. CRITICAL — Nuitka onefile path-resolution bug (THE main reason for v1.7.17)

**Symptom proven this session:** `_EXE_PATH = os.path.abspath(sys.executable)` returns the temp-extracted `python.exe` (e.g., `%TEMP%\onefile_PID_RAND_HASH\python.exe`), NOT the on-disk `displayoff.exe`. v1.7.13's freeze-mode comment block claims `sys.executable` is the on-disk .exe under Nuitka — empirically false for Nuitka 4.1.1.

**Cascading impact:**
- `tray_promoter.promote_in_background(exe_path=sys.executable)` never matches the registry's `ExecutablePath` (which Win11 correctly populates with the on-disk .exe). Never writes `IsPromoted=1`. Tray icon stays hidden.
- `_autostart_target()` returns `_EXE_PATH` for the .lnk target — would point at a temp path that changes every launch. Log shows "Stale startup shortcut" every relaunch.
- `_INSTALL_DIR = os.path.dirname(_EXE_PATH)` resolves to the temp dir. The rename-dance downloads to + renames in the temp dir, NEVER touches the on-disk install. **The dance has been structurally incapable of updating users since v1.7.13.** The v1.7.16 URL-allowlist hotfix made the network step pass but the dance still wouldn't have worked.

**Fix approach:**
1. Add a `_resolve_on_disk_exe_path()` helper. Investigate which of these returns the on-disk .exe path under Nuitka onefile 4.1.1:
   - `sys.argv[0]`
   - `os.environ.get("NUITKA_ONEFILE_PARENT")` (PID; query parent's image)
   - Win32 `GetModuleFileNameW(NULL)` from inside the child
   - `__compiled__.original_argv0` (Nuitka attribute)
   - First-class: build a tiny test binary that logs all four values, run from `proggy\Tools` location, see what each returns
2. Rewire `_EXE_PATH` to consume the helper.
3. Verify all downstream call sites: `_autostart_target()`, `_execute_rename_dance` (uses `_EXE_PATH` for `current`, `_INSTALL_DIR` for download dest), `tray_promoter.promote_in_background(exe_path=...)`, `_recover_from_failed_update`.
4. Update the misleading comment block at `displayoff.py:43-68` with the empirical reality.
5. **SANDBOX-TEST THE FULL DANCE BEFORE SHIP THIS TIME.** Skipping the sandbox test in v1.7.15 was the root cause of needing v1.7.16. Skipping it again would be the third strike. Sandbox VM with v1.7.16 .exe → fake-v1.7.17 release on a private repo → click Install now → verify the on-disk .exe actually changes + `.old` cleanup fires + child relaunches at v1.7.17.

### 2. HIGH — convergent verifier findings (8-agent round produced; full list in memory file)

- **`chrome_margin = 40` hardcoded** in `_themed_dialog` — fragile at 125%+ DPI scaling. Use `winfo_pixels("0.3i")` or similar.
- **`_PING_FIRED_THIS_PROCESS` global without lock** — violates workspace's documented free-threaded discipline. Use `threading.Lock`.
- **`_recover_from_failed_update` unconditionally deletes `.old`** — hostile to manual rollback. Skip cleanup if `.old` mtime > `current` mtime.
- **`.gitignore *.old` too broad** — narrow to `*.exe.old` or `/*.old`.
- **`build-exe.bat:17` says "should print 'displayoff 1.7.13'"** — stale.
- **`_themed_dialog` should use `dlg.minsize()` (sticky) not one-shot `geometry()` floor** — current floor doesn't survive a Tk geometry re-solve.

### 3. MEDIUM — workspace blast-radius (EQSwitch CDN bug)

User said they'd handle this in the EQSwitch terminal directly. If they didn't get to it: `eqswitch/UI/UpdateDialog.cs:467` — same `release-assets.githubusercontent.com` missing-from-allowlist bug as displayoff v1.7.13/14/15 had. Pattern fix is in `_.claude/_templates/snippets/csharp/github-self-update-allowlist.md` (canonical dual-host).

Also: three workspace templates still teach the single-host pattern. Update them:
- `_.claude/_templates/checklists/code-change/add-self-update.md`
- `_.claude/_templates/references/csharp/self-update-pattern.md`
- `_.claude/_templates/templates/github/package-manager-submission.md`

### 4. LOW — backlog from 8-agent round

See memory file for full list. Worth doing eventually but not blocking:
- `objects-origin.githubusercontent.com` defensive allowlist add
- `release.yml` permissions tightening (`contents: read` at workflow level)
- `release.yml` post-upload redirect-host smoke test (proactive future-CDN-change detection)
- `_UPDATE_MIN_EXE_SIZE = 1 MB` → tighter floor (real .exe is 52 MB)
- 300 ms parent-`os._exit` vs child-`_acquire_single_instance` race window in the rename-dance

## Workflow

1. Read the memory file + the displayoff.log empirical evidence.
2. Add diagnostic logging to confirm which API returns the on-disk .exe path under Nuitka onefile (`sys.argv[0]` is the most likely answer per Nuitka docs).
3. Build a v1.7.17-pre with the path-resolution fix.
4. **Sandbox-test the rename-dance end-to-end against a fake-v1.7.17 release** — this is the gate.
5. If sandbox test passes, fix the other convergent findings in the same commit.
6. 6-agent normal-stakes verifier round (workflow shaped + path resolution is a security boundary so could justify 8-agent but the surface is smaller than v1.7.13 → 6 is probably right).
7. Bump `__version__` to 1.7.16 → 1.7.17 in `displayoff.py` and `build-exe.bat`.
8. CHANGELOG entry — be honest about what was broken since v1.7.13.
9. Create draft release with notes (no asset files — CI uploads them on tag push).
10. Tag + push commit + tag together. CI builds, uploads, auto-promotes draft.
11. Verify SHA cross-check, verify the running .exe at `C:\Users\nate\proggy\Tools\displayoff.exe` is replaceable by clicking Install now in the v1.7.16-running instance against v1.7.17. This is the inaugural successful real-world dance.

## Context preservation

- Active workspace: `X:/_Projects/displayoff/`
- Canonical install: `C:\Users\nate\proggy\Tools\displayoff.exe` (v1.7.16, SHA `30a8e971...`)
- Backup install (build artifact, now deleted): `X:/_Projects/displayoff/build/displayoff.exe` was where the user ran from previously; current build dir holds Nuitka cache subdirs only
- `_frozen_promoted_pinged` config flag: currently absent (cleared this session). On next launch the promotion ping will fire, but `tray_promoter` STILL won't write `IsPromoted=1` until v1.7.17 ships the path-resolution fix
- All AKS hooks active (pre-edit vault context, post-commit /bug-found, completion-checkpoint v4.1 with stakes-aware verifier dispatch)
- Workspace push policy: blanket push permission granted for displayoff (non-`_.claude/` repo); commits OK to be unattributed (no Co-Authored-By per `no_claude_attribution_git`)
- Three things I claimed at end-of-v1.7.16-session that were wrong: "The dance should work end-to-end this time" + "Install now should actually work end-to-end from v1.7.16 onward" + "v1.7.16 → v1.7.17 should be the first successful in-the-wild dance exercise". v1.7.17 is the actual fix — be skeptical of my own optimism here.
