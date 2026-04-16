"""Two-panel file commander (NC/MC style) for PADB."""

import curses
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Optional

from ..device import DeviceManager


@dataclass
class FileEntry:
    name: str
    is_dir: bool
    size: int = 0
    is_link: bool = False
    link_target: str = ""

    def size_str(self) -> str:
        if self.is_dir:
            return "<DIR>"
        if self.size < 1024:
            return f"{self.size}B"
        if self.size < 1024 * 1024:
            return f"{self.size / 1024:.1f}K"
        if self.size < 1024 ** 3:
            return f"{self.size / (1024 * 1024):.1f}M"
        return f"{self.size / (1024 ** 3):.1f}G"


@dataclass
class Panel:
    path: str
    is_local: bool
    entries: list[FileEntry] = field(default_factory=list)
    cursor: int = 0
    scroll: int = 0

    @property
    def current_entry(self) -> Optional[FileEntry]:
        if self.entries and 0 <= self.cursor < len(self.entries):
            return self.entries[self.cursor]
        return None


class Commander:
    """Two-panel file commander embedded in the shell window."""

    CMDR_HELP = (
        " \u2190\u2192/TAB:Switch  \u2191\u2193:Navigate  ENTER:Open  "
        "C:Copy  D:Del  M:MkDir  R:Rename  Ctrl+R:Refresh  ESC:Exit "
    )

    def __init__(self, window: curses.window, device_manager: DeviceManager):
        self.window = window
        self.device_manager = device_manager
        self.active = True
        self.status_msg = ""
        self.status_is_error = False

        local_path, remote_path = self._load_paths()
        self.left = Panel(path=local_path, is_local=True)
        self.right = Panel(path=remote_path, is_local=False)
        self.active_side = "left"

        self._refresh_panel(self.left)
        self._refresh_panel(self.right)

    # ── Persistence ─────────────────────────────────────────────────────

    _HISTORY_FILE = ".padbrc"

    def _load_paths(self) -> tuple[str, str]:
        """Return (local_path, remote_path) from .padbrc, falling back to defaults."""
        default_local = os.path.expanduser("~")
        default_remote = "/sdcard"
        try:
            if os.path.exists(self._HISTORY_FILE):
                with open(self._HISTORY_FILE) as f:
                    data = json.load(f)
                cmdr = data.get("cmdr", {})
                local = cmdr.get("local", default_local)
                remote = cmdr.get("remote", default_remote)
                # Validate local path still exists; fall back if not
                if not os.path.isdir(local):
                    local = default_local
                return local, remote
        except (json.JSONDecodeError, IOError, KeyError):
            pass
        return default_local, default_remote

    def _save_paths(self) -> None:
        """Persist current panel paths to .padbrc."""
        try:
            data: dict = {}
            if os.path.exists(self._HISTORY_FILE):
                with open(self._HISTORY_FILE) as f:
                    data = json.load(f)
            data["cmdr"] = {"local": self.left.path, "remote": self.right.path}
            with open(self._HISTORY_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except (IOError, json.JSONDecodeError):
            pass

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def active_panel(self) -> Panel:
        return self.left if self.active_side == "left" else self.right

    @property
    def inactive_panel(self) -> Panel:
        return self.right if self.active_side == "left" else self.left

    # ── Data loading ─────────────────────────────────────────────────────

    def _refresh_panel(self, panel: Panel) -> None:
        if panel.is_local:
            panel.entries = self._list_local(panel.path)
        else:
            panel.entries = self._list_remote(panel.path)
        panel.cursor = min(panel.cursor, max(0, len(panel.entries) - 1))

    def _list_local(self, path: str) -> list[FileEntry]:
        entries: list[FileEntry] = [FileEntry(name="..", is_dir=True)]
        try:
            items = sorted(
                os.scandir(path),
                key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()),
            )
            for item in items:
                if item.name.startswith("."):
                    continue
                try:
                    is_dir = item.is_dir(follow_symlinks=False)
                    is_link = item.is_symlink()
                    size = 0 if is_dir else item.stat(follow_symlinks=False).st_size
                    entries.append(FileEntry(
                        name=item.name,
                        is_dir=is_dir,
                        size=size,
                        is_link=is_link,
                    ))
                except OSError:
                    entries.append(FileEntry(name=item.name, is_dir=False))
        except PermissionError:
            entries.append(FileEntry(name="[Permission denied]", is_dir=False))
        return entries

    def _list_remote(self, path: str) -> list[FileEntry]:
        entries: list[FileEntry] = [FileEntry(name="..", is_dir=True)]
        if not self.device_manager.current_device:
            return entries
        for item in self.device_manager.list_remote_dir(path):
            entries.append(FileEntry(
                name=item["name"],
                is_dir=item["is_dir"],
                size=item["size"],
                is_link=item["is_link"],
                link_target=item.get("link_target", ""),
            ))
        # Sort: dirs first, then by name
        head, tail = entries[:1], entries[1:]
        tail.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return head + tail

    # ── Navigation ───────────────────────────────────────────────────────

    def _navigate(self, panel: Panel, entry: FileEntry) -> None:
        if entry.name == "..":
            parent = os.path.dirname(panel.path.rstrip("/"))
            panel.path = parent if parent else "/"
        elif entry.is_dir or (entry.is_link and panel.is_local
                               and os.path.isdir(os.path.join(panel.path, entry.name))):
            if panel.is_local:
                panel.path = os.path.join(panel.path, entry.name)
            else:
                panel.path = panel.path.rstrip("/") + "/" + entry.name
        else:
            return
        panel.cursor = 0
        panel.scroll = 0
        self._refresh_panel(panel)

    # ── Operations ───────────────────────────────────────────────────────

    def _copy(self) -> None:
        src = self.active_panel
        dst = self.inactive_panel
        entry = src.current_entry
        if not entry or entry.name == "..":
            self._set_status("Nothing to copy", error=True)
            return
        if src.is_local == dst.is_local:
            self._set_status("Cannot copy between two local or two remote panels", error=True)
            return

        src_path = src.path.rstrip("/") + "/" + entry.name
        dst_path = dst.path.rstrip("/") + "/" + entry.name

        if src.is_local:
            result = self.device_manager.push(src_path, dst_path)
        else:
            result = self.device_manager.pull(src_path, dst_path)

        self._set_status(result, error="Error" in result)
        self._refresh_panel(dst)

    def _delete(self) -> None:
        panel = self.active_panel
        entry = panel.current_entry
        if not entry or entry.name == "..":
            self._set_status("Nothing to delete", error=True)
            return

        path = panel.path.rstrip("/") + "/" + entry.name

        if panel.is_local:
            try:
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                self._set_status(f"Deleted: {entry.name}")
            except Exception as e:
                self._set_status(f"Delete failed: {e}", error=True)
        else:
            ok, msg = self.device_manager.remote_delete(path, recursive=entry.is_dir)
            self._set_status(msg, error=not ok)

        # Move cursor up if we deleted the last entry
        self._refresh_panel(panel)

    def _mkdir(self) -> None:
        name = self._prompt_inline("MkDir: ")
        if not name or not name.strip():
            return
        name = name.strip()
        panel = self.active_panel
        path = panel.path.rstrip("/") + "/" + name

        if panel.is_local:
            try:
                os.makedirs(path, exist_ok=True)
                self._set_status(f"Created: {name}")
            except Exception as e:
                self._set_status(f"MkDir failed: {e}", error=True)
        else:
            ok, msg = self.device_manager.remote_mkdir(path)
            self._set_status(msg, error=not ok)

        self._refresh_panel(panel)

    def _rename(self) -> None:
        panel = self.active_panel
        entry = panel.current_entry
        if not entry or entry.name == "..":
            self._set_status("Nothing to rename", error=True)
            return

        new_name = self._prompt_inline("Rename: ", prefill=entry.name)
        if not new_name or not new_name.strip() or new_name.strip() == entry.name:
            return
        new_name = new_name.strip()

        src = panel.path.rstrip("/") + "/" + entry.name
        dst = panel.path.rstrip("/") + "/" + new_name

        if panel.is_local:
            try:
                os.rename(src, dst)
                self._set_status(f"Renamed to: {new_name}")
            except Exception as e:
                self._set_status(f"Rename failed: {e}", error=True)
        else:
            result = self.device_manager.shell(f"mv '{src}' '{dst}' 2>&1")
            if result.strip():
                self._set_status(f"Rename failed: {result.strip()}", error=True)
            else:
                self._set_status(f"Renamed to: {new_name}")

        self._refresh_panel(panel)

    # ── Inline prompt ────────────────────────────────────────────────────

    def _prompt_inline(self, prompt: str, prefill: str = "") -> Optional[str]:
        """Render an inline text input at the bottom of the window."""
        height, width = self.window.getmaxyx()
        y = height - 2
        prompt_x = 1 + len(prompt)
        max_input = width - prompt_x - 3

        buf = list(prefill[:max_input])
        cursor = len(buf)

        curses.curs_set(1)
        while True:
            text = "".join(buf)
            try:
                self.window.addstr(
                    y, 1,
                    prompt + text + " " * max(0, max_input - len(text)),
                    curses.color_pair(4),
                )
                self.window.move(y, prompt_x + cursor)
                self.window.refresh()
            except curses.error:
                pass

            key = self.window.getch()
            if key in (curses.KEY_ENTER, 10, 13):
                return "".join(buf)
            elif key == 27:
                return None
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                if cursor > 0:
                    buf.pop(cursor - 1)
                    cursor -= 1
            elif key == curses.KEY_DC:
                if cursor < len(buf):
                    buf.pop(cursor)
            elif key == curses.KEY_LEFT:
                cursor = max(0, cursor - 1)
            elif key == curses.KEY_RIGHT:
                cursor = min(len(buf), cursor + 1)
            elif key == curses.KEY_HOME:
                cursor = 0
            elif key == curses.KEY_END:
                cursor = len(buf)
            elif 32 <= key <= 126 and len(buf) < max_input:
                buf.insert(cursor, chr(key))
                cursor += 1

    # ── Input handling ───────────────────────────────────────────────────

    def handle_input(self, key: int) -> bool:
        """Handle keyboard input. Returns False when commander should exit."""
        panel = self.active_panel

        if key in (27, ord("q"), ord("Q")):  # ESC or Q
            self._save_paths()
            self.active = False
            return False

        elif key in (9, curses.KEY_LEFT, curses.KEY_RIGHT):  # TAB / ←→ — switch panels
            if key == curses.KEY_LEFT:
                self.active_side = "left"
            elif key == curses.KEY_RIGHT:
                self.active_side = "right"
            else:
                self.active_side = "right" if self.active_side == "left" else "left"
            self.status_msg = ""

        elif key == curses.KEY_UP:
            if panel.cursor > 0:
                panel.cursor -= 1
            self.status_msg = ""

        elif key == curses.KEY_DOWN:
            if panel.cursor < len(panel.entries) - 1:
                panel.cursor += 1
            self.status_msg = ""

        elif key == curses.KEY_PPAGE:
            panel.cursor = max(0, panel.cursor - 10)

        elif key == curses.KEY_NPAGE:
            panel.cursor = min(len(panel.entries) - 1, panel.cursor + 10)

        elif key in (curses.KEY_ENTER, 10, 13):
            entry = panel.current_entry
            if entry and entry.is_dir:
                self._navigate(panel, entry)

        elif key in (ord("c"), ord("C"), curses.KEY_F5):
            self._copy()

        elif key in (ord("d"), ord("D"), curses.KEY_F8):
            self._delete()

        elif key in (ord("m"), ord("M"), curses.KEY_F7):
            self._mkdir()

        elif key in (ord("r"), ord("R"), curses.KEY_F6):
            self._rename()

        elif key == 18:  # Ctrl+R — refresh both panels
            self._refresh_panel(self.left)
            self._refresh_panel(self.right)
            self._set_status("Refreshed")

        return True

    # ── Status helpers ───────────────────────────────────────────────────

    def _set_status(self, msg: str, error: bool = False) -> None:
        self.status_msg = msg
        self.status_is_error = error

    # ── Drawing ──────────────────────────────────────────────────────────

    def draw(self) -> None:
        """Render the commander into the shell window."""
        self.window.erase()
        self.window.bkgd(" ", curses.A_NORMAL)
        height, width = self.window.getmaxyx()

        if height < 8 or width < 20:
            try:
                self.window.addstr(0, 0, "Terminal too small for Commander")
            except curses.error:
                pass
            self.window.noutrefresh()
            return

        mid = width // 2
        left_x, left_w = 0, mid          # left panel columns: [0, mid)
        right_x, right_w = mid, width - mid  # right panel columns: [mid, width)

        # ── outer box ────────────────────────────────────────────────
        try:
            self.window.box()
        except curses.error:
            pass

        # ── vertical divider ─────────────────────────────────────────
        try:
            self.window.addch(0, mid, curses.ACS_TTEE)
            for row in range(1, height - 1):
                self.window.addch(row, mid, curses.ACS_VLINE)
            self.window.addch(height - 1, mid, curses.ACS_BTEE)
        except curses.error:
            pass

        # ── panel titles in top border ────────────────────────────────
        # Each title stays within its own panel half so it never touches the ┬
        # When cursor is on a symlink, replace that panel's title with the link target
        def _panel_title(panel: Panel, default: str) -> str:
            entry = panel.current_entry
            if entry and entry.is_link and entry.link_target:
                raw = f" \u2192 {entry.link_target} "
                # Available width: mid-2 for left, width-mid-2 for right
                avail = (mid - 2) if panel.is_local else (width - mid - 2)
                if len(raw) > avail:
                    raw = f" \u2192 \u2026{entry.link_target[-(avail - 5):]} "
                return raw
            return default

        l_title = _panel_title(self.left, " Local ")
        r_title = _panel_title(self.right, " Remote ")
        l_attr = curses.color_pair(4) | curses.A_BOLD if self.active_side == "left" else curses.A_DIM
        r_attr = curses.color_pair(4) | curses.A_BOLD if self.active_side == "right" else curses.A_DIM
        try:
            l_x = max(1, (mid - len(l_title)) // 2)
            if l_x + len(l_title) < mid:
                self.window.addstr(0, l_x, l_title, l_attr)
        except curses.error:
            pass
        try:
            r_x = mid + 1 + max(0, (width - mid - 1 - len(r_title)) // 2)
            if r_x + len(r_title) < width:
                self.window.addstr(0, r_x, r_title, r_attr)
        except curses.error:
            pass

        # ── path headers (row 1) ─────────────────────────────────────
        self._draw_path_row(self.left, left_x + 1, left_w - 1, 1, self.active_side == "left")
        self._draw_path_row(self.right, right_x + 1, right_w - 2, 1, self.active_side == "right")

        # ── separator below paths (row 2) ────────────────────────────
        self._draw_separator(2, width, mid)

        # ── file entry area: rows 3 .. height-3 ─────────────────────
        list_y = 3
        list_end = height - 3  # inclusive (one extra row vs old layout)
        visible = max(0, list_end - list_y + 1)

        # Pre-fill entry area so empty rows below entries have a uniform background
        blank_l = " " * max(0, left_w - 1)
        blank_r = " " * max(0, right_w - 2)
        for r in range(list_y, list_end + 1):
            try:
                self.window.addstr(r, left_x + 1, blank_l, curses.A_NORMAL)
                self.window.addstr(r, right_x + 1, blank_r, curses.A_NORMAL)
            except curses.error:
                pass

        self._draw_panel_entries(self.left, left_x + 1, left_w - 1, list_y, visible, self.active_side == "left")
        self._draw_panel_entries(self.right, right_x + 1, right_w - 2, list_y, visible, self.active_side == "right")

        # ── status row (height-2): blank when idle, message when set ─
        if self.status_msg:
            color = curses.color_pair(2) if self.status_is_error else curses.color_pair(1)
            msg = self.status_msg[: width - 4]
            try:
                self.window.addstr(height - 2, 2, msg, color | curses.A_BOLD)
            except curses.error:
                pass

        self.window.noutrefresh()

    def _draw_separator(self, row: int, width: int, mid: int) -> None:
        try:
            self.window.addch(row, 0, curses.ACS_LTEE)
            for col in range(1, width - 1):
                if col == mid:
                    self.window.addch(row, col, curses.ACS_PLUS)
                else:
                    self.window.addch(row, col, curses.ACS_HLINE)
            self.window.addch(row, width - 1, curses.ACS_RTEE)
        except curses.error:
            pass

    def _draw_path_row(self, panel: Panel, x: int, w: int, row: int, is_active: bool) -> None:
        label = "L" if panel.is_local else "R"
        dirs = sum(1 for e in panel.entries if e.is_dir and e.name != "..")
        files = sum(1 for e in panel.entries if not e.is_dir)
        count = f" {dirs}d {files}f "

        # Reserve space for label prefix and count; truncate path from the left
        prefix = f" {label}:"
        path_budget = w - len(prefix) - len(count)
        path = panel.path
        if path_budget <= 0:
            path = ""
        elif len(path) > path_budget:
            path = "\u2026" + path[-(path_budget - 1):]

        text = (prefix + path).ljust(w - len(count)) + count

        attr = curses.color_pair(4) | curses.A_BOLD if is_active else curses.A_DIM
        try:
            self.window.addstr(row, x, text[:w], attr)
        except curses.error:
            pass

    def _draw_panel_entries(
        self, panel: Panel, x: int, w: int, list_y: int, visible: int, is_active: bool
    ) -> None:
        if visible <= 0 or w <= 0:
            return

        # Adjust scroll
        if panel.cursor < panel.scroll:
            panel.scroll = panel.cursor
        elif panel.cursor >= panel.scroll + visible:
            panel.scroll = panel.cursor - visible + 1

        size_col = 6   # right-aligned size column width
        name_w = w - size_col - 1  # 1 space separator between name and size
        if name_w <= 0:
            return

        for i in range(visible):
            idx = panel.scroll + i
            row = list_y + i

            if idx >= len(panel.entries):
                continue

            entry = panel.entries[idx]
            is_cursor = (idx == panel.cursor and is_active)

            # Sanitize control chars (e.g. macOS Icon\r) — they corrupt curses drawing
            display_name = "".join(c if c.isprintable() else "\u00b7" for c in entry.name)
            if entry.is_dir and entry.name != "..":
                display_name += "/"
            if len(display_name) > name_w:
                display_name = display_name[:name_w - 1] + "\u2026"
            name_field = display_name.ljust(name_w)  # exactly name_w chars
            size_field = entry.size_str().rjust(size_col)  # exactly size_col chars

            try:
                if is_cursor:
                    # addch preserves ACS_VLINE's A_ALTCHARSET flag (chgat strips it → shows 'x')
                    self.window.addch(row, x - 1, curses.ACS_VLINE, curses.color_pair(5))
                    self.window.addstr(row, x, name_field, curses.color_pair(5) | curses.A_BOLD)
                    self.window.addstr(row, x + name_w, " " + size_field,
                                       curses.color_pair(5) | curses.A_BOLD)
                else:
                    if entry.name == "..":
                        na = curses.A_BOLD
                    elif entry.is_dir:
                        na = curses.color_pair(4) | curses.A_BOLD
                    elif entry.is_link:
                        na = curses.color_pair(3) | curses.A_BOLD
                    else:
                        na = 0
                    self.window.addstr(row, x, name_field, na)
                    self.window.addstr(row, x + name_w, " " + size_field, curses.A_DIM)
            except curses.error:
                pass

