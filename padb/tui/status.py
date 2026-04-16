"""Status bar component showing device information."""

import curses
from typing import Optional

from .. import __version__
from ..device import DeviceManager


class StatusBar:
    """Status bar showing connected device information."""

    def __init__(self, window: curses.window, device_manager: DeviceManager):
        self.window = window
        self.device_manager = device_manager
        self.override_text: Optional[str] = None

    def get_dimensions(self) -> tuple[int, int]:
        """Get window dimensions (height, width)."""
        return self.window.getmaxyx()

    def refresh(self) -> None:
        """Refresh the status bar display."""
        self.window.erase()
        height, width = self.get_dimensions()

        if self.override_text is not None:
            status = self.override_text.ljust(width - 1)[:width - 1]
            color = curses.color_pair(4) | curses.A_BOLD  # Cyan for special modes
        else:
            device = self.device_manager.current_device
            if device:
                info = self.device_manager.get_device_info()
                status = (
                    f" PADB v{__version__} | Device: {info.get('serial', 'Unknown')} | "
                    f"Model: {info.get('model', 'Unknown')} | "
                    f"Android: {info.get('android_version', 'Unknown')} "
                )
                color = curses.color_pair(1) | curses.A_BOLD  # Green
            else:
                status = f" PADB v{__version__} | No device connected "
                color = curses.color_pair(2) | curses.A_BOLD  # Red

            status = status.ljust(width - 1)[:width - 1]

        try:
            self.window.addstr(0, 0, status, color | curses.A_REVERSE)
        except curses.error:
            pass

        self.window.noutrefresh()
