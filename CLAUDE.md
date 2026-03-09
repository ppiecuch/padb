# PADB - Development Rules & Instructions

## Code Style

- Python 3.11+ required
- Follow PEP 8 conventions
- Use type hints for all function signatures
- Use `tuple[bool, str]` return pattern for operations that can succeed or fail

## Running

```bash
pip3 install -r requirements.txt
python3 main.py
python3 -m pytest
```

## Project Structure

```
padb/
├── main.py                 # Application entry point
├── requirements.txt        # Python dependencies
└── padb/                   # Main package
    ├── __init__.py          # Version defined here
    ├── device.py            # ADB device manager (all ADB communication)
    ├── wireless.py          # Persistent wireless IP state (~/.padb_wireless.json)
    └── tui/                 # TUI components (curses-based)
        ├── app.py           # Main controller: window layout, input routing, device selection, pairing dialog
        ├── shell.py         # Interactive shell: command execution, meta commands (@prefix), suggestions, history
        ├── logcat.py        # Logcat streaming: regex filtering, color-coded log levels
        └── status.py        # Status bar: device info display
```

## Architecture Rules

- `device.py` handles ALL ADB communication via `adbutils` library and subprocess for raw `adb` commands
- `wireless.py` manages persistent state only — no ADB commands
- TUI components in `tui/` must not call ADB directly — always go through `DeviceManager`
- Meta commands (prefixed with `@`) are defined and handled in `shell.py`
- When adding a new meta command: update `META_COMMANDS` list, `META_COMMANDS_HELP` text, and add handler in `_execute_meta_command()`

## Conventions

- Wireless methods use `_run_adb_command()` (subprocess) since `adbutils` doesn't support `pair`, `tcpip`, `connect`
- Command history persists to `.padbrc` (JSON), max 500 entries
- Wireless IPs persist to `~/.padb_wireless.json` via `WirelessStateManager`
- IP validation accepts `192.x`, `10.x`, `172.x` ranges — do not restrict to `192.` only
- `is_wireless_device()` uses full IP:port regex, not prefix matching

## Dependencies

- `adbutils>=2.0.0` — Python ADB client library
- `curses` — Terminal UI (Python standard library)
