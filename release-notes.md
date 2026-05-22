Backlog drain — sub-threshold items from the v1.7.13 + v1.7.14 verifier rounds, plus a build-infrastructure pass that auto-builds the .exe on tag push instead of from the maintainer's machine. No new user-facing features.

## Highlights

- **CI-built releases.** From v1.7.15 onward the `displayoff.exe` + `SHA256SUMS.txt` you download are produced by `.github/workflows/release.yml` on a `windows-latest` runner — not on the maintainer's machine. SHA-transparency: the manifest is computed from bytes the runner just built. Action pins are full commit SHAs per the workspace supply-chain baseline.
- **`tray_promoter.sweep_stale_entries` is now wired up under freeze.** Cleans `NotifyIconSettings` cruft from prior `displayoff.exe` install locations (relevant if you've ever moved the `.exe` between folders). Guarded so `.py` source mode skips entirely.
- **In-memory dedupe** for the frozen-first-launch promotion ping. Defends against any future code path re-invoking `_frozen_promote_ping` from the same process. Same-session double-fire previously possible (only theoretical — no current re-entry path) is now impossible.
- **`_TRAY_SETTLE_SECS = 1.0` constant** extracted so the first-run welcome notification's settle window and the promote-ping's settle window stay coupled across future timing changes.
- **Rename-dance step numbering reconciled** between code and the v1.7.13 CHANGELOG (9-step framing now used in both).
- **Build script comments** carry a version-check timeline for the Nuitka `--onefile-no-compression` workaround. As of 2026-05-21 (v1.7.15), Nuitka latest is still 4.1.1, py3.14 zstd fix has not shipped, flag stays.

## Verifier round

6-agent normal-stakes round (3 topics × Sonnet+Opus): T1 Diff-clean, T2 Gap-audit, T3 Code-review. Convergent findings applied before tag: stale "step 5" references in `_write_update_relaunch_state` (now "step 7"), stale `tray_promoter.sweep_stale_entries` docstring assertion ("NOT INVOKED FROM DISPLAYOFF" → "invoked from displayoff under freeze v1.7.15+"), narrow `try/except Exception` around the sweep call (defense-in-depth — the function already wraps registry I/O internally), `build-release.sh` "Next steps" banner now points at the CI flow rather than `gh release upload`, CHANGELOG wording precision on `_PING_FIRED_THIS_PROCESS` semantics (set after successful `icon.notify()`, with explicit Focus-Assist-suppresses-toast-but-`notify`-still-returns-OK note).

## Known disclaimers

- **The rename-dance still has no live end-to-end exercise.** v1.7.13 was the first .exe (nothing to update FROM). v1.7.14 was a same-day patch (also no real update flow). v1.7.15 is the first release that *could* exercise the dance — a user on v1.7.13 or v1.7.14 clicking "Install now" against this release is the test cohort. Worst-case failure is "user has to manually re-download from this page"; the next-launch recovery path (`_recover_from_failed_update`) cleans up partial-state artifacts automatically.
- **Persistent `save_config` failure under read-only `%APPDATA%`** still re-fires the promotion toast every launch. In-memory dedupe added in v1.7.15 fixes the same-session case; the cross-launch case requires write access somewhere on disk and is acceptable as documented (rare, benign — one extra toast per launch, no functional impact).

Full changelog: see `CHANGELOG.md`.
