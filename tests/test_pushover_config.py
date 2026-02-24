"""Tests for Pushover config parsing."""

from picast.config import PushoverConfig, _parse_config


class TestPushoverConfig:
    """Test [pushover] section parsing."""

    def test_pushover_section_parses(self):
        data = {
            "pushover": {
                "enabled": True,
                "api_token": "a1b2c3d4e5f6g7h8i9j0",
                "user_key": "u1v2w3x4y5z6",
                "daily_summary_hour": 9,
            }
        }
        config = _parse_config(data)
        assert config.pushover.enabled is True
        assert config.pushover.api_token == "a1b2c3d4e5f6g7h8i9j0"
        assert config.pushover.user_key == "u1v2w3x4y5z6"
        assert config.pushover.daily_summary_hour == 9

    def test_missing_pushover_defaults_to_disabled(self):
        config = _parse_config({})
        assert config.pushover.enabled is False
        assert config.pushover.api_token == ""
        assert config.pushover.user_key == ""

    def test_partial_pushover_uses_defaults(self):
        data = {
            "pushover": {
                "enabled": True,
                "api_token": "mytoken",
                "user_key": "myuser",
            }
        }
        config = _parse_config(data)
        assert config.pushover.enabled is True
        assert config.pushover.api_token == "mytoken"
        assert config.pushover.user_key == "myuser"
        assert config.pushover.daily_summary_hour == 8
