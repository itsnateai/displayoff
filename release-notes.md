UX polish: settings-dialog footer spacing on high-DPI displays, plus refreshed README screenshots.

### Fixed

- **Settings footer "Apply" button cramped against "Updates" on high-DPI displays.** The settings window width was a hardcoded `460` px, but the six footer buttons are sized in character units (`width=8`), which scale with display DPI while the pixel constant did not. At 100% scaling the two button groups (`[GitHub] [About] [Updates]` left, `[Apply] [Save] [Cancel]` right) had a comfortable centre gutter; at 125%/150% (common on laptops) the buttons rendered wider in pixels, consumed the whole interior, and the gutter collapsed to zero — so "Apply" butted directly against "Updates". Two complementary fixes, both mirroring the durable sizing the themed message-dialog helper (`_themed_dialog`, used for the update-check prompts) already used:
  - **Guaranteed gutter spacer.** `_build_footer` now packs a childless, DPI-relative (~0.3 in, min 24 px) spacer frame between the info group and the action group. Because it contributes to the footer's requested width, the window sizing always reserves room for it; any extra width from a wider window pools into the same gutter, so the gap is always at least the spacer width.
  - **Content-driven window width + sticky minsize.** The settings window now grows to `max(460, winfo_reqwidth())` after all widgets are built (instead of a flat 460) and pins a `minsize`, so the footer can't overflow the gutter and a later font-cache / DPI re-solve can't clip it.

### Changed

- **Refreshed README screenshots** (tray menu + settings dialog) to the current UI, and moved the **Screenshots** section up to directly below the intro — visible immediately instead of buried after *Why this exists → Quickstart → Features*.
