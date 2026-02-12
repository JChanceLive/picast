#!/bin/bash
# Quick-start PiCast server for local testing (no mpv needed)
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/.venv/bin/picast-server" --test "$@"
