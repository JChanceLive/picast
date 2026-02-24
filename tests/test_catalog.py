"""Tests for the curated Archive.org catalog."""

import pytest

from picast.server.catalog import (
    CATALOG,
    CATEGORIES,
    CatalogEpisode,
    CatalogSeason,
    CatalogSeries,
    find_series_by_url,
    get_series_by_category,
    get_series_by_id,
)


class TestCatalogData:
    """Test catalog data integrity."""

    def test_categories_not_empty(self):
        assert len(CATEGORIES) >= 2

    def test_categories_have_required_fields(self):
        for cat in CATEGORIES:
            assert "id" in cat
            assert "label" in cat

    def test_catalog_not_empty(self):
        assert len(CATALOG) >= 5

    def test_all_series_have_episodes(self):
        for series in CATALOG:
            assert series.total_episodes > 0, f"{series.title} has no episodes"

    def test_all_series_have_valid_category(self):
        category_ids = {c["id"] for c in CATEGORIES}
        for series in CATALOG:
            assert series.category in category_ids, (
                f"{series.title} has invalid category: {series.category}"
            )

    def test_all_episodes_have_archive_id(self):
        for series in CATALOG:
            for season in series.seasons:
                for ep in season.episodes:
                    assert ep.archive_id, f"Missing archive_id: {series.title} - {ep.title}"

    def test_episode_url_format(self):
        ep = CatalogEpisode("Test", "test_id", 1, 1)
        assert ep.url == "https://archive.org/details/test_id"

    def test_series_ids_unique(self):
        ids = [s.id for s in CATALOG]
        assert len(ids) == len(set(ids)), "Duplicate series IDs found"


class TestCatalogSeries:
    """Test CatalogSeries helper methods."""

    @pytest.fixture
    def series(self):
        return CatalogSeries(
            id="test",
            title="Test Series",
            category="tv-shows",
            seasons=[
                CatalogSeason(number=1, episodes=[
                    CatalogEpisode("S1E1", "s1e1", 1, 1),
                    CatalogEpisode("S1E2", "s1e2", 1, 2),
                    CatalogEpisode("S1E3", "s1e3", 1, 3),
                ]),
                CatalogSeason(number=2, episodes=[
                    CatalogEpisode("S2E1", "s2e1", 2, 1),
                    CatalogEpisode("S2E2", "s2e2", 2, 2),
                ]),
            ],
        )

    def test_total_episodes(self, series):
        assert series.total_episodes == 5

    def test_get_episode_by_index(self, series):
        assert series.get_episode_by_index(0).title == "S1E1"
        assert series.get_episode_by_index(2).title == "S1E3"
        assert series.get_episode_by_index(3).title == "S2E1"
        assert series.get_episode_by_index(4).title == "S2E2"

    def test_get_episode_by_index_out_of_bounds(self, series):
        assert series.get_episode_by_index(5) is None
        assert series.get_episode_by_index(-1) is None

    def test_get_episode_index(self, series):
        assert series.get_episode_index("s1e1") == 0
        assert series.get_episode_index("s1e3") == 2
        assert series.get_episode_index("s2e1") == 3

    def test_get_episode_index_not_found(self, series):
        assert series.get_episode_index("nonexistent") is None

    def test_get_next_episode(self, series):
        next_ep = series.get_next_episode(0)
        assert next_ep.title == "S1E2"

    def test_get_next_episode_cross_season(self, series):
        next_ep = series.get_next_episode(2)
        assert next_ep.title == "S2E1"

    def test_get_next_episode_at_end(self, series):
        assert series.get_next_episode(4) is None

    def test_to_dict_basic(self, series):
        d = series.to_dict()
        assert d["id"] == "test"
        assert d["title"] == "Test Series"
        assert d["total_episodes"] == 5
        assert "seasons" not in d

    def test_to_dict_with_episodes(self, series):
        d = series.to_dict(include_episodes=True)
        assert "seasons" in d
        assert len(d["seasons"]) == 2
        assert len(d["seasons"][0]["episodes"]) == 3


class TestFindSeriesByUrl:
    """Test URL-based series lookup."""

    def test_find_known_episode(self):
        # Use first episode of first series in catalog
        series = CATALOG[0]
        ep = series.seasons[0].episodes[0]
        result = find_series_by_url(ep.url)
        assert result is not None
        found_series, idx = result
        assert found_series.id == series.id
        assert idx == 0

    def test_find_returns_none_for_unknown(self):
        assert find_series_by_url("https://archive.org/details/nonexistent_xyz") is None

    def test_find_returns_none_for_non_archive_url(self):
        assert find_series_by_url("https://youtube.com/watch?v=abc") is None

    def test_find_returns_none_for_empty(self):
        assert find_series_by_url("") is None


class TestCatalogLookups:
    """Test get_series_by_id and get_series_by_category."""

    def test_get_series_by_id(self):
        series = get_series_by_id(CATALOG[0].id)
        assert series is not None
        assert series.id == CATALOG[0].id

    def test_get_series_by_id_not_found(self):
        assert get_series_by_id("nonexistent") is None

    def test_get_series_by_category(self):
        tv_shows = get_series_by_category("tv-shows")
        assert len(tv_shows) >= 1
        for s in tv_shows:
            assert s.category == "tv-shows"


class TestCatalogAPI:
    """Test catalog API endpoints."""

    def test_catalog_page_loads(self, client):
        resp = client.get("/catalog")
        assert resp.status_code == 200
        assert b"Catalog" in resp.data

    def test_catalog_categories(self, client):
        resp = client.get("/api/catalog/categories")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) >= 2

    def test_catalog_category_series(self, client):
        resp = client.get("/api/catalog/categories/tv-shows")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) >= 1
        assert "title" in data[0]

    def test_catalog_series_detail(self, client):
        series_id = CATALOG[0].id
        resp = client.get(f"/api/catalog/series/{series_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == series_id
        assert "seasons" in data
        assert data["progress"] is None  # No progress yet

    def test_catalog_series_not_found(self, client):
        resp = client.get("/api/catalog/series/nonexistent")
        assert resp.status_code == 404

    def test_catalog_queue_all(self, client):
        series_id = CATALOG[0].id
        resp = client.post(f"/api/catalog/series/{series_id}/queue-all")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["added"] == CATALOG[0].total_episodes

    def test_catalog_queue_season(self, client):
        series_id = CATALOG[0].id
        season_num = CATALOG[0].seasons[0].number
        resp = client.post(
            f"/api/catalog/series/{series_id}/queue-season",
            json={"season": season_num},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["added"] == len(CATALOG[0].seasons[0].episodes)

    def test_catalog_progress_empty(self, client):
        resp = client.get("/api/catalog/progress")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_catalog_progress_after_update(self, client):
        # Manually insert progress
        import time
        client.application.db.execute(
            "INSERT INTO catalog_progress (series_id, last_episode_index, updated_at) "
            "VALUES (?, ?, ?)",
            (CATALOG[0].id, 2, time.time()),
        )
        client.application.db.commit()
        resp = client.get("/api/catalog/progress")
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["series_id"] == CATALOG[0].id
        assert data[0]["last_episode_index"] == 2

    def test_catalog_continue(self, client):
        # Insert progress at episode 0
        import time
        client.application.db.execute(
            "INSERT INTO catalog_progress (series_id, last_episode_index, updated_at) "
            "VALUES (?, ?, ?)",
            (CATALOG[0].id, 0, time.time()),
        )
        client.application.db.commit()
        resp = client.post(f"/api/catalog/series/{CATALOG[0].id}/continue")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["episode"]["index"] == 1
