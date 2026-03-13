"""Tests for AutopilotConfig parsing."""

from picast.config import (
    AutopilotConfig,
    FleetDeviceConfig,
    _parse_config,
)


class TestAutopilotConfigDefaults:
    def test_defaults(self):
        config = AutopilotConfig()
        assert config.enabled is False
        assert config.mode == "single"
        assert config.queue_depth == 4
        assert config.pool_only is False
        assert config.discovery_ratio == 0.3
        assert config.stale_threshold_hours == 48
        assert config.fleet_devices == {}

    def test_missing_section_uses_defaults(self):
        data = {"server": {"port": 5050}}
        config = _parse_config(data)
        assert config.autopilot.enabled is False
        assert config.autopilot.mode == "single"
        assert config.autopilot.fleet_devices == {}


class TestAutopilotConfigParsing:
    def test_parse_basic_fields(self):
        data = {
            "autopilot": {
                "enabled": True,
                "mode": "fleet",
                "queue_depth": 6,
                "pool_only": True,
                "discovery_ratio": 0.5,
                "stale_threshold_hours": 24,
            }
        }
        config = _parse_config(data)
        assert config.autopilot.enabled is True
        assert config.autopilot.mode == "fleet"
        assert config.autopilot.queue_depth == 6
        assert config.autopilot.pool_only is True
        assert config.autopilot.discovery_ratio == 0.5
        assert config.autopilot.stale_threshold_hours == 24

    def test_parse_partial(self):
        data = {"autopilot": {"enabled": True}}
        config = _parse_config(data)
        assert config.autopilot.enabled is True
        assert config.autopilot.mode == "single"  # default
        assert config.autopilot.queue_depth == 4  # default

    def test_parse_fleet_devices(self):
        data = {
            "autopilot": {
                "enabled": True,
                "mode": "fleet",
                "fleet": {
                    "devices": {
                        "bedroom": {
                            "host": "picast-bedroom.local",
                            "port": 5050,
                            "room": "bedroom",
                            "mood": "chill",
                        },
                        "office": {
                            "host": "picast-office.local",
                            "port": 5051,
                            "room": "office",
                            "mood": "focus",
                        },
                    }
                },
            }
        }
        config = _parse_config(data)
        assert len(config.autopilot.fleet_devices) == 2
        bedroom = config.autopilot.fleet_devices["bedroom"]
        assert isinstance(bedroom, FleetDeviceConfig)
        assert bedroom.host == "picast-bedroom.local"
        assert bedroom.port == 5050
        assert bedroom.room == "bedroom"
        assert bedroom.mood == "chill"

        office = config.autopilot.fleet_devices["office"]
        assert office.port == 5051
        assert office.mood == "focus"

    def test_fleet_devices_partial(self):
        data = {
            "autopilot": {
                "fleet": {
                    "devices": {
                        "living": {"host": "picast-living.local"}
                    }
                }
            }
        }
        config = _parse_config(data)
        living = config.autopilot.fleet_devices["living"]
        assert living.host == "picast-living.local"
        assert living.port == 5050  # default
        assert living.room == ""  # default
        assert living.mood == ""  # default

    def test_fleet_device_mute_default(self):
        """Fleet devices default to mute=True."""
        config = FleetDeviceConfig(host="test.local")
        assert config.mute is True

    def test_fleet_device_mute_false(self):
        """StarScreen-style device with mute=false."""
        data = {
            "autopilot": {
                "fleet": {
                    "devices": {
                        "starscreen": {
                            "host": "starscreen.local",
                            "port": 5072,
                            "room": "office",
                            "mood": "chill",
                            "mute": False,
                        }
                    }
                }
            }
        }
        config = _parse_config(data)
        ss = config.autopilot.fleet_devices["starscreen"]
        assert ss.host == "starscreen.local"
        assert ss.port == 5072
        assert ss.mute is False

    def test_fleet_device_mute_true_explicit(self):
        data = {
            "autopilot": {
                "fleet": {
                    "devices": {
                        "receiver": {
                            "host": "picast-z1.local",
                            "mute": True,
                        }
                    }
                }
            }
        }
        config = _parse_config(data)
        assert config.autopilot.fleet_devices["receiver"].mute is True

    def test_no_fleet_section(self):
        data = {"autopilot": {"enabled": True}}
        config = _parse_config(data)
        assert config.autopilot.fleet_devices == {}

    def test_autopilot_does_not_affect_autoplay(self):
        """Autopilot and autoplay are separate config sections."""
        data = {
            "autopilot": {"enabled": True, "mode": "fleet"},
            "autoplay": {"enabled": True, "pool_mode": True},
        }
        config = _parse_config(data)
        assert config.autopilot.enabled is True
        assert config.autoplay.enabled is True
        assert config.autoplay.pool_mode is True
