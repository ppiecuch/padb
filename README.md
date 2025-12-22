# PADB - Python ADB TUI

![PADB Screenshot](padb.png)

A terminal user interface for Android Debug Bridge operations built with Python and curses.

## Features

- **Device Management**: Auto-detect and connect to Android devices, auto-reconnect polling when no device connected
- **Interactive Shell**: Execute ADB shell commands with command history (UP/DOWN keys)
- **Meta Commands**: Built-in commands for install, pull, push, screenshot, and more
- **Live Logcat**: Real-time log streaming with regex filtering
- **Color-coded Logs**: Visual distinction between error, warning, info, and debug messages

## Prerequisites

- Python 3.11 or higher
- ADB installed and in your PATH
- Android device with USB debugging enabled

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd padb
```

2. Install dependencies:
```bash
pip3 install -r requirements.txt
```

3. Ensure ADB server is running:
```bash
adb start-server
```

## Usage

### Starting the Application

```bash
python3 main.py
```

### Device Selection

On startup:
- If one device is connected, it auto-connects
- If multiple devices are connected, use UP/DOWN arrows to select and ENTER to connect

### Window Navigation

| Key | Action |
|-----|--------|
| TAB | Switch between Shell and Logcat windows |
| F1 | Focus Shell window |
| F2 | Focus Logcat window |
| Ctrl+C | Quit application |

### Shell Window

The top window provides an interactive shell for executing commands on the device.

| Key | Action |
|-----|--------|
| ENTER | Execute command |
| UP/DOWN | Navigate command history |
| Page Up/Down | Scroll output |
| Left/Right | Move cursor |
| Home/End | Jump to start/end of line |

**Example shell commands:**
```
ls /sdcard
pm list packages
dumpsys battery
getprop ro.build.version.release
```

**Meta Commands:**

The shell supports special meta commands prefixed with `@`:

| Command | Description |
|---------|-------------|
| `@help` | Show all available meta commands |
| `@clear` | Clear shell output |
| `@info` | Show device information |
| `@install <path>` | Install APK from local path |
| `@uninstall <package>` | Uninstall an app |
| `@packages [filter]` | List installed packages (optional filter) |
| `@pull <remote> [local]` | Pull file from device |
| `@push <local> <remote>` | Push file to device |
| `@screenshot [filename]` | Take screenshot (auto-named if no filename) |
| `@reboot [mode]` | Reboot device (modes: bootloader, recovery) |
| `@activity <package>` | Show current activity for package |
| `@input <text>` | Send text input to device |
| `@tap <x> <y>` | Tap at screen coordinates |
| `@swipe <x1> <y1> <x2> <y2>` | Swipe gesture |

**Examples:**
```
@install ~/Downloads/myapp.apk
@packages google
@screenshot
@pull /sdcard/Download/file.txt ./
@tap 500 800
```

### Logcat Window

The bottom window streams device logs in real-time.

| Key | Action |
|-----|--------|
| / | Enter filter mode |
| ESC | Exit filter mode |
| ENTER | Apply filter |
| C | Clear all logs |
| Page Up/Down | Scroll (disables auto-scroll) |
| End | Resume auto-scroll |

**Filtering:**
Press `/` to enter filter mode, then type a regex pattern:
- `error` - Show lines containing "error" (case-insensitive)
- `MyApp` - Show logs from your app
- `E/.*MyTag` - Show errors with specific tag

**Log Level Colors:**
- Red: Errors (E)
- Yellow: Warnings (W)
- Cyan: Info (I)
- Green: Debug (D)

## Project Structure

```
padb/
├── main.py              # Entry point
├── requirements.txt     # Dependencies
└── padb/                # Main package
    ├── device.py        # ADB device manager
    └── tui/             # TUI components
        ├── app.py       # Main application
        ├── shell.py     # Shell window
        ├── logcat.py    # Logcat window
        └── status.py    # Status bar
```

## Troubleshooting

### "No devices found"
1. Check USB connection
2. Enable USB debugging on device: Settings > Developer Options > USB Debugging
3. Verify device is visible: `adb devices`
4. Restart ADB server: `adb kill-server && adb start-server`

### ADB server not running
```bash
adb start-server
```

### Permission denied on Linux
Add udev rules for your device or run with sudo (not recommended for production).

## License

MIT License

## Changelog

### v0.2.0

- Initial release
- Device auto-detection and selection
- Wireless ADB connection support
- Interactive shell with command history
- Meta commands support (@install, @pull, @push, @screenshot, etc.)
- Live logcat streaming with regex filtering
- Color-coded log levels
- Auto-reconnect polling when device disconnects
