# YouTube Setup

PiCast uses yt-dlp to stream YouTube videos. YouTube requires a **PO token** for playback from server environments like a Raspberry Pi.

Without a PO token, you'll see errors like:

```
Sign in to confirm you're not a bot
```

## Get a PO Token

### Option 1: Browser Cookies (Recommended)

The simplest approach - use cookies from a browser where you're logged into YouTube.

1. Install a browser on your Pi (or use an existing one):
   ```bash
   sudo apt install chromium-browser
   ```

2. Open Chromium, go to youtube.com, and sign in to your Google account

3. Edit `~/.config/picast/picast.toml`:
   ```toml
   [server]
   ytdl_cookies_from_browser = "chromium"
   ```

4. Restart PiCast:
   ```bash
   sudo systemctl restart picast
   ```

### Option 2: PO Token via BotGuard

For headless setups without a browser:

1. Install Deno:
   ```bash
   curl -fsSL https://deno.land/install.sh | sh
   ```

2. Generate a PO token:
   ```bash
   deno run -A https://raw.githubusercontent.com/nicholasgasior/rustypipe-botguard/main/main.ts
   ```

3. Copy the token and add it to `~/.config/picast/picast.toml`:
   ```toml
   [server]
   ytdl_po_token = "YOUR_TOKEN_HERE"
   ```

4. Restart PiCast:
   ```bash
   sudo systemctl restart picast
   ```

**Note:** PO tokens expire. You'll need to regenerate periodically (every few days to weeks).

## Troubleshooting

### "Sign in to confirm you're not a bot"

You need a PO token. Follow the setup above.

### Videos fail to load but token is set

1. Check if the token expired - regenerate it
2. Check yt-dlp is up to date:
   ```bash
   pip3 install --user --upgrade yt-dlp
   ```
3. Check PiCast logs:
   ```bash
   journalctl -u picast -f
   ```

### Age-restricted videos

Age-restricted videos require a logged-in cookie. Use Option 1 (browser cookies) with a Google account that has age verification completed.

### Twitch and local files

These sources don't need a PO token. Only YouTube requires it.
