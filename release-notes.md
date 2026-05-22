Hotfix. v1.7.15's first live exercise of the rename-dance updater (introduced in v1.7.13) failed because GitHub had migrated the release-asset CDN to a domain not in the hardcoded allowlist. The update-available dialog also had a clipped button row. Both fixed.

## What's fixed

- **Self-updater works again** — added `release-assets.githubusercontent.com` to the update-host allowlist. GitHub migrated the release-asset CDN from `objects.githubusercontent.com` to the new host over 2025; the hardcoded allowlist hadn't caught up. v1.7.13 / v1.7.14 / v1.7.15 all carried this latent bug — it only surfaced when v1.7.15 was the first release to have ANOTHER release (this one) to update to. The legacy `objects.githubusercontent.com` is still in the list for any older release whose URLs were baked before the migration.
- **Update-available dialog button row no longer clips.** Middle button label shortened to "Releases page" (was "Open releases page") so the three-button row fits the dialog at default DPI. `_themed_dialog` now also floors the dialog's geometry width to the button row's required width as a defense against future widening.

## Upgrade path

If you're running v1.7.14 or v1.7.15, click Settings → Check for updates → Install now. The dance should work end-to-end this time. If it doesn't, the "Releases page" fallback button opens this page in your browser for manual download.

If you're on the .py source channel, no action needed — the dance only applies to the frozen `.exe`.

## What this exposes

v1.7.16 is the third-time's-the-charm for the rename-dance:

- **v1.7.13** — dance code shipped, no version to update FROM.
- **v1.7.14** — same-day patch on the dance's first-launch promotion ping; still no real update flow exercised.
- **v1.7.15** — dance live-but-broken-at-GitHub-end. First live attempt failed at the URL allowlist.
- **v1.7.16** — dance live-and-fixed. The v1.7.15 release brief explicitly deferred the sandbox-style end-to-end test; that deferral was the actual root cause of this hotfix. A 5-minute sandbox run would have caught the CDN-domain change before users did.

Full changelog: see `CHANGELOG.md`.
