"""DPI layout regression guard for DisplayOff.

The bug class: point-sized fonts scale with display DPI, but raw-pixel pad/coord
literals do NOT. At 125/150/200% the fonts grow while fixed pads stay put -> cramped
spacing and collisions. This guard rebuilds each real Tk surface at simulated
`tk scaling` and asserts every dimension scaled together (proportionally), so a
fixed-pixel pad that fails to scale is caught before it ships.

Measurement is reqwidth/reqheight + grid-cell geometry after update_idletasks()
(no window is ever shown). Runs under pytest OR `python tests/test_dpi_layout.py`.
Windows + a display required (tkinter).
"""
import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import displayoff as do  # noqa: E402

# Simulated DISPLAY scale -> Tk scaling (px/point). 1.0=100% (96 DPI), 1.5=150%, 2.0=200%.
SCALES = [1.0, 1.25, 1.5, 2.0]
PAD = 20  # mirrors the local PAD in _open_settings_impl (not a module global)


def _root(scale):
    r = tk.Tk()
    r.tk.call("tk", "scaling", scale * 96.0 / 72.0)
    r.withdraw()  # never shown — reqwidth/grid geometry don't need mapping
    return r


def _build_settings(r):
    """Build the real Settings body via the production row builders."""
    cfg = do.load_config()
    captured = {"modifiers": list(cfg["hotkey"]["modifiers"]), "key": cfg["hotkey"]["key"]}
    do._build_header(r, row=0, pad=PAD)
    do._build_hotkey_row(r, row=2, pad=PAD, cfg=cfg, captured=captured,
                         recording={"active": False})
    do._build_options_section(r, row=4, pad=PAD,
                              lock_var=tk.BooleanVar(master=r),
                              autostart_var=tk.BooleanVar(master=r),
                              idle_var=tk.IntVar(master=r),
                              warn_var=tk.BooleanVar(master=r))
    do._build_footer(r, row=8, pad=PAD,
                     on_save=lambda: None, on_cancel=lambda: None, on_apply=lambda: None,
                     on_about=lambda: None, on_check_updates=lambda: None)
    r.columnconfigure(1, weight=1)
    r.update_idletasks()


def _settings_metrics(scale):
    r = _root(scale)
    _build_settings(r)
    reqw, reqh = r.winfo_reqwidth(), r.winfo_reqheight()
    # vertical gap between header (row 0) and hotkey row (row 2): pure pady spacing.
    try:
        _, y0, _, h0 = r.grid_bbox(0, 0)
        _, y2, _, _ = r.grid_bbox(0, 2)
        gap = y2 - (y0 + h0)
    except Exception:
        gap = -1
    r.destroy()
    return {"reqw": reqw, "reqh": reqh, "row_gap": gap}


def _ratio(metric):
    a = _settings_metrics(1.0)[metric]
    b = _settings_metrics(2.0)[metric]
    return b / a if a else 0.0


# ── Tests ────────────────────────────────────────────────────────────────────
# Threshold 1.90: proportional layout (everything DPI-scaled) yields ~2.0 between
# 100% and 200%; fixed-pixel pads pull the ratio below 1.90. Calibrated against the
# measured red (pre-fix) and green (post-fix) numbers — see the diagnostic table.

def test_settings_height_scales_proportionally():
    ratio = _ratio("reqh")
    assert ratio >= 1.90, f"settings reqheight 100->200% ratio {ratio:.3f} < 1.90 (fixed-px pads not scaling)"


def test_settings_row_gap_scales_proportionally():
    ratio = _ratio("row_gap")
    assert ratio >= 1.90, f"header→hotkey row gap ratio {ratio:.3f} < 1.90 (fixed pady not scaling)"


def _diagnostics():
    print(f"{'scale':>6} {'reqw':>6} {'reqh':>6} {'row_gap':>8}")
    for s in SCALES:
        m = _settings_metrics(s)
        print(f"{s:>6} {m['reqw']:>6} {m['reqh']:>6} {m['row_gap']:>8}")
    print(f"\nratios 100%->200%:  reqw={_ratio('reqw'):.3f}  reqh={_ratio('reqh'):.3f}  row_gap={_ratio('row_gap'):.3f}")


if __name__ == "__main__":
    _diagnostics()
    print()
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)
