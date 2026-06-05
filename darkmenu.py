"""Fully owner-drawn dark tray menu with dark-lime separators.

Why this is the whole menu and not just the separators: a native Win32 popup
menu has no per-item draw hook on the *themed* (visual-styles) path, so the
only way to recolour a separator is `MFT_OWNERDRAW`. But the moment ANY item
in a popup is owner-drawn, Windows drops the ENTIRE menu to the classic
(non-themed) renderer — which ignores the uxtheme dark-menu theme and paints
light. So to keep a dark menu while colouring the separators, we have to
owner-draw every item and repaint the whole menu dark by hand.

What this module does:

  * `apply_dark_menu(hmenu)` — walk the top-level HMENU, capture each item's
    text / disabled / submenu state into a descriptor registry keyed by a
    sentinel `dwItemData` token, then flip every item to `MFT_OWNERDRAW`.
    Also stamps a dark background brush via `SetMenuInfo` so the menu's
    margins paint dark too. The Auto-blank *submenu* is left untouched — it
    has no owner-draw items, so it keeps the system's themed dark rendering.
  * The menu owner window (`Icon._menu_hwnd`) is subclassed to answer
    `WM_MEASUREITEM` (size each row) and `WM_DRAWITEM` (paint dark/hover
    background, light/grey text, the ▸ submenu arrow, and the lime
    separator line). Everything else chains to pystray's original window
    procedure via `CallWindowProc`.
  * `install(icon)` wires both, and re-applies on every menu rebuild
    (pystray rebuilds the HMENU after each click).

Fail-open by construction: any binding/call failure leaves the menu in its
native themed state and the tray keeps working. Cosmetic-only, never a crash
surface. Mirrors native_blank.py's binding hygiene (explicit argtypes /
restype, use_last_error).
"""

import ctypes
import logging
import sys

log = logging.getLogger("displayoff.darkmenu")


def _rgb(r, g, b):
    return (r & 0xFF) | ((g & 0xFF) << 8) | ((b & 0xFF) << 16)


# ── Tunable look (COLORREF 0x00BBGGRR + logical px @96 DPI) ─────────────────
_BG = _rgb(0x2B, 0x2B, 0x2B)        # menu surface (matches forced-dark menus)
_BG_HOVER = _rgb(0x3D, 0x3D, 0x3D)  # highlighted (hot) row
_TX = _rgb(0xEC, 0xEC, 0xEC)        # enabled text
_TX_DISABLED = _rgb(0x80, 0x80, 0x80)   # disabled / label text
_LIME = _rgb(0x6F, 0xA8, 0x2A)      # the separator accent — dark lime green

_PAD_L = 28          # text left padding (clears the check/icon gutter)
_PAD_R = 16          # text right padding
_ARROW_W = 18        # right column reserved for the submenu arrow
_VPAD = 5            # extra vertical padding per row (top + bottom each)
_MIN_ITEM_H = 22     # minimum row height
_SEP_H = 9           # separator row height
_LINE_TH = 1         # separator line thickness
_LINE_INSET = 8      # separator line left/right margin

_ENABLED = False  # flipped True only if every Win32 binding resolves

if sys.platform == "win32":
    try:
        from ctypes import wintypes

        # ── constants ──────────────────────────────────────────────────────
        MIIM_STATE = 0x00000001
        MIIM_ID = 0x00000002
        MIIM_SUBMENU = 0x00000004
        MIIM_DATA = 0x00000020
        MIIM_STRING = 0x00000040
        MIIM_FTYPE = 0x00000100
        MFT_SEPARATOR = 0x00000800
        MFT_OWNERDRAW = 0x00000100
        MFS_DISABLED = 0x00000003  # MFS_GRAYED — covers MF_GRAYED|MF_DISABLED
        ODT_MENU = 1
        ODS_SELECTED = 0x0001
        ODS_DISABLED = 0x0004
        WM_MEASUREITEM = 0x002C
        WM_DRAWITEM = 0x002B
        GWLP_WNDPROC = -4
        SPI_GETNONCLIENTMETRICS = 0x0029
        MIM_BACKGROUND = 0x00000002
        TRANSPARENT = 1
        DT_LEFT = 0x00000000
        DT_VCENTER = 0x00000004
        DT_SINGLELINE = 0x00000020
        DT_NOPREFIX = 0x00000800
        MF_SEPARATOR = 0x00000800  # self-test only
        MF_STRING = 0x00000000
        MF_POPUP = 0x00000010

        LRESULT = ctypes.c_ssize_t
        ULONG_PTR = ctypes.c_void_p
        _BASE_TOKEN = 0x0DA20000  # high tag so a stray itemData won't match

        # ── structs ────────────────────────────────────────────────────────
        class MENUITEMINFOW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT), ("fMask", wintypes.UINT),
                ("fType", wintypes.UINT), ("fState", wintypes.UINT),
                ("wID", wintypes.UINT), ("hSubMenu", wintypes.HMENU),
                ("hbmpChecked", wintypes.HBITMAP),
                ("hbmpUnchecked", wintypes.HBITMAP),
                ("dwItemData", ULONG_PTR), ("dwTypeData", wintypes.LPWSTR),
                ("cch", wintypes.UINT), ("hbmpItem", wintypes.HBITMAP)]

        class MEASUREITEMSTRUCT(ctypes.Structure):
            _fields_ = [
                ("CtlType", wintypes.UINT), ("CtlID", wintypes.UINT),
                ("itemID", wintypes.UINT), ("itemWidth", wintypes.UINT),
                ("itemHeight", wintypes.UINT), ("itemData", ULONG_PTR)]

        class DRAWITEMSTRUCT(ctypes.Structure):
            _fields_ = [
                ("CtlType", wintypes.UINT), ("CtlID", wintypes.UINT),
                ("itemID", wintypes.UINT), ("itemAction", wintypes.UINT),
                ("itemState", wintypes.UINT), ("hwndItem", wintypes.HWND),
                ("hDC", wintypes.HDC), ("rcItem", wintypes.RECT),
                ("itemData", ULONG_PTR)]

        class MENUINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD), ("fMask", wintypes.DWORD),
                ("dwStyle", wintypes.DWORD), ("cyMax", wintypes.UINT),
                ("hbrBack", wintypes.HBRUSH),
                ("dwContextHelpID", wintypes.DWORD),
                ("dwMenuData", ULONG_PTR)]

        class LOGFONTW(ctypes.Structure):
            _fields_ = [
                ("lfHeight", wintypes.LONG), ("lfWidth", wintypes.LONG),
                ("lfEscapement", wintypes.LONG),
                ("lfOrientation", wintypes.LONG), ("lfWeight", wintypes.LONG),
                ("lfItalic", wintypes.BYTE), ("lfUnderline", wintypes.BYTE),
                ("lfStrikeOut", wintypes.BYTE), ("lfCharSet", wintypes.BYTE),
                ("lfOutPrecision", wintypes.BYTE),
                ("lfClipPrecision", wintypes.BYTE),
                ("lfQuality", wintypes.BYTE),
                ("lfPitchAndFamily", wintypes.BYTE),
                ("lfFaceName", ctypes.c_wchar * 32)]

        class NONCLIENTMETRICSW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT), ("iBorderWidth", ctypes.c_int),
                ("iScrollWidth", ctypes.c_int),
                ("iScrollHeight", ctypes.c_int),
                ("iCaptionWidth", ctypes.c_int),
                ("iCaptionHeight", ctypes.c_int),
                ("lfCaptionFont", LOGFONTW),
                ("iSmCaptionWidth", ctypes.c_int),
                ("iSmCaptionHeight", ctypes.c_int),
                ("lfSmCaptionFont", LOGFONTW),
                ("iMenuWidth", ctypes.c_int), ("iMenuHeight", ctypes.c_int),
                ("lfMenuFont", LOGFONTW), ("lfStatusFont", LOGFONTW),
                ("lfMessageFont", LOGFONTW),
                ("iPaddedBorderWidth", ctypes.c_int)]

        # ── bindings ───────────────────────────────────────────────────────
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

        def _bind(dll, name, args, ret):
            fn = getattr(dll, name)
            fn.argtypes = args
            fn.restype = ret
            return fn

        _GetMenuItemCount = _bind(_user32, "GetMenuItemCount",
                                  [wintypes.HMENU], ctypes.c_int)
        _GetMenuItemInfoW = _bind(_user32, "GetMenuItemInfoW",
                                  [wintypes.HMENU, wintypes.UINT,
                                   wintypes.BOOL, ctypes.c_void_p],
                                  wintypes.BOOL)
        _SetMenuItemInfoW = _bind(_user32, "SetMenuItemInfoW",
                                  [wintypes.HMENU, wintypes.UINT,
                                   wintypes.BOOL, ctypes.c_void_p],
                                  wintypes.BOOL)
        _SetMenuInfo = _bind(_user32, "SetMenuInfo",
                             [wintypes.HMENU, ctypes.POINTER(MENUINFO)],
                             wintypes.BOOL)
        _FillRect = _bind(_user32, "FillRect",
                          [wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                           wintypes.HBRUSH], ctypes.c_int)
        _DrawTextW = _bind(_user32, "DrawTextW",
                           [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                            ctypes.POINTER(wintypes.RECT), wintypes.UINT],
                           ctypes.c_int)
        _GetDC = _bind(_user32, "GetDC", [wintypes.HWND], wintypes.HDC)
        _ReleaseDC = _bind(_user32, "ReleaseDC",
                           [wintypes.HWND, wintypes.HDC], ctypes.c_int)
        _CallWindowProcW = _bind(_user32, "CallWindowProcW",
                                 [ctypes.c_void_p, wintypes.HWND,
                                  wintypes.UINT, wintypes.WPARAM,
                                  wintypes.LPARAM], LRESULT)
        _DefWindowProcW = _bind(_user32, "DefWindowProcW",
                                [wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM], LRESULT)
        _SystemParametersInfoW = _bind(_user32, "SystemParametersInfoW",
                                       [wintypes.UINT, wintypes.UINT,
                                        ctypes.c_void_p, wintypes.UINT],
                                       wintypes.BOOL)

        if hasattr(_user32, "SetWindowLongPtrW"):
            _SetWindowLong = _bind(_user32, "SetWindowLongPtrW",
                                   [wintypes.HWND, ctypes.c_int,
                                    ctypes.c_void_p], ctypes.c_void_p)
        else:  # 32-bit Python
            _SetWindowLong = _bind(_user32, "SetWindowLongW",
                                   [wintypes.HWND, ctypes.c_int,
                                    ctypes.c_void_p], ctypes.c_void_p)

        try:
            _GetDpiForWindow = _bind(_user32, "GetDpiForWindow",
                                     [wintypes.HWND], wintypes.UINT)
        except AttributeError:
            _GetDpiForWindow = None
        try:
            _SystemParametersInfoForDpi = _bind(
                _user32, "SystemParametersInfoForDpi",
                [wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                 wintypes.UINT, wintypes.UINT], wintypes.BOOL)
        except AttributeError:
            _SystemParametersInfoForDpi = None

        _CreateSolidBrush = _bind(_gdi32, "CreateSolidBrush",
                                  [wintypes.COLORREF], wintypes.HBRUSH)
        _DeleteObject = _bind(_gdi32, "DeleteObject",
                              [wintypes.HGDIOBJ], wintypes.BOOL)
        _CreateFontIndirectW = _bind(_gdi32, "CreateFontIndirectW",
                                     [ctypes.POINTER(LOGFONTW)],
                                     wintypes.HFONT)
        _SelectObject = _bind(_gdi32, "SelectObject",
                              [wintypes.HDC, wintypes.HGDIOBJ],
                              wintypes.HGDIOBJ)
        _GetTextExtentPoint32W = _bind(_gdi32, "GetTextExtentPoint32W",
                                       [wintypes.HDC, wintypes.LPCWSTR,
                                        ctypes.c_int,
                                        ctypes.POINTER(wintypes.SIZE)],
                                       wintypes.BOOL)
        _SetBkMode = _bind(_gdi32, "SetBkMode",
                           [wintypes.HDC, ctypes.c_int], ctypes.c_int)
        _SetTextColor = _bind(_gdi32, "SetTextColor",
                              [wintypes.HDC, wintypes.COLORREF],
                              wintypes.COLORREF)

        # self-test-only menu builders
        _CreatePopupMenu = _bind(_user32, "CreatePopupMenu", [],
                                 wintypes.HMENU)
        _AppendMenuW = _bind(_user32, "AppendMenuW",
                             [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t,
                              wintypes.LPCWSTR], wintypes.BOOL)
        _DestroyMenu = _bind(_user32, "DestroyMenu", [wintypes.HMENU],
                             wintypes.BOOL)

        WNDPROCTYPE = ctypes.WINFUNCTYPE(
            LRESULT, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM)

        # Process-lifetime resources (kept referenced on purpose).
        _BG_BRUSH = _CreateSolidBrush(_BG)   # menu-margin background brush
        _SUBCLASSED = {}    # hwnd(int) -> (WNDPROCTYPE keep-alive, prev addr)
        _FONTS = {}         # dpi -> HFONT cache
        _DESCRIPTORS = {}   # itemData token -> {text, sep, disabled, submenu}

        # ── helpers ────────────────────────────────────────────────────────
        def _scale(dpi):
            return max(1.0, dpi / 96.0) if dpi else 1.0

        def _px(value, scale):
            return int(round(value * scale))

        def _dpi_for(hwnd):
            if _GetDpiForWindow is not None:
                try:
                    return _GetDpiForWindow(hwnd) or 96
                except OSError:
                    return 96
            return 96

        def _menu_font(dpi):
            hf = _FONTS.get(dpi)
            if hf is not None:
                return hf
            ncm = NONCLIENTMETRICSW()
            ncm.cbSize = ctypes.sizeof(NONCLIENTMETRICSW)
            ok = False
            if _SystemParametersInfoForDpi is not None:
                ok = _SystemParametersInfoForDpi(
                    SPI_GETNONCLIENTMETRICS, ncm.cbSize, ctypes.byref(ncm),
                    0, dpi)
            if not ok:
                ok = _SystemParametersInfoW(
                    SPI_GETNONCLIENTMETRICS, ncm.cbSize, ctypes.byref(ncm), 0)
                if ok and dpi and dpi != 96:
                    ncm.lfMenuFont.lfHeight = int(
                        ncm.lfMenuFont.lfHeight * dpi / 96)
            hf = _CreateFontIndirectW(ctypes.byref(ncm.lfMenuFont)) if ok else None
            # Only cache a real handle. Caching None on a transient SPI failure
            # would poison this DPI permanently — every later lookup returns the
            # cached None and the rows draw fontless. Leaving it uncached lets
            # the next call retry.
            if hf is not None:
                _FONTS[dpi] = hf
            return hf

        def _text_size(hwnd, text, hfont):
            hdc = _GetDC(hwnd)
            if not hdc:
                return (0, 0)
            try:
                old = _SelectObject(hdc, hfont) if hfont else None
                sz = wintypes.SIZE()
                _GetTextExtentPoint32W(hdc, text, len(text), ctypes.byref(sz))
                if old:
                    _SelectObject(hdc, old)
                return (sz.cx, sz.cy)
            finally:
                _ReleaseDC(hwnd, hdc)

        def _fill(hdc, rect, colorref):
            brush = _CreateSolidBrush(colorref)
            if not brush:
                return
            try:
                _FillRect(hdc, ctypes.byref(rect), brush)
            finally:
                _DeleteObject(brush)

        # ── owner-draw message handlers ────────────────────────────────────
        def _on_measure(hwnd, lparam):
            mis = MEASUREITEMSTRUCT.from_address(lparam)
            if mis.CtlType != ODT_MENU:
                return False
            d = _DESCRIPTORS.get(mis.itemData)
            if d is None:
                return False
            dpi = _dpi_for(hwnd)
            scale = _scale(dpi)
            if d["sep"]:
                mis.itemHeight = max(4, _px(_SEP_H, scale))
                mis.itemWidth = _px(40, scale)
                return True
            hfont = _menu_font(dpi)
            tw, th = _text_size(hwnd, d["text"] or " ", hfont)
            width = _px(_PAD_L, scale) + tw + _px(_PAD_R, scale)
            if d["submenu"]:
                width += _px(_ARROW_W, scale)
            mis.itemWidth = width
            mis.itemHeight = max(_px(_MIN_ITEM_H, scale),
                                 th + 2 * _px(_VPAD, scale))
            return True

        def _on_draw(hwnd, lparam):
            dis = DRAWITEMSTRUCT.from_address(lparam)
            if dis.CtlType != ODT_MENU:
                return False
            d = _DESCRIPTORS.get(dis.itemData)
            if d is None:
                return False
            hdc = dis.hDC
            rc = dis.rcItem
            # Use the SAME DPI source as _on_measure — the owner window's
            # GetDpiForWindow, NOT GetDeviceCaps(hdc). On a mixed-DPI
            # multi-monitor setup the popup can land on a different-DPI monitor
            # than the owner window; if measure and draw disagree on DPI a row
            # is sized at one scale and painted at another → clipped text or an
            # overlapping system arrow. Consistency between the two is what
            # prevents the clip.
            dpi = _dpi_for(hwnd)
            scale = _scale(dpi)
            selected = bool(dis.itemState & ODS_SELECTED) and not d["disabled"]
            _fill(hdc, rc, _BG_HOVER if selected else _BG)

            if d["sep"]:
                th = max(1, _px(_LINE_TH, scale))
                inset = _px(_LINE_INSET, scale)
                mid = (rc.top + rc.bottom) // 2
                line = wintypes.RECT(rc.left + inset, mid - th // 2,
                                     rc.right - inset, mid - th // 2 + th)
                _fill(hdc, line, _LIME)
                return True

            hfont = _menu_font(dpi)
            _SetBkMode(hdc, TRANSPARENT)
            _SetTextColor(hdc, _TX_DISABLED if d["disabled"] else _TX)
            old = _SelectObject(hdc, hfont) if hfont else None
            try:
                tr = wintypes.RECT(rc.left + _px(_PAD_L, scale), rc.top,
                                   rc.right - _px(_PAD_R, scale), rc.bottom)
                _DrawTextW(hdc, d["text"] or "", -1, ctypes.byref(tr),
                           DT_SINGLELINE | DT_VCENTER | DT_LEFT | DT_NOPREFIX)
            finally:
                # Always restore the prior font, even if _DrawTextW raises —
                # otherwise our font stays selected in the shared menu DC and
                # corrupts the next item's paint (matches _fill / _text_size).
                if old:
                    _SelectObject(hdc, old)
            # The submenu ▸ arrow is intentionally NOT drawn here: Windows
            # always draws its own arrow for an item that has a submenu, even
            # when the item is owner-drawn. Drawing our own stacked a second
            # arrow on top of the system one (visible doubling). We keep the
            # reserved arrow-column width in _on_measure so the system arrow
            # doesn't overlap the text.
            return True

        # ── conversion + wiring ────────────────────────────────────────────
        def _read_text(hmenu, index, cch):
            if cch <= 0:
                return ""
            buf = ctypes.create_unicode_buffer(cch + 1)
            info = MENUITEMINFOW()
            info.cbSize = ctypes.sizeof(MENUITEMINFOW)
            info.fMask = MIIM_STRING
            info.dwTypeData = ctypes.cast(buf, wintypes.LPWSTR)
            info.cch = cch + 1
            _GetMenuItemInfoW(hmenu, index, True, ctypes.byref(info))
            return buf.value

        def _apply_impl(hmenu):
            if not hmenu:
                return
            # Dark margins: paint the menu's own background dark too.
            if _BG_BRUSH:
                mi = MENUINFO()
                mi.cbSize = ctypes.sizeof(MENUINFO)
                mi.fMask = MIM_BACKGROUND
                mi.hbrBack = _BG_BRUSH
                _SetMenuInfo(hmenu, ctypes.byref(mi))

            # Build the descriptor map in a LOCAL dict, then publish it with a
            # single atomic rebind at the end. The module-level _DESCRIPTORS the
            # handlers read is therefore never the half-built map — it stays the
            # previous complete map until the rebind swaps in the new complete
            # one. (In practice apply and WM_DRAWITEM share pystray's single
            # message-pump thread and never interleave; the rebind keeps this
            # correct by construction rather than relying on that timing — and
            # under free-threaded CPython the GIL no longer makes
            # dict.clear()+repopulate look atomic.)
            global _DESCRIPTORS
            fresh = {}
            count = _GetMenuItemCount(hmenu)
            for i in range(count):
                info = MENUITEMINFOW()
                info.cbSize = ctypes.sizeof(MENUITEMINFOW)
                info.fMask = (MIIM_FTYPE | MIIM_STATE | MIIM_SUBMENU
                              | MIIM_STRING)
                info.dwTypeData = None
                info.cch = 0
                if not _GetMenuItemInfoW(hmenu, i, True, ctypes.byref(info)):
                    continue
                is_sep = bool(info.fType & MFT_SEPARATOR)
                token = _BASE_TOKEN + i
                fresh[token] = {
                    "text": "" if is_sep else _read_text(hmenu, i, info.cch),
                    "sep": is_sep,
                    "disabled": bool(info.fState & MFS_DISABLED),
                    "submenu": bool(info.hSubMenu),
                }
                upd = MENUITEMINFOW()
                upd.cbSize = ctypes.sizeof(MENUITEMINFOW)
                upd.dwItemData = token
                if is_sep:
                    # Owner-draw + keep it non-selectable like a separator.
                    upd.fMask = MIIM_FTYPE | MIIM_STATE | MIIM_DATA
                    upd.fType = MFT_OWNERDRAW
                    upd.fState = MFS_DISABLED
                else:
                    # Owner-draw; leave fState (enabled/disabled) and the
                    # submenu linkage untouched.
                    upd.fMask = MIIM_FTYPE | MIIM_DATA
                    upd.fType = MFT_OWNERDRAW
                _SetMenuItemInfoW(hmenu, i, True, ctypes.byref(upd))
            _DESCRIPTORS = fresh

        def _subclass_window(hwnd):
            key = int(hwnd)
            if key in _SUBCLASSED:
                return True
            store = {"prev": 0}

            def _proc(h, msg, wparam, lparam):
                try:
                    if msg == WM_DRAWITEM and _on_draw(h, lparam):
                        return 1
                    if msg == WM_MEASUREITEM and _on_measure(h, lparam):
                        return 1
                except Exception:
                    log.exception("darkmenu: owner-draw handler error")
                prev = store["prev"]
                if prev:
                    return _CallWindowProcW(prev, h, msg, wparam, lparam)
                return _DefWindowProcW(h, msg, wparam, lparam)

            cproc = WNDPROCTYPE(_proc)
            prev = _SetWindowLong(
                hwnd, GWLP_WNDPROC, ctypes.cast(cproc, ctypes.c_void_p))
            if not prev:
                # SetWindowLong failed — the native proc is still in place, so
                # there's no drawer for owner-draw items. Report failure so the
                # caller skips the conversion and the menu stays native (an
                # owner-draw item with no WM_DRAWITEM handler renders blank).
                log.warning(
                    "darkmenu: could not subclass menu window — staying native")
                return False
            store["prev"] = prev
            _SUBCLASSED[key] = (cproc, prev)
            return True

        def _wrap_update_menu(icon):
            if getattr(icon, "_darkmenu_wrapped", False):
                return
            orig = icon._update_menu

            def _wrapped():
                orig()
                try:
                    handle = getattr(icon, "_menu_handle", None)
                    if handle:
                        _apply_impl(handle[0])
                except Exception:
                    log.exception("darkmenu: conversion failed (non-fatal)")

            icon._update_menu = _wrapped
            icon._darkmenu_wrapped = True

        def _install_impl(icon):
            menu_hwnd = getattr(icon, "_menu_hwnd", None)
            if not menu_hwnd:
                log.debug("darkmenu: Icon._menu_hwnd not ready; skipping")
                return
            if not _subclass_window(menu_hwnd):
                # Subclass failed → there's no WM_DRAWITEM drawer. Converting
                # items to owner-draw now would render them blank, so leave the
                # menu native-themed instead.
                return
            _wrap_update_menu(icon)
            try:
                icon.update_menu()  # rebuild now so it converts immediately
                log.info("darkmenu: dark menu + lime separators installed.")
            except Exception:
                # Subclass + wrap ARE installed, so the conversion will run on
                # the next menu rebuild — but don't log success for a failed
                # initial conversion (misleading when debugging a blank menu).
                log.exception(
                    "darkmenu: initial menu conversion failed — subclass + "
                    "wrap installed; will convert on next rebuild")

        _ENABLED = True
    except Exception:  # noqa: BLE001 — any failure → stay native-themed
        log.exception("darkmenu: Win32 setup failed; menu stays native")
        _ENABLED = False


def install(icon):
    """Owner-draw the running pystray Icon's menu dark with lime separators.

    Call from the ``setup=`` callback passed to ``icon.run`` (when
    ``Icon._menu_hwnd`` exists). No-op on non-Windows or if a binding failed.
    """
    if not _ENABLED:
        return
    try:
        _install_impl(icon)
    except Exception:
        log.exception("darkmenu: install failed (non-fatal)")


def apply_dark_menu(hmenu):
    """Convert a top-level HMENU to the dark owner-draw scheme. Mostly for
    the self-test; ``install`` calls this on every rebuild for you."""
    if not _ENABLED:
        return
    try:
        _apply_impl(hmenu)
    except Exception:
        log.exception("darkmenu: apply failed (non-fatal)")


# ── self-test ──────────────────────────────────────────────────────────────
def _self_test():
    if not _ENABLED:
        print("NOT-WIN32-OR-DISABLED")
        return 0
    hmenu = _CreatePopupMenu()
    if not hmenu:
        print("FAIL: CreatePopupMenu NULL")
        return 1
    try:
        _AppendMenuW(hmenu, MF_STRING, 1, "Header")
        _AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        _AppendMenuW(hmenu, MF_STRING, 2, "Settings...")
        _apply_impl(hmenu)
        ok = True
        # All three items must now be owner-draw and registered.
        for i, want_sep in ((0, False), (1, True), (2, False)):
            mii = MENUITEMINFOW()
            mii.cbSize = ctypes.sizeof(MENUITEMINFOW)
            mii.fMask = MIIM_FTYPE | MIIM_DATA
            _GetMenuItemInfoW(hmenu, i, True, ctypes.byref(mii))
            od = bool(mii.fType & MFT_OWNERDRAW)
            d = _DESCRIPTORS.get(mii.dwItemData)
            ok = ok and od and d is not None and d["sep"] == want_sep
        print("all-ownerdraw+registered+sep-flagged -> %s"
              % ("PASS" if ok else "FAIL"))
        return 0 if ok else 1
    finally:
        _DestroyMenu(hmenu)


if __name__ == "__main__":
    sys.exit(_self_test())
