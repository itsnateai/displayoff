Hotfix. v1.7.15's first live exercise of the rename-dance updater (introduced in v1.7.13) failed because GitHub had migrated the release-asset CDN to a domain not in the hardcoded allowlist. The update-available dialog also had a clipped button row. Both fixed.

## What's fixed

- **Self-updater works again** — added `release-assets.githubusercontent.com` to the update-host allowlist. GitHub migrated the release-asset CDN from `objects.githubusercontent.com` to the new host over 2025; the hardcoded allowlist hadn't caught up. v1.7.13 / v1.7.14 / v1.7.15 all carried this latent bug — it only surfaced when v1.7.15 was the first release to have ANOTHER release (this one) to update to. The legacy `objects.githubusercontent.com` is still in the list for any older release whose URLs were baked before the migration.
- **Update-available dialog button row no longer clips.** Middle button label shortened to "Releases page" (was "Open releases page") so the three-button row fits the dialog at default DPI. `_themed_dialog` now also floors the dialog's geometry width to the button row's required width as a defense against future widening.

## Upgrade path — **manual install required for v1.7.13 / v1.7.14 / v1.7.15**

The bugs fixed in v1.7.16 are in the **client's own** updater code, not on the server side. That means v1.7.13 / v1.7.14 / v1.7.15 clients still hit the broken allowlist when they try to fetch any newer release — including this one. The chicken-and-egg solution is to install v1.7.16 manually, ONCE; from then on the in-app updater works for future releases.

**Manual install steps (from v1.7.13 / v1.7.14 / v1.7.15):**

1. Right-click the Display Off tray icon → **Quit** (releases the .exe file lock).
2. Download `displayoff.exe` from this page (the button below — or use the in-app "Open releases page" fallback if the dance failed and put you here).
3. Replace your existing `displayoff.exe` with the new file (same filename, same location).
4. Launch the new v1.7.16. Your config + autostart .lnk + idle settings carry over unchanged (config lives in `%APPDATA%\displayoff\`, the .lnk references the .exe by path).

**From v1.7.16 onward,** "Settings → Check for updates → Install now" should work end-to-end. The "Releases page" fallback button is also relabeled (was clipped as "Open releases pa…") and reliably opens this page if the dance ever fails again.

If you're on the .py source channel, no action needed — the dance only applies to the frozen `.exe`.

## What this exposes

v1.7.16 is the third-time's-the-charm for the rename-dance:

- **v1.7.13** — dance code shipped, no version to update FROM.
- **v1.7.14** — same-day patch on the dance's first-launch promotion ping; still no real update flow exercised.
- **v1.7.15** — dance live-but-broken-at-GitHub-end. First live attempt failed at the URL allowlist.
- **v1.7.16** — dance live-and-fixed. The v1.7.15 release brief explicitly deferred the sandbox-style end-to-end test; that deferral was the actual root cause of this hotfix. A 5-minute sandbox run would have caught the CDN-domain change before users did.

Full changelog: see `CHANGELOG.md`.
