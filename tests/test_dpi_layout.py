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
    m = {"reqw": r.winfo_reqwidth(), "reqh": r.winfo_reqheight()}
    r.destroy()
    return m


def _ratio(metric):
    a = _settings_metrics(1.0)[metric]
    b = _settings_metrics(2.0)[metric]
    return b / a if a else 0.0


# ── Tests ────────────────────────────────────────────────────────────────────
# When every pad is DPI-relative, the whole surface scales ~2.0x between 100% and
# 200%. Fixed-pixel pads pull the ratio down — pre-fix reqheight scaled only 1.57x.
# Threshold 1.85 cleanly separates the broken state (1.57) from the fixed state
# (~1.94), with margin for font-metric variation. reqheight is the sensitive axis
# (a tall stack of rows, each previously gapped by a fixed pady). The remaining
# ~0.06 shortfall from a perfect 2.0 is legitimate 1px hairlines (separator line,
# widget borders) that conventionally stay thin — the Tiny11 150% screenshot
# (acceptance gate) is the direct visual spacing proof.

def test_dpi_scale_doubles_at_200pct():
    r = _root(2.0)
    try:
        assert do._dpi_scale(r, 20) == 40, "design 20px should be 40 device-px at 200%"
        assert do._dpi_scale(r, 0) == 0, "explicit 0 pad must stay 0 at any DPI"
    finally:
        r.destroy()


def test_settings_height_scales_proportionally():
    ratio = _ratio("reqh")
    assert ratio >= 1.85, f"settings reqheight 100->200% ratio {ratio:.3f} < 1.85 (fixed-px pads not scaling)"


def _about_reqsize(scale):
    r = _root(scale)
    do._build_about_body(r, do.load_config(), False)
    r.update_idletasks()
    sz = (r.winfo_reqwidth(), r.winfo_reqheight())
    r.destroy()
    return sz


def test_about_scales_proportionally():
    (_, h1), (_, h2) = _about_reqsize(1.0), _about_reqsize(2.0)
    ratio = h2 / h1
    assert ratio >= 1.85, f"About reqheight 100->200% ratio {ratio:.3f} < 1.85 (fixed-px pads not scaling)"


# KNOWN GAP — no test_themed_scales. `_themed_dialog` is monolithic (Toplevel +
# grab_set + parent.wait_window blocks), so it can't be measured here without
# either a risky extract-method refactor of production dialog code or a flaky
# timing hack. Compensating controls: the themed dialog scales via the SAME
# `_dpi_scale` helper these tests prove (test_dpi_scale_doubles + the settings/
# about proportionality), its pads are verifier-confirmed, and it is screenshot-
# verified at real 150% as a release step (Tiny11 lab: `--diag-dpi-show themed`).
# To close the gap properly, extract `_build_themed_body(parent, message, buttons)`
# from `_themed_dialog` (mirror of `_build_about_body`) and measure it like About.


def _diagnostics():
    print(f"{'scale':>6} {'reqw':>6} {'reqh':>6}")
    for s in SCALES:
        m = _settings_metrics(s)
        print(f"{s:>6} {m['reqw']:>6} {m['reqh']:>6}")
    print(f"\nratios 100%->200%:  reqw={_ratio('reqw'):.3f}  reqh={_ratio('reqh'):.3f}")


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
