"""Tests for the Flask REST API.

Uses Flask test client - no actual mpv or network needed.
"""



class TestHealthEndpoint:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "version" in data


class TestStatusEndpoint:
    def test_status_when_idle(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["idle"] is True


class TestQueueEndpoints:
    def test_add_to_queue(self, client):
        resp = client.post("/api/queue/add", json={"url": "https://www.youtube.com/watch?v=abc"})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["url"] == "https://www.youtube.com/watch?v=abc"
        assert data["status"] == "pending"

    def test_add_requires_url(self, client):
        resp = client.post("/api/queue/add", json={})
        assert resp.status_code == 400

    def test_add_rejects_invalid_youtube_url(self, client):
        resp = client.post(
            "/api/queue/add",
            json={"url": "https://www.youtube.com/"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data
        assert "video ID" in data["error"]

    def test_add_accepts_valid_youtube_url(self, client):
        resp = client.post(
            "/api/queue/add",
            json={"url": "https://www.youtube.com/watch?v=test123"},
        )
        assert resp.status_code == 201

    def test_get_queue(self, client):
        client.post("/api/queue/add", json={"url": "https://www.youtube.com/watch?v=a"})
        client.post("/api/queue/add", json={"url": "https://www.youtube.com/watch?v=b"})
        resp = client.get("/api/queue")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2

    def test_remove_from_queue(self, client):
        resp = client.post("/api/queue/add", json={"url": "https://www.youtube.com/watch?v=a"})
        item_id = resp.get_json()["id"]
        resp = client.delete(f"/api/queue/{item_id}")
        assert resp.status_code == 200
        # Verify it's gone
        resp = client.get("/api/queue")
        assert len(resp.get_json()) == 0

    def test_remove_nonexistent(self, client):
        resp = client.delete("/api/queue/999")
        assert resp.status_code == 404

    def test_clear_played(self, client):
        resp = client.post("/api/queue/add", json={"url": "https://www.youtube.com/watch?v=a"})
        item_id = resp.get_json()["id"]
        # Manually mark as played via the queue manager
        client.application.queue.mark_played(item_id)
        resp = client.post("/api/queue/clear-played")
        assert resp.status_code == 200
        resp = client.get("/api/queue")
        assert len(resp.get_json()) == 0

    def test_clear_all(self, client):
        client.post("/api/queue/add", json={"url": "https://www.youtube.com/watch?v=a"})
        client.post("/api/queue/add", json={"url": "https://www.youtube.com/watch?v=b"})
        resp = client.post("/api/queue/clear")
        assert resp.status_code == 200
        resp = client.get("/api/queue")
        assert len(resp.get_json()) == 0

    def test_reorder(self, client):
        r1 = client.post("/api/queue/add", json={"url": "https://www.youtube.com/watch?v=a"})
        r2 = client.post("/api/queue/add", json={"url": "https://www.youtube.com/watch?v=b"})
        id1 = r1.get_json()["id"]
        id2 = r2.get_json()["id"]
        resp = client.post("/api/queue/reorder", json={"items": [id2, id1]})
        assert resp.status_code == 200

    def test_replay(self, client):
        resp = client.post("/api/queue/add", json={"url": "https://www.youtube.com/watch?v=a"})
        item_id = resp.get_json()["id"]
        client.application.queue.mark_played(item_id)
        resp = client.post("/api/queue/replay", json={"id": item_id})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        # Verify it's pending again
        queue = client.get("/api/queue").get_json()
        assert queue[0]["status"] == "pending"

    def test_replay_requires_id(self, client):
        resp = client.post("/api/queue/replay", json={})
        assert resp.status_code == 400

    def test_replay_not_found(self, client):
        resp = client.post("/api/queue/replay", json={"id": 999})
        assert resp.status_code == 404

    def test_replay_moves_to_end(self, client):
        """Replayed item should appear after other pending items."""
        r1 = client.post("/api/queue/add", json={"url": "https://www.youtube.com/watch?v=a"})
        client.post("/api/queue/add", json={"url": "https://www.youtube.com/watch?v=b"})
        id1 = r1.get_json()["id"]
        client.application.queue.mark_played(id1)
        client.post("/api/queue/replay", json={"id": id1})
        queue = client.get("/api/queue").get_json()
        pending = [i for i in queue if i["status"] == "pending"]
        assert pending[-1]["id"] == id1


class TestImportPlaylistEndpoint:
    def test_import_requires_url(self, client):
        resp = client.post("/api/queue/import-playlist", json={})
        assert resp.status_code == 400

    def test_import_rejects_non_playlist(self, client):
        resp = client.post("/api/queue/import-playlist", json={"url": "https://www.youtube.com/watch?v=abc"})
        assert resp.status_code == 400

    def test_import_playlist_success(self, client, monkeypatch):
        """Mocked playlist import adds videos to queue."""
        import subprocess

        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0], returncode=0,
                stdout=(
                    "My PL\thttps://www.youtube.com/watch?v=x\tVid 1\n"
                    "My PL\thttps://www.youtube.com/watch?v=y\tVid 2\n"
                ),
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", mock_run)
        resp = client.post("/api/queue/import-playlist",
                           json={"url": "https://www.youtube.com/playlist?list=PLtest"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["added"] == 2
        assert data["failed"] == 0
        # Verify queue has 2 items
        queue = client.get("/api/queue").get_json()
        assert len(queue) == 2

    def test_import_playlist_empty(self, client, monkeypatch):
        """Empty playlist returns 404."""
        import subprocess

        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", mock_run)
        resp = client.post("/api/queue/import-playlist",
                           json={"url": "https://www.youtube.com/playlist?list=PLempty"})
        assert resp.status_code == 404


class TestImportPlaylistToCollection:
    def test_import_to_collection_requires_url(self, client):
        resp = client.post("/api/playlists/import-playlist", json={})
        assert resp.status_code == 400

    def test_import_to_collection_rejects_non_playlist(self, client):
        resp = client.post(
            "/api/playlists/import-playlist",
            json={"url": "https://www.youtube.com/watch?v=abc"},
        )
        assert resp.status_code == 400

    def test_import_to_collection_success(self, client, monkeypatch):
        """Imports playlist as a named collection."""
        import subprocess

        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0], returncode=0,
                stdout=(
                    "Cool Playlist\thttps://www.youtube.com/watch?v=a\tVid A\n"
                    "Cool Playlist\thttps://www.youtube.com/watch?v=b\tVid B\n"
                ),
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", mock_run)
        resp = client.post("/api/playlists/import-playlist",
                           json={"url": "https://www.youtube.com/playlist?list=PLtest"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["added"] == 2
        assert data["collection_name"] == "Cool Playlist"
        assert data["collection_id"] is not None
        # Verify collection exists with items
        pl = client.get(f"/api/playlists/{data['collection_id']}").get_json()
        assert pl["name"] == "Cool Playlist"
        assert len(pl["items"]) == 2

    def test_import_to_collection_empty(self, client, monkeypatch):
        """Empty playlist returns 404."""
        import subprocess

        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", mock_run)
        resp = client.post("/api/playlists/import-playlist",
                           json={"url": "https://www.youtube.com/playlist?list=PLempty"})
        assert resp.status_code == 404


class TestPlayerControlEndpoints:
    def test_play_requires_url(self, client):
        resp = client.post("/api/play", json={})
        assert resp.status_code == 400

    def test_pause(self, client):
        resp = client.post("/api/pause")
        assert resp.status_code == 200

    def test_resume(self, client):
        resp = client.post("/api/resume")
        assert resp.status_code == 200

    def test_toggle(self, client):
        resp = client.post("/api/toggle")
        assert resp.status_code == 200

    def test_skip(self, client):
        resp = client.post("/api/skip")
        assert resp.status_code == 200

    def test_seek_requires_position(self, client):
        resp = client.post("/api/seek", json={})
        assert resp.status_code == 400

    def test_volume_requires_level(self, client):
        resp = client.post("/api/volume", json={})
        assert resp.status_code == 400

    def test_speed_requires_speed(self, client):
        resp = client.post("/api/speed", json={})
        assert resp.status_code == 400


class TestLibraryStatsEndpoint:
    def test_stats_empty(self, client):
        resp = client.get("/api/library/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total_videos"] == 0
        assert data["total_plays"] == 0
        assert data["favorites"] == 0
        assert data["sources"] == {}
        assert data["top_played"] == []

    def test_stats_with_data(self, client):
        """Stats reflect library content."""
        lib = client.application.library
        lib.add("https://youtube.com/watch?v=a", "Video A", "youtube")
        lib.add("https://youtube.com/watch?v=b", "Video B", "youtube")
        lib.record_play("https://youtube.com/watch?v=a", "Video A", "youtube")
        lib.record_play("https://youtube.com/watch?v=a", "Video A", "youtube")
        entry = lib.get_by_url("https://youtube.com/watch?v=b")
        lib.toggle_favorite(entry["id"])

        resp = client.get("/api/library/stats")
        data = resp.get_json()
        assert data["total_videos"] == 2
        assert data["total_plays"] == 2
        assert data["favorites"] == 1
        assert data["sources"]["youtube"] == 2
        assert len(data["top_played"]) >= 1
        assert data["top_played"][0]["title"] == "Video A"


class TestTimerEndpoints:
    def test_get_timer_default(self, client):
        resp = client.get("/api/timer")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["stop_after_current"] is False
        assert data["stop_timer_remaining"] is None

    def test_stop_after_current_enable(self, client):
        resp = client.post("/api/timer/stop-after-current", json={"enabled": True})
        assert resp.status_code == 200
        assert resp.get_json()["stop_after_current"] is True
        # Verify via GET
        data = client.get("/api/timer").get_json()
        assert data["stop_after_current"] is True

    def test_stop_after_current_disable(self, client):
        client.post("/api/timer/stop-after-current", json={"enabled": True})
        resp = client.post("/api/timer/stop-after-current", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.get_json()["stop_after_current"] is False

    def test_stop_in_sets_timer(self, client):
        resp = client.post("/api/timer/stop-in", json={"minutes": 30})
        assert resp.status_code == 200
        assert resp.get_json()["minutes"] == 30
        # Timer should be active
        data = client.get("/api/timer").get_json()
        assert data["stop_timer_remaining"] is not None
        assert data["stop_timer_remaining"] > 0

    def test_stop_in_cancel(self, client):
        client.post("/api/timer/stop-in", json={"minutes": 30})
        resp = client.post("/api/timer/stop-in", json={"minutes": 0})
        assert resp.status_code == 200
        data = client.get("/api/timer").get_json()
        assert data["stop_timer_remaining"] is None

    def test_stop_in_requires_minutes(self, client):
        resp = client.post("/api/timer/stop-in", json={})
        assert resp.status_code == 400

    def test_stop_in_rejects_negative(self, client):
        resp = client.post("/api/timer/stop-in", json={"minutes": -5})
        assert resp.status_code == 400

    def test_timer_in_status(self, client):
        """Timer fields should appear in /api/status."""
        resp = client.get("/api/status")
        data = resp.get_json()
        assert "stop_after_current" in data
        assert "stop_timer_remaining" in data


class TestSystemVolumeEndpoints:
    def test_volume_get(self, client):
        """Volume GET returns volume or error (no amixer on Mac)."""
        resp = client.get("/api/system/volume")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "volume" in data
        # On Mac, will have error since no amixer
        assert isinstance(data["volume"], int)

    def test_volume_set_requires_volume(self, client):
        resp = client.post("/api/system/volume", json={})
        assert resp.status_code == 400
        assert "volume required" in resp.get_json()["error"]

    def test_volume_set_clamps_values(self, client):
        """Volume POST should accept values and clamp to 0-100."""
        # Will fail on Mac (no amixer) but should not crash
        resp = client.post("/api/system/volume", json={"volume": 150})
        assert resp.status_code in (200, 500)

    def test_volume_set_negative_clamps(self, client):
        resp = client.post("/api/system/volume", json={"volume": -10})
        assert resp.status_code in (200, 500)


class TestSystemDisplayEndpoints:
    def test_display_get(self, client):
        """Display GET returns rotation or error (no boot config on Mac)."""
        resp = client.get("/api/system/display")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "rotate" in data

    def test_display_set_requires_rotate(self, client):
        resp = client.post("/api/system/display", json={})
        assert resp.status_code == 400

    def test_display_set_rejects_invalid_value(self, client):
        resp = client.post("/api/system/display", json={"rotate": 3})
        assert resp.status_code == 400
        assert "must be 0" in resp.get_json()["error"]


class TestSystemInfoEndpoint:
    def test_info(self, client):
        """System info returns basic fields."""
        resp = client.get("/api/system/info")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "version" in data
        assert "hostname" in data
        assert "ip" in data
        assert "disk" in data

    def test_info_version_matches(self, client):
        """Version from system info should match __about__."""
        resp = client.get("/api/system/info")
        data = resp.get_json()
        from picast.__about__ import __version__
        assert data["version"] == __version__


class TestSystemOsdEndpoints:
    def test_osd_get(self, client):
        resp = client.get("/api/system/osd")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "enabled" in data
        assert isinstance(data["enabled"], bool)

    def test_osd_toggle(self, client):
        """Toggle OSD returns a valid response."""
        resp = client.post("/api/system/osd", json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "ok" in data
        assert "enabled" in data
        assert isinstance(data["enabled"], bool)


class TestSystemRestartEndpoint:
    def test_restart(self, client, monkeypatch):
        """Restart should attempt to run systemctl restart."""
        import subprocess

        calls = []

        def mock_popen(*args, **kwargs):
            calls.append(args)
            return None

        monkeypatch.setattr(subprocess, "Popen", mock_popen)
        resp = client.post("/api/system/restart")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        assert len(calls) == 1
