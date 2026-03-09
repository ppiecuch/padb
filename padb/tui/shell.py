"""Shell window component for executing ADB shell commands."""

import curses
import json
import os
import shlex
from pathlib import Path
from typing import Optional

from ..device import DeviceManager


# History file path
HISTORY_FILE = ".padbrc"
MAX_HISTORY_SIZE = 500


# Commands that expect local file paths and their allowed extensions
# None means all files, empty tuple means directories only
FILE_ARGUMENT_COMMANDS: dict[str, tuple[str, ...] | None] = {
    "install": (".apk",),
    "reinstall": (".apk",),
    "push": None,  # Any file
}


# List of meta commands with their descriptions for suggestions
META_COMMANDS = [
    ("help", "Show available meta commands"),
    ("clear", "Clear shell output"),
    ("info", "Show device info"),
    ("install", "Install APK from local path"),
    ("reinstall", "Reinstall APK with -r -d flags"),
    ("uninstall", "Uninstall an app"),
    ("packages", "List installed packages"),
    ("pull", "Pull file from device"),
    ("push", "Push file to device"),
    ("screenshot", "Take screenshot"),
    ("reboot", "Reboot device"),
    ("activity", "Show current activity for package"),
    ("input", "Send text input to device"),
    ("tap", "Tap at coordinates"),
    ("swipe", "Swipe gesture"),
    # Wireless device management
    ("discover", "Discover and connect via mDNS"),
    ("pair", "Pair with device (Android 11+)"),
    ("wireless", "Auto-detect and connect wirelessly"),
    ("connect", "Connect to device by IP"),
    ("reconnect", "Reconnect saved wireless devices"),
    ("devices", "List all connected devices"),
    ("test", "Test device connectivity"),
    ("server", "ADB server management"),
    ("saved", "View saved wireless IPs"),
    ("forget", "Remove saved wireless IP"),
    ("disconnect", "Disconnect wireless device"),
]


# Meta commands help text
META_COMMANDS_HELP = """
Available meta commands:

Device Operations:
  @help                    - Show this help
  @clear                   - Clear shell output
  @info                    - Show device info
  @test                    - Test device connectivity

App Management:
  @install <path>          - Install APK from local path
  @reinstall <path>        - Reinstall APK with -r -d flags
  @uninstall <package>     - Uninstall an app
  @packages [filter]       - List installed packages

File Operations:
  @pull <remote> [local]   - Pull file from device
  @push <local> <remote>   - Push file to device
  @screenshot [filename]   - Take screenshot

Input Simulation:
  @activity <package>      - Show current activity for package
  @input <text>            - Send text input to device
  @tap <x> <y>             - Tap at coordinates
  @swipe <x1> <y1> <x2> <y2> - Swipe gesture

System:
  @reboot [mode]           - Reboot device (mode: bootloader, recovery)
  @server restart          - Restart ADB server

Wireless Device Management:
  @discover                - Discover devices via mDNS and auto-connect
  @pair <ip:port> <code>   - Pair with device (Android 11+ wireless debugging)
  @wireless                - Auto-detect USB devices, enable wireless
  @connect <ip[:port]>     - Connect to device by IP
  @disconnect <ip>         - Disconnect from wireless device
  @reconnect               - Reconnect to saved wireless devices
  @devices                 - List all connected devices
  @saved                   - View saved wireless IPs
  @forget <ip>             - Remove IP from saved list
""".strip()


class ShellWindow:
    """Interactive shell window for ADB commands."""

    def __init__(self, window: curses.window, device_manager: DeviceManager):
        self.window = window
        self.device_manager = device_manager
        self.active = False
        self.command_history: list[str] = []
        self.history_index = -1
        self.current_input = ""
        self.cursor_pos = 0
        self.output_lines: list[str] = []
        self.scroll_offset = 0
        # Suggestion state
        self.suggestion_mode = False
        self.suggestion_index = 0
        self.suggestions: list[tuple[str, str]] = []
        self.file_suggestion_mode = False  # True when suggesting file paths
        # Cursor position for rendering (set during refresh)
        self._cursor_y = 0
        self._cursor_x = 0
        # Load history from file
        self._load_history()

    def _load_history(self) -> None:
        """Load command history from .padbrc file."""
        try:
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, "r") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "history" in data:
                        self.command_history = data["history"][-MAX_HISTORY_SIZE:]
        except (json.JSONDecodeError, IOError, KeyError):
            # If file is corrupted or unreadable, start fresh
            self.command_history = []

    def _save_history(self) -> None:
        """Save command history to .padbrc file."""
        try:
            data = {"history": self.command_history[-MAX_HISTORY_SIZE:]}
            with open(HISTORY_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except IOError:
            pass  # Silently fail if we can't write

    def set_active(self, active: bool) -> None:
        """Set whether this window is active."""
        self.active = active

    def get_cursor_position(self) -> tuple[int, int]:
        """Get the cursor position for when this window is active."""
        return (self._cursor_y, self._cursor_x)

    def get_dimensions(self) -> tuple[int, int]:
        """Get window dimensions (height, width)."""
        return self.window.getmaxyx()

    def _get_file_suggestions(
        self, partial_path: str, extensions: tuple[str, ...] | None
    ) -> list[tuple[str, str]]:
        """Get file suggestions based on partial path and allowed extensions."""
        suggestions: list[tuple[str, str]] = []

        # Handle path expansion
        if partial_path.startswith("~"):
            expanded = os.path.expanduser(partial_path)
            base_for_display = "~"
        else:
            expanded = partial_path
            base_for_display = ""

        # Determine directory to list and prefix to filter
        if os.path.isdir(expanded):
            search_dir = expanded
            prefix = ""
        else:
            search_dir = os.path.dirname(expanded) or "."
            prefix = os.path.basename(expanded).lower()

        try:
            entries = os.listdir(search_dir)
        except OSError:
            return suggestions

        for entry in sorted(entries):
            # Skip hidden files unless user is explicitly typing a dot
            if entry.startswith(".") and not prefix.startswith("."):
                continue

            if not entry.lower().startswith(prefix):
                continue

            full_path = os.path.join(search_dir, entry)
            is_dir = os.path.isdir(full_path)

            # Filter by extension if specified
            if not is_dir and extensions is not None:
                if not any(entry.lower().endswith(ext) for ext in extensions):
                    continue

            # Build display path
            if base_for_display:
                rel_search = search_dir.replace(os.path.expanduser("~"), "~", 1)
                display_path = os.path.join(rel_search, entry)
            else:
                display_path = os.path.join(search_dir, entry) if search_dir != "." else entry

            # Add trailing slash for directories
            if is_dir:
                display_path += "/"
                desc = "<dir>"
            else:
                # Show file size as description
                try:
                    size = os.path.getsize(full_path)
                    if size < 1024:
                        desc = f"{size} B"
                    elif size < 1024 * 1024:
                        desc = f"{size // 1024} KB"
                    else:
                        desc = f"{size // (1024 * 1024)} MB"
                except OSError:
                    desc = ""

            suggestions.append((display_path, desc))

        return suggestions[:20]  # Limit to 20 suggestions

    def _update_suggestions(self) -> None:
        """Update suggestions based on current input."""
        self.file_suggestion_mode = False

        if self.current_input.startswith("@"):
            # Parse the input to see if we have a complete command
            parts = self.current_input[1:].split(maxsplit=1)

            if len(parts) >= 1:
                cmd = parts[0].lower()

                # Check if we're in file argument mode
                if cmd in FILE_ARGUMENT_COMMANDS and (
                    len(parts) > 1 or self.current_input.endswith(" ")
                ):
                    # We have a command that expects a file path
                    partial_path = parts[1] if len(parts) > 1 else ""
                    # Unquote the path if it was quoted (for paths with spaces)
                    try:
                        unquoted = shlex.split(partial_path)
                        partial_path = unquoted[0] if unquoted else ""
                    except ValueError:
                        # Incomplete quote, strip leading quote for lookup
                        partial_path = partial_path.lstrip("'\"")
                    extensions = FILE_ARGUMENT_COMMANDS[cmd]
                    self.suggestions = self._get_file_suggestions(partial_path, extensions)
                    self.file_suggestion_mode = True
                    self.suggestion_mode = len(self.suggestions) > 0
                    if self.suggestion_index >= len(self.suggestions):
                        self.suggestion_index = 0
                    return

            # Regular command suggestions - only if no arguments yet
            if len(parts) > 1 or self.current_input.endswith(" "):
                # User has started typing arguments, stop showing command suggestions
                self.suggestion_mode = False
                self.suggestions = []
                self.suggestion_index = 0
            else:
                partial = parts[0] if parts else ""
                self.suggestions = [
                    (cmd, desc)
                    for cmd, desc in META_COMMANDS
                    if cmd.startswith(partial.lower())
                ]
                self.suggestion_mode = len(self.suggestions) > 0
                if self.suggestion_index >= len(self.suggestions):
                    self.suggestion_index = 0
        else:
            self.suggestion_mode = False
            self.suggestions = []
            self.suggestion_index = 0

    def _accept_suggestion(self) -> None:
        """Accept the currently selected suggestion."""
        if self.suggestions and self.suggestion_index < len(self.suggestions):
            selected = self.suggestions[self.suggestion_index][0]

            if self.file_suggestion_mode:
                # For file suggestions, replace just the path portion
                # Extract command name (everything between @ and first space)
                after_at = self.current_input[1:]
                space_idx = after_at.find(" ")
                cmd = after_at[:space_idx] if space_idx != -1 else after_at
                # Quote path if it contains spaces or special characters
                if " " in selected or any(c in selected for c in "'\"\\"):
                    quoted_path = shlex.quote(selected.rstrip("/"))
                    if selected.endswith("/"):
                        quoted_path += "/"
                else:
                    quoted_path = selected
                # If it's a directory, don't add trailing space
                if selected.endswith("/"):
                    self.current_input = f"@{cmd} {quoted_path}"
                else:
                    self.current_input = f"@{cmd} {quoted_path} "
                self.cursor_pos = len(self.current_input)
                # Re-trigger suggestions if it was a directory
                if selected.endswith("/"):
                    self._update_suggestions()
                    return
            else:
                # For command suggestions
                self.current_input = f"@{selected} "
                self.cursor_pos = len(self.current_input)

            self.suggestion_mode = False
            self.suggestions = []
            self.suggestion_index = 0
            self.file_suggestion_mode = False

    def handle_input(self, key: int) -> None:
        """Handle keyboard input."""
        # Handle suggestion navigation when in suggestion mode
        if self.suggestion_mode and self.suggestions:
            if key == curses.KEY_UP:
                if self.suggestion_index > 0:
                    self.suggestion_index -= 1
                return
            elif key == curses.KEY_DOWN:
                if self.suggestion_index < len(self.suggestions) - 1:
                    self.suggestion_index += 1
                return
            elif key == 9:  # TAB - accept suggestion
                self._accept_suggestion()
                return
            elif key in (curses.KEY_ENTER, 10, 13):
                # Accept suggestion and execute
                self._accept_suggestion()
                # Don't return - let it fall through to execute
            elif key == 27:  # ESC - cancel suggestion mode
                self.suggestion_mode = False
                self.suggestions = []
                self.suggestion_index = 0
                return

        if key == curses.KEY_UP:
            self._history_up()
        elif key == curses.KEY_DOWN:
            self._history_down()
        elif key == curses.KEY_LEFT:
            if self.cursor_pos > 0:
                self.cursor_pos -= 1
        elif key == curses.KEY_RIGHT:
            if self.cursor_pos < len(self.current_input):
                self.cursor_pos += 1
        elif key == curses.KEY_HOME:
            self.cursor_pos = 0
        elif key == curses.KEY_END:
            self.cursor_pos = len(self.current_input)
        elif key == curses.KEY_BACKSPACE or key == 127:
            if self.cursor_pos > 0:
                self.current_input = (
                    self.current_input[: self.cursor_pos - 1]
                    + self.current_input[self.cursor_pos:]
                )
                self.cursor_pos -= 1
                self._update_suggestions()
        elif key == curses.KEY_DC:  # Delete
            if self.cursor_pos < len(self.current_input):
                self.current_input = (
                    self.current_input[: self.cursor_pos]
                    + self.current_input[self.cursor_pos + 1:]
                )
                self._update_suggestions()
        elif key in (curses.KEY_ENTER, 10, 13):
            self._execute_command()
        elif key == curses.KEY_PPAGE:  # Page Up
            self._scroll_up()
        elif key == curses.KEY_NPAGE:  # Page Down
            self._scroll_down()
        elif 32 <= key <= 126:  # Printable characters
            self.current_input = (
                self.current_input[: self.cursor_pos]
                + chr(key)
                + self.current_input[self.cursor_pos:]
            )
            self.cursor_pos += 1
            self._update_suggestions()

    def _history_up(self) -> None:
        """Navigate up in command history."""
        if not self.command_history:
            return
        if self.history_index < len(self.command_history) - 1:
            self.history_index += 1
            self.current_input = self.command_history[
                len(self.command_history) - 1 - self.history_index
            ]
            self.cursor_pos = len(self.current_input)

    def _history_down(self) -> None:
        """Navigate down in command history."""
        if self.history_index > 0:
            self.history_index -= 1
            self.current_input = self.command_history[
                len(self.command_history) - 1 - self.history_index
            ]
            self.cursor_pos = len(self.current_input)
        elif self.history_index == 0:
            self.history_index = -1
            self.current_input = ""
            self.cursor_pos = 0

    def _scroll_up(self) -> None:
        """Scroll output up."""
        height, _ = self.get_dimensions()
        max_scroll = max(0, len(self.output_lines) - (height - 4))
        if self.scroll_offset < max_scroll:
            self.scroll_offset += 1

    def _scroll_down(self) -> None:
        """Scroll output down."""
        if self.scroll_offset > 0:
            self.scroll_offset -= 1

    def _execute_command(self) -> None:
        """Execute the current command."""
        command = self.current_input.strip()
        if not command:
            return

        # Add to history and save
        if not self.command_history or self.command_history[-1] != command:
            self.command_history.append(command)
            self._save_history()
        self.history_index = -1

        # Check for meta command
        if command.startswith("@"):
            self.output_lines.append(f"@ {command[1:]}")
            result = self._execute_meta_command(command[1:])
        else:
            self.output_lines.append(f"$ {command}")
            result = self.device_manager.shell(command)

        # Add result to output
        for line in result.split("\n"):
            self.output_lines.append(line)

        # Limit output buffer
        max_lines = 1000
        if len(self.output_lines) > max_lines:
            self.output_lines = self.output_lines[-max_lines:]

        # Reset scroll and input
        self.scroll_offset = 0
        self.current_input = ""
        self.cursor_pos = 0

    def _execute_meta_command(self, command: str) -> str:
        """Execute a meta command."""
        try:
            parts = shlex.split(command)
        except ValueError:
            parts = command.split()

        if not parts:
            return "Error: Empty command"

        cmd = parts[0].lower()
        args = parts[1:]

        if cmd == "help":
            return META_COMMANDS_HELP

        elif cmd == "clear":
            self.output_lines.clear()
            return "Output cleared"

        elif cmd == "info":
            info = self.device_manager.get_device_info()
            if not info:
                return "No device connected"
            lines = [
                f"Serial:  {info.get('serial', 'Unknown')}",
                f"Model:   {info.get('model', 'Unknown')}",
                f"Brand:   {info.get('brand', 'Unknown')}",
                f"Android: {info.get('android_version', 'Unknown')}",
            ]
            return "\n".join(lines)

        elif cmd == "install":
            if not args:
                return "Usage: @install <apk_path>"
            return self.device_manager.install(args[0])

        elif cmd == "reinstall":
            if not args:
                return "Usage: @reinstall <apk_path>"
            return self.device_manager.reinstall(args[0])

        elif cmd == "uninstall":
            if not args:
                return "Usage: @uninstall <package_name>"
            return self.device_manager.uninstall(args[0])

        elif cmd == "packages":
            filter_text = args[0] if args else ""
            return self.device_manager.list_packages(filter_text)

        elif cmd == "pull":
            if not args:
                return "Usage: @pull <remote_path> [local_path]"
            remote = args[0]
            local = args[1] if len(args) > 1 else None
            return self.device_manager.pull(remote, local)

        elif cmd == "push":
            if len(args) < 2:
                return "Usage: @push <local_path> <remote_path>"
            return self.device_manager.push(args[0], args[1])

        elif cmd == "screenshot":
            filename = args[0] if args else None
            return self.device_manager.screenshot(filename)

        elif cmd == "reboot":
            mode = args[0] if args else ""
            return self.device_manager.reboot(mode)

        elif cmd == "activity":
            if not args:
                return "Usage: @activity <package_name>"
            result = self.device_manager.shell(
                f"dumpsys activity activities | grep -A1 'mResumedActivity.*{args[0]}'"
            )
            return result if result.strip() else f"No activity found for {args[0]}"

        elif cmd == "input":
            if not args:
                return "Usage: @input <text>"
            text = " ".join(args).replace(" ", "%s")
            return self.device_manager.shell(f"input text '{text}'")

        elif cmd == "tap":
            if len(args) < 2:
                return "Usage: @tap <x> <y>"
            try:
                x, y = int(args[0]), int(args[1])
                return self.device_manager.shell(f"input tap {x} {y}")
            except ValueError:
                return "Error: x and y must be integers"

        elif cmd == "swipe":
            if len(args) < 4:
                return "Usage: @swipe <x1> <y1> <x2> <y2> [duration_ms]"
            try:
                x1, y1, x2, y2 = int(args[0]), int(args[1]), int(args[2]), int(args[3])
                duration = int(args[4]) if len(args) > 4 else 300
                return self.device_manager.shell(f"input swipe {x1} {y1} {x2} {y2} {duration}")
            except ValueError:
                return "Error: coordinates must be integers"

        # ==================== Wireless Device Management ====================

        elif cmd == "discover":
            results = self.device_manager.discover_and_connect()
            if not results:
                # Show raw discovery too
                discovered = self.device_manager.discover_mdns()
                if not discovered:
                    return "No devices found via mDNS.\nEnsure wireless debugging is enabled on the device."
                lines = ["Discovered but could not connect:"]
                for d in discovered:
                    status = "needs pairing" if d["needs_pairing"] else "ready"
                    lines.append(f"  {d['address']} ({d['name']}) [{status}]")
                return "\n".join(lines)
            lines = ["mDNS discovery results:"]
            for addr, success, msg in results:
                status = "[OK]" if success else "[FAIL]"
                lines.append(f"  {status} {addr}: {msg}")
            return "\n".join(lines)

        elif cmd == "pair":
            if len(args) < 2:
                return (
                    "Usage: @pair <ip:port> <pairing-code>\n"
                    "\n"
                    "Android 11+ wireless debugging:\n"
                    "  1. On device: Settings > Developer Options > Wireless debugging\n"
                    "  2. Tap 'Pair device with pairing code'\n"
                    "  3. Note the IP:port and 6-digit code shown\n"
                    "  4. Run: @pair <ip:port> <code>\n"
                    "  5. After pairing, connect with: @connect <ip:port>\n"
                    "     (use the IP:port from 'Wireless debugging' screen,\n"
                    "      NOT the pairing port)"
                )
            address = args[0]
            pairing_code = args[1]
            if ":" not in address:
                return "Error: Pairing requires ip:port (port shown on device pairing screen)"
            success, msg = self.device_manager.pair_wireless(
                ip=address, pairing_code=pairing_code
            )
            if success:
                # Extract IP for follow-up connect hint
                ip_part = address.split(":")[0]
                return (
                    f"{msg}\n"
                    f"\nNext: connect with @connect <ip:port>\n"
                    f"Use the IP:port shown on the 'Wireless debugging' screen\n"
                    f"(different from the pairing port)"
                )
            return msg

        elif cmd == "wireless":
            results = self.device_manager.auto_enable_wireless()
            if not results:
                return "No USB devices found to enable wireless mode"
            lines = ["Auto-wireless results:"]
            for ip, success, msg in results:
                status = "[OK]" if success else "[FAIL]"
                lines.append(f"  {status} {ip}: {msg}")
            return "\n".join(lines)

        elif cmd == "connect":
            if not args:
                return "Usage: @connect <ip[:port]>"
            ip = args[0]
            port = 5555
            if ":" in ip:
                parts = ip.split(":")
                ip = parts[0]
                try:
                    port = int(parts[1])
                except ValueError:
                    return "Error: Invalid port number"
            success, msg = self.device_manager.connect_wireless(ip, port)
            return msg

        elif cmd == "disconnect":
            if not args:
                return "Usage: @disconnect <ip[:port]>"
            success, msg = self.device_manager.disconnect_wireless(args[0])
            return msg

        elif cmd == "reconnect":
            results = self.device_manager.reconnect_saved()
            if not results:
                return "No saved wireless devices to reconnect"
            lines = ["Reconnect results:"]
            for ip, success, msg in results:
                status = "[OK]" if success else "[FAIL]"
                lines.append(f"  {status} {ip}: {msg}")
            return "\n".join(lines)

        elif cmd == "devices":
            devices = self.device_manager.get_all_devices_info()
            if not devices:
                return "No devices connected"
            lines = ["Connected devices:"]
            for d in devices:
                icon = "W" if d["is_wireless"] else "U"
                lines.append(f"  [{icon}] {d['serial']} ({d['model']})")
            return "\n".join(lines)

        elif cmd == "test":
            success, msg = self.device_manager.test_device()
            return msg

        elif cmd == "server":
            if not args or args[0].lower() != "restart":
                return "Usage: @server restart"
            success, msg, reconnect_results = self.device_manager.restart_server()
            lines = [msg]
            if reconnect_results:
                lines.append("Reconnect results:")
                for ip, ok, rmsg in reconnect_results:
                    status = "[OK]" if ok else "[FAIL]"
                    lines.append(f"  {status} {ip}: {rmsg}")
            return "\n".join(lines)

        elif cmd == "saved":
            ips = self.device_manager.get_saved_ips()
            if not ips:
                return "No saved wireless devices"
            lines = ["Saved wireless devices:"]
            for ip in ips:
                lines.append(f"  {ip}")
            return "\n".join(lines)

        elif cmd == "forget":
            if not args:
                return "Usage: @forget <ip[:port]>"
            if self.device_manager.forget_ip(args[0]):
                return f"Removed {args[0]} from saved devices"
            return f"IP {args[0]} not found in saved devices"

        else:
            return f"Unknown meta command: {cmd}\nType @help for available commands"

    def refresh(self) -> None:
        """Refresh the window display."""
        self.window.erase()
        height, width = self.get_dimensions()

        # Draw border and title
        self.window.box()
        title = " Shell "
        if self.active:
            self.window.addstr(0, 2, title, curses.color_pair(5) | curses.A_BOLD)
        else:
            self.window.addstr(0, 2, title, curses.color_pair(6))

        # Calculate output area
        output_height = height - 4  # -2 for border, -2 for input line

        # Draw output lines
        visible_lines = self.output_lines[-(output_height + self.scroll_offset):]
        if self.scroll_offset > 0:
            visible_lines = visible_lines[:output_height]
        else:
            visible_lines = visible_lines[-output_height:]

        for i, line in enumerate(visible_lines):
            y = 1 + i
            if y < height - 3:
                try:
                    self.window.addnstr(y, 1, line, width - 2)
                except curses.error:
                    pass

        # Draw scroll indicator if needed
        if self.scroll_offset > 0:
            try:
                self.window.addstr(1, width - 4, "[^]")
            except curses.error:
                pass

        # Draw input line
        input_y = height - 2
        prompt = "$ "
        try:
            self.window.addstr(input_y, 1, prompt)
            # Show input with scrolling if too long
            input_width = width - len(prompt) - 3
            display_start = max(0, self.cursor_pos - input_width + 1)
            display_input = self.current_input[display_start : display_start + input_width]
            self.window.addstr(input_y, 1 + len(prompt), display_input)

            # Calculate cursor position when window is active
            if self.active:
                cursor_x = 1 + len(prompt) + (self.cursor_pos - display_start)
                # Store cursor position for the app to set after all refreshes
                win_y, win_x = self.window.getbegyx()
                self._cursor_y = win_y + input_y
                self._cursor_x = win_x + cursor_x
        except curses.error:
            pass

        # Draw suggestion box if in suggestion mode
        if self.suggestion_mode and self.suggestions:
            self._draw_suggestion_box(input_y, width)

        self.window.noutrefresh()

    def _draw_suggestion_box(self, input_y: int, width: int) -> None:
        """Draw the suggestion box above the input line."""
        if not self.suggestions:
            return

        # Calculate box dimensions
        max_cmd_len = max(len(cmd) for cmd, _ in self.suggestions)
        max_desc_len = max(len(desc) for _, desc in self.suggestions)
        box_width = min(max_cmd_len + max_desc_len + 5, width - 4)
        box_height = min(len(self.suggestions) + 2, input_y - 1)  # +2 for borders
        visible_count = box_height - 2

        # Position box above input line
        box_y = input_y - box_height
        box_x = 3  # Start after prompt

        if box_y < 1 or visible_count < 1:
            return

        # Draw box background and border
        try:
            # Draw top border
            self.window.addstr(box_y, box_x, "┌" + "─" * (box_width - 2) + "┐")

            # Calculate which suggestions to show (scroll if needed)
            start_idx = 0
            if self.suggestion_index >= visible_count:
                start_idx = self.suggestion_index - visible_count + 1

            # Draw suggestions
            for i in range(visible_count):
                idx = start_idx + i
                if idx >= len(self.suggestions):
                    break

                cmd, desc = self.suggestions[idx]
                line_y = box_y + 1 + i

                # Format: "│ cmd  - description │"
                content = f" {cmd:<{max_cmd_len}} - {desc}"
                content = content[: box_width - 3]
                padding = box_width - len(content) - 3

                # Highlight selected item
                attr = curses.A_REVERSE if idx == self.suggestion_index else 0
                self.window.addstr(line_y, box_x, "│")
                self.window.addstr(line_y, box_x + 1, content + " " * padding, attr)
                self.window.addstr(line_y, box_x + box_width - 1, "│")

            # Draw bottom border
            bottom_y = box_y + min(len(self.suggestions), visible_count) + 1
            self.window.addstr(bottom_y, box_x, "└" + "─" * (box_width - 2) + "┘")

            # Show scroll indicator if there are more items
            if len(self.suggestions) > visible_count:
                if start_idx > 0:
                    self.window.addstr(box_y, box_x + box_width - 3, "▲")
                if start_idx + visible_count < len(self.suggestions):
                    self.window.addstr(bottom_y, box_x + box_width - 3, "▼")

        except curses.error:
            pass
