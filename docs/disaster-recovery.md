# Disaster Recovery

How to restore PiCast on a fresh Pi or after an SD card failure.

## What's Backed Up

The nightly backup (`~/picast-data/`) pushes to GitHub and includes:

| File | Contains |
|------|----------|
| `picast.db` | Library, playlists, watch history |
| `queue.json` | Current queue state |
| `config/picast.toml` | Server config (port, Telegram token, yt-dlp format) |
| `config/mpv.conf` | mpv cache and playback settings |
| `config/picast.service` | systemd service definition |

## Fresh Pi Setup

### 1. Flash Raspberry Pi OS

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) to flash Raspberry Pi OS Lite (64-bit) to an SD card.

In the imager settings:
- Set hostname (e.g., `JoPi`)
- Enable SSH with your public key
- Set username and password
- Configure WiFi if not using ethernet

### 2. Boot and connect

```bash
ssh youruser@hostname.local
```

### 3. Install PiCast

```bash
curl -sSL https://raw.githubusercontent.com/JChanceLive/picast/main/install-pi.sh | bash
```

### 4. Restore backup data

```bash
# Clone the backup repo
git clone git@github.com:JChanceLive/picast-data.git ~/picast-data

# Restore database
cp ~/picast-data/picast.db ~/.picast/picast.db

# Restore config
cp ~/picast-data/config/picast.toml ~/.config/picast/picast.toml
cp ~/picast-data/config/mpv.conf ~/.config/mpv/mpv.conf

# Restore service file (if customized)
sudo cp ~/picast-data/config/picast.service /etc/systemd/system/picast.service
sudo systemctl daemon-reload
```

### 5. Set up YouTube

Follow [YouTube Setup Guide](youtube-setup.md) to configure PO token or browser cookies.

### 6. Restart and verify

```bash
sudo systemctl restart picast
curl http://localhost:5050/api/health
```

### 7. Re-enable nightly backup

```bash
# Add to crontab
crontab -e
# Add this line:
0 3 * * * /home/youruser/picast-data/backup.sh >> /home/youruser/picast-data/backup.log 2>&1
```

## SD Card Imaging

For a full system backup (not just PiCast data):

### Create image from running Pi

From your Mac:
```bash
# Find the Pi's SD card (if mounted)
diskutil list

# Or SSH and create image remotely
ssh user@pi "sudo dd if=/dev/mmcblk0 bs=4M status=progress" | gzip > picast-backup.img.gz
```

### Restore image to new SD card

```bash
gunzip -c picast-backup.img.gz | sudo dd of=/dev/diskN bs=4M status=progress
```

Replace `/dev/diskN` with the actual disk number from `diskutil list`.

## Verify Backup Health

```bash
# Check last backup time
ssh user@pi "cd ~/picast-data && git log -1 --format='%ci'"

# Check backup log
ssh user@pi "tail -5 ~/picast-data/backup.log"

# Check cron is running
ssh user@pi "crontab -l | grep backup"
```
