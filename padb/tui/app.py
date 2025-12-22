"""Main TUI application using curses."""

import curses
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

        # No devices - wait for connection
        if not devices:
            return self.wait_for_device()

        if len(devices) == 1:
            self.device_manager.connect(devices[0])
            return True

        # Multiple devices - show selector
        return self.show_device_list(devices)

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
            help_text = "Press Q to quit"
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

            # Handle input
            key = self.stdscr.getch()
            if key in (ord("q"), ord("Q"), 3):  # Q or Ctrl+C
                return False

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
