"""Tests for URL validation on POST endpoints."""

import pytest


class TestQueueAddValidation:
    """URL validation on /api/queue/add."""

    def test_valid_youtube_url(self, client):
        resp = client.post(
            "/api/queue/add",
            json={"url": "https://www.youtube.com/watch?v=abc12345678"},
        )
        assert resp.status_code == 201

    def test_bare_video_id_normalized(self, client):
        """Bare 11-char video IDs should be expanded to full URLs."""
        resp = client.post(
            "/api/queue/add",
            json={"url": "abc12345678"},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert "youtube.com" in data["url"]

    def test_missing_url(self, client):
        resp = client.post("/api/queue/add", json={})
        assert resp.status_code == 400

    def test_empty_url(self, client):
        resp = client.post("/api/queue/add", json={"url": ""})
        assert resp.status_code == 400

    def test_invalid_scheme(self, client):
        resp = client.post(
            "/api/queue/add",
            json={"url": "ftp://example.com/video"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data

    def test_no_host(self, client):
        resp = client.post(
            "/api/queue/add",
            json={"url": "http://"},
        )
        assert resp.status_code == 400


class TestPoolAddValidation:
    """URL validation on /api/autoplay/pool/<block> POST."""

    def test_valid_youtube_url(self, client):
        resp = client.post(
            "/api/autoplay/pool/test-block",
            json={"url": "https://www.youtube.com/watch?v=xyz98765432"},
        )
        assert resp.status_code == 201

    def test_bare_video_id_normalized(self, client):
        """Bare 11-char video IDs should be expanded to full URLs."""
        resp = client.post(
            "/api/autoplay/pool/test-block",
            json={"url": "xyz98765432"},
        )
        assert resp.status_code == 201

    def test_missing_url(self, client):
        resp = client.post("/api/autoplay/pool/test-block", json={})
        assert resp.status_code == 400

    def test_empty_url(self, client):
        resp = client.post(
            "/api/autoplay/pool/test-block",
            json={"url": ""},
        )
        assert resp.status_code == 400

    def test_invalid_scheme(self, client):
        resp = client.post(
            "/api/autoplay/pool/test-block",
            json={"url": "ftp://example.com/video"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data

    def test_no_host(self, client):
        resp = client.post(
            "/api/autoplay/pool/test-block",
            json={"url": "http://"},
        )
        assert resp.status_code == 400

    def test_duplicate_returns_409(self, client):
        """Adding the same URL twice should return 409 conflict."""
        url = "https://www.youtube.com/watch?v=dup12345678"
        resp1 = client.post(
            "/api/autoplay/pool/test-block",
            json={"url": url},
        )
        assert resp1.status_code == 201
        resp2 = client.post(
            "/api/autoplay/pool/test-block",
            json={"url": url},
        )
        assert resp2.status_code == 409

    def test_archive_org_url(self, client):
        resp = client.post(
            "/api/autoplay/pool/test-block",
            json={"url": "https://archive.org/details/test-video"},
        )
        assert resp.status_code == 201

    def test_with_title_and_tags(self, client):
        resp = client.post(
            "/api/autoplay/pool/test-block",
            json={
                "url": "https://www.youtube.com/watch?v=tag12345678",
                "title": "Test Video",
                "tags": "chill,lofi",
                "source": "manual",
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["title"] == "Test Video"
