# Changelog — Display Off

## [1.7.13] — 2026-05-21

First public single-file `displayoff.exe` (Nuitka onefile build) and the rename-dance self-updater that goes with it. Python source distribution continues alongside as a parallel channel — the same `displayoff.py` runs in both modes, dispatching on a `_is_frozen()` helper that detects the Nuitka `__compiled__` sentinel.

### Added

- **`displayoff.exe` — single-file Windows build via Nuitka onefile.** First public .exe release. Compiles the Python source to C and bundles the runtime, pystray, Pillow, pynput, tkinter, and the icon resource into one ~55 MB executable. Build recipe lives at `build-exe.bat` (Windows cmd) and uses `python -m nuitka --onefile --onefile-no-compression --windows-console-mode=disable --windows-icon-from-ico=displayoff.ico --include-data-files=displayoff.ico=displayoff.ico --include-module=native_blank --include-module=tray_promoter --include-module=PIL.Image --enable-plugin=tk-inter`. The `--onefile-no-compression` flag is a Nuitka 4.1.1 + Python 3.14 workaround — the zstd compressor used in the onefile bootloader throws `zstd.ZstdError: Allocation error : not enough memory` despite ample free memory. Compiled binary is fine; only the compression step fails. Once Nuitka ships the py3.14 zstd fix, dropping that flag would cut the .exe to ~20 MB. Tracking issue noted in `build-exe.bat`.
- **Rename-dance self-updater (frozen .exe only).** Replaces the v1.7.12 "open release page in browser" flow when running as `displayoff.exe`. Mechanics adapted from the workspace's C# add-self-update canonical checklist (MicMute / SyncthingPause):
  1. Hit GitHub releases API for the latest tag + assets (existing flow).
  2. Fetch `SHA256SUMS.txt` from the same release; parse `<64_hex>  <filename>` lines for the `displayoff.exe` entry.
  3. Download new exe to `<install_dir>/displayoff.exe.tmp`.
  4. SHA256-verify the .tmp against the manifest digest. Mismatch aborts and preserves the .tmp for forensics.
  5. `os.rename(displayoff.exe, displayoff.exe.old)` — atomic on NTFS.
  6. `os.rename(displayoff.exe.tmp, displayoff.exe)` — atomic. On failure, restore from `.old`.
  7. Write `%APPDATA%\displayoff\_update_relaunch.json` with the new version string + parent PID for forensics.
  8. `subprocess.Popen([displayoff.exe, "--after-update"], DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)` then `os._exit(0)`.
  9. The new child process reads + deletes the relaunch-state file, deletes `.old`, logs the version transition, and falls through to normal tray startup.
- **Hardcoded URL allowlist `_ALLOWED_UPDATE_HOSTS`.** `("https://github.com/itsnateai/", "https://objects.githubusercontent.com/")` — both prefix-validated case-insensitive before any fetch. Never read from config or env — a config-driven allowlist becomes admin-elevation bait. `github.com/itsnateai/...` is the canonical asset URL returned by the API; `objects.githubusercontent.com/...` is what github.com redirects to (S3-backed with a short-lived signed token). Both are needed because `urllib.request.urlopen` auto-follows the redirect, and the post-redirect URL has to pass implicit allowlist check via host presence in the constant.
- **`SHA256SUMS.txt` manifest format.** GNU coreutils `sha256sum -b` output: one line per asset, `<64_hex>  *<filename>` or `<64_hex>  <filename>`. The release workflow uploads exactly one manifest entry today (for `displayoff.exe`), but the parser tolerates additional lines / blank lines / `#` comments. Manifest fetch caps the read at 16 KiB so a malicious 200-OK with a huge body can't OOM the updater.
- **Recovery from interrupted updates.** `_recover_from_failed_update()` runs at the top of `main()` (before any other CLI flag handling) under freeze mode. Cleans up two independent artifacts: a stale `displayoff.exe.tmp` from a download that crashed before the rename, and a stale `displayoff.exe.old` from a dance that completed but couldn't trigger the `--after-update` cleanup. Both are deleted; no destructive operations beyond removing intermediates the dance is supposed to manage. No-op under `.py` source mode.
- **`--after-update` CLI flag.** Internal — spawned by step 6 of the dance. Reads + clears `_update_relaunch.json`, logs the version transition (new version + parent PID + parent exe path), strips the flag from `sys.argv`, then falls through to the normal tray-startup path. Idempotent if invoked manually (no state file → just logs "launched without relaunch state").

### Changed

- **Freeze-mode dispatch helpers.** New `_is_frozen()` returns True under either Nuitka's `__compiled__` sentinel or PyInstaller's `sys.frozen` (defensive — we ship Nuitka, but the helper handles either). New `_EXE_PATH` / `_INSTALL_DIR` constants resolve at module load: under freeze, `_EXE_PATH` is the on-disk .exe path (`sys.executable`) and `_INSTALL_DIR` is its containing directory; under source, `_EXE_PATH` is None and `_INSTALL_DIR` is the script dir. Existing `_HERE` keeps its semantics — the bundle's runtime extraction dir (Nuitka onefile temp dir or PyInstaller `_MEIPASS`), which is where `displayoff.ico` lands for pystray's `Image.open(_ICON_PATH)`.
- **Autostart `.lnk` now self-rewires after switching from source to frozen.** Refactored `_create_startup_lnk` + `autostart_enabled` to dispatch on `_is_frozen()` through a new `_autostart_target()` helper that returns `(target, arguments, working_dir, icon_location)`. Frozen mode: target is `displayoff.exe`, arguments empty, IconLocation references the .exe's embedded icon resource (`<exe>,0`). Source mode: unchanged from v1.7.12 (pythonw.exe + `"<script_path>"` arg). When a user with a v1.7.12 source-mode `.lnk` upgrades to v1.7.13 frozen and toggles autostart from Settings, the `.lnk` automatically re-creates pointing at the new .exe — `autostart_enabled()` detects the stale target and treats the .lnk as "not enabled" until the next Save reconciles it via `_create_startup_lnk`'s idempotency.
- **`check_for_updates` now returns a 5-tuple** `(has_update, latest, html_url, error, assets)` where `assets` is `{asset_name: browser_download_url}` extracted from the GitHub releases API response. Consumed by `_can_use_rename_dance` to detect whether the latest release publishes the asset names the dance expects. The in-process update-check cache stores 5-tuples now; cold cache on first launch after upgrade (no persistence migration needed — cache only lives in-memory).
- **Update dialog adds "Install now" button when frozen.** When the release publishes both `displayoff.exe` and `SHA256SUMS.txt` on allowlisted hosts AND we're running as the frozen .exe, the Update-available dialog shows `[Install now] [Open releases page] [Cancel]` instead of the v1.7.12 `[Yes] [No]` browser-only flow. "Install now" runs the rename-dance in a background worker; on success the worker `os._exit(0)`s after a 300 ms settle for the spawned child to claim the tray. On any failure (network, SHA mismatch, .exe locked, spawn failed) the worker marshals an error dialog back to the Settings Tk thread with the failure reason + "Open releases page" fallback.

### Fixed

- **`_migrate_legacy_data` `_MIGRATED` flag no longer suppresses retry on transient failures** (`displayoff.py`). v1.7.12 unconditionally set `_MIGRATED = True` after the migration loop completed, regardless of whether any file move had raised `OSError`. A transient lock (AV scanner momentarily holding the source open, OneDrive in-progress sync, locked `displayoff.log` from a not-quite-released file handle) would permanently strand the file in `_HERE` for the process lifetime — a retry 30 seconds later would have succeeded but the flag suppressed it. v1.7.13 tracks `had_recoverable_failure` across the loop; the flag is set only when every file either moved cleanly or hit a "benign race" (destination materialized post-failure, meaning a concurrent launcher won). True unrecoverable failures leave `_MIGRATED = False`, allowing the next call to retry the loop. Surfaced by T2 Sonnet+Opus round 5 verifiers (convergent).
- **DPI awareness function declarations moved to the module-level bindings block.** v1.7.12 set `fn.argtypes` / `fn.restype` on each tier's function object every time `_set_dpi_awareness()` ran — idempotent in practice (function called once at startup) but a shared-state mutation that defeats the bindings-block convention's intent. v1.7.13 declares `_SetProcessDpiAwarenessContext`, `_SetProcessDpiAwareness`, `_SetProcessDPIAware` once at module load with try/AttributeError guards so missing entry points on older Windows leave the corresponding bound name as `None`; the function body now just checks `is not None` and calls. Surfaced by T2 Opus + T3 Sonnet round 5 verifiers (convergent).
- **`uxtheme` ordinal lookups use bound `WinDLL` instead of `ctypes.windll.uxtheme`.** `_enable_dark_mode_menus` now reads from a new module-level `_uxtheme = ctypes.WinDLL("uxtheme", use_last_error=True)` (or None on platforms without uxtheme.dll) instead of the raw `ctypes.windll.uxtheme` lookup. No live bug (downstream code doesn't consult `LastError` for these calls), but the consistency closes a future-bug surface where a `ctypes.get_last_error()` after a uxtheme call could read a stale value from an unrelated DLL's syscall. Carried over from v1.7.12 backlog (T3 Opus round 1).

### Notes — verifier-round security hardening

After the initial v1.7.13 implementation, a high-stakes 8-agent verifier dispatch (T1 Diff-clean / T2 Gap-audit / T3 Code-review / T4 Blast-radius × Sonnet+Opus) surfaced three CRITICAL findings that converged across pairs. All three are addressed in this release:

- **`.tmp` is now deleted on SHA256 mismatch** instead of preserved "for forensics." The previous design kept the failed-verification bytes on disk in `_INSTALL_DIR` with the `.tmp` suffix — a file-write primitive for an attacker who could control a release manifest. The actual SHA is now logged inline (so debug info isn't lost) and the file is removed; `log.warning` covers any cleanup-failure case. (T2-Opus C2 + T3-Sonnet S1, convergent.)
- **URL allowlist tightened to parsed (scheme, netloc, path) checks.** v1.7.13-rc1 used `url.lower().startswith(host.lower())` which accepted any path under `https://github.com/itsnateai/`. The release version uses `urllib.parse.urlsplit` and requires (a) `scheme == "https"`, (b) netloc is exactly `github.com` AND path starts with `/itsnateai/displayoff/`, OR (c) netloc is exactly `objects.githubusercontent.com`. This rejects user-prefix attacks (`itsnateai-attacker`), path-prefix attacks (`evil/itsnateai/displayoff/...`), HTTP downgrade, `file://`, `javascript:`, and any future repo Nate publishes under `itsnateai/` that isn't `displayoff`. (T2-Sonnet C2 + T3-Opus M1 + T2-Opus C1, convergent.)
- **HTTPRedirectHandler re-validates every redirect hop against the allowlist.** `urllib.request.urlopen` follows redirects without any check — a compromised github.com asset row could 302 to anywhere. SHA256 verification catches tampered bytes, but the redirect itself leaks request fingerprint (IP/UA/timing) to attacker-controlled domains via the Location-header GET. The new `_AllowlistedRedirectHandler` raises `URLError` on any hop whose target fails `_download_url_allowed`. (T3-Opus H1 + T2-Sonnet C2, convergent.)
- **`logging.shutdown()` runs before `os._exit(0)`** to flush the `RotatingFileHandler`'s pending writes. Without this, the dance's last 3-4 log lines ("renaming", "spawning child") were lost in the buffer when `os._exit` bypassed normal Python teardown — the lines that matter most for diagnosing an update problem. (T3-Sonnet S3 + T3-Opus M3, convergent.)
- **`--after-update` cross-checks the state file's version against `__version__`** and logs a warning on mismatch. Surfaces stale-state corruption (a left-over `_update_relaunch.json` from a previously-failed dance consumed by a manually-launched `--after-update`) instead of silently logging bogus forensics. (T2-Opus C3, single-finding but cheap defense-in-depth.)

### Notes — distribution

- **Parallel distribution.** v1.7.13 ships the `displayoff.exe` alongside the existing Python source — not replacing it. Users who already run from `python displayoff.py` or `pythonw displayoff.py` keep working; users who download the .exe get the same logic plus the rename-dance updater. Whether to deprecate the source channel in v1.8.0 is deferred until the .exe has a few weeks of real-world telemetry.
- **`build-exe.bat` not in the GitHub release.** The build script + `build/` directory are committed to source control so future maintainers can rebuild, but they don't ship as release assets. The release publishes only `displayoff.exe` + `SHA256SUMS.txt`.

## [1.7.12] — 2026-05-21

Pre-existing-cleanup pass. Two real fixes from carried-forward backlog plus a Notes section correcting documentation drift in v1.7.11's CHANGELOG entry.

### Changed

- **`_migrate_legacy_data` now short-circuits on a module-level `_MIGRATED` flag** in both `displayoff.py` and `native_blank.py`. Previously, the migration function ran its 10-entry file-existence loop on every invocation — idempotent, but wasted ~5 `os.path.exists` syscalls per blank fire on a fully-migrated install. On an active workstation firing the idle-blank watcher 40-60 times/day, that's a few hundred wasted stat calls. The flag short-circuits the entire loop after the first successful pass; mirrors the standard "one-shot completion gate" pattern. Resets only on process restart. Surfaced by T2 Opus round 4.
- **`_set_dpi_awareness` now uses bound `WinDLL` names instead of raw `ctypes.windll.user32` / `ctypes.windll.shcore`** — converted to the workspace convention "Never call `ctypes.windll.*` directly outside the bindings block." The bindings block gained a lazy `_shcore = ctypes.WinDLL("shcore", use_last_error=True)` wrapped in `try/except OSError` so Win7 (no `shcore.dll`) still loads the module — the per-monitor Win8.1+ tier is silently skipped on those systems, falling through to the universally-available `SetProcessDPIAware` third tier. Bound function declarations replace the inline `getattr`-via-`windll` pattern. No live bug (DPI awareness returns are not currently `LastError`-inspected), but the consistency closes a future-bug surface where a downstream `ctypes.get_last_error()` call could read a stale value from a different DLL's syscall. Surfaced by T3 Opus round 1.

### Notes — corrections to v1.7.11 CHANGELOG entry

The v1.7.11 entry below carries two documentation inaccuracies surfaced by the round-4 verifier dispatch but not corrected in-place to avoid divergence with the v1.7.11 GitHub release notes (extracted at tag time):

- v1.7.11 "Removed" section says `restore_ok` lived at `native_blank.py:639-642`. The actual range of the removed variable was lines 639 + 642 (set to False then True around the `_write_display_timeouts` call); the line that now remains is the historical reference at line 648 of the post-removal file. (T2 Opus round 4.)
- v1.7.11 "Fixed" section describes the `_MIGRATION_LOG.clear()` gate as "gated on whether any non-`NullHandler` is attached to the root logger" — accurate for `displayoff.main()` (which inspects `logging.getLogger().handlers`), but `native_blank._flush_migration_log` uses a local `drained` flag set before `basicConfig` runs. The two implementations are functionally equivalent but the prose described only the displayoff mechanism. (T2 Sonnet round 4.)

### Backlog for v1.7.13+

- **`_set_pystray_dark_titlebar` / dark-mode menu setup uses `ctypes.windll.uxtheme` for ordinal lookups** (line 2397). Different shape from `_set_dpi_awareness`'s named-symbol pattern (uxtheme symbols 135 + 136 are name-less exports, only resolvable by ordinal), so the conversion is non-trivial. Same convention violation, lower priority — uxtheme failures fall through to a try/except with no `LastError` consultation.
- **Rename-dance updater** — applies once frozen to `.exe`, currently inapplicable.

## [1.7.11] — 2026-05-21

Backlog cleanup pass — five small fixes from the v1.7.9 + v1.7.10 verifier rounds that were deferred. No new features.

### Fixed

- **`native_blank.py` logging fallback is now symmetric with `displayoff.py main()`.** v1.7.10 hardened `displayoff.main()` against an unwritable `%APPDATA%` via a `try/except OSError` around `RotatingFileHandler` + `NullHandler` degenerate-case fallback. `native_blank.py`'s `_setup_logging()` (standalone-CLI entry point, `python native_blank.py --blank`) and `_ensure_module_logger_has_filehandler()` (import-driven entry point) had the same vulnerability — they would raise `OSError` uncaught from the `RotatingFileHandler` constructor when `_DATA_DIR` was unwritable, crashing the standalone CLI with a traceback or the import with an uncaught exception. Both functions now mirror `displayoff.main()`'s posture: `try/except OSError` around the file-handler creation, stderr breadcrumb dump when stderr is attached, NullHandler fallback when it isn't. Surfaced by 2 of 6 round-3 verifiers (T2 Sonnet+Opus convergent).
- **`_MIGRATION_LOG.clear()` no longer wipes the buffer when nothing drained it.** When the NullHandler degenerate path fires (pythonw + unwritable `%APPDATA%`), `log.info("data-dir migration: %s", ...)` calls go to `/dev/null`. The previous unconditional `_MIGRATION_LOG.clear()` then erased the breadcrumbs with no forensic surface for the user. The clear is now gated on whether any non-`NullHandler` is attached to the root logger; the buffer survives in the silent path so a future About-dialog readout, `/diagnostics` CLI flag, or exception handler could surface what migration was attempted.

### Changed

- **`native_blank.py` no longer mutates the filesystem at module-import time.** Previously `_ensure_data_dir()` and `_migrate_legacy_data()` both ran unconditionally at module-level scope, so any `import native_blank` from a test harness, REPL, or peer module triggered `os.makedirs` + `shutil.move` calls under `%APPDATA%`. `_migrate_legacy_data()` is now invoked lazily from inside the two logging-setup entry points (`_setup_logging` for standalone-CLI usage, `_ensure_module_logger_has_filehandler` for imported usage), each running before the file handler attaches. Both calls are idempotent, so duplicate invocation across both entry points (the imported case where displayoff.main() already migrated) is a safe no-op. `_ensure_data_dir()` still runs at module load — it's a cheap idempotent `os.makedirs(exist_ok=True)` with no destructive side effects. Surfaced by T3 Opus rounds 1+2.
- **Code-comment precision at the v1.7.10 `basicConfig(handlers=[])` fallback site.** The previous comment said `basicConfig(handlers=[])` "leaves the root logger with its default WARNING threshold." Empirically not quite right — `basicConfig` *also* fails to apply the `level=INFO` kwarg when it bails on the empty handlers list, but the silence-on-INFO behavior comes from `lastResort`'s WARNING gate (the module-level fallback handler at `logging.lastResort` that fires when no handlers exist), not from any threshold on root itself. Comment now describes the actual mechanism so a future maintainer reading it doesn't misdiagnose a regression. Surfaced by T2 Sonnet round 3.

### Removed

- **Dead `restore_ok` boolean in `native_blank.py:639-642`.** The variable was set in the `try`/`except` around `_write_display_timeouts` but never read — the verification gate that actually decides whether to clear the sentinel is the post-restore `_read_display_timeouts` comparison, not the boolean. Deleting it removes a misleading read-signal that suggested it was load-bearing. Surfaced by T3 Sonnet round 1.

## [1.7.10] — 2026-05-21

Closes a silent-zombie failure mode introduced by v1.7.9's hardening commit (`5650712`), and retroactively documents that hardening commit (which v1.7.9's CHANGELOG entry omitted).

### Fixed

- **Logging fallback no longer silently drops every log call under `pythonw.exe` + unwritable `%APPDATA%`.** v1.7.9 wrapped the `RotatingFileHandler` init in a `try/except OSError` so an unwritable `_DATA_DIR` wouldn't crash `main()`. But when both the file handler failed AND `sys.stderr is None` (the exact case `pythonw.exe` lands in), the resulting `_handlers` list was empty, and `logging.basicConfig(handlers=[])` is a documented no-op — it leaves the root logger at its default `WARNING` threshold with no handlers attached. Every subsequent `log.info(...)` then dropped silently via Python's `lastResort` handler, which only fires at `WARNING` or higher. The tray ran but produced zero log output for its entire lifetime — exactly the silent-zombie state the hardening was supposed to prevent. Now: a `NullHandler` is appended when `_handlers` would otherwise be empty, so `basicConfig` stays in its happy path. The migration breadcrumbs are still lost (no destination exists to hold them), but the rest of the app remains observable to any later logging reconfiguration. Surfaced by 4 of 6 verifiers (T2 Sonnet+Opus, T3 Sonnet+Opus convergent CRITICAL) on the v1.7.9 round-2 audit.

### Notes — retroactive documentation for v1.7.9 hardening (commit `5650712`)

The v1.7.9 CHANGELOG entry below describes only the original v1.7.9 scope (APPDATA migration, right-click reset, `_themed_dialog` typo guard, tooltip non-BMP comment). It omits the three verifier-hardening fixes that shipped in v1.7.9 commit `5650712`:

- `RotatingFileHandler` init wrapped in `try/except OSError`, falling back to console + stderr breadcrumb dump. (The bug v1.7.10 closes above is a degenerate case of THIS fallback.)
- `_migrate_legacy_data` race-loss handling: if `shutil.move` raises but `dst` exists post-failure, log as benign concurrent-launch race rather than user-facing migration failure. Mirrored in `displayoff.py` and `native_blank.py`.
- `_menu_header_text` body wrapped in `try/except Exception` so a callable failure on pystray's menu-paint thread doesn't render an empty header label (pystray's Win32 backend silently swallows exceptions from dynamic-property callables).

Future releases will document hardening commits in their own CHANGELOG section.

## [1.7.9] — 2026-05-21

Closes the two items v1.7.8 deferred: relocation of per-user state to `%APPDATA%\displayoff\`, and right-click reset of the double-click timer. Plus two non-blocking nits from the v1.7.8 verifier pass — `_themed_dialog` typo'd-kind hardening and a tooltip non-BMP analysis comment.

### Changed

- **Config, logs, and the crash-recovery sentinel now live in `%APPDATA%\displayoff\` instead of the script directory.** A shared install (e.g. one clone in `C:\Program Files\` used by two Windows accounts) previously had every user reading and writing the same `displayoff_config.json`, `displayoff.log` (+ rotated `.1`/`.2`/`.3`), `native_blank.log` (+ rotated), and `.native_blank_in_progress.json` — leaking one user's idle-pattern history, log file, and in-progress sentinel into another user's session. Each user now has their own private `%APPDATA%\displayoff\` directory, matching the per-user discipline that's already used for the Startup-folder `.lnk`. The icon `displayoff.ico` stays bundled with the script as a read-only asset. Existing files in the script directory are auto-migrated to the new location on first launch of v1.7.9; the migration is one-shot and idempotent — a partial migration safely resumes on the next launch, and breadcrumbs land in the new `displayoff.log` so support can diagnose any moves that failed. If you're upgrading in-place, quit the running v1.7.8 tray first (otherwise the held log handle blocks its own migration until the next clean launch).

### Fixed

- **Right-click on the tray icon now resets the pending double-click timer.** A user who double-clicked (firing a blank), then immediately right-clicked to open the menu, then left-clicked twice more inside the 500ms double-click window could see the second left-click pair interpreted as a fresh double-click — firing a second blank while the context menu was on screen. Pystray doesn't expose a menu-open event, so the fix piggybacks on the existing dynamic-text callable that pystray re-evaluates whenever it paints the right-click menu: a side effect on the version-header item clears `last_icon_click` if it's nonzero. Left-clicks bypass menu rendering entirely (they route to the hidden `default=True` item), so legitimate double-click detection is unaffected.
- **`_themed_dialog` now logs and coerces unknown `kind` values instead of silently rendering no glyph.** Typos like `kind="warn"` (vs `"warning"`) or `kind="err"` (vs `"error"`) previously fell through the dict lookup and produced a glyph-less dialog with no indication anything was wrong. Now: a `log.debug` line records the unknown kind and the call is coerced to `"info"` so the user still gets a severity glyph. Surfaced by both Sonnet and Opus T2 verifiers during v1.7.8 review.

### Notes

- **Tooltip 127-character truncation** in `tray_promoter.py` now carries a comment explaining the non-BMP-codepoint analysis (Python `str[:127]` counts code points; Win32 NIF_TIP truncates by `wchar_t` / UTF-16 code unit, so an emoji-bearing tooltip would diverge between the two paths). The comparison is symmetric on both sides of `==`, so equality still holds for any tooltip Display Off produces, and Microsoft documents `NotifyIconData_W` as never truncating mid-surrogate-pair on the Win32 side. Defer the real `wchar_t`-aware truncation until / if Display Off ever needs a non-BMP tooltip.
- Still on the backlog for a future release: the rename-dance self-updater (`.tmp` → rename current `.exe` to `.old` → move new in place → relaunch with `--after-update` → clean up `.old` on next launch), required once Display Off eventually freezes from Python source to a single `.exe`. Currently inapplicable.

## [1.7.8] — 2026-05-21

P2/P3 backlog from the pre-public-release audit. Seven items: five tray-correctness fixes, one network-fingerprint reduction, one dialog-severity affordance. No functional behavior changes beyond closing the bugs and clarifying the surface.

### Fixed

- **Hotkey-capture state no longer gets stuck if pynput fails to import or initialize.** Companion to the TclError variant fixed in v1.7.6. If a broken/partial install or AV quarantine left `pynput` un-importable, clicking the hotkey field set `recording["active"] = True`, ran the import at line 1693, raised ImportError out of the function, and stranded the UI in the "Press your hotkey..." state for the rest of the session — the field would not accept further clicks. Now: import and listener startup are each wrapped in a guard that restores the UI (display text, sunken relief) and clears the recording flag on failure, with the ImportError path also surfacing a themed dialog pointing the user at `pip install --upgrade pynput`.
- **Update-check dialog no longer logs ERROR when the user closes Settings mid-request.** The result-marshalling and dialog-creation paths in `_run_update_check` both raise `tkinter.TclError` once `parent_root` is destroyed — entirely expected when the user dismisses Settings before the GitHub API call lands. v1.7.7 caught these under the catch-all `except Exception` and routed them through `log.exception`, producing noisy ERROR-level traceback spam every time someone closed Settings during the 5-second update window. Now: a dedicated `except tk.TclError` branch logs at DEBUG with "(expected when parent window closed mid-request)".
- **Tray-promoter tooltip comparison normalizes whitespace and applies the NIF_TIP 127-char cap to both sides.** Phase 1 of `tray_promoter.try_promote` matched the `(ExecutablePath, InitialTooltip)` tuple byte-for-byte against the stored registry value. Explorer normalizes whitespace and truncates at 127 chars before persisting the InitialTooltip — comparing the raw 130-char tooltip we passed at `NIM_ADD` against the 127-char stored value would silently miss the otherwise-perfectly-matched subkey, stranding the poll thread on the every-30-seconds backoff interval. Both sides now flow through `.strip()[:127]` so the comparison matches what Explorer actually wrote.
- **`ctypes.WinDLL` `use_last_error=True` is now uniformly applied** to `user32`, `kernel32`, `shell32`, `dwmapi`, and `advapi32`. Previously `user32` and `shell32` were loaded via the global `ctypes.windll.*` shortcut, which does NOT enable the LastError thread-local capture — a `ctypes.get_last_error()` call following a user32 syscall would read 0 or a stale value from a different binding's syscall. No live bug from this in 1.7.7 (kernel32 was the only call site reading LastError), but the consistency closes a future-bug surface.
- **Foreground-elevation watcher logs a per-miss hint** when an elevated window has focus and the global hotkey is being silently suppressed by UIPI. Previously a single INFO line at startup told the user "may not fire while elevated window has focus" — users would routinely miss it 30 minutes into a session, fail to fire the hotkey while Task Manager / an admin terminal was foreground, and assume the app was broken. Now: a 30-second-poll daemon thread queries `GetForegroundWindow → GetWindowThreadProcessId → OpenProcess → OpenProcessToken → GetTokenInformation(TokenElevation)` and re-emits the same INFO line, rate-limited to once per 60 seconds while exposure continues. Probe is no-op when we ourselves are elevated. ACCESS_DENIED across the UIPI boundary is treated as evidence of elevation, since a non-admin OpenProcess can't probe a higher-IL process.

### Added

- **Themed dialog severity glyphs.** `_themed_dialog` now accepts a `kind` parameter (`info` / `warning` / `error` / `none`, default `info`) that prepends a Unicode glyph (ℹ︎ / ⚠︎ / ❌) to the body text. Saves-failure, hotkey-block, and update-error dialogs surface as errors; idle-validation, hotkey-safety warning, and autostart-failure dialogs surface as warnings; update-available and up-to-date as info. Replaces the v1.7.7 visual-monotony of every dialog looking identical regardless of severity.

### Changed

- **Update-check User-Agent is now `displayoff-updater` instead of `DisplayOff/{version}`.** GitHub requires a non-empty UA on API requests and ignores its content; the previous value let any passive network observer (corporate proxy, ISP, GitHub's own request log) fingerprint the exact installed build of every Display Off user behind the same exit IP. The generic value still satisfies the API and removes the version side-channel.

### Notes

- This release closes all but two items from the v1.7.6 P2/P3 backlog. Deferred to v1.7.9: (1) config + log + sentinel relocation from script directory to `%APPDATA%\displayoff\` (needs careful migration testing across multi-user installs), (2) right-click reset of the double-click timer (needs pystray-API spelunking to confirm the event hook). Also added to the v1.7.9+ backlog: if/when displayoff ships as a frozen `.exe`, the self-updater must follow the workspace rename-dance pattern — download to `.tmp`, rename current exe to `.old`, move new in place, relaunch with `--after-update`, cleanup `.old` on next launch — with the same allowlist of `github.com/itsnateai/` + `objects.githubusercontent.com/` used elsewhere in the workspace.

## [1.7.7] — 2026-05-21

UX polish: all in-app dialogs now render in the dark theme.

### Fixed

- **Update-check result, settings-save error, hotkey-safety warning, and autostart-failure dialogs all stopped being white-themed.** Every previous use of `tkinter.messagebox.*` (which delegates to the native Win32 MessageBox primitive) painted stock light-mode chrome regardless of our app theme — a visible white flash next to the dark Settings/About windows. Replaced with `_themed_dialog`, a `tk.Toplevel`-based modal that reuses the same dark palette + DWM titlebar trick the Settings and About dialogs already use. All 8 dialog sites converted. Enter fires the default button, Esc / window-close dismiss without action.

## [1.7.6] — 2026-05-21

Audit-driven correctness + security pass. Seven fixes surfaced by a pre-public-release code audit; no functional behavior changes beyond closing the bugs.

### Fixed

- **Sentinel file write is now atomic.** `_write_sentinel` previously wrote the saved AC/DC display-off timeouts directly via `open(..., "w") + json.dump`. A kill mid-write (BSOD, OOM, Task Manager) left a partial JSON on disk; `_recover_from_stale_sentinel` would catch the JSONDecodeError and **delete the corrupt sentinel** — permanently losing the saved values and leaving the user trapped in a 1-second display-off timeout. Now: write to `_SENTINEL_PATH + ".tmp"` + `f.flush()` + `os.fsync()` + `os.replace()`. Either the full sentinel commits to disk or nothing does.
- **GitHub API "rate limit reached" now shows a specific error message** instead of the misleading generic "Verify your internet connection". GitHub's unauthenticated API limit is 60 req/hr/IP, shared across `gh`, GitHub Desktop, VS Code extensions, and any other tool hitting the API from the same network. The 403/rate-limit error path now spells this out and points the user to the releases page.
- **`html_url` from GitHub API response is now validated before being opened** in the browser. A compromised release or MITM-injected JSON could previously set `html_url` to a `file://` or `javascript:` URI which `webbrowser.open` would hand to the OS handler. Now: allowlist `https://github.com/` prefix; fall back to the hardcoded releases URL otherwise.
- **Hotkey-capture state no longer gets stuck if the Settings dialog is closed mid-recording.** Clicking Cancel while "Press your hotkey…" was active left `recording["active"] = True` (because the queued `poll_capture` raised TclError into Tk's report_callback_exception, never reaching the cleanup line) AND left the pynput listener alive consuming input. Now: `poll_capture` catches TclError, stops the listener, and resets the flag.
- **About dialog no longer hangs the Tk event loop on cold-boot Win11.** `_show_about` was re-calling `autostart_enabled()` on the Tk thread — that helper spawns a PowerShell subprocess with a 30-second timeout, which on a cold-boot Win11 system with AV scanning can take 10-30s and visibly hangs the About dialog. Now: the Settings dialog passes its already-cached `autostart_state["enabled"]` value into `_show_about`. The optional parameter falls back to the helper call when no cached value is provided.
- **`DwmSetWindowAttribute` is now bound with explicit `argtypes`/`restype`.** Previously called via raw `ctypes.windll.dwmapi.DwmSetWindowAttribute(...)`; HWND is pointer-sized on x64, default-c_int argtype silently truncates handles above 2 GB. HRESULT restype default (c_int) was actually correct, but the binding hygiene matches the workspace convention "never call ctypes.windll.* directly outside a bound-name pattern".

### Added

- **Update check is now cached for 6 hours.** Repeated clicks of *Settings → Updates → Check for Updates* within the cache TTL hit the in-memory cache instead of GitHub's API, avoiding burns of GitHub's 60-req/hr unauthenticated rate-limit budget. Errors are NOT cached — a transient outage doesn't poison future checks. Internal `force=True` kwarg bypasses the cache (not currently wired to any UI affordance).

### Notes

- This release closes all P1 items raised during the pre-public-release code audit. The audit also surfaced ~10 P2/P3 items (UIPI per-miss logging, dwmapi `use_last_error=True`, multi-user file-permission consideration, etc.) — those are non-urgent polish and may land in a future release.

## [1.7.5] — 2026-05-20

UX + correctness pass.

### Fixed

- **Idle watcher gave up forever if its blank attempt didn't actually take effect.** The watcher set a single-shot `fired = True` flag when it triggered a blank, and only re-armed when the user became active again. But on some hardware/software combos the kernel's native idle-blank silently fails (a stay-awake tool resetting `GetLastInputInfo` during our 5-second policy window, a peripheral driver injecting a phantom mouse event, PowerToys Awake on a timer, etc.) — the watcher would record "fired", the screen would never blank, the user would stay idle, and the watcher would refuse to retry until the user came back. Net result: monitor lit after a long absence even with `idle_blank_minutes = 5` configured correctly. Now: if the cooldown expires and the user is *still* idle past threshold, the watcher infers the previous blank didn't stick and re-fires. Adds a heartbeat log every 5 minutes so a silently dead watcher thread is easy to spot in `displayoff.log`.
- **Quit menu killed in-flight blanks mid-restore.** Clicking Quit (or any path through `icon.stop()`) returned from `icon.run()` immediately, ran `main()` to its end, and let the interpreter shut down — even if the blank worker thread was still inside `blank_via_idle_path` holding the 1-second VIDEOIDLE timeout. The daemon thread would be killed mid-restore, the powercfg restore call never ran, and the user was left with a 1-second display-off timeout until the next launch's sentinel recovery fired. `native_blank`'s per-invocation `atexit` handler covered most of the cases in practice, but the race was real. Now: `on_quit` checks `_turn_off_lock.locked()` and waits up to 6 seconds for the in-flight blank to release the lock (cleanly finishing its own try/finally restore) before stopping the tray. Falls through to the `atexit` belt-and-suspenders if the wait times out.
- **About dialog flashed open at (0, 0) then jumped to centre + repainted with default-light titlebar before applying dark theme.** Matched the pattern already used by the Settings dialog: `withdraw()` immediately after `Toplevel()` creation, build all widgets and compute geometry against `winfo_reqwidth`/`reqheight`, apply dark titlebar, then `deiconify()` to show in final form.
- **About dialog crashed with `TclError: bad screen distance "0 10"`** as soon as the link Label was created. Tuple-form padding (`pady=(0, 10)`) is only valid on the geometry manager (`pack`/`grid`/`place`); on the widget constructor Tk parses the value as a single screen-distance and raises. Moved the tuple to `link.pack(padx=20, pady=(0, 10))` and dropped the unused constructor padding. Probably also explains the user-reported "About window draws laggy" — the body Label rendered, then the link Label exploded mid-build, leaving the dialog half-painted before the surrounding try/except swallowed the error.
- **About button in Settings appeared to do nothing.** Settings sets `-topmost True` on its root window. About is a `tk.Toplevel(parent_root)` and the original code deliberately *avoided* `-topmost`/`transient` "for fully independent z-order" — but the consequence is that About opens at normal Z-order, is immediately covered by the always-on-top Settings window, and the user sees no visible change. Now: About also sets `-topmost True` — the younger window (About) renders above the elder (Settings); closing About leaves Settings on top as expected. Both still stay above other apps while open, which matches user expectation for a modal info dialog.
- **Settings + About dialogs flickered on open** — multiple visible flashes between window creation, withdraw/deiconify, and dark-titlebar application. Root cause: Win11 paints default-light chrome on a previously-withdrawn window's first-show event, BEFORE our `DwmSetWindowAttribute(DWMWA_USE_IMMERSIVE_DARK_MODE)` re-paints it dark. The "re-assert dark titlebar after deiconify" line was already in place but ran on a visible window. Switched both dialogs to the alpha-mask pattern: `attributes("-alpha", 0)` → `deiconify()` → `update()` → `_apply_dark_titlebar()` → `update()` → `attributes("-alpha", 1)`. All chrome-repaint churn now happens with the window invisible; the user only sees the final, fully-themed window.
- **Right-click menu carried 5–7 chars of extra width** from the longest item `"  • Double-click this icon"` (26 chars with leading 2-space indent + bullet). pystray's native Win32 menu auto-sizes to the longest label, so trimming that item naturally narrows the whole popup. Now: `"• Double-click icon"` (19 chars) — same information, ~25 % less menu width.

### Added

- **Safe-hotkey guard.** The Settings dialog's only prior validation was "you pressed at least one non-modifier key" — meaning users could pick a bare letter (intercepts every press of that letter system-wide, can't type it normally any more), a bare F-key (intercepts F12 in every other app), a reserved combo like Alt+F4 / Alt+Tab / Ctrl+Esc (Windows gets them before pynput, hotkey silently dead), or a common-app shortcut like Ctrl+S / Ctrl+V (intercepts copy/paste/save in every other app — technically works, but probably not what the user meant). `_validate_hotkey_safety` now classifies the captured combo into one of three buckets:
  - **Block** (refuse to save): no modifier, modifier-only-no-key, or OS-reserved combo. Error dialog explains *why* the combo would silently fail and asks the user to pick another.
  - **Warn** (yes/no confirm before saving): common app shortcuts like Ctrl+C / V / X / Z / S / P / F / W / T / N / O / A / Y / Q. Users who genuinely want Ctrl+P (Print) as their blank-displays hotkey can still proceed.
  - **Allow**: everything else.

## [1.7.4] — 2026-05-16

Follow-up to v1.7.3 after a thorough code review. Five real findings + four stale-doc claims fixed; the rest were either false positives or out of scope.

### Fixed

- **`--off` / `--lock-and-off` / `--no-lock-off` / `--native-off` / `--legacy-off` / `--start-off` CLI paths bypassed `recover_stale_sentinel()`.** Eager sentinel recovery normally fires inside `run_tray()`, but every one-shot CLI-blank path `return`ed before reaching it. If a previous tray process was killed mid-blank (BSOD, power loss, Task Manager), the on-disk sentinel still named the original AC/DC display-off timeouts to restore; running `--off` in that state would have written a fresh sentinel over the saved values and trapped the user in a 1-second display-off loop. `main()` now runs `recover_stale_sentinel()` up-front whenever any of the off-flags is present in `sys.argv`. Idempotent + safe no-op when no sentinel is on disk; layered with the existing in-`run_tray` call so tray-mode launches still get the same protection.
- **`_run_update_check` blocked the Tk event loop for up to 5 seconds.** Clicking "Check for Updates" froze the Settings window during the GitHub API request; users would double-click thinking it had hung, queuing a second request, and the dialog window appeared crashed. Network call now runs in a `daemon=True` worker thread; result is marshalled back to the Tk thread via `parent_root.after(0, ...)` so the dialog stays responsive.
- **`_resolve_key` silently returned `None` for `vkNNN` config values.** `_pynput_key_to_name` emits `f"vk{key.vk}"` for KeyCodes with no printable char (media keys, app-defined keys), but the round-trip path on next launch had no branch for the `vk` prefix — so the recorded hotkey silently disabled itself. Added `keyboard.KeyCode.from_vk(int(name[2:]))` for `vk*` inputs.
- **`webbrowser.open()` return value was silently dropped** in three sites (Settings "GitHub" button, About dialog link, update-check "Open release page"). On locked-down user profiles where no URL handler is registered for `http://`, the click did nothing and there was no log entry to diagnose it. Extracted `_open_url(url)` wrapper that logs a warning when `webbrowser.open` returns False.
- **`_create_icon_image()` would `AttributeError` on Pillow < 8.2.** `rounded_rectangle` was added in Pillow 8.2 (2021). Production installs use the pinned `pillow==12.2.0` from `requirements.txt` and are unaffected, but a bare clone that `pip install`ed Pillow loose on an old Python environment would crash inside the fallback path — which is itself the safety-net for missing `.ico`. Now wraps the rounded calls in try/except `AttributeError` and degrades to a plain `rectangle` (square corners instead of rounded), with a one-line warning telling the user to upgrade Pillow.

### Cleanup

- Removed dead `on_turn_off` function in `run_tray` — defined but never wired to any menu item (the right-click menu deliberately has no clickable "Turn Off Displays" item per v1.6.0 — empirically the menu-item path triggered the identical code chain but the kernel never acted on the policy change). The orphan function would have been a footgun on any future menu refactor.
- `_RELEASES_API` was hardcoded in three places (`itsnateai/displayoff` URL duplicated in update-check, About dialog, GitHub-button). Consolidated to `_GITHUB_REPO`, `_GITHUB_REPO_URL`, `_GITHUB_RELEASES_URL`, `_RELEASES_API` — derived from one source-of-truth string. Renaming or forking the repo is now a one-line change.
- Documented the `DARK_BG` color invariant in `_create_icon_image()`: the moon-bite ellipse uses `DARK_BG` to carve the crescent out of the gold disc, which works because the icon's interior fill is also `DARK_BG`. If a future change ever introduces a different fill inside the monitor frame, the bite would become visible as a pixel-mismatch — comment now makes the constraint explicit so it doesn't silently drift.

### Documentation

- `README.md`: "Run at Windows startup" entry corrected from "registered in `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`" to "creates a `.lnk` shortcut in the user's Startup folder" — the registry path was the pre-v1.7.0 mechanism. v1.7.0+ uses Startup-folder `.lnk` and auto-cleans the legacy registry key on first toggle.
- `README.md`: Log-rotation note corrected from "plain `FileHandler` (no rotation) — clear them manually" to "`RotatingFileHandler` with 1 MB cap × 3 backups (v1.7.2+)". No manual cleanup needed.
- `CHANGELOG.md` v1.7.3 entry: removed the "Previous .ico preserved at `displayoff.ico.pre-v171-vis.bak`" line — that backup file was deleted during the wrap-up cleanup, so the claim was false. Rollback path is git history (`git checkout b130492 -- displayoff.ico`). Also reformatted the entry from one giant paragraph to bullets for skim-readability, and softened the "legible on any background" claim — only tested against Win11 dark-mode taskbar.

## [1.7.3] — 2026-05-16

### Changed

- **Tray icon redesigned for visibility on the Windows 11 dark taskbar.** The previous icon was a `(15, 15, 30)` dark navy disc with a `(100, 100, 200)` muted blue monitor outline and a small yellow moon. Against the `(32, 32, 32)` Win11 dark-mode taskbar the disc and the monitor outline both fell below the contrast threshold and the icon read as "a small yellow dot you could miss."
- **Design changes**:
  - Rounded square (~14% corner radius, Win11 app-icon convention) instead of a circle — fills more of the tray cell.
  - Bright cyan rim `(130, 200, 255)` is the load-bearing silhouette element on the Win11 dark taskbar (only tested against dark-mode; on light-mode taskbars the rim has lower contrast and the dark interior carries the silhouette instead).
  - Dark monitor `(18, 24, 40)` interior with a near-white frame `(235, 240, 250)`.
  - Gold crescent moon `(255, 210, 95)` inside the monitor.
- **Multi-size `.ico` re-baked at 9 sizes (16/20/24/32/40/48/64/128/256).** 16/20/24 are hand-drawn since downsampling the 256 design at tray sizes produced mush — the small variants drop detail progressively (16 = dominant gold crescent, no monitor; 20/24 add a hinted monitor; 32+ use the full design).
- **Programmatic fallback `_create_icon_image()` synced** to the same palette + shape so bare clones without `displayoff.ico` don't render a second-class icon. PIL < 8.2 lacks `rounded_rectangle`; the fallback now catches `AttributeError` and degrades to a plain `rectangle` so the tray still starts.
- **Rollback path**: git history. The pre-v1.7.3 `.ico` lives at commit `b130492` (`git checkout b130492 -- displayoff.ico` to restore the previous icon).

## [1.7.2] — 2026-05-15

24/7-readiness pass. Three parallel reviews (native handles / Python heap+threading / OS resources) against v1.7.1 with the program now expected to stay resident continuously. Two reviews returned SHIP with no findings; the OS-resource review caught one real unbounded-growth path.

### Fixed

- **`native_blank.log` would grow unbounded over the process lifetime.** `native_blank.py` used plain `logging.FileHandler` at both setup sites (`_setup_logging` and `_ensure_module_logger_has_filehandler`), while the sibling `displayoff.log` already used `RotatingFileHandler(maxBytes=1_000_000, backupCount=3)` with an explicit comment about why. With native idle-blank now the default path (v1.6.0+) every blank trigger writes ~10–15 log lines (pre-blank settle, idle samples every 250 ms during the 5-8 s blank, post-restore); at ~10 fires/day that's ~12 KB/day, ~4 MB/year — slow but unbounded, and the policy mismatch with `displayoff.log` was the real signal. Both handlers now use `RotatingFileHandler` with the same 1 MB × 3 budget. The `isinstance(h, logging.FileHandler)` idempotency check is preserved (RotatingFileHandler is a FileHandler subclass).
- **PIL `Image.open(_ICON_PATH)` held a file descriptor on `displayoff.ico` for the entire process lifetime.** PIL's lazy-load keeps the source fd open until the image is consumed or closed; pystray's consumption path doesn't guarantee a prompt close. On a 24/7 tray, that means the .ico file is locked against any in-place icon refresh or asset replacement for as long as the program runs. Wrapped in `with Image.open(...) as _im: icon_image = _im.copy()` to release the fd immediately while keeping the pixel data live. The `.copy()` is wrapped in try/except — `with`-block forces eager decode, so a truncated / 0-byte / corrupt `.ico` (Syncthing partial, OneDrive placeholder, AV quarantine-restore mid-read) raises here instead of being deferred into pystray. Failures fall through to the `_create_icon_image()` programmatic fallback that previously only fired on `isfile=False`.

### Hardened (follow-up to review round 2)

Five surfaced concerns from the review, fixed in scope so the 24/7 runtime guarantees hold under more failure modes:

- **`logging.StreamHandler()` would noisily emit-fail under `pythonw.exe`.** Both `displayoff.py:main` and `native_blank.py:_setup_logging` passed a bare `StreamHandler()` whose default `sys.stderr` is `None` under `pythonw.exe`. Every log emit raised `AttributeError: 'NoneType' object has no attribute 'write'` and was caught by `Handler.handleError`, but the swallowing path itself is wasteful and a footgun for future refactors. Both sites now conditionally append `StreamHandler()` only when `sys.stderr is not None`. File logging unchanged.
- **Idle-watcher could re-fire faster than intended on rapid input-jitter.** The `fired` flag normally serves as the cooldown (stays True while idle ≥ threshold, resets when user input drops it below), but the reset semantics are fragile to future refactors. Added `_IDLE_REFIRE_COOLDOWN_SECS = 60` — a hard wall-clock floor between fires regardless of idle state, as a belt-and-suspenders defense.
- **Cross-process race between tray and CLI.** `python native_blank.py --blank` run while the tray is mid-blank would see the in-flight sentinel as "stale crash recovery" and clobber the saved AC/DC values, leaving the user stuck at a 1 second display-off timeout. Added a named Win32 mutex `Local\DisplayOff_NativeBlank` that serializes all sentinel + powercfg writes across processes. Wrap points: `blank_via_idle_path` (tray import path), `recover_stale_sentinel` (eager startup recovery), and the CLI `--toggle` / `--blank` entries in `native_blank.main`. `--read` stays unguarded — it's truly read-only. `WAIT_ABANDONED` (previous owner crashed) is treated as a successful acquire; the next `_recover_from_stale_sentinel` call cleans up.
- **`_apply_dark_titlebar` called `ctypes.windll.user32.GetParent` directly,** violating the documented constraint "Never call `ctypes.windll.*` directly outside the bindings block" — default `c_int` restype truncates HWNDs above 2 GB on 64-bit. Added `GetParent` to the bound-name block with `argtypes=[HWND]`, `restype=HWND`; `_apply_dark_titlebar` now uses the bound name.
- **`CreateMutexW` + separate `GetLastError` binding could race across GIL release.** ctypes captures Win32 LastError into a thread-local IMMEDIATELY after the call returns when the binding handle has `use_last_error=True`; without it, a stray Python operation between `CreateMutexW(...)` and `GetLastError()` could clobber the value. Switched `_kernel32` in both `displayoff.py` and `native_blank.py` to `ctypes.WinDLL("kernel32", use_last_error=True)`. Read sites now call `ctypes.get_last_error()` (`_acquire_single_instance`, `_signal_other_to_quit`, `_watch_quit_event`). The bound `GetLastError = _kernel32.GetLastError` symbol was removed entirely — keeping it on a `use_last_error=True` DLL is a latent footgun, because calling it via ctypes would itself reset the saved thread-local and silently poison the next `get_last_error()` read. A docstring comment in the bindings block warns future contributors not to add it back.

### Round 3 follow-up

A third review round caught two ordering bugs the round-2 fixes introduced:

- **Idle-watcher cooldown gate ran BEFORE the `fired` reset.** During the 60 s cooldown window, the `continue` short-circuited past the `if idle < threshold: fired = False` reset — so a user who triggered a blank, woke the display, and then went idle again would never re-fire because `fired` stayed True past cooldown expiry. Reordered: `fired` reset now runs first (so user activity during cooldown is observed), `fired` check next, cooldown wall last (only blocks the actual fire). Also resets `last_fire = 0.0` when `idle_blank_minutes` is set to 0, so re-enabling within 60 s of a prior fire isn't blocked by stale cooldown state.
- **`_blank_mutex` fail-open vs `_acquire_single_instance` fail-closed asymmetry on CreateMutexW NULL** is intentional (different stakes — duplicate-tray cost > sentinel-clobber cost) but was undocumented. Added an explicit comment in `_blank_mutex` explaining the asymmetry, and upgraded the fail-open log from `warning` to `error` so the rare condition is more visible if it ever fires in production.

### Verified (no change)

- **No native Win32 / GDI handle leaks.** No `SetThreadExecutionState` calls (ES_DISPLAY_REQUIRED balance is vacuously satisfied). No `CreateWindow` / `RegisterClass` / `GetDC` paths in `native_blank.py` — the "native" in the name refers to the kernel's native idle-blank policy chain, not a Win32 window. The single-instance mutex and quit-event are held once for process lifetime and reaped by the kernel on exit.
- **No Python heap / threading retention.** Every `threading.Thread` is daemon-flagged; the watchdog, idle-watcher, and quit-watch are single-instance; per-blank thread spawn is gated by `_turn_off_lock.acquire(blocking=False)` so duplicates drop. `current_keys` is capped at 20 (`_KEY_TRACKER_OVERFLOW_CAP`). The `atexit.register` / `atexit.unregister` pattern in `native_blank.py` was already hardened against per-invocation accumulation (named function, unregister-in-finally) — a previous hardening pass that the audit confirmed still holds. Tk roots (Settings / About / Update-check) are destroyed on every close path; `root.after(50, poll_capture)` self-terminates when the pynput record-listener stops.
- **`subprocess.run` calls are fully waited.** No `Popen` fire-and-forget anywhere. Config JSON I/O is atomic (`.tmp` + `os.replace`) and `with`-guarded.

## [1.7.1] — 2026-05-14

Patch release closing the gaps surfaced by a v1.7.0 follow-up review. v1.7.0 introduced new helpers (`_ps_sq_escape`, `_read_lnk_target_path`, `_normalize_path`, etc.) which themselves carried second-order bugs — this release closes them before they reach production.

### Fixed

- **UTF-8 BOM in `_read_lnk_target_path` would have made stale-detection backfire** — `Write-Output` under `pythonw.exe` on Win11 can prepend a UTF-8 BOM (`﻿`) to the first line; the previous code's `.strip()` doesn't remove BOMs, so `os.path.normcase` comparison in `autostart_enabled()` would have failed forever (BOM-prefixed string never equals clean string). Symptom: every Settings open logs "Stale startup shortcut" and re-creates the .lnk on every Save. Fixed by adding `$OutputEncoding = [System.Text.UTF8Encoding]::new($false)` to the PS script (no-BOM directive) AND defensively stripping any residual BOM via `.lstrip("﻿")`.
- **Double-quote injection in the `Arguments` field** — `_create_startup_lnk` embeds `script` inside an inner double-quoted context (`'"{script_q}"'`) but only ran `_ps_sq_escape`. A path containing `"` (legal NTFS, rare but possible) would break out of the inner DQ context. New `_ps_dq_escape` helper doubles `"` per PS DQ rules; `script` is now passed through `_ps_dq_escape(_ps_sq_escape(...))` for both contexts.
- **`_read_lnk_target_path` hardcoded `timeout=10`** while every other PS call used `_PS_AUTOSTART_TIMEOUT_SECS = 30`. Cold-boot Win11 systems where PS JIT exceeds 10s would silently return `None` from the read, and `autostart_enabled()` would fall through to "assume valid" — a false-positive on the stale-detection path. Now uses the shared module constant via `_ps_run` default.
- **`autostart_enabled()` path comparison missed NTFS junctions / 8.3 short names / symlinks** — `os.path.normcase(os.path.abspath(...))` doesn't resolve any of those. Enterprise folder-redirected user profiles, installs under `C:\PROGRA~1`, or `WScript.Shell.TargetPath` returning the short form would all spuriously trip stale-detection. New `_normalize_path()` helper uses `os.path.realpath` + `normcase` to canonicalize before comparison; falls back to `abspath` if `realpath` raises (e.g., target doesn't exist).
- **`set_autostart()` introduced a `bool|str` type pollution** in the v1.7.0 commit — `legacy_state = _legacy_run_key_present()` could be `bool`, then on `OSError` rebound to `"unreadable"` (str). Harmless today (only used in log.info) but a footgun for future `if legacy_state:` refactors that would silently treat a locked hive as "present". Refactored to build a `legacy_desc` string for the log line only, keeping `_legacy_run_key_present`'s return contract a clean `bool|raise`.

### Changed (UX)

- **Settings dialog now caches the autostart on-disk state at open time** instead of re-spawning a PS subprocess on every Save's change-detection. Previously, opening Settings + clicking Save triggered TWO PS subprocesses to answer "did the checkbox change?" — each potentially adding multiple seconds to the dialog's response time on cold-boot systems.
- **Autostart-failure messagebox text fixed.** v1.7.0's text told the user to "Dismiss this dialog, then re-open Settings to retry" — but v1.7.0 also changed `_apply_settings` to return `False` on autostart failure, which keeps the dialog open. The messagebox is now consistent: "Your other settings were saved. Adjust and click Save again to retry — the dialog stays open."

### Docs

- `CLAUDE.md` Tech Stack section now reflects v1.7.0+ reality — `.lnk` shortcut via PowerShell + `WScript.Shell` COM is canonical; `winreg` is retained for legacy-cleanup only. LOC count bumped from "~1200" to "~2200" (the autostart hardening grew the file).

## [1.7.0] — 2026-05-14

### Fixed

- **"Run at Windows startup" + Save silently did nothing.** `_create_startup_lnk` referenced `subprocess.STARTUPINFO`, `subprocess.run`, and `subprocess.STARTF_USESHOWWINDOW` against an undefined name — `subprocess` was never imported at module top. Every Save click with the autostart checkbox ticked raised `NameError` inside Tk's button callback. Under `pythonw.exe` (no console), Tk's default `report_callback_exception` writes the traceback to a stderr that has nowhere to go, so the exception evaporated with no error dialog and no log entry. The Settings dialog stayed open because `root.destroy()` was never reached; the user saw "Save does nothing." Root-cause fix: `import subprocess` at module top, plus 3 layers of defense-in-depth listed below.

### Changed (autostart subsystem hardening — 2026-05-14)

- **Switched from HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run registry entry to a `.lnk` shortcut in the user's Startup folder** (`%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\Display Off.lnk`). The `.lnk` is visible and manageable in File Explorer. Legacy HKCU Run entries from v1.6.0 are detected and removed automatically on first toggle (logged as `Removed legacy HKCU Run\\DisplayOff autostart entry (migrated to Startup-folder .lnk)`).
- **`autostart_enabled()` now validates the .lnk's `TargetPath`** against the current `pythonw.exe` path resolved by `_autostart_target_pythonw()`. A stale shortcut pointing at a Python install that was upgraded or moved is treated as "not enabled" so the next Save automatically refreshes it instead of silently leaving autostart broken. Validation uses the same `WScript.Shell` COM API to read `TargetPath`; if reading fails (PS missing / COM error / timeout) we conservatively assume the .lnk is valid and let the next user-initiated Save reconcile.
- **PowerShell single-quote injection in `_create_startup_lnk`** — every interpolated value (`_STARTUP_LNK_PATH`, `py`, `script`, `working_dir`, `icon_path`) is now run through new `_ps_sq_escape` helper which doubles every `'` per PS literal rules. Paths containing single-quotes (legal NTFS, e.g., `C:\\Users\\O'Brien\\...`) previously would have terminated the PS string early, producing either a parse error or — in a pathological hostile-path case — arbitrary PS execution.
- **`_remove_startup_lnk` is now TOCTOU-safe** — uses `try: os.remove ... except FileNotFoundError: pass` instead of an `os.path.exists` precheck. Added a post-removal verify-back symmetric to the create-side verify, so a sync-software replication (OneDrive, Syncthing) or AV restore-from-quarantine putting the .lnk back gets surfaced as an error instead of silently flipping autostart back on at next logon.
- **PowerShell timeout bumped 10s → 30s** via new `_PS_AUTOSTART_TIMEOUT_SECS` constant. 10s could fire `subprocess.TimeoutExpired` on cold-boot Win11 systems where first-launch PS JIT, group-policy script-block-logging, or AV real-time scanning briefly delay PS startup past the budget. `TimeoutExpired` is NOT an `OSError` subclass — it would have escaped the v1.6.0 `except OSError` guard silently.
- **New `_ps_run` wrapper** catches `FileNotFoundError` (powershell.exe not on PATH — PSCore-stripped systems / locked-down profiles) and `subprocess.TimeoutExpired`, translating both to `OSError` with a clear diagnostic message so `set_autostart`'s "Raises OSError on creation failure" docstring contract is truthful.
- **`_legacy_run_key_present` distinguishes `FileNotFoundError` (definitely absent) from `PermissionError` (locked hive / Group Policy / can't tell)** — previously broadly caught both as "absent" which silently broke the v1.6.0→v1.7.0 migration on locked profiles. Caller in `autostart_enabled()` still treats "can't tell" as "not enabled" (best-effort) but the warning lands in `displayoff.log` so a user with a locked Run hive can see why their legacy entry persists.
- **`_delete_legacy_run_key` logs (does NOT raise) on `PermissionError`** — caller treats legacy cleanup as best-effort but the warning is now visible.
- **`APPDATA` environment-variable check at module load** — if `APPDATA` is unset, `_STARTUP_LNK_PATH` is empty and every autostart function raises a clear `OSError("APPDATA environment variable is not set...")` instead of the v1.6.0 behavior of silently joining onto an empty string and writing/reading a CWD-relative path that wouldn't actually autostart.

### Changed (Tk silent-failure prevention — applies to Settings, About, Updates dialogs)

- **`root.report_callback_exception` is hooked to the logger** in `_open_settings_impl` immediately after `tk.Tk()`. Tk's default callback handler writes tracebacks to stderr, which is /dev/null under pythonw.exe — any exception in a button command, key bind, `after()` callback, or virtual-event handler that wasn't explicitly caught (e.g., a future `NameError`, an `AttributeError` on a pynput KeyCode shape change) would otherwise vanish with no log entry. With this hook, every Tk-callback exception now lands in `displayoff.log` with full traceback. This single line of defense protects every other button in the dialog (Cancel, About, Updates, GitHub link) without per-callback try/except boilerplate.
- **`_apply_settings` autostart exception catch widened from `OSError` to `Exception`** with `log.exception` and a more informative messagebox that includes the exception type and prompts the user to re-open Settings and retry. `NameError`, `AttributeError`, `subprocess.TimeoutExpired`, and `TclError` are NOT `OSError` subclasses and would have escaped the v1.6.0 guard.
- **`_apply_settings` now returns False on autostart failure** so the Settings dialog stays open for retry instead of destroying the root and forcing the user back to the tray menu. The autostart_var is also refreshed to the actual on-disk state via `autostart_var.set(autostart_enabled())` so the checkbox visually matches reality.

### Added (observability)

- **`log.info` instrumentation in every autostart entry/exit** — `set_autostart()` logs the desired state plus current `.lnk` and legacy-registry presence; `_create_startup_lnk()` logs the target/args/lnk paths before invocation and a post-create confirmation with byte size; `_remove_startup_lnk()` logs both the successful-remove and the no-op-already-absent path. Catches future regressions where a UI element claims success but no underlying state actually changed.
- **PowerShell stderr-on-success is now logged at DEBUG level.** rc=0 with non-empty stderr is usually deprecation warnings or profile-script noise; previously thrown away silently.
- **`displayoff.log` rotation** — switched from unbounded `FileHandler` to `RotatingFileHandler(maxBytes=1_000_000, backupCount=3)`. A tray app logs every icon click, blank-trigger, listener-watchdog tick, idle-watcher sample; unbounded growth was an inevitability waiting to bite an active user.
- **Verify-back on `.lnk` creation** — `PowerShell rc=0` doesn't guarantee the file landed on disk (AV quarantine, COM `Save()` silent no-op on locked-down profiles, exec-policy edge cases). Post-write `os.path.exists` check raises `OSError` with a diagnostic message including stdout/stderr from the PS run. Same pattern as the post-publish GitHub release-asset verify-back used by the workspace's sibling tray apps.

### Notes

- The v1.6.0 `HKCU\\...\\Run\\DisplayOff` autostart code was the only path that ever shipped. The `.lnk`-based code in v1.6.0's source tree was staged but broken from the first commit (missing import, never tested via the Settings GUI under pythonw); no user ever successfully used the .lnk path on v1.6.0. v1.7.0 is the actual first working release of the Startup-folder shortcut migration.
- The Tk-silent-swallow trap under pythonw is a general risk for any Python tray app using Tk dialogs — the `report_callback_exception` hook here is the recommended defense pattern.

## [1.6.0] — 2026-05-14

### UX

- **Double-click the tray icon to blank.** Single-click is a no-op (opens menu / does nothing visible). Ctrl+Alt+F12 hotkey unchanged.
- **No clickable "Turn Off Displays" menu item.** The right-click menu shows a disabled informational label documenting the two paths that work (double-click + hotkey). The clickable menu item was removed after empirical testing on the developer's hardware: the menu-item path ran the identical code chain as double-click and hotkey (verified via `displayoff.log` + `native_blank.log` instrumentation — same `_fire_native_idle_blank` → `blank_via_idle_path` → powercfg writes → idle counter accumulating past threshold per `GetLastInputInfo` polling, with `powercfg /requests` confirming nothing was holding the display awake), but the kernel never acted on the policy change for menu-triggered invocations. Hypothesis: `powercfg /setactive SCHEME_CURRENT` is a lazy refresh that gets optimized away when the active scheme is unchanged, and the kernel only re-reads the live policy when prodded by the right state changes — the two working paths produce some side effect the menu path doesn't. Rather than ship a silently-broken click, the item is now an informational label.
- **Click-timing implementation:** pystray on Windows fires `default=True` menu items on every left-click (single, double, triple) — its API has no separate single-vs-double event. To get true double-click semantics, the tray menu includes a hidden `default=True` item (`visible=False`) that's routed to a click-gap handler. The handler measures the time between successive icon clicks and only fires the blank when two land within 500ms (matching Windows' `GetDoubleClickTime()`). First click records a timestamp and exits silently; second click within the window fires the blank and resets the pair.

### Diagnostics

- **`displayoff.log`** — new file-backed logger so pythonw.exe runs are debuggable. Records every blank trigger source (icon-double-click, menu-turn-off via the now-removed item, hotkey path) and lock-collision drops. Previously every `log.*` call under pythonw went to a NullHandler.
- **`native_blank.py` import-path logging** — when imported by displayoff.py (rather than run as a script) it now attaches a FileHandler to its own logger so log entries still reach `native_blank.log`. Without this, blank invocations from the tray left zero forensic trail.
- **Idle-counter sampling during the sleep window** — `_sleep_with_idle_log` polls `GetLastInputInfo` every 250ms and logs the idle-seconds samples. Made it possible to prove that the menu-item path's failure was NOT an idle-reset issue (samples cleanly accumulate past threshold) but a kernel-policy-refresh issue.

### Hardening (post-implementation review)

- **`_fire_native_idle_blank` no longer falls back to `SC_MONITORPOWER` on `ImportError`.** The whole reason v1.6.0 exists is that `SC_MONITORPOWER` cycles the display on affected hardware; silently falling back to it on a broken install would re-introduce the very bug v1.6.0 ships to fix. Now refuses to blank, logs loudly.
- **`native_blank()` finally block is now resilient.** Previous version called `_write_display_timeouts(saved_ac, saved_dc)` followed by `_clear_sentinel()`. If powercfg failed during restore, the un-wrapped `RuntimeError` propagated out before `_clear_sentinel()` could run — leaving the sentinel orphaned on disk forever. Wrapped in try/except; verifies values match before clearing sentinel; logs manual-recovery command if restore fails.
- **`_recover_from_stale_sentinel` deletes corrupt/invalid sentinels.** Previous version logged a warning and left the unreadable file in place; every subsequent launch hit the same wall and bailed. Now deletes the unreadable file so the system can recover from a one-shot corruption.
- **Hidden powercfg subprocess windows.** Under `pythonw.exe`, every `subprocess.run("powercfg.exe", ...)` call was allocating a fresh console window, producing ~10 visible terminal flashes per blank invocation. Added `creationflags=CREATE_NO_WINDOW` + `STARTUPINFO(dwFlags=STARTF_USESHOWWINDOW, wShowWindow=SW_HIDE)` in `native_blank._run_powercfg`. The window churn was also resetting Windows' idle-input counter, preventing the native blank from firing — hiding the subprocesses fixed both symptoms.

### Changed

- **Native idle-blank is now the default mechanism** for *every* blank trigger: tray icon click, tray menu "Turn Off Displays" item, Ctrl+Alt+F12 hotkey, idle-blank watcher, and `--off` / `--lock-and-off` / `--no-lock-off` / `--start-off` CLI flags. All paths now route through `turn_off_monitors()` which dispatches to `_fire_native_idle_blank()` by default and to the legacy `_fire_sc_monitorpower()` only when explicitly opted in.
- **Production blank window** in `_fire_native_idle_blank()` is **5 seconds** (down from the 8s test-harness default in `native_blank.py`). Bumped from the originally-planned 2.5s after empirical "menu click → no blank" reports — when the user navigates the right-click menu, the mouse moves continuously and the kernel's idle counter keeps resetting, so a tight window of 2.5s left no time for the kernel to cross the 1s threshold. 5s tolerates ~3s of post-click motion. Combined with the 0.5s pre-blank settle (`_NATIVE_PROD_SETTLE_SECS`), the dispatcher lock is held ~5.5s per blank — silently dropped duplicate triggers are now explicitly logged in `displayoff.log`.

### New

- **`use_legacy_sc_monitorpower` config key** (default `false`) — set to `true` in `displayoff_config.json` to force every blank trigger back to the v1.0–v1.5 `SC_MONITORPOWER` behavior. Useful on hardware where the legacy path works fine and you want the slightly faster blank (~0.5s vs ~1–2s).
- **`--native-off` CLI flag** — forces the native idle-blank path regardless of config. Identity-clear opt-in for scripts/shortcuts that must blank via this path no matter what.
- **`--legacy-off` CLI flag** — forces `SC_MONITORPOWER` regardless of config. Symmetric counterpart to `--native-off`, useful for testing or for users who want one-shot legacy behavior without mutating their config.
- **`force_path` parameter** on `turn_off_monitors()` — `"native"` / `"legacy"` / `None` (honor config). Both new CLI flags route through the unified dispatcher so they inherit the single-instance lock, RDP early-return, and `lock_first` handling.

### Why this is the right default

The v1.5.0 changelog explained why the native path is required on some hardware (Modern Standby + hybrid GPU laptops where `SC_MONITORPOWER` triggers a wake-handshake loop). v1.6.0 takes the conclusion to its logical end: native is strictly safer (works on every Windows version since Win95, is OEM-driver-friendly, uses the same code path as the built-in Settings dropdown). The only downside is a ~1-second-slower blank, which doesn't matter for the "click and walk away" use case. Users on hardware where `SC_MONITORPOWER` is fine can opt back in with one config key.

## [1.5.0] — 2026-05-14

### New Features

- **`--native-off` CLI flag** — turns off displays via Windows' own idle-display-off code path instead of `SC_MONITORPOWER`. Temporarily writes `GUID_VIDEO_POWERDOWN_TIMEOUT = 1s` via `PowerWriteACValueIndex` + `PowerSetActiveScheme`, waits ~8s for the kernel to fire its native idle-blank, then restores the original AC/DC timeouts. No `SC_MONITORPOWER` message is sent — uses the exact mechanism wired to **Settings ▸ Power ▸ "Turn off the display after N minutes."** Required on Modern Standby + hybrid-GPU hardware where `SC_MONITORPOWER` triggers a wake-handshake loop (verified empirically — repeated SC_MONITORPOWER events fire in rapid succession with no input recovery).
- **`native_blank.py`** — standalone helper module with three modes:
  - `--read` — print current AC/DC display-off timeouts (zero risk, no writes)
  - `--toggle` — write 1s timeouts, sleep 0.5s (too short to actually blank), restore (plumbing test)
  - `--blank` — full sequence with 8s blank window and 6s "hands off keyboard/mouse" countdown
  - Crash-safe: writes a sentinel file before changing timeouts; uses `try/finally` + `atexit` + sentinel-based recovery on next launch so a hard kill mid-run cannot leave the user stuck with a 1-second display timeout. Logs to `native_blank.log`.

### Why a second code path

`SC_MONITORPOWER` is the documented, canonical API and works on virtually every Windows PC. But on certain Modern Standby + hybrid-GPU laptops, the userland-message → kernel-power-policy handoff lands in a no-recovery wake loop. The native idle-display-off path (the one the Settings dropdown writes to) has been working reliably on every Windows version since Win95 and is OEM-driver-friendly. `--native-off` is the safe fallback for users on affected hardware. `--off` and `--lock-and-off` continue to use `SC_MONITORPOWER` for backward compatibility on hardware where it works fine.

## [1.4.0] — 2026-05-08

### New Features

- **Lock-and-off** — optional Settings checkbox + `--lock-and-off` CLI flag. Locks the workstation before powering off displays, so a passerby can't wake the screen and see your work.
- **Autostart toggle** — Settings checkbox to register Display Off in `HKCU\…\Run` (uses `pythonw.exe` so there's no console flash at logon).
- **Auto-blank when idle** — Settings spinbox sets a "blank after N minutes idle" threshold. A 15-second-poll watcher reads `GetLastInputInfo` and fires once when the threshold is crossed; the "fired" flag re-arms the next time idle drops below the threshold (so brief activity windows shorter than the 15-second poll may permit a second fire). 0 = off (default).
- **About dialog** — new tray menu item showing version, current hotkey, lock/idle/autostart state, and the project URL.
- **Check for Updates** — new tray menu item that hits the GitHub releases API and offers to open the release page in the browser if a newer version is available. No automatic phone-home — manual only.
- **First-run notification** — one-time tray balloon on initial launch announcing the configured hotkey.
- **Apply button** in Settings — persist changes without closing the dialog. Save persists and closes; Cancel just closes the window (any in-dialog edits not yet Saved/Applied are discarded — already-Applied changes are persisted to disk).
- **CLI flags** — `--lock-and-off` (force lock + blank), `--no-lock-off` (force blank-without-lock; overrides config), `--quit-other` (signal a running tray instance to quit), `--reset-config` (delete the config file).
- **Esc cancels hotkey recording** in the Settings dialog.
- **Listener watchdog** — a 30-second poll restarts the global hotkey listener if pynput's hook is missing or its thread is dead. Common causes are session lock, RDP connect, and fast-user-switch; the watchdog detects them indirectly via liveness polling, not via session-event subscription.

### Fixed

- **Settings dialog could spawn two Tk roots and crash** if clicked twice in quick succession — now claims the slot under a lock before the worker thread is spawned, and the dialog flag is always cleared via try/finally.
- **`current_keys` overflow guard fired on the wrong path** — moved from `on_release` (where the set just shrank) to `on_press` (where missed-release accumulation actually grows it).
- **`save_config()` exceptions left the dialog flag stuck** — read-only file or full disk would silently disable the hotkey for the rest of the session. Now caught with a user-facing error and the flag reset cleanly.
- **Single-instance mutex used `Global\` scope**, blocking second-user sessions under Fast User Switching. Switched to `Local\` (per-session).
- **`SC_MONITORPOWER` was a confusing no-op inside RDP sessions** — now early-returns with a log message when `GetSystemMetrics(SM_REMOTESESSION)` is non-zero.
- **Tk dialogs were not DPI-aware** — calls `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` before creating the root, falling back gracefully on older Win10 builds.
- **Hotkey listener restart could briefly double-fire** — old listener thread is now joined before the new one starts.
- **Hotkey-only-modifier capture left recording stuck** — pressing only Ctrl/Alt/Shift and releasing no longer hangs the recorder; Esc cancels.
- **Save-time hotkey validation** — refuses to save a binding without at least one non-modifier key.
- **First-run welcome could clobber a user's saved settings** — the welcome thread now re-checks `displayoff_config.json` existence after the 1-second delay, so a fast user who opens Settings and saves before the welcome fires won't see their config overwritten with defaults.
- **`_get_modifier_map` lazy init was not thread-safe** under nogil/free-threaded Python. Now uses double-checked locking.

### Code Hygiene

- `logging.basicConfig` moved into `main()` — no longer clobbers a host application's root logger if the module is ever imported.
- `hotkey_display_name(cfg)` no longer has a `cfg=None` default that silently does I/O.
- Magic numbers (`20`, `0.5`, `5000`, `183`) named as module constants.
- `os.startfile(URL)` for the GitHub button replaced with `webbrowser.open(URL)`.
- Pinned `requirements.txt` to known-good versions (was floating `>=` bounds).
- `_create_icon_image` documented as fallback-only.
- **Settings dialog decomposed** into row builders (`_build_header`, `_build_hotkey_row`, `_build_options_section`, `_build_footer`). The orchestrating `_open_settings_impl` shrank from 168 lines to 74. Adding a new option row is now a one-line `_build_*(root, row=N, ...)` call in the impl plus a sibling builder.
- **UIPI hint at startup**: when running unelevated, logs a one-line note that the hotkey may not fire while an elevated window has focus (Task Manager, admin terminals, UAC consent). Documented in README's Caveats section.

### Review closeout (post-audit hardening)

The above v1.4.0 changes were reviewed (concurrency, Win32, functional walkthrough, doc-vs-code gap) before tag. The following hardening followed:

- **Win32 HANDLE truncation** — `CreateMutexW`, `CreateEventW`, `OpenEventW` were called via raw `ctypes.windll.*` lookups, defaulting `restype` to `c_int` (4 bytes). On 64-bit Windows the kernel could in theory return a HANDLE with bit 31 set, which would round-trip incorrectly through `c_int` and cause `CloseHandle` on a stale value. Now bound with `restype = HANDLE` (`c_void_p`) in the platform-guarded block.
- **`GetTickCount` signed arithmetic** — without a `restype = DWORD` binding, ctypes returned signed `c_int`, which goes negative after ~24.8 days of uptime and silently breaks idle-blank arithmetic. Now bound `restype = DWORD`; the subtraction is also masked with `& 0xFFFFFFFF` so the wraparound at ~49.7 days produces correct elapsed time.
- **Watchdog stale-listener race** — the watchdog snapshotted `_active_listener` outside `_listener_lock`, then called `is_alive()` on the snapshot. A concurrent Save→restart could leave the watchdog acting on a stale reference and force-restart a healthy listener. Fixed by adding `start_hotkey_listener(force=False)` which performs the liveness check + conditional restart atomically under the lock; the watchdog now just calls that.
- **`_dialog_active` cleared without lock** — three `finally` blocks cleared the flag with a bare assignment. Cleaned up via a `_release_dialog_slot()` helper that takes `_dialog_lock` for forward-compat with free-threaded Python.
- **`SetEvent` / `WaitForSingleObject` failure paths** — `_signal_other_to_quit` collapsed "instance found, signal failed" into the same "no instance" return; now returns a tri-state (`signaled` / `missing` / `error`) and the caller logs accordingly. `_watch_quit_event` now checks for `WAIT_FAILED` and `WAIT_ABANDONED` explicitly.
- **`save_config` non-atomic write** — `open(..., 'w')` truncates before writing; the idle watcher's 15-second `cfg_provider()` could read a half-written file. Replaced with a write-temp-then-`os.replace` pattern (atomic on NTFS).
- **`SetProcessDpiAwarenessContext(-4)`** — the `-4` sentinel needed `c_void_p` for sign-extension on 64-bit. Bound `argtypes`/`restype` correctly.
- **`_LASTINPUTINFO` conditional dead code** — the non-Windows branch of the field-type ternary was unreachable. Simplified to unconditional `c_uint`.

## [1.3.0] — 2026-04-18

### New Features
- Single-instance mutex — launching a second instance brings the existing tray icon forward instead of starting a duplicate
- `--start-off` CLI flag — turn monitors off and then start into tray in one step

### Fixed
- Crash on resume from sleep (now targets desktop window instead of broadcasting)
- Hotkey repeat no longer spawns redundant threads
- Semgrep security scanning workflow on CI

## [1.1.0] — 2026-03-21

### New Features
- `--version` flag to print current version
- `displayoff.ico` — crisp multi-size icon (16–256px) used in tray when present
- `README.md` — user-facing documentation
- Proper `logging` module replaces raw `print` statements

### Bug Fixes
- Fix ctypes 64-bit pointer truncation by setting `argtypes`/`restype` on `SendMessageW`
- Fix mixed modifier hotkey (e.g. Ctrl_L + Alt_R + F12) silently failing
- Guard `ctypes.windll` behind platform check so module imports on non-Windows
- Prevent thread spam from repeated hotkey/tray triggers via `threading.Lock`
- Cap `current_keys` set to prevent unbounded memory growth from missed release events
- Remove unused `MONITOR_ON` constant

## [1.0.0] — 2026-03-21

### New Features
- System tray app with programmatic moon/monitor icon
- Turn off all displays via tray click or global hotkey (Ctrl+Alt+F12)
- `--off` flag for immediate one-shot display off (no tray)
- 300ms delay prevents immediate wake from trigger input
- Graceful fallback if pynput not installed (tray-only mode)
