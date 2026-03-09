#!/usr/bin/env bash
# test-refresh.sh — Test the taste profile refresh script without API cost
#
# Runs refresh-taste-profile.sh in --dry-run mode with verbose output.
# Verifies PiCast is reachable, data can be pulled, and prompt assembles correctly.
#
# Usage:
#   ./test-refresh.sh                    # Dry run with verbose output
#   ./test-refresh.sh --with-mock        # Use mock data (PiCast not needed)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REFRESH_SCRIPT="${SCRIPT_DIR}/refresh-taste-profile.sh"

echo "=== PiCast Taste Profile Refresh Test ==="
echo ""

if [[ "${1:-}" == "--with-mock" ]]; then
    echo "Mock mode: Creating sample data..."
    CACHE_DIR="${HOME}/.picast/taste-cache"
    mkdir -p "$CACHE_DIR"

    # Create mock history
    cat > "${CACHE_DIR}/mock-history.json" << 'MOCK_HIST'
[
  {"video_id":"abc123","title":"Lofi Hip Hop Radio","block_name":"morning-foundation","rating":1,"played_at":"2026-03-09T07:15:00Z","completed":1,"stop_reason":""},
  {"video_id":"def456","title":"Jazz Cafe Ambience","block_name":"morning-foundation","rating":0,"played_at":"2026-03-09T07:45:00Z","completed":1,"stop_reason":""},
  {"video_id":"ghi789","title":"Focus Music 4K","block_name":"creation-stack","rating":1,"played_at":"2026-03-09T10:00:00Z","completed":0,"stop_reason":"skip"},
  {"video_id":"jkl012","title":"Space Documentary","block_name":"evening-transition","rating":1,"played_at":"2026-03-08T20:00:00Z","completed":1,"stop_reason":""},
  {"video_id":"mno345","title":"Cooking Show","block_name":"evening-transition","rating":-1,"played_at":"2026-03-08T21:00:00Z","completed":0,"stop_reason":"skip"}
]
MOCK_HIST

    # Create mock pool
    cat > "${CACHE_DIR}/mock-pools.json" << 'MOCK_POOL'
{
  "mappings": {"morning-foundation": 5, "creation-stack": 3, "evening-transition": 4},
  "pools": {
    "morning-foundation": [
      {"video_id":"abc123","title":"Lofi Hip Hop Radio","rating":1,"skip_count":0,"completion_count":5,"play_count":8,"tags":"lofi,ambient,chill","duration":3600},
      {"video_id":"def456","title":"Jazz Cafe Ambience","rating":0,"skip_count":1,"completion_count":2,"play_count":4,"tags":"jazz,ambient,cafe","duration":7200}
    ],
    "creation-stack": [
      {"video_id":"ghi789","title":"Focus Music 4K","rating":1,"skip_count":2,"completion_count":1,"play_count":5,"tags":"focus,ambient,4k","duration":5400}
    ],
    "evening-transition": [
      {"video_id":"jkl012","title":"Space Documentary","rating":1,"skip_count":0,"completion_count":3,"play_count":4,"tags":"documentary,space,science","duration":2700},
      {"video_id":"mno345","title":"Cooking Show","rating":-1,"skip_count":4,"completion_count":0,"play_count":4,"tags":"cooking,food","duration":1800}
    ]
  }
}
MOCK_POOL

    echo "Mock data created at ${CACHE_DIR}/"
    echo ""
    echo "--- Mock History ---"
    jq '.[].title' "${CACHE_DIR}/mock-history.json"
    echo ""
    echo "--- Mock Pools ---"
    jq '.pools | to_entries[] | "\(.key): \(.value | length) videos"' "${CACHE_DIR}/mock-pools.json"
    echo ""
    echo "To test full flow with this mock data, run the refresh script"
    echo "with PiCast running (data will be re-pulled from the API)."
    exit 0
fi

# Run the refresh script in dry-run + verbose mode
echo "Running: ${REFRESH_SCRIPT} --dry-run --verbose"
echo ""

"$REFRESH_SCRIPT" --dry-run --verbose

echo ""
echo "=== Test Complete ==="
echo ""
echo "Check assembled prompt: ~/.picast/taste-cache/assembled-prompt.md"
echo "Check cached data:      ~/.picast/taste-cache/"
echo ""
echo "To run for real: ${REFRESH_SCRIPT}"
