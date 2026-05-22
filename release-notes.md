Path-resolver hotfix + diagnostic-observability fix. v1.7.18's inaugural in-the-wild rename-dance failed empirically: a v1.7.17 → v1.7.18 update attempt resolved the running .exe path to the temp-extracted Nuitka bootstrap python.exe (Strategy 3 — the broken last-resort) and tried to rename that instead of the actual on-disk `displayoff.exe`. Worse, the resolver's diagnostic candidates were silently swallowed because v1.7.18 only logged them when data-dir migration ran, and the affected process had already migrated.

v1.7.19 fixes both: a new Strategy 0 (Nuitka's `__compiled__.original_argv0`, which was correct in every resolver-candidates line we have on file) layered before Strategy 1, AND `path-resolver:` log lines that fire on EVERY startup, AND a `--diagnose-paths` CLI flag for ad-hoc triage.

## What's fixed

- **New Strategy 0**: `__compiled__.original_argv0` — Nuitka-authoritative invocation path, available even after the bootstrap parent exits (one plausible Strategy 1 failure mode). Same hardening triple as Strategies 1 & 2: `.exe` extension, `os.path.isfile`, not under any TEMP-like dir.
- **Path-resolver diagnostics now log on every startup**, with prefix `path-resolver:`. v1.7.18 buffered them through `_MIGRATION_LOG` which only emitted with prefix `data-dir migration:` and only when migration ran. Result: the v1.7.18 dance failure was un-diagnosable from the log alone.
- **`displayoff.exe --diagnose-paths`** — new CLI flag. Prints version, frozen state, the winning `_EXE_PATH`, and every resolver candidate / strategy decision to stdout. Runs before `%APPDATA%` setup so the flag works even when the data dir is broken. Anyone reporting a future dance failure can paste the output into a GitHub issue without needing to attach the log.
- **Strategy 3 WARNING** now points users at `--diagnose-paths` instead of `displayoff.log`.

## Upgrade path

- **v1.7.18 users** — click Settings → Check for updates → Install now. On a clean v1.7.18 install (Strategy 1 working at startup), the dance should run end-to-end. If it fails, the post-recovery v1.7.19 will print the full resolver state via `displayoff.exe --diagnose-paths`.
- **v1.7.17 users** — manual install is still required. v1.7.17's resolver does not have the v1.7.18 hardening AND does not have the v1.7.19 Strategy 0, so the in-app updater can still fall to broken Strategy 3 against this release. Quit the tray, download `displayoff.exe` below, replace the binary in place, and relaunch.
- **v1.7.13 / v1.7.14 / v1.7.15 / v1.7.16 users** — manual install same as above. These releases have the structural v1.7.13 path-resolution bug.
- **.py source-mode users** — no action; resolver returns `None` under source, rename-dance is skipped.

## What v1.7.19 doesn't yet fix (deferred to v1.7.20+)

- 300 ms parent-`os._exit` vs child-mutex race in the dance child relaunch.
- `_UPDATE_MIN_EXE_SIZE` tighter floor.
- `release.yml` permissions tightening + post-upload redirect-host smoke test.
- `_themed_dialog` sticky `minsize()` floor.
- `_download_url_allowed` URL parser hardening, `_migrate_legacy_data` cross-device atomicity, `_DwmSetWindowAttribute` rebinding, tray_promoter docstring.

These are deliberately held back so v1.7.19's changelog is narrowly about "the dance failed; here's how to make it observable and safer". Polish bundles into v1.7.20.

Full changelog: see `CHANGELOG.md`.
