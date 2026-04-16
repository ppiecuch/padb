"""PyInstaller runtime hook — inject bundled adb into ADBUTILS_ADB_PATH.

This file runs before main.py when the app is frozen with --embedded-adb.
It locates the adb binary that was added as data by PyInstaller and makes
adbutils use it unconditionally, so no system adb is needed.
"""

import os
import stat
import sys

if getattr(sys, "frozen", False):
    adb_bin = os.path.join(sys._MEIPASS, "adb")  # type: ignore[attr-defined]
    if os.path.isfile(adb_bin):
        # Ensure executable bit survives the extraction (PyInstaller preserves
        # permissions on macOS/Linux, but be explicit just in case)
        current = os.stat(adb_bin).st_mode
        os.chmod(adb_bin, current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.environ["ADBUTILS_ADB_PATH"] = adb_bin
