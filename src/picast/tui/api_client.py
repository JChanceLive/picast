"""HTTP client for communicating with the PiCast server.

Provides both sync and async methods for all API endpoints.
Used by the TUI to poll status and send commands.
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 5.0


class PiCastAPIError(Exception):
    """Error communicating with PiCast server."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class PiCastClient:
    """HTTP client for the PiCast REST API.

    Usage:
        client = PiCastClient("raspberrypi.local", 5000)
        status = client.get_status()
        client.pause()
        client.add_to_queue("https://youtube.com/watch?v=abc")
    """

    def __init__(self, host: str = "raspberrypi.local", port: int = 5000):
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self._client = httpx.Client(base_url=self.base_url, timeout=DEFAULT_TIMEOUT)

    def close(self):
        self._client.close()

    def _get(self, path: str) -> Any:
        try:
            resp = self._client.get(path)
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            raise PiCastAPIError(f"Cannot connect to {self.base_url}")
        except httpx.TimeoutException:
            raise PiCastAPIError("Request timed out")
        except httpx.HTTPStatusError as e:
            raise PiCastAPIError(str(e), e.response.status_code)

    def _post(self, path: str, data: dict | None = None) -> Any:
        try:
            resp = self._client.post(path, json=data)
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            raise PiCastAPIError(f"Cannot connect to {self.base_url}")
        except httpx.TimeoutException:
            raise PiCastAPIError("Request timed out")
        except httpx.HTTPStatusError as e:
            raise PiCastAPIError(str(e), e.response.status_code)

    def _delete(self, path: str) -> Any:
        try:
            resp = self._client.delete(path)
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            raise PiCastAPIError(f"Cannot connect to {self.base_url}")
        except httpx.TimeoutException:
            raise PiCastAPIError("Request timed out")
        except httpx.HTTPStatusError as e:
            raise PiCastAPIError(str(e), e.response.status_code)

    # --- Player Control ---

    def get_status(self) -> dict:
        return self._get("/api/status")

    def get_health(self) -> dict:
        return self._get("/api/health")

    def play(self, url: str, title: str = "") -> dict:
        return self._post("/api/play", {"url": url, "title": title})

    def pause(self) -> dict:
        return self._post("/api/pause")

    def resume(self) -> dict:
        return self._post("/api/resume")

    def toggle(self) -> dict:
        return self._post("/api/toggle")

    def skip(self) -> dict:
        return self._post("/api/skip")

    def stop(self) -> dict:
        return self._post("/api/stop")

    def seek(self, position: float, mode: str = "absolute") -> dict:
        return self._post("/api/seek", {"position": position, "mode": mode})

    def set_volume(self, level: int) -> dict:
        return self._post("/api/volume", {"level": level})

    def set_speed(self, speed: float) -> dict:
        return self._post("/api/speed", {"speed": speed})

    # --- Queue ---

    def get_queue(self) -> list[dict]:
        return self._get("/api/queue")

    def add_to_queue(self, url: str, title: str = "") -> dict:
        return self._post("/api/queue/add", {"url": url, "title": title})

    def remove_from_queue(self, item_id: int) -> dict:
        return self._delete(f"/api/queue/{item_id}")

    def reorder_queue(self, item_ids: list[int]) -> dict:
        return self._post("/api/queue/reorder", {"items": item_ids})

    def clear_played(self) -> dict:
        return self._post("/api/queue/clear-played")

    def clear_queue(self) -> dict:
        return self._post("/api/queue/clear")

    # --- Library ---

    def get_library(self, sort: str = "recent", limit: int = 50, offset: int = 0) -> list[dict]:
        return self._get(f"/api/library?sort={sort}&limit={limit}&offset={offset}")

    def search_library(self, query: str) -> list[dict]:
        return self._get(f"/api/library/search?q={query}")

    def get_library_item(self, library_id: int) -> dict:
        return self._get(f"/api/library/{library_id}")

    def update_notes(self, library_id: int, notes: str) -> dict:
        return self._client.put(f"/api/library/{library_id}/notes", json={"notes": notes}).json()

    def toggle_favorite(self, library_id: int) -> dict:
        return self._post(f"/api/library/{library_id}/favorite")

    def queue_library_item(self, library_id: int) -> dict:
        return self._post(f"/api/library/{library_id}/queue")

    def delete_library_item(self, library_id: int) -> dict:
        return self._delete(f"/api/library/{library_id}")

    # --- Playlists ---

    def get_playlists(self) -> list[dict]:
        return self._get("/api/playlists")

    def create_playlist(self, name: str, description: str = "") -> dict:
        return self._post("/api/playlists", {"name": name, "description": description})

    def get_playlist(self, playlist_id: int) -> dict:
        return self._get(f"/api/playlists/{playlist_id}")

    def queue_playlist(self, playlist_id: int) -> dict:
        return self._post(f"/api/playlists/{playlist_id}/queue")

    def add_to_playlist(self, playlist_id: int, library_id: int) -> dict:
        return self._post(f"/api/playlists/{playlist_id}/items", {"library_id": library_id})

    def delete_playlist(self, playlist_id: int) -> dict:
        return self._delete(f"/api/playlists/{playlist_id}")


class AsyncPiCastClient:
    """Async HTTP client for the PiCast REST API.

    For use with Textual's async workers.
    """

    def __init__(self, host: str = "raspberrypi.local", port: int = 5000):
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=DEFAULT_TIMEOUT)

    async def close(self):
        await self._client.aclose()

    async def _get(self, path: str) -> Any:
        try:
            resp = await self._client.get(path)
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            raise PiCastAPIError(f"Cannot connect to {self.base_url}")
        except httpx.TimeoutException:
            raise PiCastAPIError("Request timed out")
        except httpx.HTTPStatusError as e:
            raise PiCastAPIError(str(e), e.response.status_code)

    async def _post(self, path: str, data: dict | None = None) -> Any:
        try:
            resp = await self._client.post(path, json=data)
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            raise PiCastAPIError(f"Cannot connect to {self.base_url}")
        except httpx.TimeoutException:
            raise PiCastAPIError("Request timed out")
        except httpx.HTTPStatusError as e:
            raise PiCastAPIError(str(e), e.response.status_code)

    async def _delete(self, path: str) -> Any:
        try:
            resp = await self._client.delete(path)
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            raise PiCastAPIError(f"Cannot connect to {self.base_url}")
        except httpx.TimeoutException:
            raise PiCastAPIError("Request timed out")
        except httpx.HTTPStatusError as e:
            raise PiCastAPIError(str(e), e.response.status_code)

    # --- Player Control ---

    async def get_status(self) -> dict:
        return await self._get("/api/status")

    async def get_health(self) -> dict:
        return await self._get("/api/health")

    async def play(self, url: str, title: str = "") -> dict:
        return await self._post("/api/play", {"url": url, "title": title})

    async def pause(self) -> dict:
        return await self._post("/api/pause")

    async def resume(self) -> dict:
        return await self._post("/api/resume")

    async def toggle(self) -> dict:
        return await self._post("/api/toggle")

    async def skip(self) -> dict:
        return await self._post("/api/skip")

    async def stop(self) -> dict:
        return await self._post("/api/stop")

    async def seek(self, position: float, mode: str = "absolute") -> dict:
        return await self._post("/api/seek", {"position": position, "mode": mode})

    async def set_volume(self, level: int) -> dict:
        return await self._post("/api/volume", {"level": level})

    async def set_speed(self, speed: float) -> dict:
        return await self._post("/api/speed", {"speed": speed})

    # --- Queue ---

    async def get_queue(self) -> list[dict]:
        return await self._get("/api/queue")

    async def add_to_queue(self, url: str, title: str = "") -> dict:
        return await self._post("/api/queue/add", {"url": url, "title": title})

    async def remove_from_queue(self, item_id: int) -> dict:
        return await self._delete(f"/api/queue/{item_id}")

    async def reorder_queue(self, item_ids: list[int]) -> dict:
        return await self._post("/api/queue/reorder", {"items": item_ids})

    async def clear_played(self) -> dict:
        return await self._post("/api/queue/clear-played")

    async def clear_queue(self) -> dict:
        return await self._post("/api/queue/clear")

    # --- Library ---

    async def get_library(self, sort: str = "recent", limit: int = 50, offset: int = 0) -> list[dict]:
        return await self._get(f"/api/library?sort={sort}&limit={limit}&offset={offset}")

    async def search_library(self, query: str) -> list[dict]:
        return await self._get(f"/api/library/search?q={query}")

    async def get_library_item(self, library_id: int) -> dict:
        return await self._get(f"/api/library/{library_id}")

    async def update_notes(self, library_id: int, notes: str) -> dict:
        try:
            resp = await self._client.put(f"/api/library/{library_id}/notes", json={"notes": notes})
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            raise PiCastAPIError(f"Cannot connect to {self.base_url}")
        except httpx.TimeoutException:
            raise PiCastAPIError("Request timed out")
        except httpx.HTTPStatusError as e:
            raise PiCastAPIError(str(e), e.response.status_code)

    async def toggle_favorite(self, library_id: int) -> dict:
        return await self._post(f"/api/library/{library_id}/favorite")

    async def queue_library_item(self, library_id: int) -> dict:
        return await self._post(f"/api/library/{library_id}/queue")

    async def delete_library_item(self, library_id: int) -> dict:
        return await self._delete(f"/api/library/{library_id}")

    # --- Playlists ---

    async def get_playlists(self) -> list[dict]:
        return await self._get("/api/playlists")

    async def create_playlist(self, name: str, description: str = "") -> dict:
        return await self._post("/api/playlists", {"name": name, "description": description})

    async def get_playlist(self, playlist_id: int) -> dict:
        return await self._get(f"/api/playlists/{playlist_id}")

    async def queue_playlist(self, playlist_id: int) -> dict:
        return await self._post(f"/api/playlists/{playlist_id}/queue")

    async def add_to_playlist(self, playlist_id: int, library_id: int) -> dict:
        return await self._post(f"/api/playlists/{playlist_id}/items", {"library_id": library_id})

    async def delete_playlist(self, playlist_id: int) -> dict:
        return await self._delete(f"/api/playlists/{playlist_id}")
