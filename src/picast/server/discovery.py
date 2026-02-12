"""mDNS/Zeroconf device discovery for PiCast multi-Pi support.

Registers the local PiCast server as a service on the network and
discovers other PiCast instances via mDNS.

Requires: pip install zeroconf
"""

import logging
import socket
import threading
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_picast._tcp.local."


@dataclass
class DeviceInfo:
    """Information about a PiCast device."""

    name: str
    host: str
    port: int
    source: str = "config"  # "config", "discovered", "local"
    online: bool = True
    version: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class DeviceRegistry:
    """Manages known PiCast devices from config and mDNS discovery.

    Works without zeroconf installed - just uses config-based devices.
    """

    def __init__(self, local_name: str = "", local_port: int = 5000):
        self._devices: dict[str, DeviceInfo] = {}
        self._lock = threading.Lock()
        self._local_name = local_name or socket.gethostname()
        self._local_port = local_port
        self._zeroconf = None
        self._browser = None
        self._service_info = None

        # Always register self
        self._register_local()

    def _register_local(self):
        """Register the local device."""
        with self._lock:
            self._devices[self._local_name] = DeviceInfo(
                name=self._local_name,
                host="localhost",
                port=self._local_port,
                source="local",
                online=True,
            )

    def add_from_config(self, name: str, host: str, port: int = 5000):
        """Add a device from config file."""
        with self._lock:
            self._devices[name] = DeviceInfo(
                name=name,
                host=host,
                port=port,
                source="config",
                online=True,  # Assume online; health checks can update
            )

    def add_discovered(self, name: str, host: str, port: int, version: str = ""):
        """Add a device from mDNS discovery."""
        with self._lock:
            # Don't overwrite config entries with discovered ones
            if name in self._devices and self._devices[name].source == "config":
                self._devices[name].online = True
                return
            self._devices[name] = DeviceInfo(
                name=name,
                host=host,
                port=port,
                source="discovered",
                online=True,
                version=version,
            )
            logger.info("Discovered PiCast device: %s at %s:%d", name, host, port)

    def remove_discovered(self, name: str):
        """Mark a discovered device as offline."""
        with self._lock:
            if name in self._devices and self._devices[name].source == "discovered":
                self._devices[name].online = False

    def list_devices(self, include_offline: bool = False) -> list[dict]:
        """List all known devices."""
        with self._lock:
            devices = list(self._devices.values())
        if not include_offline:
            devices = [d for d in devices if d.online]
        return [d.to_dict() for d in devices]

    def get_device(self, name: str) -> dict | None:
        """Get a specific device by name."""
        with self._lock:
            device = self._devices.get(name)
            return device.to_dict() if device else None

    def start_discovery(self):
        """Start mDNS discovery and registration (requires zeroconf)."""
        try:
            from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf
        except ImportError:
            logger.info("zeroconf not installed, skipping mDNS discovery")
            return

        try:
            self._zeroconf = Zeroconf()

            # Register our own service
            local_ip = _get_local_ip()
            self._service_info = ServiceInfo(
                SERVICE_TYPE,
                f"{self._local_name}.{SERVICE_TYPE}",
                addresses=[socket.inet_aton(local_ip)],
                port=self._local_port,
                properties={"version": _get_version()},
            )
            self._zeroconf.register_service(self._service_info)
            logger.info(
                "Registered mDNS service: %s on %s:%d",
                self._local_name, local_ip, self._local_port,
            )

            # Browse for other PiCast instances
            self._browser = ServiceBrowser(
                self._zeroconf,
                SERVICE_TYPE,
                handlers=[self._on_service_change],
            )
            logger.info("Started mDNS discovery for PiCast devices")

        except Exception as e:
            logger.warning("Failed to start mDNS: %s", e)

    def _on_service_change(self, zeroconf, service_type, name, state_change):
        """Handle mDNS service state changes."""
        from zeroconf import ServiceStateChange

        if state_change == ServiceStateChange.Added:
            info = zeroconf.get_service_info(service_type, name)
            if info:
                host = socket.inet_ntoa(info.addresses[0]) if info.addresses else ""
                device_name = name.replace(f".{SERVICE_TYPE}", "")
                if host and device_name != self._local_name:
                    version = info.properties.get(b"version", b"").decode()
                    self.add_discovered(device_name, host, info.port, version)

        elif state_change == ServiceStateChange.Removed:
            device_name = name.replace(f".{SERVICE_TYPE}", "")
            self.remove_discovered(device_name)

    def stop_discovery(self):
        """Stop mDNS discovery and unregister service."""
        if self._zeroconf:
            if self._service_info:
                self._zeroconf.unregister_service(self._service_info)
            self._zeroconf.close()
            self._zeroconf = None
            logger.info("Stopped mDNS discovery")


def check_device_health(host: str, port: int, timeout: float = 3.0) -> bool:
    """Check if a PiCast device is reachable."""
    try:
        import httpx
        resp = httpx.get(f"http://{host}:{port}/api/health", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


def _get_local_ip() -> str:
    """Get the local IP address (best effort)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _get_version() -> str:
    try:
        from picast.__about__ import __version__
        return __version__
    except ImportError:
        return "unknown"
