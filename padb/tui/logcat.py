"""Logcat window component for streaming Android logs."""

import curses
import re
import threading
from collections import deque
from typing import Optional

from ..device import DeviceManager


class LogcatWindow:
    """Logcat streaming window with filtering."""

    def __init__(
        self,
        window: curses.window,
        device_manager: DeviceManager,
        max_lines: int = 1000,
    ):
        self.window = window
        self.device_manager = device_manager
        self.active = False
        self.filter_text = ""
        self.filter_cursor = 0
        self.editing_filter = False
        self.max_lines = max_lines
        self.log_lines: deque[str] = deque(maxlen=max_lines)
        self.filtered_lines: list[str] = []
        self.scroll_offset = 0
        self.auto_scroll = True
        self.line_wrap = False
        self._lock = threading.Lock()
        self._filter_regex: Optional[re.Pattern] = None

    def set_active(self, active: bool) -> None:
        """Set whether this window is active."""
        self.active = active

    def get_dimensions(self) -> tuple[int, int]:
        """Get window dimensions (height, width)."""
        return self.window.getmaxyx()

    def _compile_filter(self) -> None:
        """Compile the filter regex."""
        if not self.filter_text:
            self._filter_regex = None
        else:
            try:
                self._filter_regex = re.compile(self.filter_text, re.IGNORECASE)
            except re.error:
                self._filter_regex = None

    def _update_filtered_lines(self) -> None:
        """Update the filtered lines based on current filter."""
        with self._lock:
            if self._filter_regex:
                self.filtered_lines = [
                    line for line in self.log_lines
                    if self._filter_regex.search(line)
                ]
            else:
                self.filtered_lines = list(self.log_lines)

    def add_log_line(self, line: str) -> None:
        """Add a log line (called from device manager)."""
        with self._lock:
            self.log_lines.append(line)
            should_add = False
            if self._filter_regex:
                if self._filter_regex.search(line):
                    should_add = True
            else:
                should_add = True

            if should_add:
                self.filtered_lines.append(line)
                # Keep viewport stable when in HOLD mode
                if not self.auto_scroll:
                    self.scroll_offset += 1

            # Limit filtered lines
            if len(self.filtered_lines) > self.max_lines:
                self.filtered_lines = self.filtered_lines[-self.max_lines:]

    def start_logcat(self) -> None:
        """Start streaming logcat."""
        self.device_manager.start_logcat(self.add_log_line, clear=True)

    def handle_input(self, key: int) -> None:
        """Handle keyboard input."""
        if self.editing_filter:
            self._handle_filter_input(key)
        else:
            self._handle_normal_input(key)

    def _handle_normal_input(self, key: int) -> None:
        """Handle input in normal mode."""
        if key == ord("/"):
            self.editing_filter = True
            self.filter_cursor = len(self.filter_text)
        elif key in (curses.KEY_ENTER, 10, 13):
            # Jump to end and resume auto-scroll
            self.scroll_offset = 0
            self.auto_scroll = True
        elif key == curses.KEY_UP:
            self._scroll_up(lines=1)
        elif key == curses.KEY_DOWN:
            self._scroll_down(lines=1)
        elif key == curses.KEY_PPAGE:  # Page Up
            self._scroll_up()
        elif key == curses.KEY_NPAGE:  # Page Down
            self._scroll_down()
        elif key == curses.KEY_HOME:
            self.scroll_offset = max(0, len(self.filtered_lines) - self._get_output_height())
            self.auto_scroll = False
        elif key == curses.KEY_END:
            self.scroll_offset = 0
            self.auto_scroll = True
        elif key == ord("w") or key == ord("W"):
            # Toggle line wrap
            self.line_wrap = not self.line_wrap
        elif key == ord("c") or key == ord("C"):
            # Clear logs
            with self._lock:
                self.log_lines.clear()
                self.filtered_lines.clear()
            self.scroll_offset = 0
            self.auto_scroll = True

    def _handle_filter_input(self, key: int) -> None:
        """Handle input while editing filter."""
        if key == 27:  # ESC
            self.editing_filter = False
        elif key in (curses.KEY_ENTER, 10, 13):
            self.editing_filter = False
            self._compile_filter()
            self._update_filtered_lines()
            self.scroll_offset = 0
            self.auto_scroll = True
        elif key == curses.KEY_LEFT:
            if self.filter_cursor > 0:
                self.filter_cursor -= 1
        elif key == curses.KEY_RIGHT:
            if self.filter_cursor < len(self.filter_text):
                self.filter_cursor += 1
        elif key == curses.KEY_HOME:
            self.filter_cursor = 0
        elif key == curses.KEY_END:
            self.filter_cursor = len(self.filter_text)
        elif key == curses.KEY_BACKSPACE or key == 127:
            if self.filter_cursor > 0:
                self.filter_text = (
                    self.filter_text[: self.filter_cursor - 1]
                    + self.filter_text[self.filter_cursor:]
                )
                self.filter_cursor -= 1
        elif key == curses.KEY_DC:  # Delete
            if self.filter_cursor < len(self.filter_text):
                self.filter_text = (
                    self.filter_text[: self.filter_cursor]
                    + self.filter_text[self.filter_cursor + 1:]
                )
        elif 32 <= key <= 126:  # Printable characters
            self.filter_text = (
                self.filter_text[: self.filter_cursor]
                + chr(key)
                + self.filter_text[self.filter_cursor:]
            )
            self.filter_cursor += 1

    def _get_output_height(self) -> int:
        """Get the height available for output lines."""
        height, _ = self.get_dimensions()
        return height - 4  # -2 for border, -2 for filter line

    def _scroll_up(self, lines: Optional[int] = None) -> None:
        """Scroll output up."""
        output_height = self._get_output_height()
        if lines is None:
            lines = output_height  # Page scroll
        max_scroll = max(0, len(self.filtered_lines) - output_height)
        if self.scroll_offset < max_scroll:
            self.scroll_offset = min(self.scroll_offset + lines, max_scroll)
            self.auto_scroll = False

    def _scroll_down(self, lines: Optional[int] = None) -> None:
        """Scroll output down."""
        output_height = self._get_output_height()
        if lines is None:
            lines = output_height  # Page scroll
        if self.scroll_offset > 0:
            self.scroll_offset = max(0, self.scroll_offset - lines)
        if self.scroll_offset == 0:
            self.auto_scroll = True

    def _get_log_level_color(self, line: str) -> int:
        """Get color pair based on log level."""
        if " E " in line or "/E " in line:
            return curses.color_pair(2)  # Red for errors
        elif " W " in line or "/W " in line:
            return curses.color_pair(3)  # Yellow for warnings
        elif " I " in line or "/I " in line:
            return curses.color_pair(4)  # Cyan for info
        elif " D " in line or "/D " in line:
            return curses.color_pair(1)  # Green for debug
        return 0

    def refresh(self) -> None:
        """Refresh the window display."""
        self.window.erase()
        height, width = self.get_dimensions()

        # Draw border and title
        self.window.box()
        title = " Logcat "
        if self.active:
            self.window.addstr(0, 2, title, curses.color_pair(5) | curses.A_BOLD)
        else:
            self.window.addstr(0, 2, title, curses.color_pair(6))

        # Show wrap indicator and log count
        wrap_str = "[W] " if self.line_wrap else ""
        count_str = f" {wrap_str}[{len(self.filtered_lines)}/{len(self.log_lines)}] "
        try:
            self.window.addstr(0, width - len(count_str) - 2, count_str)
        except curses.error:
            pass

        # Calculate output area
        output_height = self._get_output_height()

        # Get visible lines
        with self._lock:
            if self.auto_scroll:
                visible_lines = self.filtered_lines[-output_height:]
            else:
                end_idx = len(self.filtered_lines) - self.scroll_offset
                start_idx = max(0, end_idx - output_height)
                visible_lines = self.filtered_lines[start_idx:end_idx]

        # Draw log lines
        if self.line_wrap:
            # With line wrap: wrap long lines across multiple display rows
            display_row = 1
            content_width = width - 2
            for line in visible_lines:
                if display_row >= height - 3:
                    break
                color = self._get_log_level_color(line)
                # Wrap the line
                if len(line) <= content_width:
                    try:
                        self.window.addnstr(display_row, 1, line, content_width, color)
                    except curses.error:
                        pass
                    display_row += 1
                else:
                    # Split into wrapped segments
                    pos = 0
                    while pos < len(line) and display_row < height - 3:
                        segment = line[pos:pos + content_width]
                        try:
                            self.window.addnstr(display_row, 1, segment, content_width, color)
                        except curses.error:
                            pass
                        display_row += 1
                        pos += content_width
        else:
            # No line wrap: truncate long lines
            for i, line in enumerate(visible_lines):
                y = 1 + i
                if y < height - 3:
                    try:
                        color = self._get_log_level_color(line)
                        self.window.addnstr(y, 1, line, width - 2, color)
                    except curses.error:
                        pass

        # Draw scroll indicator (only if actually scrolled back, not at bottom)
        if not self.auto_scroll and self.scroll_offset > 0:
            try:
                self.window.addstr(1, width - 6, "[HOLD]", curses.color_pair(3))
            except curses.error:
                pass

        # Draw filter line
        filter_y = height - 2
        filter_prompt = "Filter: " if not self.editing_filter else "Filter> "
        try:
            self.window.addstr(filter_y, 1, filter_prompt)
            filter_width = width - len(filter_prompt) - 3
            display_start = max(0, self.filter_cursor - filter_width + 1)
            display_filter = self.filter_text[display_start : display_start + filter_width]
            attr = curses.A_UNDERLINE if self.editing_filter else 0
            self.window.addstr(filter_y, 1 + len(filter_prompt), display_filter, attr)

            # Position cursor when editing
            if self.active and self.editing_filter:
                cursor_x = 1 + len(filter_prompt) + (self.filter_cursor - display_start)
                self.window.move(filter_y, cursor_x)

            # Show hint
            if self.active and not self.editing_filter:
                hint = " (/ filter, C clear, W wrap)"
                hint_x = width - len(hint) - 2
                if hint_x > len(filter_prompt) + len(self.filter_text) + 2:
                    self.window.addstr(filter_y, hint_x, hint, curses.A_DIM)
        except curses.error:
            pass

        self.window.noutrefresh()
