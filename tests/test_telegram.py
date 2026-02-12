"""Tests for the Telegram bot module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from picast.server.telegram_bot import PiCastBot, _format_time, _progress_bar

# --- Helper formatting tests ---


class TestFormatTime:
    def test_zero(self):
        assert _format_time(0) == "0:00"

    def test_seconds(self):
        assert _format_time(45) == "0:45"

    def test_minutes(self):
        assert _format_time(125) == "2:05"

    def test_hours(self):
        assert _format_time(3661) == "1:01:01"

    def test_negative(self):
        assert _format_time(-5) == "0:00"


class TestProgressBar:
    def test_empty(self):
        bar = _progress_bar(0, 100)
        assert bar.startswith(">")
        assert len(bar) == 15

    def test_half(self):
        bar = _progress_bar(50, 100)
        assert "=" in bar
        assert ">" in bar

    def test_full(self):
        bar = _progress_bar(100, 100)
        assert bar.count("=") == 15

    def test_zero_duration(self):
        bar = _progress_bar(0, 0)
        assert bar == "-" * 15


# --- Bot initialization tests ---


class TestBotInit:
    def test_default_config(self):
        bot = PiCastBot("fake-token")
        assert bot.token == "fake-token"
        assert bot.api_url == "http://localhost:5000"
        assert bot.allowed_users == set()

    def test_custom_config(self):
        bot = PiCastBot("tok", api_url="http://pi:8080/", allowed_users=[123, 456])
        assert bot.api_url == "http://pi:8080"
        assert bot.allowed_users == {123, 456}

    def test_auth_no_restrictions(self):
        bot = PiCastBot("tok")
        assert bot._is_authorized(999) is True

    def test_auth_allowed(self):
        bot = PiCastBot("tok", allowed_users=[100, 200])
        assert bot._is_authorized(100) is True

    def test_auth_denied(self):
        bot = PiCastBot("tok", allowed_users=[100, 200])
        assert bot._is_authorized(999) is False


# --- Controls keyboard tests ---


class TestControlsKeyboard:
    def test_playing_keyboard(self):
        bot = PiCastBot("tok")
        kb = bot._controls_keyboard(paused=False)
        # First row, first button should be Pause when playing
        assert kb.inline_keyboard[0][0].text == "Pause"
        assert kb.inline_keyboard[0][0].callback_data == "pause"

    def test_paused_keyboard(self):
        bot = PiCastBot("tok")
        kb = bot._controls_keyboard(paused=True)
        assert kb.inline_keyboard[0][0].text == "Resume"
        assert kb.inline_keyboard[0][0].callback_data == "resume"

    def test_keyboard_has_skip(self):
        bot = PiCastBot("tok")
        kb = bot._controls_keyboard()
        buttons = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Skip" in buttons

    def test_keyboard_has_volume(self):
        bot = PiCastBot("tok")
        kb = bot._controls_keyboard()
        buttons = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Vol +" in buttons
        assert "Vol -" in buttons


# --- Command handler tests (mock the API) ---


def _make_update(user_id=123, text="", args=None):
    """Create a mock Telegram Update."""
    update = MagicMock(spec=["effective_user", "message", "callback_query"])
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.message = AsyncMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def _make_context(args=None):
    """Create a mock context."""
    ctx = MagicMock()
    ctx.args = args or []
    return ctx


class TestCommandHandlers:
    @pytest.fixture
    def bot(self):
        return PiCastBot("tok", allowed_users=[123])

    @pytest.mark.asyncio
    async def test_start_authorized(self, bot):
        update = _make_update(user_id=123)
        await bot.cmd_start(update, _make_context())
        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "PiCast Remote Control" in text

    @pytest.mark.asyncio
    async def test_start_unauthorized(self, bot):
        update = _make_update(user_id=999)
        await bot.cmd_start(update, _make_context())
        update.message.reply_text.assert_called_once_with("Not authorized.")

    @pytest.mark.asyncio
    async def test_status_idle(self, bot):
        update = _make_update(user_id=123)
        with patch.object(bot, "_api_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {"idle": True}
            await bot.cmd_status(update, _make_context())
            update.message.reply_text.assert_called_once()
            text = update.message.reply_text.call_args[0][0]
            assert "Nothing playing" in text

    @pytest.mark.asyncio
    async def test_status_playing(self, bot):
        update = _make_update(user_id=123)
        with patch.object(bot, "_api_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {
                "idle": False,
                "title": "Test Video",
                "position": 30,
                "duration": 120,
                "volume": 80,
                "speed": 1.0,
                "paused": False,
                "source_type": "youtube",
            }
            await bot.cmd_status(update, _make_context())
            text = update.message.reply_text.call_args[0][0]
            assert "PLAYING" in text
            assert "Test Video" in text

    @pytest.mark.asyncio
    async def test_play_no_url(self, bot):
        update = _make_update(user_id=123)
        await bot.cmd_play(update, _make_context(args=[]))
        text = update.message.reply_text.call_args[0][0]
        assert "Usage" in text

    @pytest.mark.asyncio
    async def test_play_with_url(self, bot):
        update = _make_update(user_id=123)
        with patch.object(bot, "_api_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = {"ok": True, "message": "Playing: http://example.com"}
            await bot.cmd_play(update, _make_context(args=["http://example.com"]))
            mock_post.assert_called_once_with("/api/play", {"url": "http://example.com"})

    @pytest.mark.asyncio
    async def test_pause(self, bot):
        update = _make_update(user_id=123)
        with patch.object(bot, "_api_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = {"ok": True}
            await bot.cmd_pause(update, _make_context())
            mock_post.assert_called_once_with("/api/pause")
            text = update.message.reply_text.call_args[0][0]
            assert text == "Paused"

    @pytest.mark.asyncio
    async def test_resume(self, bot):
        update = _make_update(user_id=123)
        with patch.object(bot, "_api_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = {"ok": True}
            await bot.cmd_resume(update, _make_context())
            mock_post.assert_called_once_with("/api/resume")

    @pytest.mark.asyncio
    async def test_skip(self, bot):
        update = _make_update(user_id=123)
        with patch.object(bot, "_api_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = {"ok": True}
            await bot.cmd_skip(update, _make_context())
            mock_post.assert_called_once_with("/api/skip")

    @pytest.mark.asyncio
    async def test_queue_show_empty(self, bot):
        update = _make_update(user_id=123)
        with patch.object(bot, "_api_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []
            await bot.cmd_queue(update, _make_context())
            text = update.message.reply_text.call_args[0][0]
            assert "empty" in text.lower()

    @pytest.mark.asyncio
    async def test_queue_show_items(self, bot):
        update = _make_update(user_id=123)
        with patch.object(bot, "_api_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = [
                {"url": "http://a.com", "title": "Video A", "status": "pending"},
                {"url": "http://b.com", "title": "Video B", "status": "pending"},
            ]
            await bot.cmd_queue(update, _make_context())
            text = update.message.reply_text.call_args[0][0]
            assert "Video A" in text
            assert "Video B" in text

    @pytest.mark.asyncio
    async def test_queue_add_url(self, bot):
        update = _make_update(user_id=123)
        with patch.object(bot, "_api_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = {"title": "New Video", "id": 1}
            await bot.cmd_queue(update, _make_context(args=["http://example.com"]))
            mock_post.assert_called_once_with("/api/queue/add", {"url": "http://example.com"})
            text = update.message.reply_text.call_args[0][0]
            assert "Queued" in text

    @pytest.mark.asyncio
    async def test_volume_show(self, bot):
        update = _make_update(user_id=123)
        with patch.object(bot, "_api_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {"volume": 75}
            await bot.cmd_volume(update, _make_context())
            text = update.message.reply_text.call_args[0][0]
            assert "75" in text

    @pytest.mark.asyncio
    async def test_volume_set(self, bot):
        update = _make_update(user_id=123)
        with patch.object(bot, "_api_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = {"ok": True}
            await bot.cmd_volume(update, _make_context(args=["50"]))
            mock_post.assert_called_once_with("/api/volume", {"level": 50})

    @pytest.mark.asyncio
    async def test_speed_set(self, bot):
        update = _make_update(user_id=123)
        with patch.object(bot, "_api_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = {"ok": True}
            await bot.cmd_speed(update, _make_context(args=["1.5"]))
            mock_post.assert_called_once_with("/api/speed", {"speed": 1.5})

    @pytest.mark.asyncio
    async def test_library_empty(self, bot):
        update = _make_update(user_id=123)
        with patch.object(bot, "_api_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []
            await bot.cmd_library(update, _make_context())
            text = update.message.reply_text.call_args[0][0]
            assert "empty" in text.lower()

    @pytest.mark.asyncio
    async def test_library_with_items(self, bot):
        update = _make_update(user_id=123)
        with patch.object(bot, "_api_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = [
                [{"id": 1, "title": "Saved Video", "url": "http://a.com", "favorite": True}],
                {"count": 1},
            ]
            await bot.cmd_library(update, _make_context())
            text = update.message.reply_text.call_args[0][0]
            assert "Saved Video" in text
            assert "*" in text  # favorite marker

    @pytest.mark.asyncio
    async def test_playlists_empty(self, bot):
        update = _make_update(user_id=123)
        with patch.object(bot, "_api_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []
            await bot.cmd_playlists(update, _make_context())
            text = update.message.reply_text.call_args[0][0]
            assert "No playlists" in text

    @pytest.mark.asyncio
    async def test_playlists_with_items(self, bot):
        update = _make_update(user_id=123)
        with patch.object(bot, "_api_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = [
                {"id": 1, "name": "Watch Later", "item_count": 5},
            ]
            await bot.cmd_playlists(update, _make_context())
            text = update.message.reply_text.call_args[0][0]
            assert "Watch Later" in text
            assert "5" in text


# --- URL handler tests ---


class TestURLHandler:
    @pytest.fixture
    def bot(self):
        return PiCastBot("tok")

    @pytest.mark.asyncio
    async def test_auto_queue_url(self, bot):
        update = _make_update(user_id=123, text="https://youtube.com/watch?v=test")
        with patch.object(bot, "_api_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = {"id": 1, "title": "Test Vid"}
            await bot.handle_url(update, _make_context())
            mock_post.assert_called_once()
            text = update.message.reply_text.call_args[0][0]
            assert "Queued" in text

    @pytest.mark.asyncio
    async def test_ignores_non_url(self, bot):
        update = _make_update(user_id=123, text="hello world")
        await bot.handle_url(update, _make_context())
        update.message.reply_text.assert_not_called()


# --- Callback handler tests ---


class TestCallbackHandler:
    @pytest.fixture
    def bot(self):
        return PiCastBot("tok", allowed_users=[123])

    def _make_callback_update(self, data, user_id=123):
        update = MagicMock(spec=["callback_query"])
        update.callback_query = AsyncMock()
        update.callback_query.from_user = MagicMock()
        update.callback_query.from_user.id = user_id
        update.callback_query.data = data
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        return update

    @pytest.mark.asyncio
    async def test_pause_callback(self, bot):
        update = self._make_callback_update("pause")
        with patch.object(bot, "_api_post", new_callable=AsyncMock) as mock_post, \
             patch.object(bot, "_format_status", new_callable=AsyncMock) as mock_status:
            mock_post.return_value = {"ok": True}
            mock_status.return_value = ("Paused status", MagicMock())
            await bot.handle_callback(update, _make_context())
            mock_post.assert_called_once_with("/api/pause")

    @pytest.mark.asyncio
    async def test_unauthorized_callback(self, bot):
        update = self._make_callback_update("pause", user_id=999)
        await bot.handle_callback(update, _make_context())
        update.callback_query.answer.assert_called_once_with("Not authorized.")

    @pytest.mark.asyncio
    async def test_clear_all_callback(self, bot):
        update = self._make_callback_update("clear_all")
        with patch.object(bot, "_api_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = {"ok": True}
            await bot.handle_callback(update, _make_context())
            mock_post.assert_called_once_with("/api/queue/clear")


# --- Application builder test ---


class TestBuildApplication:
    def test_builds_without_error(self):
        bot = PiCastBot("fake-token:for-testing")
        app = bot.build_application()
        # Should have registered handlers
        assert len(app.handlers[0]) > 0  # Group 0 handlers
