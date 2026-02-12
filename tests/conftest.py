"""Shared test fixtures for PiCast test suite."""

import pytest

from picast.config import ServerConfig
from picast.server.app import create_app
from picast.server.database import Database
from picast.server.events import EventBus
from picast.server.library import Library
from picast.server.queue_manager import QueueManager


@pytest.fixture
def db(tmp_path):
    """Create a fresh test database."""
    return Database(str(tmp_path / "test.db"))


@pytest.fixture
def lib(db):
    """Create a Library instance backed by the test database."""
    return Library(db)


@pytest.fixture
def queue(db):
    """Create a QueueManager instance backed by the test database."""
    return QueueManager(db)


@pytest.fixture
def event_bus(db):
    """Create an EventBus instance backed by the test database."""
    return EventBus(db)


@pytest.fixture
def app(tmp_path):
    """Create a Flask test app with no player loop."""
    config = ServerConfig(
        mpv_socket="/tmp/picast-test-socket",
        db_file=str(tmp_path / "test.db"),
        data_dir=str(tmp_path / "data"),
    )
    app = create_app(config)
    app.player.stop()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    """Create a Flask test client."""
    return app.test_client()
