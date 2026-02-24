"""Tests for ntfy config parsing."""

from picast.config import NtfyConfig, _parse_config, load_config


class TestNtfyConfig:
    """Test [ntfy] section parsing."""

    def test_ntfy_section_parses(self):
        data = {
            "ntfy": {
                "enabled": True,
                "server_url": "http://192.168.1.50:8080",
                "alert_topic": "my-alerts",
                "summary_topic": "my-health",
                "daily_summary_hour": 9,
            }
        }
        config = _parse_config(data)
        assert config.ntfy.enabled is True
        assert config.ntfy.server_url == "http://192.168.1.50:8080"
        assert config.ntfy.alert_topic == "my-alerts"
        assert config.ntfy.summary_topic == "my-health"
        assert config.ntfy.daily_summary_hour == 9

    def test_missing_ntfy_defaults_to_disabled(self):
        config = _parse_config({})
        assert config.ntfy.enabled is False
        assert config.ntfy.server_url == "http://10.0.0.103:5555"
        assert config.ntfy.alert_topic == "picast-alerts"

    def test_partial_ntfy_uses_defaults(self):
        data = {
            "ntfy": {
                "enabled": True,
            }
        }
        config = _parse_config(data)
        assert config.ntfy.enabled is True
        assert config.ntfy.server_url == "http://10.0.0.103:5555"
        assert config.ntfy.alert_topic == "picast-alerts"
        assert config.ntfy.summary_topic == "picast-health"
        assert config.ntfy.daily_summary_hour == 8
