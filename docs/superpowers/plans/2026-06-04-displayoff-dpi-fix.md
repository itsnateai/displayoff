# DisplayOff DPI Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline, chosen) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every DisplayOff Tk surface render proportionally identical at 100% and 150% display scale, by construction, with a permanent regression guard and a real-150% screenshot proof.

**Architecture:** Add one DPI helper (`_dpi_scale`) + explicit `tk scaling` from an authoritative DPI source + process-global awareness at the GUI entry; then replace every fixed-pixel `padx`/`pady` literal with `_dpi_scale(...)` and give the About dialog the content-driven `minsize` treatment Settings/`_themed_dialog` already use. Zero behavior change to the blanking core.

**Tech Stack:** Python 3.14, tkinter, ctypes (Win32), Nuitka `--standalone` build, Hyper-V Tiny11 lab for real-150% verification.

---

## File Structure

- **Modify:** `displayoff.py`
  - Win32 bindings block (~900–1025): add `GetDpiForSystem` binding.
  - New helpers near `_set_dpi_awareness` (~3880): `_dpi_scale(widget, n)`, `_apply_tk_scaling(root)`.
  - `run_tray` (~5110): call `_set_dpi_awareness()` before `pystray.Icon(...)`.
  - `_open_settings_impl` (4385/4387): drop the late awareness call; call `_apply_tk_scaling(root)` after `tk.Tk()`; convert `PAD`/footer/grid pad literals.
  - `_build_footer` (4291), `_build_header`/`_build_hotkey_row`/`_build_options_section`: convert fixed `padx`/`pady` to `_dpi_scale`.
  - `_show_about` (4645): scale `padx`/`pady`, add `minsize`.
  - `_themed_dialog` (3742): audit/convert any fixed pad literals.
  - `main` (5619) dispatch: add gated `--diag-dpi-show <settings|about|themed>`.
- **Create:** `tests/test_dpi_layout.py` — permanent regression guard (stdlib-runnable + pytest-compatible).

---

### Task 1: Baseline — regression test (red) + real-150% evidence

**Files:**
- Create: `tests/test_dpi_layout.py`

- [ ] **Step 1: Write the measurement harness + failing proportionality/overlap tests**

```python
"""DPI layout regression guard. Faithful measurement: map each surface off-screen
at simulated `tk scaling`, read real widget geometry, assert proportional + no overlap.
Runs under pytest OR `python tests/test_dpi_layout.py`. Windows + display required."""
import os, sys, tkinter as tk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import displayoff as do

SCALES = [1.0, 1.25, 1.5, 2.0]

def _root(scale):
    r = tk.Tk()
    r.tk.call("tk", "scaling", scale * 96.0 / 72.0)
    r.attributes("-alpha", 0.0)            # invisible
    r.geometry("+6000+6000")               # off the visible desktop
    return r

def _map(r):
    r.update_idletasks(); r.deiconify(); r.update()

def _hbox(w):
    return (w.winfo_rootx(), w.winfo_rootx() + w.winfo_width())

def _no_horizontal_overlap(container):
    boxes = sorted(_hbox(k) for k in container.winfo_children() if k.winfo_ismapped())
    for (a0, a1), (b0, b1) in zip(boxes, boxes[1:]):
        assert b0 >= a1 - 1, f"overlap: {(a0,a1)} vs {(b0,b1)}"

def _settings_reqwidth(scale):
    r = _root(scale)
    do._build_header(r, row=0, pad=do.PAD)
    # (cfg/captured/recording stubs match _open_settings_impl call shapes)
    cfg = do.load_config()
    cap = {"modifiers": list(cfg["hotkey"]["modifiers"]), "key": cfg["hotkey"]["key"]}
    do._build_hotkey_row(r, row=2, pad=do.PAD, cfg=cfg, captured=cap, recording={"active": False})
    do._build_options_section(r, row=4, pad=do.PAD, lock_var=tk.BooleanVar(),
        autostart_var=tk.BooleanVar(), idle_var=tk.IntVar(), warn_var=tk.BooleanVar())
    do._build_footer(r, row=8, pad=do.PAD, on_save=lambda: None, on_cancel=lambda: None,
        on_apply=lambda: None, on_about=lambda: None, on_check_updates=lambda: None)
    r.update_idletasks()
    rw = r.winfo_reqwidth()
    r.destroy()
    return rw

def test_settings_scales_proportionally():
    w1, w2 = _settings_reqwidth(1.0), _settings_reqwidth(2.0)
    ratio = w2 / w1
    assert ratio >= 1.90, f"settings reqwidth ratio {ratio:.3f} < 1.90 (fixed-px pads not scaling)"

if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try: fn(); print(f"PASS {name}")
            except Exception as e: fails += 1; print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)
```

- [ ] **Step 2: Run it — expect RED on current code**

Run: `cd /x/_Projects/displayoff && .venv/Scripts/python.exe tests/test_dpi_layout.py`
Expected: `FAIL test_settings_scales_proportionally: ... ratio < 1.90` (current fixed-px `PAD=20`/footer pads pull the ratio below proportional). Record the actual ratio as the "before" number.

- [ ] **Step 3: Capture real-150% evidence (Tiny11), under the lock protocol**

```bash
# claim + check first
cat D:/Hyper-V/Tiny11Lab/.in-use 2>/dev/null; grep -i tiny11 /x/_Projects/_.claude/_comms/active-work.md
# if free or stale (>15m): claim
echo "$SESSION_ID $(date) DisplayOff DPI 150% render" > D:/Hyper-V/Tiny11Lab/.in-use
powershell.exe -File D:/Hyper-V/Tiny11Lab/lab.ps1 status
powershell.exe -File D:/Hyper-V/Tiny11Lab/lab.ps1 snapshot
powershell.exe -File D:/Hyper-V/Tiny11Lab/lab.ps1 dpi 150
powershell.exe -File D:/Hyper-V/Tiny11Lab/lab.ps1 deploy X:/_Projects/displayoff
# in guest: python displayoff.py --diag-dpi-show settings  (Task 6 flag; for baseline use current settings open)
powershell.exe -File D:/Hyper-V/Tiny11Lab/lab.ps1 shot   # -> latest.png
```
Expected: a 150% screenshot of the current Settings dialog = the "before" reference. Also probe `root.winfo_fpixels('1i')` in the guest to confirm whether Tk auto-detects 144 (informs whether explicit scaling is load-bearing or belt-and-suspenders).

- [ ] **Step 4: Commit the (red) guard**

```bash
git add tests/test_dpi_layout.py
git commit -m "test(dpi): add layout proportionality regression guard (red)"
```

---

### Task 2: Foundation — authoritative DPI scaling + helper + centralized awareness

**Files:**
- Modify: `displayoff.py` (bindings block; helpers near 3880; `run_tray`; `_open_settings_impl`)

- [ ] **Step 1: Add `GetDpiForSystem` binding** (Win32 bindings block, alongside the existing DPI declarations ~932–951; guard with try/except like its siblings)

```python
    try:
        _GetDpiForSystem = _user32.GetDpiForSystem      # Win10 1607+
        _GetDpiForSystem.argtypes = []
        _GetDpiForSystem.restype = ctypes.c_uint
    except AttributeError:
        _GetDpiForSystem = None
```
(and `_GetDpiForSystem = None` in the non-win32 fallback block ~1023–1025.)

- [ ] **Step 2: Add helpers near `_set_dpi_awareness` (~3878)**

```python
def _dpi_scale(widget, n):
    """Device pixels for a 96-DPI design pixel `n`, using the widget's live Tk
    scaling. Ties pad/coord literals to the same factor that scales point-fonts,
    so 100% and 150% stay proportional by construction."""
    try:
        return max(1, round(n * widget.winfo_fpixels("1i") / 96.0))
    except Exception:
        return n  # never break layout on a measurement hiccup

def _apply_tk_scaling(root):
    """Set Tk `scaling` from the authoritative system DPI so point-fonts scale
    deterministically (don't rely on Tk's Windows auto-detect). No-op when the
    DPI source is unavailable (pre-1607) — Tk's own default then stands."""
    if _GetDpiForSystem is None:
        return
    try:
        dpi = int(_GetDpiForSystem())
        if dpi >= 72:
            root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:
        log.debug("tk scaling set skipped", exc_info=True)
```

- [ ] **Step 3: Centralize awareness at the GUI entry** — in `run_tray` (~5110), add as the first statement (before `pystray.Icon(...)`):

```python
    _set_dpi_awareness()   # process-global, before any window (tray HWND + Tk roots)
```
And in `_open_settings_impl`: replace the line `    _set_dpi_awareness()` (4385) with a call to scaling right after the root exists — delete 4385, and after `root = tk.Tk()` (4387) + the `report_callback_exception` wiring, add:
```python
    _apply_tk_scaling(root)
```

- [ ] **Step 4: Run the guard — still RED (no pad conversion yet), but no crash**

Run: `.venv/Scripts/python.exe tests/test_dpi_layout.py`
Expected: still `FAIL ... ratio < 1.90` (foundation alone doesn't convert pads) — confirms the harness builds the surface cleanly through the new scaling path.

- [ ] **Step 5: Commit**

```bash
git add displayoff.py
git commit -m "feat(dpi): authoritative tk scaling, _dpi_scale helper, awareness at GUI entry"
```

---

### Task 3: Convert Settings + footer + row-builder pad literals

**Files:**
- Modify: `displayoff.py` (`_open_settings_impl` PAD; `_build_footer` 4312/4329/4332/4336/4338/4341; `_build_header`/`_build_hotkey_row`/`_build_options_section` pad usages)

- [ ] **Step 1: Convert the pad literals** — transform rule: every fixed numeric `padx=`/`pady=`/`pad=` that positions widgets becomes `_dpi_scale(<that widget's master>, <n>)`. Specific sites:
  - `_open_settings_impl`: `PAD = 20` stays as the *design* constant, but each `pad=PAD` consumer scales it (below). Footer grid call (4312) `padx=pad, pady=(16, pad)` → `padx=_dpi_scale(footer, pad), pady=(_dpi_scale(footer,16), _dpi_scale(footer,pad))`.
  - `_build_footer` inter-button pads (4329/4332/4336/4338/4341) `padx=(4, 0)` / `(0, 4)` → `_dpi_scale(footer, 4)` based tuples. Gutter (4352) already DPI-relative — leave it.
  - `_build_header` / `_build_hotkey_row` / `_build_options_section`: wrap each `padx=pad`/`pady=...` literal in `_dpi_scale(<master>, ...)` (exact sites enumerated at execution from a `padx=|pady=` grep of each builder).

- [ ] **Step 2: Run the guard at all scales**

Run: `.venv/Scripts/python.exe tests/test_dpi_layout.py`
Expected: `PASS test_settings_scales_proportionally` (ratio now ≥ 1.90). If the post-fix ratio is, say, 1.97, the 1.90 bound holds with margin; record it.

- [ ] **Step 3: Commit**

```bash
git add displayoff.py
git commit -m "fix(dpi): scale Settings/footer/row-builder pad literals (100%≡150%)"
```

---

### Task 4: About dialog — content-driven + DPI-relative pads + minsize

**Files:**
- Modify: `displayoff.py` `_show_about` (4693–4732)
- Modify: `tests/test_dpi_layout.py` (add About coverage)

- [ ] **Step 1: Add an About proportionality test** (append to `tests/test_dpi_layout.py`)

```python
def _about_reqsize(scale):
    r = _root(scale)
    do._build_about_body(r, do.load_config(), autostart_value=False)  # extracted in Step 2
    r.update_idletasks()
    sz = (r.winfo_reqwidth(), r.winfo_reqheight())
    r.destroy()
    return sz

def test_about_scales_proportionally():
    (w1, h1), (w2, h2) = _about_reqsize(1.0), _about_reqsize(2.0)
    assert w2 / w1 >= 1.90, f"about width ratio {w2/w1:.3f} < 1.90"
    assert h2 / h1 >= 1.90, f"about height ratio {h2/h1:.3f} < 1.90"
```

- [ ] **Step 2: Extract `_build_about_body(parent, cfg, autostart_value)`** from `_show_about` (the body Label + link + button-frame block, 4684–4720), so the surface is testable without the Toplevel/mainloop. `_show_about` calls it into its `about` Toplevel. In the extracted body, convert `padx=20, pady=15` (4696) and `pack(padx=20, pady=(0,10))` (4708) and `pack(pady=(0,15))` (4712) to `_dpi_scale(parent, …)`.

- [ ] **Step 3: Add `minsize` to `_show_about`** after the geometry set (4732):
```python
        about.minsize(w, h)
```

- [ ] **Step 4: Run guard — expect PASS for About**

Run: `.venv/Scripts/python.exe tests/test_dpi_layout.py`
Expected: `PASS test_about_scales_proportionally`.

- [ ] **Step 5: Commit**

```bash
git add displayoff.py tests/test_dpi_layout.py
git commit -m "fix(dpi): About dialog content-driven + DPI-relative pads + minsize"
```

---

### Task 5: Themed/update dialog pad audit

**Files:**
- Modify: `displayoff.py` `_themed_dialog` (3742–3861) + `_run_update_check` if it sizes anything fixed.

- [ ] **Step 1:** Grep `_themed_dialog` body for `padx=|pady=|pad=` numeric literals; convert each to `_dpi_scale(<master>, …)`. The chrome margin (`winfo_pixels("0.4i")`, 3845) and content-driven width + minsize (3847/3861) already scale — leave them.
- [ ] **Step 2:** Confirm `_run_update_check` reuses `_themed_dialog` (no independent fixed sizing). If it creates its own root, call `_apply_tk_scaling` on it.
- [ ] **Step 3: Commit**
```bash
git add displayoff.py
git commit -m "fix(dpi): scale themed-dialog pad literals"
```

---

### Task 6: Gated `--diag-dpi-show <surface>` flag for VM screenshots

**Files:**
- Modify: `displayoff.py` `main` dispatch (near other early flags, ~5627)

- [ ] **Step 1:** Add, before the tray launch, a flag that opens exactly one surface standalone and blocks (so the VM can screenshot it), then exits:

```python
    if "--diag-dpi-show" in sys.argv:
        import tkinter as tk
        which = sys.argv[sys.argv.index("--diag-dpi-show") + 1]
        _set_dpi_awareness()
        root = tk.Tk(); _apply_tk_scaling(root); root.title(f"DPI diag: {which}")
        if which == "settings":
            _open_settings_impl(None, None)           # builds + runs its own root
        elif which == "about":
            _apply_dark_titlebar(root); _build_about_body(root, load_config(), False); root.mainloop()
        elif which == "themed":
            _themed_dialog(root, "Display Off", "DPI diagnostic sample text.", ("OK",))
        sys.exit(0)
```
(Exact wiring adjusted to each builder's real signature at execution — `_open_settings_impl` already creates its own root, so the `settings` branch just calls it. No `#if DEBUG` equivalent in Python; the flag is harmless/undocumented in the shipped exe.)

- [ ] **Step 2: Smoke locally**

Run: `.venv/Scripts/pythonw.exe displayoff.py --diag-dpi-show about`
Expected: the About window opens standalone at host 100%; close it; exit 0.

- [ ] **Step 3: Commit**
```bash
git add displayoff.py
git commit -m "feat(dpi): --diag-dpi-show flag to render one surface standalone for VM capture"
```

---

### Task 7: Real-150% acceptance on Tiny11 (gate)

- [ ] **Step 1:** Under the lock protocol (Task 1 Step 3), with DPI already at 150%, deploy the fixed build and screenshot each surface:
```bash
powershell.exe -File D:/Hyper-V/Tiny11Lab/lab.ps1 deploy X:/_Projects/displayoff
# guest: pythonw displayoff.py --diag-dpi-show settings ; lab.ps1 shot -> settings-150.png
# repeat: about, themed
```
- [ ] **Step 2:** `lab.ps1 dpi 125` → re-deploy → shot each → compare. Then `lab.ps1 dpi 100` for the reference set.
- [ ] **Step 3:** Compare 100% vs 150% vs 125% side by side: assert proportionally identical (spacing, no clipping, no collision). Screenshots are ground truth.
- [ ] **Step 4:** Restore the VM as found: `lab.ps1 dpi 100`; `lab.ps1 restore` (or leave at the snapshot); clear the lock: `rm D:/Hyper-V/Tiny11Lab/.in-use`; update `active-work.md`.

---

### Task 8: Ship — version, changelog, build, verifier swarm, confirm push

- [ ] **Step 1:** Bump `__version__ = "1.7.25"` (patch — correctness/polish, no behavior change, no user-facing feature).
- [ ] **Step 2:** CHANGELOG.md v1.7.25 entry: full DPI-correctness pass (foundation + per-surface pad conversion + About minsize + permanent guard), real-150%-verified on Tiny11. No blanking-core change.
- [ ] **Step 3:** Build: existing Nuitka `--standalone` flow. Verify the exe launches + opens Settings.
- [ ] **Step 4:** Run the full guard once more: `.venv/Scripts/python.exe tests/test_dpi_layout.py` → all PASS.
- [ ] **Step 5:** Dispatch verifier swarm (normal stakes — Python source: 3 topics × Sonnet+Opus = Diff-clean / Gap-audit / Code-review), feeding the 150% screenshots as ground truth.
- [ ] **Step 6:** Commit the release; **confirm the push/release with Nate** before pushing (his standing rule: confirm release).

---

## Self-Review (against the spec)

- **Spec coverage:** Foundation (awareness+scaling+helper) → Task 2; per-surface conversion → Tasks 3–5; harness+baseline → Task 1; real-150% gate → Task 7; permanent guard → Tasks 1/3/4; ship+verifier → Task 8; VM lock protocol → Task 1/7. Canvas: spec made it conditional; baseline confirmed tray-bitmap = out of scope (noted, no task). ✅ all spec sections mapped.
- **Placeholder scan:** load-bearing code shown in full (helper, scaling, test, About extraction, flag); mechanical pad conversions specified by exact site + transform rule (not vague). ✅
- **Type/name consistency:** `_dpi_scale(widget, n)`, `_apply_tk_scaling(root)`, `_GetDpiForSystem`, `_build_about_body(parent, cfg, autostart_value)`, `do.PAD` used consistently across Tasks 2/3/4/6. ✅
