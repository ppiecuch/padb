"""Device manager for ADB operations"""

import os
import re
import subprocess
import threading
from typing import Callable, Optional
from adbutils import AdbClient, AdbDevice

from .wireless import state_manager


class DeviceManager:
    """Manages ADB device connections and operations."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5037):
        self.client = AdbClient(host=host, port=port)
        self.current_device: Optional[AdbDevice] = None
        self._logcat_thread: Optional[threading.Thread] = None
        self._logcat_running = False

    def list_devices(self) -> list[AdbDevice]:
        """List all connected ADB devices."""
        try:
            return self.client.device_list()
        except Exception:
            return []

    def connect(self, device: AdbDevice) -> bool:
        """Connect to a specific device."""
        try:
            self.current_device = device
            return True
        except Exception:
            self.current_device = None
            return False

    def connect_by_serial(self, serial: str) -> bool:
        """Connect to a device by its serial number."""
        try:
            device = self.client.device(serial)
            self.current_device = device
            return True
        except Exception:
            self.current_device = None
            return False

    def auto_connect(self) -> Optional[AdbDevice]:
        """Auto-connect if only one device is available."""
        devices = self.list_devices()
        if len(devices) == 1:
            self.connect(devices[0])
            return self.current_device
        return None

    def get_device_info(self) -> dict:
        """Get information about the current device."""
        if not self.current_device:
            return {}

        try:
            props = {}
            props["serial"] = self.current_device.serial
            props["model"] = self.current_device.prop.model or "Unknown"
            props["brand"] = self.current_device.prop.get("ro.product.brand", "Unknown")
            props["android_version"] = self.current_device.prop.get(
                "ro.build.version.release", "Unknown"
            )
            return props
        except Exception:
            return {"serial": self.current_device.serial if self.current_device else "Unknown"}

    def shell(self, command: str) -> str:
        """Execute a shell command on the device."""
        if not self.current_device:
            return "Error: No device connected"
        try:
            return self.current_device.shell(command)
        except Exception as e:
            return f"Error: {e}"

    def start_logcat(
        self,
        callback: Callable[[str], None],
        clear: bool = True,
    ) -> None:
        """Start streaming logcat output."""
        if not self.current_device:
            return

        self.stop_logcat()
        self._logcat_running = True

        def logcat_worker():
            conn = None
            try:
                if clear:
                    self.current_device.shell("logcat -c")

                # Get streaming connection
                conn = self.current_device.shell(
                    "logcat -v threadtime",
                    stream=True,
                )

                # Read from connection line by line
                buffer = ""
                while self._logcat_running:
                    try:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        buffer += chunk.decode("utf-8", errors="replace")

                        # Process complete lines
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            if line:
                                callback(line.rstrip("\r"))
                    except Exception:
                        break
            except Exception:
                pass
            finally:
                self._logcat_running = False
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

        self._logcat_thread = threading.Thread(target=logcat_worker, daemon=True)
        self._logcat_thread.start()

    def stop_logcat(self) -> None:
        """Stop the logcat stream."""
        self._logcat_running = False
        if self._logcat_thread and self._logcat_thread.is_alive():
            self._logcat_thread.join(timeout=1.0)
        self._logcat_thread = None

    def disconnect(self) -> None:
        """Disconnect from the current device."""
        self.stop_logcat()
        self.current_device = None

    def install(self, apk_path: str) -> str:
        """Install an APK on the device."""
        if not self.current_device:
            return "Error: No device connected"

        # Expand user home directory
        apk_path = os.path.expanduser(apk_path)

        if not os.path.exists(apk_path):
            return f"Error: File not found: {apk_path}"
        if not apk_path.lower().endswith(".apk"):
            return "Error: File must be an APK"
        try:
            # Use shell-based install to avoid adbutils verbose output
            # First push the APK to device
            remote_path = f"/data/local/tmp/{os.path.basename(apk_path)}"
            self.current_device.sync.push(apk_path, remote_path)

            # Then install using pm install
            result = self.current_device.shell(f"pm install -r -t {remote_path}")

            # Clean up
            self.current_device.shell(f"rm {remote_path}")

            if "Success" in result:
                return f"Successfully installed: {os.path.basename(apk_path)}"
            else:
                # Return just the relevant error, not the full output
                error_line = [l for l in result.split('\n') if l.strip()]
                return f"Install failed: {error_line[-1] if error_line else 'Unknown error'}"
        except Exception as e:
            return f"Error installing: {e}"

    def reinstall(self, apk_path: str) -> str:
        """Reinstall an APK with -r -d flags to handle certificate conflicts."""
        if not self.current_device:
            return "Error: No device connected"

        apk_path = os.path.expanduser(apk_path)

        if not os.path.exists(apk_path):
            return f"Error: File not found: {apk_path}"
        if not apk_path.lower().endswith(".apk"):
            return "Error: File must be an APK"
        try:
            remote_path = f"/data/local/tmp/{os.path.basename(apk_path)}"
            self.current_device.sync.push(apk_path, remote_path)

            result = self.current_device.shell(f"pm install -r -d {remote_path}")

            self.current_device.shell(f"rm {remote_path}")

            if "Success" in result:
                return f"Successfully reinstalled: {os.path.basename(apk_path)}"
            else:
                error_line = [l for l in result.split('\n') if l.strip()]
                return f"Reinstall failed: {error_line[-1] if error_line else 'Unknown error'}"
        except Exception as e:
            return f"Error reinstalling: {e}"

    def uninstall(self, package: str) -> str:
        """Uninstall an app from the device."""
        if not self.current_device:
            return "Error: No device connected"
        try:
            self.current_device.uninstall(package)
            return f"Successfully uninstalled: {package}"
        except Exception as e:
            return f"Error uninstalling: {e}"

    def pull(self, remote_path: str, local_path: Optional[str] = None) -> str:
        """Pull a file from the device."""
        if not self.current_device:
            return "Error: No device connected"
        try:
            if local_path is None:
                local_path = os.path.basename(remote_path)
            self.current_device.sync.pull(remote_path, local_path)
            return f"Pulled: {remote_path} -> {local_path}"
        except Exception as e:
            return f"Error pulling file: {e}"

    def push(self, local_path: str, remote_path: str) -> str:
        """Push a file to the device."""
        if not self.current_device:
            return "Error: No device connected"
        if not os.path.exists(local_path):
            return f"Error: Local file not found: {local_path}"
        try:
            self.current_device.sync.push(local_path, remote_path)
            return f"Pushed: {local_path} -> {remote_path}"
        except Exception as e:
            return f"Error pushing file: {e}"

    def screenshot(self, filename: Optional[str] = None) -> str:
        """Take a screenshot and save it locally."""
        if not self.current_device:
            return "Error: No device connected"
        try:
            if filename is None:
                from datetime import datetime
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"screenshot_{timestamp}.png"

            img = self.current_device.screenshot()
            img.save(filename)
            return f"Screenshot saved: {filename}"
        except Exception as e:
            return f"Error taking screenshot: {e}"

    def reboot(self, mode: str = "") -> str:
        """Reboot the device."""
        if not self.current_device:
            return "Error: No device connected"
        try:
            if mode:
                self.current_device.shell(f"reboot {mode}")
            else:
                self.current_device.shell("reboot")
            return f"Rebooting device{' into ' + mode if mode else ''}..."
        except Exception as e:
            return f"Error rebooting: {e}"

    def list_packages(self, filter_text: str = "") -> str:
        """List installed packages."""
        if not self.current_device:
            return "Error: No device connected"
        try:
            result = self.current_device.shell("pm list packages")
            packages = [line.replace("package:", "") for line in result.strip().split("\n")]
            if filter_text:
                packages = [p for p in packages if filter_text.lower() in p.lower()]
            packages.sort()
            return "\n".join(packages) if packages else "No packages found"
        except Exception as e:
            return f"Error listing packages: {e}"

    # ==================== Wireless Device Management ====================

    _adb_path: Optional[str] = None

    @classmethod
    def _find_adb(cls) -> Optional[str]:
        """Find the adb binary path."""
        if cls._adb_path:
            return cls._adb_path

        import shutil

        # Check PATH first
        adb = shutil.which("adb")
        if adb:
            cls._adb_path = adb
            return adb

        # Common locations
        locations = [
            os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
            os.path.expanduser("~/Android/Sdk/platform-tools/adb"),
            "/opt/homebrew/bin/adb",
            "/usr/local/bin/adb",
            "/usr/bin/adb",
            # Windows
            os.path.expanduser("~/AppData/Local/Android/Sdk/platform-tools/adb.exe"),
        ]

        for path in locations:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                cls._adb_path = path
                return path

        return None

    def _run_adb_command(self, args: list[str]) -> tuple[str, str]:
        """Run an ADB command via subprocess."""
        adb = self._find_adb()
        if not adb:
            return "", "ADB not found. Install Android SDK or add adb to PATH"

        try:
            result = subprocess.run(
                [adb] + args,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return "", "Command timed out"
        except FileNotFoundError:
            return "", "ADB not found"
        except Exception as e:
            return "", str(e)

    def is_wireless_device(self, serial: str) -> bool:
        """Check if a device serial indicates a wireless connection."""
        # Wireless devices have IP:port format
        pattern = r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+$"
        return bool(re.match(pattern, serial))

    def get_device_ip(self, device: Optional[AdbDevice] = None) -> Optional[str]:
        """Get the WiFi IP address of a device."""
        target = device or self.current_device
        if not target:
            return None

        try:
            # Try wlan0 first (most common)
            result = target.shell("ip -f inet addr show wlan0")
            for line in result.splitlines():
                line = line.strip()
                if line.startswith("inet "):
                    ip = line.split()[1].split("/")[0]
                    if ip.startswith(("192.", "10.", "172.")):
                        return ip

            # Fallback: try to get any wireless IP
            result = target.shell("ip route get 1.1.1.1 2>/dev/null | head -1")
            match = re.search(r"src (\d+\.\d+\.\d+\.\d+)", result)
            if match:
                return match.group(1)

            return None
        except Exception:
            return None

    def enable_tcpip(self, device: Optional[AdbDevice] = None, port: int = 5555) -> tuple[bool, str]:
        """Enable TCP/IP mode on a device."""
        target = device or self.current_device
        if not target:
            return False, "No device specified"

        # Skip if already a wireless device
        if self.is_wireless_device(target.serial):
            return True, "Already in wireless mode"

        stdout, stderr = self._run_adb_command(["-s", target.serial, "tcpip", str(port)])
        if stderr and "error" in stderr.lower():
            return False, stderr
        return True, stdout or f"TCP/IP mode enabled on port {port}"

    def discover_mdns(self) -> list[dict]:
        """Discover wireless debugging devices via mDNS.

        Returns list of dicts with keys: name, service, address (ip:port).
        """
        # Check if mdns is available
        stdout, stderr = self._run_adb_command(["mdns", "check"])
        if "mdns daemon" not in (stdout + stderr).lower():
            return []

        # Get discovered services
        stdout, stderr = self._run_adb_command(["mdns", "services"])
        if not stdout:
            return []

        devices = []
        for line in stdout.splitlines():
            # Format: name\tservice_type\tip:port
            # Example: adb-RG405M01372952-P1y5Yg	_adb-tls-connect._tcp	192.168.1.35:37055
            parts = line.split("\t")
            if len(parts) >= 3 and ":" in parts[2]:
                service_type = parts[1].strip()
                # _adb-tls-connect = ready to connect (already paired)
                # _adb-tls-pairing = waiting to pair
                devices.append({
                    "name": parts[0].strip(),
                    "service": service_type,
                    "address": parts[2].strip(),
                    "needs_pairing": "_pairing" in service_type,
                })
        return devices

    def discover_and_connect(self) -> list[tuple[str, bool, str]]:
        """Discover devices via mDNS and connect to ones that are ready.

        Returns list of (address, success, message) tuples.
        """
        discovered = self.discover_mdns()
        if not discovered:
            return []

        results = []
        for device in discovered:
            if device["needs_pairing"]:
                results.append((
                    device["address"],
                    False,
                    f"Needs pairing first ({device['name']})",
                ))
                continue

            success, msg = self.connect_wireless(device["address"])
            if success:
                state_manager.add_ip(device["address"])
            results.append((device["address"], success, f"{msg} ({device['name']})"))

        return results

    def pair_wireless(self, ip: str, port: int = 0, pairing_code: str = "") -> tuple[bool, str]:
        """Pair with a device using Android 11+ wireless debugging.

        Args:
            ip: Device IP address (may include :port).
            port: Pairing port shown on device (random high port).
            pairing_code: 6-digit pairing code shown on device.
        """
        if not pairing_code:
            return False, "Pairing code is required"

        # Build address
        if ":" in ip:
            address = ip
        elif port:
            address = f"{ip}:{port}"
        else:
            return False, "Pairing port is required (shown on device screen)"

        stdout, stderr = self._run_adb_command(["pair", address, pairing_code])
        combined = stdout + stderr

        if "successfully paired" in combined.lower():
            # After pairing, try to find and connect to the device's debug port
            # The debug port is different from the pairing port
            return True, f"Successfully paired with {address}"
        elif "failed" in combined.lower():
            return False, combined or f"Pairing failed with {address}"
        else:
            # Some versions return different messages
            if stderr and "error" in stderr.lower():
                return False, stderr
            return True, combined or f"Pair command sent to {address}"

    def connect_wireless(self, ip: str, port: int = 5555) -> tuple[bool, str]:
        """Connect to a device via TCP/IP."""
        address = f"{ip}:{port}" if ":" not in ip else ip
        stdout, stderr = self._run_adb_command(["connect", address])

        combined = stdout + stderr
        if "connected" in combined.lower() or "already connected" in combined.lower():
            # Save to state
            state_manager.add_ip(address)
            return True, f"Connected to {address}"
        return False, combined or f"Failed to connect to {address}"

    def disconnect_wireless(self, ip: str) -> tuple[bool, str]:
        """Disconnect from a wireless device."""
        address = ip if ":" in ip else f"{ip}:5555"
        stdout, stderr = self._run_adb_command(["disconnect", address])

        combined = stdout + stderr
        if "disconnected" in combined.lower() or not stderr:
            return True, f"Disconnected from {address}"
        return False, combined or f"Failed to disconnect from {address}"

    def auto_enable_wireless(self) -> list[tuple[str, bool, str]]:
        """Auto-detect USB devices and enable wireless connections.

        Returns list of (ip:port, success, message) tuples.
        """
        results = []
        devices = self.list_devices()

        for device in devices:
            # Skip already wireless devices
            if self.is_wireless_device(device.serial):
                continue

            # Get device IP
            ip = self.get_device_ip(device)
            if not ip:
                results.append((device.serial, False, "Could not detect IP"))
                continue

            # Enable tcpip mode
            success, msg = self.enable_tcpip(device)
            if not success:
                results.append((ip, False, f"tcpip failed: {msg}"))
                continue

            # Small delay to let tcpip mode activate
            import time
            time.sleep(1)

            # Connect wirelessly
            success, msg = self.connect_wireless(ip)
            results.append((f"{ip}:5555", success, msg))

        return results

    def reconnect_saved(self) -> list[tuple[str, bool, str]]:
        """Reconnect to all saved wireless devices.

        Returns list of (ip:port, success, message) tuples.
        """
        results = []
        saved_ips = state_manager.load_ips()

        for ip in saved_ips:
            stdout, stderr = self._run_adb_command(["connect", ip])
            combined = stdout + stderr
            if "connected" in combined.lower() or "already connected" in combined.lower():
                results.append((ip, True, "Connected"))
            else:
                results.append((ip, False, combined or "Connection failed"))

        return results

    def get_all_devices_info(self) -> list[dict]:
        """Get information about all connected devices with wireless status."""
        devices = self.list_devices()
        info_list = []

        for device in devices:
            is_wireless = self.is_wireless_device(device.serial)
            try:
                model = device.prop.model or "Unknown"
            except Exception:
                model = "Unknown"

            info_list.append({
                "serial": device.serial,
                "model": model,
                "is_wireless": is_wireless,
                "type": "wireless" if is_wireless else "USB",
            })

        return info_list

    def test_device(self) -> tuple[bool, str]:
        """Test current device connectivity."""
        if not self.current_device:
            return False, "No device connected"

        try:
            model = self.current_device.shell("getprop ro.product.model").strip()
            brand = self.current_device.shell("getprop ro.product.brand").strip()
            android = self.current_device.shell("getprop ro.build.version.release").strip()

            if model:
                return True, f"Device OK: {brand} {model} (Android {android})"
            return False, "Device not responding"
        except Exception as e:
            return False, f"Test failed: {e}"

    def restart_server(self) -> tuple[bool, str, list[tuple[str, bool, str]]]:
        """Restart ADB server and reconnect saved devices.

        Returns (success, message, reconnect_results).
        """
        messages = []

        # Kill server
        stdout, stderr = self._run_adb_command(["kill-server"])
        messages.append(f"Kill server: {stdout or stderr or 'OK'}")

        # Start server
        stdout, stderr = self._run_adb_command(["start-server"])
        if stderr and "error" in stderr.lower():
            return False, f"Failed to start server: {stderr}", []
        messages.append(f"Start server: {stdout or stderr or 'OK'}")

        # Reconnect saved devices
        reconnect_results = self.reconnect_saved()

        # Reset current device (it may have been disconnected)
        self.current_device = None

        return True, "\n".join(messages), reconnect_results

    def get_saved_ips(self) -> list[str]:
        """Get list of saved wireless IPs."""
        return state_manager.load_ips()

    def forget_ip(self, ip: str) -> bool:
        """Remove an IP from saved list."""
        return state_manager.remove_ip(ip)

    # ==================== Remote Filesystem Operations ====================

    _LS_MODERN = re.compile(
        r"^(?P<perms>[dl\-][rwxst\-]{9})\s+\d+\s+\S+\s+(?:\S+\s+)?"
        r"(?P<size>\d+)\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+(?P<name>.+)$"
    )
    _LS_LEGACY = re.compile(
        r"^(?P<perms>[dl\-][rwxst\-]{9})\s+\d+\s+\S+\s+(?:\S+\s+)?"
        r"(?P<size>\d+)\s+[A-Z][a-z]{2}\s+\d{1,2}\s+[\d:]+\s+(?P<name>.+)$"
    )

    def list_remote_dir(self, path: str) -> list[dict]:
        """List files in a remote directory.

        Returns list of dicts: {name, is_dir, is_link, size}.
        """
        if not self.current_device:
            return []

        raw = self.shell(f"ls -la '{path}' 2>/dev/null")
        if not raw or raw.startswith("Error:"):
            return []

        entries: list[dict] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("total"):
                continue

            m = self._LS_MODERN.match(line) or self._LS_LEGACY.match(line)
            if not m:
                continue

            perms = m.group("perms")
            name = m.group("name").strip()
            size = int(m.group("size"))
            is_dir = perms.startswith("d")
            is_link = perms.startswith("l")

            # Capture and strip symlink target
            link_target = ""
            if is_link and " -> " in name:
                parts = name.split(" -> ", 1)
                name = parts[0]
                link_target = parts[1] if len(parts) > 1 else ""

            if name in (".", ".."):
                continue

            entries.append({
                "name": name,
                "is_dir": is_dir,
                "is_link": is_link,
                "link_target": link_target,
                "size": size,
            })

        # Batch-test which symlinks resolve to directories (one shell round-trip)
        link_names = [e["name"] for e in entries if e["is_link"]]
        if link_names:
            quoted = " ".join(f"'{n}'" for n in link_names)
            result = self.shell(
                f"cd '{path}' 2>/dev/null && "
                f"for f in {quoted}; do [ -d \"$f\" ] && printf '%s\\n' \"$f\"; done 2>/dev/null"
            )
            dir_links = set((result or "").splitlines())
            for e in entries:
                if e["is_link"] and e["name"] in dir_links:
                    e["is_dir"] = True

        return entries

    def remote_mkdir(self, path: str) -> tuple[bool, str]:
        """Create a directory on the device."""
        if not self.current_device:
            return False, "No device connected"
        result = self.shell(f"mkdir -p '{path}' 2>&1")
        if result.strip():
            return False, result.strip()
        return True, f"Created: {path}"

    def remote_delete(self, path: str, recursive: bool = False) -> tuple[bool, str]:
        """Delete a file or directory on the device."""
        if not self.current_device:
            return False, "No device connected"
        flag = "-rf" if recursive else "-f"
        result = self.shell(f"rm {flag} '{path}' 2>&1")
        if result.strip():
            return False, result.strip()
        return True, f"Deleted: {path}"
