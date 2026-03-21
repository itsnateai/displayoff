# Changelog — Display Off

## [1.1.0] — 2026-03-21

### New Features
- `--version` flag to print current version
- `displayoff.ico` — crisp multi-size icon (16–256px) used in tray when present
- `README.md` — user-facing documentation
- Proper `logging` module replaces raw `print` statements

### Bug Fixes
- Fix ctypes 64-bit pointer truncation by setting `argtypes`/`restype` on `SendMessageW`
- Fix mixed modifier hotkey (e.g. Ctrl_L + Alt_R + F12) silently failing
- Guard `ctypes.windll` behind platform check so module imports on non-Windows
- Prevent thread spam from repeated hotkey/tray triggers via `threading.Lock`
- Cap `current_keys` set to prevent unbounded memory growth from missed release events
- Remove unused `MONITOR_ON` constant

## [1.0.0] — 2026-03-21

### New Features
- System tray app with programmatic moon/monitor icon
- Turn off all displays via tray click or global hotkey (Ctrl+Alt+F12)
- `--off` flag for immediate one-shot display off (no tray)
- 300ms delay prevents immediate wake from trigger input
- Graceful fallback if pynput not installed (tray-only mode)
