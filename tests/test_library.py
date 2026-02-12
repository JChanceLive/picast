"""Tests for library and playlist operations."""



class TestLibraryAdd:
    def test_add_new(self, lib):
        entry = lib.add("https://www.youtube.com/watch?v=abc", "Test Video")
        assert entry["url"] == "https://www.youtube.com/watch?v=abc"
        assert entry["title"] == "Test Video"
        assert entry["source_type"] == "youtube"

    def test_add_duplicate_returns_existing(self, lib):
        e1 = lib.add("https://www.youtube.com/watch?v=abc", "First")
        e2 = lib.add("https://www.youtube.com/watch?v=abc", "Second")
        assert e1["id"] == e2["id"]
        # Title should keep first (non-empty) value
        assert e2["title"] == "First"

    def test_add_duplicate_updates_title_if_missing(self, lib):
        lib.add("https://www.youtube.com/watch?v=abc", "")
        e2 = lib.add("https://www.youtube.com/watch?v=abc", "Now Has Title")
        assert e2["title"] == "Now Has Title"

    def test_add_sets_source_type(self, lib):
        entry = lib.add("https://www.twitch.tv/ch", source_type="twitch")
        assert entry["source_type"] == "twitch"


class TestLibraryRecordPlay:
    def test_record_play_creates_entry(self, lib):
        entry = lib.record_play("https://www.youtube.com/watch?v=abc", "Test")
        assert entry["play_count"] == 1
        assert entry["first_played_at"] is not None
        assert entry["last_played_at"] is not None

    def test_record_play_increments(self, lib):
        lib.record_play("https://www.youtube.com/watch?v=abc")
        entry = lib.record_play("https://www.youtube.com/watch?v=abc")
        assert entry["play_count"] == 2


class TestLibraryGet:
    def test_get_by_id(self, lib):
        added = lib.add("https://www.youtube.com/watch?v=abc", "Test")
        entry = lib.get(added["id"])
        assert entry is not None
        assert entry["url"] == "https://www.youtube.com/watch?v=abc"

    def test_get_nonexistent(self, lib):
        assert lib.get(999) is None

    def test_get_by_url(self, lib):
        lib.add("https://www.youtube.com/watch?v=abc", "Test")
        entry = lib.get_by_url("https://www.youtube.com/watch?v=abc")
        assert entry is not None

    def test_get_by_url_nonexistent(self, lib):
        assert lib.get_by_url("https://nope.com") is None


class TestLibraryNotes:
    def test_update_notes(self, lib):
        entry = lib.add("https://www.youtube.com/watch?v=abc", "Test")
        lib.update_notes(entry["id"], "Great video!")
        updated = lib.get(entry["id"])
        assert updated["notes"] == "Great video!"

    def test_update_notes_overwrite(self, lib):
        entry = lib.add("https://www.youtube.com/watch?v=abc", "Test")
        lib.update_notes(entry["id"], "First note")
        lib.update_notes(entry["id"], "Second note")
        updated = lib.get(entry["id"])
        assert updated["notes"] == "Second note"


class TestLibraryFavorite:
    def test_toggle_on(self, lib):
        entry = lib.add("https://www.youtube.com/watch?v=abc")
        result = lib.toggle_favorite(entry["id"])
        assert result is True
        updated = lib.get(entry["id"])
        assert updated["favorite"] == 1

    def test_toggle_off(self, lib):
        entry = lib.add("https://www.youtube.com/watch?v=abc")
        lib.toggle_favorite(entry["id"])
        result = lib.toggle_favorite(entry["id"])
        assert result is False
        updated = lib.get(entry["id"])
        assert updated["favorite"] == 0

    def test_toggle_nonexistent(self, lib):
        assert lib.toggle_favorite(999) is False


class TestLibrarySearch:
    def test_search_by_title(self, lib):
        lib.add("https://a.com", "Python Tutorial")
        lib.add("https://b.com", "JavaScript Guide")
        results = lib.search("Python")
        assert len(results) == 1
        assert results[0]["title"] == "Python Tutorial"

    def test_search_by_url(self, lib):
        lib.add("https://special-domain.com/video", "Something")
        results = lib.search("special-domain")
        assert len(results) == 1

    def test_search_case_insensitive(self, lib):
        lib.add("https://a.com", "UPPERCASE Title")
        results = lib.search("uppercase")
        assert len(results) == 1

    def test_search_no_results(self, lib):
        lib.add("https://a.com", "Something")
        results = lib.search("nonexistent")
        assert len(results) == 0


class TestLibraryBrowse:
    def test_browse_all(self, lib):
        lib.add("https://a.com", "A")
        lib.add("https://b.com", "B")
        results = lib.browse()
        assert len(results) == 2

    def test_browse_by_source(self, lib):
        lib.add("https://a.com", "A", source_type="youtube")
        lib.add("/local/file.mp4", "B", source_type="local")
        results = lib.browse(source_type="youtube")
        assert len(results) == 1
        assert results[0]["source_type"] == "youtube"

    def test_browse_favorites(self, lib):
        e1 = lib.add("https://a.com", "A")
        lib.add("https://b.com", "B")
        lib.toggle_favorite(e1["id"])
        results = lib.browse(favorites_only=True)
        assert len(results) == 1
        assert results[0]["url"] == "https://a.com"

    def test_browse_sort_title(self, lib):
        lib.add("https://b.com", "Banana")
        lib.add("https://a.com", "Apple")
        results = lib.browse(sort="title")
        assert results[0]["title"] == "Apple"
        assert results[1]["title"] == "Banana"

    def test_browse_limit_offset(self, lib):
        for i in range(10):
            lib.add(f"https://x.com/{i}", f"Video {i}")
        results = lib.browse(limit=3, offset=2)
        assert len(results) == 3


class TestLibraryCount:
    def test_count_all(self, lib):
        lib.add("https://a.com", "A")
        lib.add("https://b.com", "B")
        assert lib.count() == 2

    def test_count_by_source(self, lib):
        lib.add("https://a.com", "A", source_type="youtube")
        lib.add("/file.mp4", "B", source_type="local")
        assert lib.count("youtube") == 1

    def test_count_empty(self, lib):
        assert lib.count() == 0


class TestLibraryDelete:
    def test_delete(self, lib):
        entry = lib.add("https://a.com", "A")
        lib.delete(entry["id"])
        assert lib.get(entry["id"]) is None

    def test_count_after_delete(self, lib):
        entry = lib.add("https://a.com", "A")
        lib.delete(entry["id"])
        assert lib.count() == 0


class TestLibraryRecent:
    def test_recent_returns_played(self, lib):
        lib.record_play("https://a.com", "A")
        lib.add("https://b.com", "B")  # Not played
        results = lib.recent()
        assert len(results) == 1
        assert results[0]["url"] == "https://a.com"


class TestPlaylists:
    def test_create_playlist(self, lib):
        pl = lib.create_playlist("My Favorites", "Best videos")
        assert pl["name"] == "My Favorites"
        assert pl["description"] == "Best videos"

    def test_list_playlists(self, lib):
        lib.create_playlist("A")
        lib.create_playlist("B")
        pls = lib.list_playlists()
        assert len(pls) == 2

    def test_list_includes_item_count(self, lib):
        pl = lib.create_playlist("Test")
        entry = lib.add("https://a.com", "A")
        lib.add_to_playlist(pl["id"], entry["id"])
        pls = lib.list_playlists()
        assert pls[0]["item_count"] == 1

    def test_get_playlist(self, lib):
        pl = lib.create_playlist("Test")
        result = lib.get_playlist(pl["id"])
        assert result["name"] == "Test"

    def test_get_playlist_nonexistent(self, lib):
        assert lib.get_playlist(999) is None

    def test_update_playlist(self, lib):
        pl = lib.create_playlist("Old Name")
        lib.update_playlist(pl["id"], name="New Name")
        updated = lib.get_playlist(pl["id"])
        assert updated["name"] == "New Name"

    def test_delete_playlist(self, lib):
        pl = lib.create_playlist("Test")
        lib.delete_playlist(pl["id"])
        assert lib.get_playlist(pl["id"]) is None

    def test_add_to_playlist(self, lib):
        pl = lib.create_playlist("Test")
        entry = lib.add("https://a.com", "A")
        result = lib.add_to_playlist(pl["id"], entry["id"])
        assert result is not None

    def test_add_duplicate_to_playlist(self, lib):
        pl = lib.create_playlist("Test")
        entry = lib.add("https://a.com", "A")
        lib.add_to_playlist(pl["id"], entry["id"])
        result = lib.add_to_playlist(pl["id"], entry["id"])
        assert result is None

    def test_get_playlist_items(self, lib):
        pl = lib.create_playlist("Test")
        e1 = lib.add("https://a.com", "A")
        e2 = lib.add("https://b.com", "B")
        lib.add_to_playlist(pl["id"], e1["id"])
        lib.add_to_playlist(pl["id"], e2["id"])
        items = lib.get_playlist_items(pl["id"])
        assert len(items) == 2
        assert items[0]["title"] == "A"
        assert items[1]["title"] == "B"

    def test_remove_from_playlist(self, lib):
        pl = lib.create_playlist("Test")
        entry = lib.add("https://a.com", "A")
        lib.add_to_playlist(pl["id"], entry["id"])
        lib.remove_from_playlist(pl["id"], entry["id"])
        items = lib.get_playlist_items(pl["id"])
        assert len(items) == 0

    def test_queue_playlist(self, lib):
        pl = lib.create_playlist("Test")
        e1 = lib.add("https://a.com", "A")
        e2 = lib.add("https://b.com", "B")
        lib.add_to_playlist(pl["id"], e1["id"])
        lib.add_to_playlist(pl["id"], e2["id"])
        items = lib.queue_playlist(pl["id"])
        assert len(items) == 2

    def test_delete_playlist_cascades(self, lib):
        pl = lib.create_playlist("Test")
        entry = lib.add("https://a.com", "A")
        lib.add_to_playlist(pl["id"], entry["id"])
        lib.delete_playlist(pl["id"])
        # Library item should still exist
        assert lib.get(entry["id"]) is not None
