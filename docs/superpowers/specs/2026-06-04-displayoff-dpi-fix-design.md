# DisplayOff — DPI correctness (100% ≡ 150%) — Design

- **Date:** 2026-06-04
- **Status:** Approved (brainstorming → writing-plans)
- **Owner:** Claude (autonomous), Nate (release gate)
- **Baseline version:** 1.7.24

## Problem

DisplayOff's Tk UI is built focused on 100% display scale. It declares PerMonitor-V2
DPI awareness, so Windows does **not** bitmap-scale it — the app owns all scaling.
Tk grows *point-sized fonts* with DPI, but *raw pixel literals* (`PAD = 20`, fixed
`geometry`, canvas coords) do not. At 125%/150% the fonts grow while fixed dimensions
stay put → cramped padding, clipped/colliding widgets. It looks perfect at 100% (where
we develop) and only breaks on a high-DPI machine — so it survives visual review.

This is the same disease that shipped EQSwitch broken to real users. The cure-philosophy
is identical; the mechanism differs (tkinter, not WinForms).

### Two confirmed root-cause findings (from live code, 2026-06-04)
1. **Awareness is set in exactly one place** — `_set_dpi_awareness()` is called only in
   `_open_settings_impl` (displayoff.py:4385), never in `main`, `run_tray`, `_show_about`,
   or `_run_update_check`. About/Updates inherit it today only because they open as
   children of Settings — load-bearing coincidence, not design. (Contradicts the CLAUDE.md
   claim "declared before creating any root.")
2. **No explicit `tk scaling` anywhere** — font scaling at high DPI is left to Tk's flaky
   Windows auto-detect.

## Goal & acceptance bar

Every UI surface — **Settings**, **About**, **Update/themed dialogs** — renders
**proportionally identical** at 100% and 150% (and 125/175/200%): same relative spacing,
no clipping, no collisions. Achieved **by construction** (relational + DPI-relative
layout), **guarded** by a permanent regression test, and **proven** on a real 150%
screenshot from the Tiny11 VM.

## Non-goals

- No GUI-toolkit switch (match codebase conventions — single-file tkinter).
- **Zero behavior change** to the blanking core (`turn_off_monitors`, native/legacy paths,
  hotkey, idle, single-instance). This is a layout/DPI-only change.
- The **tray-icon bitmap** (programmatic fallback, displayoff.py:~3952) is shell-scaled
  from a multi-size ICO; in scope only if the baseline shows its coords are *not* already
  proportional to the bitmap size.

## Approach — a tiny DPI helper layer, then relational conversion

The tkinter analog of EQSwitch's `CardLayout.cs` / `DpiScale`, kept minimal:

1. **Foundation (eliminates both root-cause bugs by construction):**
   - Move `_set_dpi_awareness()` to the **top of `main()`** — once, before the pystray
     icon and any Tk root. Awareness is process-global; every dialog inherits it on any
     entry path.
   - Add **explicit `tk scaling = real_DPI / 72`** right after each root is created, so
     point-fonts scale deterministically.
2. **One helper:** `_dpi_scale(widget, design_px) -> int` — device pixels for a 96-DPI
   "design pixel" (the tkinter `LogicalToDeviceUnits`). Keep `winfo_pixels("0.3i")` where a
   physical unit reads naturally (gutters/margins).
3. **Convert each surface so layout holds zero raw pixel literals:**
   - `PAD = 20` and every fixed `padx`/`pady` → `_dpi_scale`-based.
   - `_show_about` → content-driven width + `minsize` + DPI-relative pads (match
     `_themed_dialog`, already correct).
   - Settings (`_open_settings_impl` + `_build_*` row builders) — size already
     content-driven (v1.7.24); convert remaining fixed pads; confirm grid weights relational.
   - Audit every canvas/literal-coord draw: scale on-screen coords; leave shell-scaled tray
     bitmap proportional to its own size.

## Phases

1. **Baseline (evidence-led).** Offline tk-scaling harness renders Settings/About/themed at
   1.0/1.25/1.5/2.0 and **measures** actual pixel gaps between adjacent widgets; one real-150%
   Tiny11 screenshot pass for ground truth. Output: precise "what distorts where" list.
   *Nothing is rebuilt before the damage is visible.*
2. **Foundation.** Centralize awareness in `main()`; explicit `tk scaling`; add `_dpi_scale`.
   Re-run harness.
3. **Convert surfaces.** About dialog, fixed pads, on-screen canvas coords → relational /
   DPI-relative. Re-run harness after each surface.
4. **Verify at real 150% (acceptance gate).** Add a gated `--diag-dpi-show <settings|about|themed>`
   flag (opens one dialog standalone so the VM can screenshot it). On Tiny11: lock → snapshot →
   `dpi 150` → deploy → shot each surface → compare to 100% reference; also 125%. Screenshots
   are ground truth.
5. **Guard, ship, verify.** Commit a permanent `test_dpi_layout.py` (asserts inter-widget gaps
   > 0 at 1.25/1.5/2.0). Version bump + CHANGELOG + Nuitka `--standalone` build. Dispatch the
   verifier swarm (normal stakes → 3 topics × Sonnet+Opus). **Confirm release with Nate.**

## Verification strategy

- **Fast inner loop:** offline harness asserting gaps > 0 at simulated scales. Caveat
  (EQSwitch lesson): font-multiplier sims diverge from real DPI for some metrics — the harness
  is a pre-filter, not the gate.
- **Acceptance gate:** real 150% (and 125%) screenshots from Tiny11, compared to the 100%
  reference. Screenshots beat static reasoning.
- **Permanent guard:** committed `test_dpi_layout.py` so future edits can't silently regress
  DPI correctness (feeds `project_winforms_dpi_by_default_enforcement`).

## Tiny11 VM coordination (shared with the MicMute-DPI session)

Before any `lab.ps1 dpi/deploy/shot/restore`:
1. Claim the lock `D:\Hyper-V\Tiny11Lab\.in-use` = session id + timestamp + "DisplayOff DPI 150% render".
2. Note it in `_.claude/_comms/active-work.md`.
3. Check both first; if another session's claim is **fresh**, **queue — never reboot/change DPI**
   while it's live. Stale (>~15 min) = reclaim.
4. `snapshot` before changing DPI; `dpi 100` + `restore` to leave the VM as found.

## Risks & mitigations

- **Tk Windows scaling quirks** → explicit `tk scaling` + real-150% gate (don't trust harness alone).
- **VM contention** → lock protocol above.
- **Transient-dialog screenshotting** → `--diag-dpi-show` flag.
- **Regression in blanking core** → out of scope by construction; verifier swarm + diff-clean topic confirms.

## Alternatives rejected

- *Set `tk scaling` only + trust existing auto-grow* — leaves fixed pads/coords un-scaled →
  proportion drift; fails "proportionally identical."
- *Switch GUI toolkit* — massive; violates match-codebase-conventions.
- *Targeted patch only* — the "wait until you see the slop, then patch" workflow Nate rejected.

## Decisions locked

- **Keep PerMonitor-V2** awareness (no injected child windows → EQSwitch SystemAware carve-out
  does not apply).
- **Plan through a release-ready, 150%-verified build; confirm the actual push with Nate.**
