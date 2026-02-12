# Deployment

PiCast supports two update methods that coexist without conflict.

## Auto-Update (Production)

After installation, PiCast checks GitHub daily for new versions.

- **When:** 4 AM local time (with 30-min random jitter)
- **What:** Compares installed version to `__about__.py` on `main` branch
- **Action:** If different, `pip install` from GitHub + restart service
- **Log:** `~/.picast/update.log`

### Manual trigger

```bash
picast-update
```

### Check timer status

```bash
systemctl list-timers | grep picast
```

### View update log

```bash
cat ~/.picast/update.log
```

### Disable auto-update

```bash
sudo systemctl disable picast-update.timer
sudo systemctl stop picast-update.timer
```

## rsync Deploy (Development)

For live development on the same network, deploy directly from your Mac:

```bash
rsync -avz --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
  src/picast/ jopi@10.0.0.25:/home/jopi/.local/lib/python3.11/site-packages/picast/
ssh jopi@10.0.0.25 "sudo systemctl restart picast"
```

This overwrites the installed package files directly. The auto-updater will not interfere because it only updates when the version number changes.

### Coexistence rules

- rsync deploys are immediate but don't change `__about__.__version__`
- Auto-updater compares version strings, so it won't downgrade a dev deploy
- When you're ready to release, bump the version and push to GitHub
- The next auto-update cycle will pick up the new version

## Multiple Pi Setup

Each Pi is independent:

1. Run `install-pi.sh` on each Pi
2. Each gets its own config at `~/.config/picast/picast.toml`
3. Each auto-updates independently from GitHub
4. Open Chromium and sign in to YouTube on each Pi separately
