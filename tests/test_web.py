"""Tests for web UI routes and API integration."""



class TestWebPages:
    def test_player_page(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"PiCast" in resp.data
        assert b"now-playing" in resp.data

    def test_history_page(self, client):
        resp = client.get("/history")
        assert resp.status_code == 200
        assert b"History" in resp.data

    def test_collections_page(self, client):
        resp = client.get("/collections")
        assert resp.status_code == 200
        assert b"Collections" in resp.data

    def test_library_redirects_to_history(self, client):
        resp = client.get("/library")
        assert resp.status_code == 301

    def test_playlists_redirects_to_collections(self, client):
        resp = client.get("/playlists")
        assert resp.status_code == 301

    def test_static_css(self, client):
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        assert b"--bg" in resp.data

    def test_static_js(self, client):
        resp = client.get("/static/app.js")
        assert resp.status_code == 200

    def test_app_js_has_shared_toast(self, client):
        resp = client.get("/static/app.js")
        assert b"showToast" in resp.data

    def test_app_js_has_shared_esc(self, client):
        resp = client.get("/static/app.js")
        assert b"function esc" in resp.data

    def test_app_js_has_loading_helper(self, client):
        resp = client.get("/static/app.js")
        assert b"withLoading" in resp.data

    def test_player_has_loading_buttons(self, client):
        resp = client.get("/")
        assert b"doToggle" in resp.data
        assert b"doSkip" in resp.data
        assert b"doStop" in resp.data

    def test_css_has_laptop_breakpoint(self, client):
        resp = client.get("/static/style.css")
        assert b"min-width: 768px" in resp.data

    def test_css_has_landscape_media(self, client):
        resp = client.get("/static/style.css")
        assert b"orientation: landscape" in resp.data

    def test_manifest_json_served(self, client):
        resp = client.get("/static/manifest.json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "PiCast"
        assert data["display"] == "standalone"
        assert data["start_url"] == "/"

    def test_pwa_meta_tags(self, client):
        resp = client.get("/")
        assert b'rel="manifest"' in resp.data
        assert b"apple-mobile-web-app-capable" in resp.data
        assert b"theme-color" in resp.data

    def test_pwa_meta_on_all_pages(self, client):
        for path in ["/", "/history", "/collections"]:
            resp = client.get(path)
            assert b'href="/static/manifest.json"' in resp.data, f"Missing manifest on {path}"

    def test_player_has_queue_search(self, client):
        resp = client.get("/")
        assert b"queue-search" in resp.data
        assert b"filterQueue" in resp.data

    def test_player_has_playlist_detection(self, client):
        resp = client.get("/")
        assert b"detectPlaylist" in resp.data
        assert b"import-playlist" in resp.data
        assert b"import-target" in resp.data

    def test_history_has_stats_bar(self, client):
        resp = client.get("/history")
        assert b"stats-bar" in resp.data
        assert b"loadStats" in resp.data

    def test_collections_has_play_button(self, client):
        resp = client.get("/collections")
        assert b"playFromItem" in resp.data
        assert b"pdi-url" in resp.data

    def test_player_has_aria_labels(self, client):
        """Icon-only buttons should have aria-label for accessibility."""
        resp = client.get("/")
        assert b"aria-label" in resp.data

    def test_css_has_stats_bar(self, client):
        resp = client.get("/static/style.css")
        assert b".stats-bar" in resp.data

    def test_css_has_queue_search(self, client):
        resp = client.get("/static/style.css")
        assert b".queue-search" in resp.data


class TestLibraryAPI:
    def test_library_browse_empty(self, client):
        resp = client.get("/api/library")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_library_search_requires_q(self, client):
        resp = client.get("/api/library/search")
        assert resp.status_code == 400

    def test_library_search(self, client):
        # Add to library via play recording
        client.application.library.add("https://a.com", "Python Tutorial")
        resp = client.get("/api/library/search?q=Python")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1

    def test_library_count(self, client):
        resp = client.get("/api/library/count")
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 0

    def test_library_get(self, client):
        entry = client.application.library.add("https://a.com", "Test")
        resp = client.get(f"/api/library/{entry['id']}")
        assert resp.status_code == 200
        assert resp.get_json()["title"] == "Test"

    def test_library_get_not_found(self, client):
        resp = client.get("/api/library/999")
        assert resp.status_code == 404

    def test_library_update_notes(self, client):
        entry = client.application.library.add("https://a.com", "Test")
        resp = client.put(
            f"/api/library/{entry['id']}/notes",
            json={"notes": "Great video!"},
        )
        assert resp.status_code == 200
        updated = client.application.library.get(entry["id"])
        assert updated["notes"] == "Great video!"

    def test_library_toggle_favorite(self, client):
        entry = client.application.library.add("https://a.com", "Test")
        resp = client.post(f"/api/library/{entry['id']}/favorite")
        assert resp.status_code == 200
        assert resp.get_json()["favorite"] is True

    def test_library_delete(self, client):
        entry = client.application.library.add("https://a.com", "Test")
        resp = client.delete(f"/api/library/{entry['id']}")
        assert resp.status_code == 200
        assert client.application.library.get(entry["id"]) is None

    def test_library_queue(self, client):
        entry = client.application.library.add("https://a.com", "Test")
        resp = client.post(f"/api/library/{entry['id']}/queue")
        assert resp.status_code == 201
        queue = client.get("/api/queue").get_json()
        assert len(queue) == 1

    def test_library_queue_not_found(self, client):
        resp = client.post("/api/library/999/queue")
        assert resp.status_code == 404

    def test_library_recent(self, client):
        client.application.library.record_play("https://a.com", "Test")
        resp = client.get("/api/library/recent")
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1


class TestPlaylistAPI:
    def test_list_playlists_empty(self, client):
        resp = client.get("/api/playlists")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_create_playlist(self, client):
        resp = client.post("/api/playlists", json={"name": "My Playlist"})
        assert resp.status_code == 201
        assert resp.get_json()["name"] == "My Playlist"

    def test_create_playlist_requires_name(self, client):
        resp = client.post("/api/playlists", json={})
        assert resp.status_code == 400

    def test_create_duplicate_playlist(self, client):
        client.post("/api/playlists", json={"name": "Dupe"})
        resp = client.post("/api/playlists", json={"name": "Dupe"})
        assert resp.status_code == 409

    def test_get_playlist(self, client):
        create_resp = client.post("/api/playlists", json={"name": "Test"})
        pl_id = create_resp.get_json()["id"]
        resp = client.get(f"/api/playlists/{pl_id}")
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "Test"
        assert "items" in resp.get_json()

    def test_get_playlist_not_found(self, client):
        resp = client.get("/api/playlists/999")
        assert resp.status_code == 404

    def test_update_playlist(self, client):
        create_resp = client.post("/api/playlists", json={"name": "Old"})
        pl_id = create_resp.get_json()["id"]
        resp = client.put(f"/api/playlists/{pl_id}", json={"name": "New"})
        assert resp.status_code == 200

    def test_delete_playlist(self, client):
        create_resp = client.post("/api/playlists", json={"name": "Delete Me"})
        pl_id = create_resp.get_json()["id"]
        resp = client.delete(f"/api/playlists/{pl_id}")
        assert resp.status_code == 200

    def test_add_item_to_playlist(self, client):
        pl = client.post("/api/playlists", json={"name": "Test"}).get_json()
        entry = client.application.library.add("https://a.com", "A")
        resp = client.post(
            f"/api/playlists/{pl['id']}/items",
            json={"library_id": entry["id"]},
        )
        assert resp.status_code == 201

    def test_add_item_requires_library_id(self, client):
        pl = client.post("/api/playlists", json={"name": "Test"}).get_json()
        resp = client.post(f"/api/playlists/{pl['id']}/items", json={})
        assert resp.status_code == 400

    def test_remove_item_from_playlist(self, client):
        pl = client.post("/api/playlists", json={"name": "Test"}).get_json()
        entry = client.application.library.add("https://a.com", "A")
        client.post(f"/api/playlists/{pl['id']}/items", json={"library_id": entry["id"]})
        resp = client.delete(f"/api/playlists/{pl['id']}/items/{entry['id']}")
        assert resp.status_code == 200

    def test_queue_playlist(self, client):
        pl = client.post("/api/playlists", json={"name": "Test"}).get_json()
        e1 = client.application.library.add("https://a.com", "A")
        e2 = client.application.library.add("https://b.com", "B")
        client.post(f"/api/playlists/{pl['id']}/items", json={"library_id": e1["id"]})
        client.post(f"/api/playlists/{pl['id']}/items", json={"library_id": e2["id"]})
        resp = client.post(f"/api/playlists/{pl['id']}/queue")
        assert resp.status_code == 200
        assert resp.get_json()["queued"] == 2
        queue = client.get("/api/queue").get_json()
        assert len(queue) == 2


class TestSourcesAPI:
    def test_list_sources(self, client):
        resp = client.get("/api/sources")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "youtube" in data
        assert "local" in data
        assert "twitch" in data

    def test_detect_source(self, client):
        resp = client.post("/api/sources/detect", json={"url": "https://youtube.com/watch?v=abc"})
        assert resp.status_code == 200
        assert resp.get_json()["source_type"] == "youtube"

    def test_detect_requires_url(self, client):
        resp = client.post("/api/sources/detect", json={})
        assert resp.status_code == 400

    def test_browse_sources_root(self, client):
        resp = client.get("/api/sources/browse")
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), list)

    def test_browse_directory(self, client, tmp_path):
        (tmp_path / "movie.mp4").touch()
        resp = client.get(f"/api/sources/browse?path={tmp_path}")
        assert resp.status_code == 200
        items = resp.get_json()
        assert any(i["title"] == "movie" for i in items)

    def test_drives_endpoint(self, client):
        resp = client.get("/api/sources/drives")
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), list)
