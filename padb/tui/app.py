"""Main TUI application using curses."""

import curses
import re
import socket
import time
from typing import Optional

from .. import __version__
from ..device import DeviceManager
from .shell import ShellWindow
from .logcat import LogcatWindow
from .status import StatusBar


class Application:
    """Main TUI application for PADB."""

    def __init__(self):
        self.device_manager = DeviceManager()
        self.stdscr: Optional[curses.window] = None
        self.shell_window: Optional[ShellWindow] = None
        self.logcat_window: Optional[LogcatWindow] = None
        self.status_bar: Optional[StatusBar] = None
        self.active_window = "shell"  # "shell" or "logcat"
        self.running = True

    def init_colors(self) -> None:
        """Initialize color pairs for the TUI."""
        curses.start_color()
        curses.use_default_colors()
        # Color pairs: (pair_number, foreground, background)
        curses.init_pair(1, curses.COLOR_GREEN, -1)   # Status connected
        curses.init_pair(2, curses.COLOR_RED, -1)     # Status disconnected
        curses.init_pair(3, curses.COLOR_YELLOW, -1)  # Warnings
        curses.init_pair(4, curses.COLOR_CYAN, -1)    # Info
        curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_BLUE)  # Active window title
        curses.init_pair(6, curses.COLOR_WHITE, -1)   # Inactive window title

    def create_windows(self) -> None:
        """Create all TUI windows."""
        height, width = self.stdscr.getmaxyx()

        # Status bar at top (1 line)
        status_height = 1

        # Calculate window heights (split remaining space)
        remaining_height = height - status_height - 1  # -1 for help line at bottom
        shell_height = remaining_height // 2
        logcat_height = remaining_height - shell_height

        # Create status bar
        self.status_bar = StatusBar(
            self.stdscr.subwin(status_height, width, 0, 0),
            self.device_manager,
        )

        # Create shell window (top half)
        self.shell_window = ShellWindow(
            self.stdscr.subwin(shell_height, width, status_height, 0),
            self.device_manager,
        )

        # Create logcat window (bottom half)
        self.logcat_window = LogcatWindow(
            self.stdscr.subwin(logcat_height, width, status_height + shell_height, 0),
            self.device_manager,
        )

    def show_device_selector(self) -> bool:
        """Show device selection dialog. Returns True if device selected."""
        devices = self.device_manager.list_devices()

        # No devices — try reconnecting saved wireless IPs and mDNS discovery
        if not devices:
            self._try_auto_reconnect()
            devices = self.device_manager.list_devices()

        # Still no devices - wait for connection
        if not devices:
            return self.wait_for_device()

        if len(devices) == 1:
            self.device_manager.connect(devices[0])
            return True

        # Multiple devices - show selector
        return self.show_device_list(devices)

    def _try_auto_reconnect(self) -> None:
        """Try to reconnect saved wireless devices and discover via mDNS."""
        # First try saved IPs
        saved = self.device_manager.get_saved_ips()
        if saved:
            self.device_manager.reconnect_saved()
            if self.device_manager.list_devices():
                return

        # Then try mDNS discovery
        self.device_manager.discover_and_connect()

    @staticmethod
    def _get_local_ip_prefix() -> str:
        """Get the local network IP prefix (e.g. '192.168.1.')."""
        try:
            # Connect to a public DNS to determine the local IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            # Return first 3 octets as prefix
            parts = local_ip.split(".")
            if len(parts) == 4:
                return f"{parts[0]}.{parts[1]}.{parts[2]}."
        except Exception:
            pass
        return "192.168.1."

    def _curses_input(self, y: int, x: int, prompt: str,
                      prefill: str = "", max_len: int = 40) -> Optional[str]:
        """Read a line of text input at the given position.

        Returns the entered string, or None if cancelled with ESC.
        """
        curses.curs_set(1)
        self.stdscr.addstr(y, x, prompt)
        self.stdscr.refresh()

        buf = list(prefill)
        cursor = len(buf)
        input_x = x + len(prompt)

        while True:
            # Draw current input with padding to clear old chars
            display = "".join(buf)
            self.stdscr.addstr(y, input_x, display + " " * (max_len - len(display)))
            self.stdscr.move(y, input_x + cursor)
            self.stdscr.refresh()

            key = self.stdscr.getch()
            if key in (curses.KEY_ENTER, 10, 13):
                return "".join(buf)
            elif key == 27:  # ESC
                return None
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                if cursor > 0:
                    buf.pop(cursor - 1)
                    cursor -= 1
            elif key == curses.KEY_DC:  # Delete
                if cursor < len(buf):
                    buf.pop(cursor)
            elif key == curses.KEY_LEFT:
                if cursor > 0:
                    cursor -= 1
            elif key == curses.KEY_RIGHT:
                if cursor < len(buf):
                    cursor += 1
            elif key == curses.KEY_HOME:
                cursor = 0
            elif key == curses.KEY_END:
                cursor = len(buf)
            elif 32 <= key <= 126 and len(buf) < max_len:
                buf.insert(cursor, chr(key))
                cursor += 1

    def _show_pair_dialog(self) -> bool:
        """Show wireless pairing dialog. Returns True if device paired and connected."""
        self.stdscr.clear()
        height, width = self.stdscr.getmaxyx()
        ip_prefix = self._get_local_ip_prefix()

        # Header
        title = "Wireless Pairing (Android 11+)"
        self.stdscr.addstr(1, (width - len(title)) // 2, title, curses.A_BOLD)

        # Instructions
        instructions = [
            "On your device:",
            "  1. Settings > Developer Options > Wireless debugging",
            "  2. Tap 'Pair device with pairing code'",
            "  3. Enter the IP, port, and code shown below",
        ]
        y = 3
        for line in instructions:
            self.stdscr.addstr(y, 4, line)
            y += 1

        y += 1
        self.stdscr.addstr(y + 3, 4, "(ESC to cancel at any step)")
        self.stdscr.refresh()

        # Collect IP
        ip = self._curses_input(y, 4, "IP address: ", prefill=ip_prefix)
        if ip is None:
            return False

        # Collect port
        y += 1
        port = self._curses_input(y, 4, "Pairing port: ")
        if port is None:
            return False
        if not port.isdigit():
            self.stdscr.addstr(y + 2, 4, "Invalid port number. Press any key.")
            self.stdscr.refresh()
            self.stdscr.getch()
            return False

        # Collect pairing code
        y += 1
        code = self._curses_input(y, 4, "Pairing code: ")
        if code is None:
            return False

        # Execute pairing
        y += 2
        address = f"{ip}:{port}"
        self.stdscr.addstr(y, 4, f"Pairing with {address}...")
        self.stdscr.refresh()

        success, msg = self.device_manager.pair_wireless(
            ip=address, pairing_code=code
        )

        y += 1
        if success:
            self.stdscr.addstr(y, 4, msg, curses.color_pair(1) | curses.A_BOLD)
            y += 2

            # Prompt for connection port
            self.stdscr.addstr(y, 4, "Now connect to the device.")
            y += 1
            self.stdscr.addstr(y, 4, "Use the port from 'Wireless debugging' screen")
            self.stdscr.addstr(y + 1, 4, "(NOT the pairing port).")
            y += 2
            conn_port = self._curses_input(y, 4, "Connection port: ")
            if conn_port is None:
                return False
            if not conn_port.isdigit():
                self.stdscr.addstr(y + 2, 4, "Invalid port. Press any key.")
                self.stdscr.refresh()
                self.stdscr.getch()
                return False

            conn_address = f"{ip}:{conn_port}"
            y += 1
            self.stdscr.addstr(y, 4, f"Connecting to {conn_address}...")
            self.stdscr.refresh()

            conn_ok, conn_msg = self.device_manager.connect_wireless(conn_address)
            y += 1
            if conn_ok:
                self.stdscr.addstr(y, 4, conn_msg, curses.color_pair(1) | curses.A_BOLD)
                self.stdscr.refresh()
                time.sleep(1)

                # Select the newly connected device
                devices = self.device_manager.list_devices()
                for dev in devices:
                    if dev.serial == conn_address:
                        self.device_manager.connect(dev)
                        return True
                # If exact match not found, try partial IP match
                for dev in devices:
                    if ip in dev.serial:
                        self.device_manager.connect(dev)
                        return True
                if devices:
                    self.device_manager.connect(devices[0])
                    return True
            else:
                self.stdscr.addstr(y, 4, conn_msg, curses.color_pair(2) | curses.A_BOLD)
        else:
            self.stdscr.addstr(y, 4, msg, curses.color_pair(2) | curses.A_BOLD)

        y += 2
        self.stdscr.addstr(y, 4, "Press any key to continue...")
        self.stdscr.refresh()
        self.stdscr.getch()
        return False

    def _try_mdns_connect(self) -> bool:
        """Try mDNS discovery and connect. Shows status on screen."""
        self.stdscr.clear()
        height, width = self.stdscr.getmaxyx()

        title = "mDNS Device Discovery"
        self.stdscr.addstr(1, (width - len(title)) // 2, title, curses.A_BOLD)
        self.stdscr.addstr(3, 4, "Scanning for wireless debugging devices...")
        self.stdscr.refresh()

        results = self.device_manager.discover_and_connect()
        y = 5

        if not results:
            discovered = self.device_manager.discover_mdns()
            if not discovered:
                self.stdscr.addstr(y, 4, "No devices found via mDNS.", curses.color_pair(2))
                self.stdscr.addstr(y + 1, 4, "Ensure wireless debugging is enabled on the device.")
            else:
                self.stdscr.addstr(y, 4, "Found devices but none ready to connect:")
                for d in discovered:
                    y += 1
                    status = "needs pairing" if d["needs_pairing"] else "ready"
                    self.stdscr.addstr(y, 6, f"{d['address']} ({d['name']}) [{status}]")
            y += 2
            self.stdscr.addstr(y, 4, "Press any key to continue...")
            self.stdscr.refresh()
            self.stdscr.getch()
            return False

        for addr, success, msg in results:
            status_str = "[OK]" if success else "[FAIL]"
            color = curses.color_pair(1) if success else curses.color_pair(2)
            self.stdscr.addstr(y, 4, f"{status_str} {addr}: {msg}", color)
            y += 1

            if success:
                y += 1
                self.stdscr.addstr(y, 4, "Connected!", curses.color_pair(1) | curses.A_BOLD)
                self.stdscr.refresh()
                time.sleep(1)

                devices = self.device_manager.list_devices()
                for dev in devices:
                    if dev.serial == addr:
                        self.device_manager.connect(dev)
                        return True
                if devices:
                    self.device_manager.connect(devices[0])
                    return True

        y += 1
        self.stdscr.addstr(y, 4, "Press any key to continue...")
        self.stdscr.refresh()
        self.stdscr.getch()
        return False

    def wait_for_device(self) -> bool:
        """Wait for a device to be connected, checking every 5 seconds."""
        self.stdscr.timeout(100)  # 100ms for responsive UI
        last_check = time.time()
        check_interval = 5.0
        spinner_chars = "|/-\\"
        spinner_idx = 0

        while self.running:
            self.stdscr.clear()
            height, width = self.stdscr.getmaxyx()

            # Title with version
            title = f"PADB v{__version__} - Waiting for device"
            self.stdscr.addstr(1, (width - len(title)) // 2, title, curses.A_BOLD)

            # Spinner and message
            spinner = spinner_chars[spinner_idx % len(spinner_chars)]
            msg = f"  {spinner}  No devices connected. Waiting..."
            self.stdscr.addstr(height // 2, (width - len(msg)) // 2, msg)

            # Check interval info
            now = time.time()
            next_check = int(check_interval - (now - last_check))
            if next_check < 0:
                next_check = 0
            check_msg = f"Next check in {next_check}s"
            self.stdscr.addstr(height // 2 + 2, (width - len(check_msg)) // 2, check_msg, curses.A_DIM)

            # Help
            help_text = "D: Discover (mDNS) | P: Pair wirelessly | Q: Quit"
            self.stdscr.addstr(height - 2, (width - len(help_text)) // 2, help_text)

            self.stdscr.refresh()

            # Check for new devices every 5 seconds
            if now - last_check >= check_interval:
                last_check = now
                devices = self.device_manager.list_devices()
                if devices:
                    if len(devices) == 1:
                        self.device_manager.connect(devices[0])
                        return True
                    else:
                        return self.show_device_list(devices)

                # No devices found via USB/existing connections — try mDNS discovery
                results = self.device_manager.discover_and_connect()
                for addr, success, msg in results:
                    if success:
                        devices = self.device_manager.list_devices()
                        if devices:
                            if len(devices) == 1:
                                self.device_manager.connect(devices[0])
                                return True
                            else:
                                return self.show_device_list(devices)

            # Handle input
            key = self.stdscr.getch()
            if key in (ord("q"), ord("Q"), 3):  # Q or Ctrl+C
                return False
            elif key in (ord("d"), ord("D")):
                if self._try_mdns_connect():
                    return True
                last_check = time.time()
            elif key in (ord("p"), ord("P")):
                if self._show_pair_dialog():
                    return True
                # Reset check timer after returning from dialog
                last_check = time.time()

            # Update spinner
            spinner_idx += 1
            time.sleep(0.1)

        return False

    def show_device_list(self, devices: list) -> bool:
        """Show device selection list. Returns True if device selected."""
        self.stdscr.clear()
        height, width = self.stdscr.getmaxyx()

        title = "Select a device:"
        self.stdscr.addstr(1, (width - len(title)) // 2, title, curses.A_BOLD)

        selected = 0
        while True:
            for i, device in enumerate(devices):
                try:
                    model = device.prop.model or "Unknown"
                except Exception:
                    model = "Unknown"
                line = f"  {device.serial} ({model})"
                attr = curses.A_REVERSE if i == selected else 0
                y = 3 + i
                if y < height - 2:
                    self.stdscr.addstr(y, 2, line[:width - 4], attr)

            self.stdscr.addstr(
                height - 2, 2, "Use UP/DOWN to select, ENTER to connect, Q to quit"
            )
            self.stdscr.refresh()

            key = self.stdscr.getch()
            if key == curses.KEY_UP and selected > 0:
                selected -= 1
            elif key == curses.KEY_DOWN and selected < len(devices) - 1:
                selected += 1
            elif key in (curses.KEY_ENTER, 10, 13):
                self.device_manager.connect(devices[selected])
                return True
            elif key in (ord("q"), ord("Q")):
                return False

    def show_message(self, message: str) -> None:
        """Show a message dialog."""
        self.stdscr.clear()
        height, width = self.stdscr.getmaxyx()
        y = height // 2
        x = max(0, (width - len(message)) // 2)
        self.stdscr.addstr(y, x, message)
        self.stdscr.addstr(y + 2, (width - 20) // 2, "Press any key to exit")
        self.stdscr.refresh()
        self.stdscr.getch()

    def draw_help_line(self) -> None:
        """Draw the help line at the bottom."""
        height, width = self.stdscr.getmaxyx()
        help_text = " TAB: Switch window | F1: Shell | F2: Logcat | Ctrl+C: Quit "
        try:
            self.stdscr.addstr(
                height - 1, 0, help_text.center(width)[:width - 1], curses.A_REVERSE
            )
        except curses.error:
            pass

    def handle_input(self, key: int) -> None:
        """Handle keyboard input."""
        # Global keys
        if key == 9:  # TAB
            # If shell is active and in suggestion mode, pass TAB to shell
            if (self.active_window == "shell" and self.shell_window
                    and self.shell_window.suggestion_mode):
                self.shell_window.handle_input(key)
            else:
                self.toggle_active_window()
        elif key == curses.KEY_F1:
            self.set_active_window("shell")
        elif key == curses.KEY_F2:
            self.set_active_window("logcat")
        elif key == 3:  # Ctrl+C
            self.running = False
        else:
            # Pass to active window
            if self.active_window == "shell" and self.shell_window:
                self.shell_window.handle_input(key)
            elif self.active_window == "logcat" and self.logcat_window:
                self.logcat_window.handle_input(key)

    def toggle_active_window(self) -> None:
        """Toggle between shell and logcat windows."""
        if self.active_window == "shell":
            self.set_active_window("logcat")
        else:
            self.set_active_window("shell")

    def set_active_window(self, window: str) -> None:
        """Set the active window."""
        self.active_window = window
        if self.shell_window:
            self.shell_window.set_active(window == "shell")
        if self.logcat_window:
            self.logcat_window.set_active(window == "logcat")

    def refresh_all(self) -> None:
        """Refresh all windows."""
        if self.status_bar:
            self.status_bar.refresh()
        if self.logcat_window:
            self.logcat_window.refresh()
        if self.shell_window:
            self.shell_window.refresh()
        self.draw_help_line()
        self.stdscr.noutrefresh()
        # Set cursor position after all noutrefresh calls, right before doupdate
        if self.active_window == "shell" and self.shell_window:
            cursor_y, cursor_x = self.shell_window.get_cursor_position()
            curses.setsyx(cursor_y, cursor_x)
        curses.doupdate()

    def run(self, stdscr: curses.window) -> None:
        """Main application loop."""
        self.stdscr = stdscr
        curses.curs_set(1)  # Show cursor
        self.stdscr.keypad(True)
        self.stdscr.timeout(100)  # Non-blocking getch with 100ms timeout

        self.init_colors()

        # Device selection
        if not self.show_device_selector():
            return

        # Create windows after device selection
        self.create_windows()
        self.set_active_window("shell")

        # Start logcat
        if self.logcat_window:
            self.logcat_window.start_logcat()

        # Main loop
        while self.running:
            self.refresh_all()

            key = self.stdscr.getch()
            if key != -1:
                self.handle_input(key)

        # Cleanup
        self.device_manager.stop_logcat()


def main() -> None:
    """Entry point for the TUI application."""
    app = Application()
    try:
        curses.wrapper(app.run)
    except KeyboardInterrupt:
        pass
