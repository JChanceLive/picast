"""Telegram bot for remote PiCast control.

Provides command-based and inline keyboard control of the PiCast server
via Telegram. The bot talks to the Flask API over HTTP, so it can run
alongside the server or on a separate machine.

Requires: pip install "picast[telegram]"
"""

import asyncio
import logging
import threading
from typing import Optional

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logger = logging.getLogger(__name__)


def _format_time(seconds: float) -> str:
    """Format seconds as H:MM:SS or M:SS."""
    if seconds <= 0:
        return "0:00"
    s = int(seconds)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _progress_bar(position: float, duration: float, width: int = 15) -> str:
    """Create a text progress bar."""
    if duration <= 0:
        return "-" * width
    ratio = min(position / duration, 1.0)
    filled = int(ratio * width)
    return "=" * filled + ">" + "-" * (width - filled - 1)


class PiCastBot:
    """Telegram bot for PiCast remote control.

    Args:
        token: Telegram bot token from @BotFather
        api_url: PiCast server API URL (e.g. http://localhost:5000)
        allowed_users: List of Telegram user IDs allowed to use the bot.
                      Empty list = allow everyone (not recommended for public bots).
    """

    def __init__(
        self,
        token: str,
        api_url: str = "http://localhost:5000",
        allowed_users: Optional[list[int]] = None,
    ):
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.allowed_users = set(allowed_users or [])
        self._app: Optional[Application] = None
        self._thread: Optional[threading.Thread] = None

    def _is_authorized(self, user_id: int) -> bool:
        """Check if a user is authorized."""
        if not self.allowed_users:
            return True
        return user_id in self.allowed_users

    async def _api_get(self, path: str) -> dict:
        """Make a GET request to the PiCast API."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.api_url}{path}", timeout=10)
            return resp.json()

    async def _api_post(self, path: str, data: dict | None = None) -> dict:
        """Make a POST request to the PiCast API."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{self.api_url}{path}", json=data or {}, timeout=10)
            return resp.json()

    async def _api_delete(self, path: str) -> dict:
        """Make a DELETE request to the PiCast API."""
        async with httpx.AsyncClient() as client:
            resp = await client.delete(f"{self.api_url}{path}", timeout=10)
            return resp.json()

    def _controls_keyboard(self, paused: bool = False) -> InlineKeyboardMarkup:
        """Build the inline control keyboard."""
        play_btn = ("Resume", "resume") if paused else ("Pause", "pause")
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(play_btn[0], callback_data=play_btn[1]),
                InlineKeyboardButton("Skip", callback_data="skip"),
                InlineKeyboardButton("Stop", callback_data="stop"),
            ],
            [
                InlineKeyboardButton("Vol -", callback_data="vol_down"),
                InlineKeyboardButton("Vol +", callback_data="vol_up"),
                InlineKeyboardButton("Speed", callback_data="speed_cycle"),
            ],
            [
                InlineKeyboardButton("Queue", callback_data="show_queue"),
                InlineKeyboardButton("Refresh", callback_data="refresh_status"),
            ],
        ])

    async def _format_status(self) -> tuple[str, InlineKeyboardMarkup]:
        """Get formatted status text and keyboard."""
        try:
            status = await self._api_get("/api/status")
        except (httpx.HTTPError, Exception) as e:
            return f"Could not reach PiCast server:\n{e}", InlineKeyboardMarkup([])

        if status.get("idle", True):
            return "Nothing playing. Send a URL to queue it.", InlineKeyboardMarkup([
                [InlineKeyboardButton("Queue", callback_data="show_queue")],
            ])

        title = status.get("title", "Unknown")
        position = status.get("position", 0)
        duration = status.get("duration", 0)
        volume = status.get("volume", 100)
        speed = status.get("speed", 1.0)
        paused = status.get("paused", False)
        source = status.get("source_type", "")

        state = "PAUSED" if paused else "PLAYING"
        bar = _progress_bar(position, duration)

        lines = [
            f"{state}: {title}",
            f"[{bar}]",
            f"{_format_time(position)} / {_format_time(duration)}",
            f"Vol: {int(volume)}%  Speed: {speed}x",
        ]
        if source:
            lines.append(f"Source: {source}")

        return "\n".join(lines), self._controls_keyboard(paused)

    # --- Command Handlers ---

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return

        await update.message.reply_text(
            "PiCast Remote Control\n\n"
            "Commands:\n"
            "/status - Now playing with controls\n"
            "/play <url> - Play a URL now\n"
            "/queue - Show queue\n"
            "/pause - Pause playback\n"
            "/resume - Resume playback\n"
            "/skip - Skip current video\n"
            "/volume <0-100> - Set volume\n"
            "/speed <0.25-4.0> - Set speed\n"
            "/library - Browse library\n"
            "/playlists - List playlists\n\n"
            "Or just send a URL to add it to the queue."
        )

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command."""
        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return

        text, keyboard = await self._format_status()
        await update.message.reply_text(text, reply_markup=keyboard)

    async def cmd_play(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /play <url> command."""
        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /play <url>")
            return

        url = context.args[0]
        try:
            result = await self._api_post("/api/play", {"url": url})
            await update.message.reply_text(result.get("message", "Playing"))
        except (httpx.HTTPError, Exception) as e:
            await update.message.reply_text(f"Error: {e}")

    async def cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /pause command."""
        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return

        try:
            await self._api_post("/api/pause")
            await update.message.reply_text("Paused")
        except (httpx.HTTPError, Exception) as e:
            await update.message.reply_text(f"Error: {e}")

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /resume command."""
        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return

        try:
            await self._api_post("/api/resume")
            await update.message.reply_text("Resumed")
        except (httpx.HTTPError, Exception) as e:
            await update.message.reply_text(f"Error: {e}")

    async def cmd_skip(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /skip command."""
        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return

        try:
            await self._api_post("/api/skip")
            await update.message.reply_text("Skipped")
        except (httpx.HTTPError, Exception) as e:
            await update.message.reply_text(f"Error: {e}")

    async def cmd_queue(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /queue [url] command."""
        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return

        # If URL provided, add to queue
        if context.args:
            url = context.args[0]
            try:
                result = await self._api_post("/api/queue/add", {"url": url})
                title = result.get("title", url)
                await update.message.reply_text(f"Queued: {title}")
                return
            except (httpx.HTTPError, Exception) as e:
                await update.message.reply_text(f"Error: {e}")
                return

        # Otherwise show queue
        try:
            items = await self._api_get("/api/queue")
            pending = [i for i in items if i["status"] == "pending"]
            playing = [i for i in items if i["status"] == "playing"]

            if not pending and not playing:
                await update.message.reply_text("Queue is empty.")
                return

            lines = []
            if playing:
                lines.append(f"Now: {playing[0].get('title') or playing[0]['url']}")
                lines.append("")

            if pending:
                lines.append(f"Up next ({len(pending)}):")
                for i, item in enumerate(pending[:10], 1):
                    title = item.get("title") or item["url"]
                    # Truncate long titles
                    if len(title) > 50:
                        title = title[:47] + "..."
                    lines.append(f"  {i}. {title}")
                if len(pending) > 10:
                    lines.append(f"  ... +{len(pending) - 10} more")

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Clear Played", callback_data="clear_played"),
                    InlineKeyboardButton("Clear All", callback_data="clear_all"),
                ],
            ])
            await update.message.reply_text("\n".join(lines), reply_markup=keyboard)
        except (httpx.HTTPError, Exception) as e:
            await update.message.reply_text(f"Error: {e}")

    async def cmd_volume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /volume <level> command."""
        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return

        if not context.args:
            # Show current volume
            try:
                status = await self._api_get("/api/status")
                vol = status.get("volume", "?")
                await update.message.reply_text(f"Volume: {int(vol)}%")
            except (httpx.HTTPError, Exception) as e:
                await update.message.reply_text(f"Error: {e}")
            return

        try:
            level = int(context.args[0])
            await self._api_post("/api/volume", {"level": level})
            await update.message.reply_text(f"Volume: {level}%")
        except ValueError:
            await update.message.reply_text("Usage: /volume <0-100>")
        except (httpx.HTTPError, Exception) as e:
            await update.message.reply_text(f"Error: {e}")

    async def cmd_speed(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /speed <rate> command."""
        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return

        if not context.args:
            try:
                status = await self._api_get("/api/status")
                spd = status.get("speed", 1.0)
                await update.message.reply_text(f"Speed: {spd}x")
            except (httpx.HTTPError, Exception) as e:
                await update.message.reply_text(f"Error: {e}")
            return

        try:
            spd = float(context.args[0])
            await self._api_post("/api/speed", {"speed": spd})
            await update.message.reply_text(f"Speed: {spd}x")
        except ValueError:
            await update.message.reply_text("Usage: /speed <0.25-4.0>")
        except (httpx.HTTPError, Exception) as e:
            await update.message.reply_text(f"Error: {e}")

    async def cmd_library(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /library command."""
        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return

        try:
            items = await self._api_get("/api/library/recent?limit=10")
            if not items:
                await update.message.reply_text("Library is empty.")
                return

            count_data = await self._api_get("/api/library/count")
            total = count_data.get("count", len(items))

            lines = [f"Library ({total} items, showing recent):"]
            for item in items:
                title = item.get("title") or item.get("url", "?")
                if len(title) > 45:
                    title = title[:42] + "..."
                fav = " *" if item.get("favorite") else ""
                lines.append(f"  {title}{fav}")

            # Inline buttons for each item to re-queue
            buttons = []
            for item in items[:5]:
                title = item.get("title") or "?"
                if len(title) > 20:
                    title = title[:17] + "..."
                buttons.append([InlineKeyboardButton(
                    f"Queue: {title}",
                    callback_data=f"lib_queue_{item['id']}",
                )])

            keyboard = InlineKeyboardMarkup(buttons) if buttons else None
            await update.message.reply_text("\n".join(lines), reply_markup=keyboard)
        except (httpx.HTTPError, Exception) as e:
            await update.message.reply_text(f"Error: {e}")

    async def cmd_playlists(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /playlists command."""
        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return

        try:
            playlists = await self._api_get("/api/playlists")
            if not playlists:
                await update.message.reply_text("No playlists.")
                return

            lines = ["Playlists:"]
            buttons = []
            for pl in playlists:
                count = pl.get("item_count", 0)
                lines.append(f"  {pl['name']} ({count} items)")
                buttons.append([InlineKeyboardButton(
                    f"Queue: {pl['name']}",
                    callback_data=f"pl_queue_{pl['id']}",
                )])

            keyboard = InlineKeyboardMarkup(buttons) if buttons else None
            await update.message.reply_text("\n".join(lines), reply_markup=keyboard)
        except (httpx.HTTPError, Exception) as e:
            await update.message.reply_text(f"Error: {e}")

    # --- Callback Query Handler (inline keyboard buttons) ---

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline keyboard button presses."""
        query = update.callback_query
        if not self._is_authorized(query.from_user.id):
            await query.answer("Not authorized.")
            return

        data = query.data
        await query.answer()

        try:
            if data == "pause":
                await self._api_post("/api/pause")
                text, keyboard = await self._format_status()
                await query.edit_message_text(text, reply_markup=keyboard)

            elif data == "resume":
                await self._api_post("/api/resume")
                text, keyboard = await self._format_status()
                await query.edit_message_text(text, reply_markup=keyboard)

            elif data == "skip":
                await self._api_post("/api/skip")
                await asyncio.sleep(0.5)
                text, keyboard = await self._format_status()
                await query.edit_message_text(text, reply_markup=keyboard)

            elif data == "stop":
                await self._api_post("/api/stop")
                await query.edit_message_text("Stopped.")

            elif data == "vol_up":
                status = await self._api_get("/api/status")
                vol = min(100, int(status.get("volume", 50)) + 10)
                await self._api_post("/api/volume", {"level": vol})
                text, keyboard = await self._format_status()
                await query.edit_message_text(text, reply_markup=keyboard)

            elif data == "vol_down":
                status = await self._api_get("/api/status")
                vol = max(0, int(status.get("volume", 50)) - 10)
                await self._api_post("/api/volume", {"level": vol})
                text, keyboard = await self._format_status()
                await query.edit_message_text(text, reply_markup=keyboard)

            elif data == "speed_cycle":
                status = await self._api_get("/api/status")
                current = status.get("speed", 1.0)
                speeds = [1.0, 1.25, 1.5, 1.75, 2.0]
                idx = 0
                for i, s in enumerate(speeds):
                    if abs(current - s) < 0.05:
                        idx = (i + 1) % len(speeds)
                        break
                await self._api_post("/api/speed", {"speed": speeds[idx]})
                text, keyboard = await self._format_status()
                await query.edit_message_text(text, reply_markup=keyboard)

            elif data == "show_queue":
                items = await self._api_get("/api/queue")
                pending = [i for i in items if i["status"] == "pending"]
                if not pending:
                    await query.edit_message_text("Queue is empty.")
                else:
                    lines = [f"Queue ({len(pending)}):"]
                    for i, item in enumerate(pending[:10], 1):
                        title = item.get("title") or item["url"]
                        if len(title) > 50:
                            title = title[:47] + "..."
                        lines.append(f"  {i}. {title}")
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("Refresh", callback_data="refresh_status")],
                    ])
                    await query.edit_message_text("\n".join(lines), reply_markup=keyboard)

            elif data == "refresh_status":
                text, keyboard = await self._format_status()
                await query.edit_message_text(text, reply_markup=keyboard)

            elif data == "clear_played":
                await self._api_post("/api/queue/clear-played")
                await query.edit_message_text("Cleared played items.")

            elif data == "clear_all":
                await self._api_post("/api/queue/clear")
                await query.edit_message_text("Queue cleared.")

            elif data.startswith("lib_queue_"):
                lib_id = int(data.split("_")[2])
                await self._api_post(f"/api/library/{lib_id}/queue")
                await query.edit_message_text("Added to queue.")

            elif data.startswith("pl_queue_"):
                pl_id = int(data.split("_")[2])
                result = await self._api_post(f"/api/playlists/{pl_id}/queue")
                queued = result.get("queued", 0)
                await query.edit_message_text(f"Queued {queued} items from playlist.")

        except (httpx.HTTPError, Exception) as e:
            logger.error("Callback error: %s", e)
            try:
                await query.edit_message_text(f"Error: {e}")
            except Exception:
                pass

    # --- URL Handler (auto-queue URLs sent as messages) ---

    async def handle_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Auto-queue URLs sent as plain messages."""
        if not self._is_authorized(update.effective_user.id):
            return

        text = update.message.text.strip()
        # Check if it looks like a URL
        if not (text.startswith("http://") or text.startswith("https://") or text.startswith("/")):
            return

        try:
            result = await self._api_post("/api/queue/add", {"url": text})
            title = result.get("title") or text
            if len(title) > 50:
                title = title[:47] + "..."
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Play Now", callback_data=f"play_now_{result['id']}")],
            ])
            await update.message.reply_text(f"Queued: {title}", reply_markup=keyboard)
        except (httpx.HTTPError, Exception) as e:
            await update.message.reply_text(f"Error adding to queue: {e}")

    def build_application(self) -> Application:
        """Build the Telegram application with all handlers."""
        self._app = Application.builder().token(self.token).build()

        # Command handlers
        self._app.add_handler(CommandHandler("start", self.cmd_start))
        self._app.add_handler(CommandHandler("help", self.cmd_start))
        self._app.add_handler(CommandHandler("status", self.cmd_status))
        self._app.add_handler(CommandHandler("play", self.cmd_play))
        self._app.add_handler(CommandHandler("pause", self.cmd_pause))
        self._app.add_handler(CommandHandler("resume", self.cmd_resume))
        self._app.add_handler(CommandHandler("skip", self.cmd_skip))
        self._app.add_handler(CommandHandler("queue", self.cmd_queue))
        self._app.add_handler(CommandHandler("q", self.cmd_queue))
        self._app.add_handler(CommandHandler("volume", self.cmd_volume))
        self._app.add_handler(CommandHandler("vol", self.cmd_volume))
        self._app.add_handler(CommandHandler("speed", self.cmd_speed))
        self._app.add_handler(CommandHandler("library", self.cmd_library))
        self._app.add_handler(CommandHandler("lib", self.cmd_library))
        self._app.add_handler(CommandHandler("playlists", self.cmd_playlists))
        self._app.add_handler(CommandHandler("pl", self.cmd_playlists))

        # Inline keyboard callback
        self._app.add_handler(CallbackQueryHandler(self.handle_callback))

        # URL auto-queue (catch-all for messages with URLs)
        self._app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self.handle_url,
        ))

        return self._app

    def run_polling(self):
        """Run the bot with polling (blocking)."""
        app = self.build_application()
        logger.info("Telegram bot starting (polling)")
        app.run_polling(drop_pending_updates=True)

    def start_background(self):
        """Start the bot in a background thread."""
        self._thread = threading.Thread(
            target=self._run_in_thread,
            daemon=True,
            name="telegram-bot",
        )
        self._thread.start()
        logger.info("Telegram bot started in background thread")

    def _run_in_thread(self):
        """Run the bot in a new event loop (for background thread)."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        app = self.build_application()
        loop.run_until_complete(app.initialize())
        loop.run_until_complete(app.start())
        loop.run_until_complete(app.updater.start_polling(drop_pending_updates=True))

        # Keep running until the thread is stopped
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(app.updater.stop())
            loop.run_until_complete(app.stop())
            loop.run_until_complete(app.shutdown())
            loop.close()

    def stop_background(self):
        """Stop the background bot thread."""
        if self._thread and self._thread.is_alive():
            # Get the event loop from the thread and stop it
            if self._app and self._app.updater and self._app.updater._running:
                loop = self._app.updater._loop
                if loop and loop.is_running():
                    loop.call_soon_threadsafe(loop.stop)
            self._thread.join(timeout=10)
            self._thread = None
            logger.info("Telegram bot stopped")
