"""Wireless device state manager for persistent IP storage."""

import json
import os
import re
from pathlib import Path
from typing import Optional


# State file location in home directory
STATE_FILE = Path.home() / ".padb_wireless.json"


class WirelessStateManager:
    """Manages persistent storage of wireless device IPs."""

    def __init__(self, file_path: Optional[Path] = None):
        self.file_path = file_path or STATE_FILE
        self._ensure_file_exists()

    def _ensure_file_exists(self) -> None:
        """Ensure the state file exists with valid structure."""
        if not self.file_path.exists():
            self._write_state({"wireless_ips": []})

    def _read_state(self) -> dict:
        """Read state from file."""
        try:
            with open(self.file_path, "r") as f:
                data = json.load(f)
                if not isinstance(data, dict) or "wireless_ips" not in data:
                    return {"wireless_ips": []}
                return data
        except (json.JSONDecodeError, IOError):
            return {"wireless_ips": []}

    def _write_state(self, state: dict) -> bool:
        """Write state to file atomically."""
        temp_file = self.file_path.with_suffix(".tmp")
        try:
            with open(temp_file, "w") as f:
                json.dump(state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.rename(temp_file, self.file_path)
            return True
        except (IOError, OSError):
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass
            return False

    @staticmethod
    def validate_ip(ip: str) -> bool:
        """Validate IP:port format."""
        # Pattern: xxx.xxx.xxx.xxx:port
        pattern = r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3}):(\d+)$"
        match = re.match(pattern, ip)
        if not match:
            return False

        # Validate each octet is 0-255
        for i in range(1, 5):
            octet = int(match.group(i))
            if octet < 0 or octet > 255:
                return False

        # Validate port is 1-65535
        port = int(match.group(5))
        if port < 1 or port > 65535:
            return False

        return True

    @staticmethod
    def normalize_ip(ip: str, default_port: int = 5555) -> str:
        """Normalize IP to include port."""
        if ":" not in ip:
            return f"{ip}:{default_port}"
        return ip

    def load_ips(self) -> list[str]:
        """Load saved wireless IPs."""
        state = self._read_state()
        ips = state.get("wireless_ips", [])
        # Filter to only valid IPs
        return [ip for ip in ips if self.validate_ip(ip)]

    def save_ips(self, ips: list[str]) -> bool:
        """Save wireless IPs (replaces existing)."""
        valid_ips = []
        for ip in ips:
            normalized = self.normalize_ip(ip)
            if self.validate_ip(normalized) and normalized not in valid_ips:
                valid_ips.append(normalized)
        return self._write_state({"wireless_ips": valid_ips})

    def add_ip(self, ip: str) -> bool:
        """Add a wireless IP to the saved list."""
        normalized = self.normalize_ip(ip)
        if not self.validate_ip(normalized):
            return False

        ips = self.load_ips()
        if normalized not in ips:
            ips.append(normalized)
            return self.save_ips(ips)
        return True  # Already exists

    def remove_ip(self, ip: str) -> bool:
        """Remove a wireless IP from the saved list."""
        normalized = self.normalize_ip(ip)
        ips = self.load_ips()
        if normalized in ips:
            ips.remove(normalized)
            return self.save_ips(ips)
        return False  # Not found


# Global instance
state_manager = WirelessStateManager()
