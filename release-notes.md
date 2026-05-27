Same-day follow-up to v1.7.22. Two small UX fixes surfaced once the v1.7.22 standalone bundle landed on a real install path with autostart re-enabled.

### Fixed

- **Windows Startup-Apps toast / Task Manager showed the long file description as the program name.** v1.7.22 set `--file-description="Force all monitors to sleep without putting the PC to sleep."` so Windows would display that full sentence anywhere it sourced the user-facing app name from the PE `FileDescription` field — most visibly in the "App is now configured to run when you sign in" toast that fires when autostart is enabled. Shortened to `"Display Off"` in both `build-release.sh` and `build-exe.bat`. The longer descriptive sentence lives in the README and the GitHub release description, which is where it belongs. `--product-name` was already `"Display Off"`; this aligns `FileDescription` with it.

### Added

- **Tray right-click submenu: "Auto-blank when idle"** with presets Off / 5 minutes / 10 minutes / 30 minutes. Each item is a radio (only one checked at a time, reflecting `cfg['idle_blank_minutes']`). Clicking a preset persists the value via `save_config` + re-renders the menu + fires a `Display Off — Auto-blank: X min idle` toast for confirmation. Before v1.7.23 the only way to change the idle threshold was the Settings dialog spinbox; the submenu makes the common-case toggle a single right-click instead of opening the dialog.
  - **Custom (non-preset) values still work** via Settings. If `idle_blank_minutes` holds e.g. 15, none of the submenu radio items render checked, matching standard radio-button UX — clicking any preset overwrites the custom value (intentional; that's the meaning of clicking a preset).
  - **`_idle_check` / `_idle_set` helpers** live inside `run_tray()`, read `load_config()` fresh on every render and click so a stale value never wins against a config edit from elsewhere. 4 small JSON reads per right-click is bounded — `displayoff_config.json` is <200 bytes, <1ms per read.

