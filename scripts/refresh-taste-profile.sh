#!/usr/bin/env bash
# refresh-taste-profile.sh — Daily taste profile generation for PiCast AI Autopilot
#
# Pulls viewing data from PiCast API, sends to Sonnet 4.5 for analysis,
# validates the output, and pushes the new profile back to PiCast.
#
# Usage:
#   ./refresh-taste-profile.sh              # Full run (API call + push)
#   ./refresh-taste-profile.sh --dry-run    # Pull data, show prompt, skip API call
#   ./refresh-taste-profile.sh --verbose    # Extra logging
#   ./refresh-taste-profile.sh --renew      # Reset 30-day trial for another cycle
#
# Requires:
#   - ANTHROPIC_API_KEY environment variable
#   - PiCast server running (default: picast.local:5050)
#   - curl, jq
#
# Scheduled via launchd: com.picast.refresh-taste (daily at 6:00 AM)

set -euo pipefail

# --- Configuration ---
PICAST_HOST="${PICAST_HOST:-picast.local}"
PICAST_PORT="${PICAST_PORT:-5050}"
PICAST_BASE="http://${PICAST_HOST}:${PICAST_PORT}"
PIPULSE_HOST="${PIPULSE_HOST:-pipulse.local}"
PIPULSE_PORT="${PIPULSE_PORT:-5055}"
PIPULSE_BASE="http://${PIPULSE_HOST}:${PIPULSE_PORT}"
MODEL="${PICAST_MODEL:-claude-sonnet-4-5-20250929}"
MAX_TOKENS=2000
HISTORY_LIMIT=50
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMPT_TEMPLATE="${SCRIPT_DIR}/taste-prompt.md"
VALIDATOR="${SCRIPT_DIR}/validate-profile.py"
LOG_DIR="${HOME}/.picast"
LOG_FILE="${LOG_DIR}/refresh-log.json"
CACHE_DIR="${LOG_DIR}/taste-cache"

# --- Flags ---
DRY_RUN=false
VERBOSE=false

RENEW=false

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --verbose) VERBOSE=true ;;
        --renew) RENEW=true ;;
        *) echo "Unknown flag: $arg"; exit 1 ;;
    esac
done

# --- Helpers ---
log() { echo "[$(date '+%H:%M:%S')] $*"; }
debug() { [[ "$VERBOSE" == true ]] && log "DEBUG: $*" || true; }
die() { log "ERROR: $*" >&2; alert_failure "$*"; exit 1; }

retry_curl() {
    # Retry a curl command with exponential backoff.
    # Usage: retry_curl <max_retries> <base_delay_secs> <curl_args...>
    local max_retries="$1"; shift
    local base_delay="$1"; shift
    local attempt=0
    local delay="$base_delay"

    while true; do
        if curl "$@" 2>/dev/null; then
            return 0
        fi
        attempt=$((attempt + 1))
        if [[ $attempt -ge $max_retries ]]; then
            return 1
        fi
        log "Retry $attempt/$max_retries in ${delay}s..."
        sleep "$delay"
        delay=$((delay * 2))
    done
}

alert_failure() {
    # Send a Pushover alert via PiPulse on failure (best-effort, don't block on failure)
    local message="$1"
    curl -sf --connect-timeout 5 --max-time 10 \
        -X POST "${PIPULSE_BASE}/api/notify" \
        -H "Content-Type: application/json" \
        -d "$(jq -n --arg msg "[PiCast Taste] $message" --arg t "PiCast Autopilot" \
            '{message: $msg, title: $t, priority: 0, sound: "gamelan"}')" \
        >/dev/null 2>&1 || true
}

log_result() {
    # Append a JSON entry to the refresh log
    local status="$1"
    local detail="${2:-}"
    local cost="${3:-0}"
    local baseline="${4:-null}"
    mkdir -p "$LOG_DIR"
    local entry
    entry=$(jq -n \
        --arg ts "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
        --arg status "$status" \
        --arg detail "$detail" \
        --arg model "$MODEL" \
        --argjson cost "$cost" \
        --argjson dry_run "$DRY_RUN" \
        --argjson baseline "$baseline" \
        '{timestamp: $ts, status: $status, detail: $detail, model: $model, cost: $cost, dry_run: $dry_run, baseline: $baseline}')
    # Append to JSONL log file
    echo "$entry" >> "$LOG_FILE"
    debug "Logged: $status"
}

# --- Preflight Checks ---
command -v curl >/dev/null || die "curl not found"
command -v jq >/dev/null || die "jq not found"

# Load .env for headless/launchd runs (API key, overrides)
[[ -f "${HOME}/.picast/.env" ]] && source "${HOME}/.picast/.env"

# --- 30-Day Trial Guard ---
TRIAL_FILE="${LOG_DIR}/trial-start"
TRIAL_DAYS="${PICAST_TRIAL_DAYS:-30}"

if [[ "$RENEW" == true ]]; then
    date '+%Y-%m-%d' > "$TRIAL_FILE"
    log "Trial renewed for ${TRIAL_DAYS} days (expires $(date -v+${TRIAL_DAYS}d '+%Y-%m-%d'))"
fi

if [[ ! -f "$TRIAL_FILE" ]]; then
    # First run — start the trial
    mkdir -p "$LOG_DIR"
    date '+%Y-%m-%d' > "$TRIAL_FILE"
    log "Trial started: ${TRIAL_DAYS} days (expires $(date -v+${TRIAL_DAYS}d '+%Y-%m-%d'))"
elif [[ "$DRY_RUN" == false ]]; then
    TRIAL_START=$(cat "$TRIAL_FILE")
    TRIAL_START_EPOCH=$(date -j -f '%Y-%m-%d' "$TRIAL_START" '+%s' 2>/dev/null || echo 0)
    NOW_EPOCH=$(date '+%s')
    DAYS_ELAPSED=$(( (NOW_EPOCH - TRIAL_START_EPOCH) / 86400 ))
    if [[ $DAYS_ELAPSED -ge $TRIAL_DAYS ]]; then
        log "Trial expired after ${DAYS_ELAPSED} days (started ${TRIAL_START})"
        log "Run with --renew to extend for another ${TRIAL_DAYS} days"
        alert_failure "Trial expired (${DAYS_ELAPSED}d). Run: refresh-taste-profile.sh --renew"
        log_result "trial_expired" "Started ${TRIAL_START}, ${DAYS_ELAPSED} days elapsed"
        exit 0
    fi
    DAYS_LEFT=$((TRIAL_DAYS - DAYS_ELAPSED))
    debug "Trial: day ${DAYS_ELAPSED}/${TRIAL_DAYS} (${DAYS_LEFT} days left)"
    # Warn at 5 days remaining
    if [[ $DAYS_LEFT -le 5 ]]; then
        log "WARNING: Trial expires in ${DAYS_LEFT} days — run --renew to extend"
    fi
fi

# --- Monthly Cost Cap (before API key check — no key needed if capped) ---
MONTHLY_CAP="${PICAST_MONTHLY_CAP:-5.00}"
if [[ -f "$LOG_FILE" && "$DRY_RUN" == false ]]; then
    CURRENT_MONTH=$(date '+%Y-%m')
    MONTH_SPEND=$(jq -r "select(.timestamp | startswith(\"${CURRENT_MONTH}\")) | .cost" "$LOG_FILE" 2>/dev/null \
        | awk '{s+=$1} END {printf "%.4f", s+0}')
    CAP_EXCEEDED=$(echo "$MONTH_SPEND >= $MONTHLY_CAP" | bc 2>/dev/null || echo 0)
    if [[ "$CAP_EXCEEDED" -eq 1 ]]; then
        log "Monthly cost cap reached (\$${MONTH_SPEND} / \$${MONTHLY_CAP}), skipping"
        log_result "skipped" "Monthly cost cap: \$${MONTH_SPEND} / \$${MONTHLY_CAP}"
        exit 0
    fi
    debug "Monthly spend: \$${MONTH_SPEND} / \$${MONTHLY_CAP}"
fi

if [[ "$DRY_RUN" == false ]]; then
    [[ -n "${ANTHROPIC_API_KEY:-}" ]] || die "ANTHROPIC_API_KEY not set"
fi

# Check PiCast is reachable (3 retries with backoff: 2s, 4s, 8s)
log "Checking PiCast at ${PICAST_BASE}..."
if ! retry_curl 3 2 -sf --connect-timeout 5 "${PICAST_BASE}/api/health" -o /dev/null; then
    log_result "error" "PiCast unreachable at ${PICAST_BASE} after 3 retries"
    die "PiCast unreachable at ${PICAST_BASE} after 3 retries"
fi
log "PiCast is up."

# --- Step 1: Pull Data from PiCast ---
log "Pulling viewing data..."
mkdir -p "$CACHE_DIR"

# Play history (last 50 entries covers ~48h)
HISTORY=$(curl -sf "${PICAST_BASE}/api/autoplay/history?limit=${HISTORY_LIMIT}" 2>/dev/null) \
    || die "Failed to fetch play history"
debug "History entries: $(echo "$HISTORY" | jq 'length')"

# Pool overview (block list + mappings)
POOL_OVERVIEW=$(curl -sf "${PICAST_BASE}/api/autoplay" 2>/dev/null) \
    || die "Failed to fetch pool overview"
debug "Pool overview fetched"

# Fetch full video details for each block
BLOCK_NAMES=$(echo "$POOL_OVERVIEW" | jq -r '.pools[].block_name' 2>/dev/null)
POOL_VIDEOS="{"
first=true
for block in $BLOCK_NAMES; do
    videos=$(curl -sf "${PICAST_BASE}/api/autoplay/pool/${block}" 2>/dev/null) || continue
    count=$(echo "$videos" | jq 'length' 2>/dev/null || echo 0)
    debug "  ${block}: ${count} videos"
    if [[ "$first" == true ]]; then first=false; else POOL_VIDEOS+=","; fi
    POOL_VIDEOS+="\"${block}\":${videos}"
done
POOL_VIDEOS+="}"
debug "Full pool data fetched"

# Autopilot status (includes block info)
STATUS=$(curl -sf "${PICAST_BASE}/api/autopilot/status" 2>/dev/null) \
    || die "Failed to fetch autopilot status"
debug "Autopilot status fetched"

# Feedback signals from behavioral summary endpoint
FEEDBACK=$(curl -sf "${PICAST_BASE}/api/autoplay/feedback-summary?days=7" 2>/dev/null) \
    || FEEDBACK="[]"
debug "Feedback summary fetched"

# Build effectiveness baseline from feedback + history (for cross-generation comparison)
BASELINE=$(echo "$FEEDBACK" | jq '{
    completion_rates: [.block_completion_rates[]? | {block: .block_name, pct: .completion_pct}],
    rating_velocity: .rating_velocity,
    discovery: .discovery_effectiveness.discovery,
    total_plays: ([.block_completion_rates[]?.plays] | add // 0),
    total_completions: ([.block_completion_rates[]?.completions] | add // 0)
}' 2>/dev/null || echo "null")
debug "Baseline captured"

# Save raw data for debugging
echo "$HISTORY" > "${CACHE_DIR}/history.json"
echo "$POOL_VIDEOS" > "${CACHE_DIR}/pools.json"
echo "$STATUS" > "${CACHE_DIR}/status.json"
echo "$FEEDBACK" > "${CACHE_DIR}/feedback.json"
debug "Raw data cached to ${CACHE_DIR}/"

# --- Step 2: Build Prompt ---
log "Building prompt for ${MODEL}..."

[[ -f "$PROMPT_TEMPLATE" ]] || die "Prompt template not found: ${PROMPT_TEMPLATE}"

# Format history for readability (compact: video_id, title, block, rating, played_at)
HISTORY_FORMATTED=$(echo "$HISTORY" | jq -r '
    [.[] | {
        video_id,
        title: (.title // "untitled"),
        block: .block_name,
        rating: (.rating // 0),
        played_at,
        completed: (.completed // 0),
        stop_reason: (.stop_reason // "")
    }]' 2>/dev/null || echo "[]")

# Format pool data (per-block video details with ratings, tags, skips)
POOL_FORMATTED=$(echo "$POOL_VIDEOS" | jq '{
    blocks: [to_entries[] | {
        block: .key,
        video_count: (.value | length),
        liked: ([.value[] | select(.rating == 1)] | length),
        disliked: ([.value[] | select(.rating == -1)] | length),
        avg_skips: (if (.value | length) > 0 then ([.value[] | .skip_count] | add / length) else 0 end),
        tags: ([.value[] | .tags // "" | split(",") | .[] | select(. != "")] | unique),
        videos: [.value[] | {
            video_id,
            title: (.title // "untitled"),
            rating,
            skip_count: (.skip_count // 0),
            completion_count: (.completion_count // 0),
            play_count: (.play_count // 0),
            tags: (.tags // ""),
            duration: (.duration // 0)
        }]
    }]
}' 2>/dev/null || echo '{"blocks":[]}')

# Format block schedule
BLOCK_FORMATTED=$(echo "$STATUS" | jq '{
    current_block: .current_block,
    mode: .mode,
    stale: .stale,
    profile_version: .profile.version
}' 2>/dev/null || echo '{}')

# Block-to-mood mapping (connects time blocks to energy profiles)
BLOCK_MOODS=$(cat <<'MOODS'
{
  "morning-foundation": {"mood": "chill", "context": "Early morning routine, gentle start to the day"},
  "creation-stack": {"mood": "focus", "context": "Deep creative work session, needs steady non-distracting content"},
  "pro-gears": {"mood": "focus", "context": "Professional development and learning blocks"},
  "midday-reset": {"mood": "vibes", "context": "Lunch break, casual browsing energy"},
  "sys-gears": {"mood": "focus", "context": "System maintenance and technical work"},
  "evening-transition": {"mood": "chill", "context": "Winding down from work, relaxing content"},
  "night-restoration": {"mood": "chill", "context": "Late evening, ambient background viewing"},
  "night-lab": {"mood": "focus", "context": "Late-night tinkering and experimentation"}
}
MOODS
)
debug "Block-mood mapping loaded"

# Read prompt template and inject data
PROMPT=$(cat "$PROMPT_TEMPLATE")
PROMPT="${PROMPT//\{\{PLAY_HISTORY\}\}/$HISTORY_FORMATTED}"
PROMPT="${PROMPT//\{\{POOL_SUMMARY\}\}/$POOL_FORMATTED}"
PROMPT="${PROMPT//\{\{BLOCK_SCHEDULE\}\}/$BLOCK_FORMATTED}"
PROMPT="${PROMPT//\{\{FEEDBACK_SIGNALS\}\}/$FEEDBACK}"
PROMPT="${PROMPT//\{\{BLOCK_MOODS\}\}/$BLOCK_MOODS}"

# Save assembled prompt
echo "$PROMPT" > "${CACHE_DIR}/assembled-prompt.md"
debug "Prompt assembled ($(echo "$PROMPT" | wc -c | tr -d ' ') bytes)"

# --- Dry Run Exit ---
if [[ "$DRY_RUN" == true ]]; then
    log "=== DRY RUN ==="
    log "Would call ${MODEL} with $(echo "$PROMPT" | wc -c | tr -d ' ') byte prompt"
    log "Assembled prompt saved to: ${CACHE_DIR}/assembled-prompt.md"
    log "Cached data in: ${CACHE_DIR}/"
    echo ""
    echo "--- Prompt Preview (first 80 lines) ---"
    head -80 "${CACHE_DIR}/assembled-prompt.md"
    echo ""
    echo "--- Pool Summary ---"
    echo "$POOL_FORMATTED" | jq '.blocks[] | {block, video_count, liked, disliked, tags}' 2>/dev/null || echo "(no pool data)"
    log_result "dry_run" "Prompt assembled, API call skipped"
    exit 0
fi

# --- Step 3: Call Anthropic API (with 1 retry on invalid JSON) ---

call_api() {
    # Calls Opus API and extracts profile text. Sets PROFILE_RAW, INPUT_TOKENS, OUTPUT_TOKENS, COST.
    log "Calling ${MODEL}..."

    # Build API request
    local api_body
    api_body=$(jq -n \
        --arg model "$MODEL" \
        --argjson max_tokens "$MAX_TOKENS" \
        --arg prompt "$PROMPT" \
        '{
            model: $model,
            max_tokens: $max_tokens,
            messages: [{
                role: "user",
                content: $prompt
            }]
        }')

    local http_code
    API_RESPONSE=$(curl -s \
        --connect-timeout 30 \
        --max-time 120 \
        -w "\n%{http_code}" \
        -H "Content-Type: application/json" \
        -H "x-api-key: ${ANTHROPIC_API_KEY}" \
        -H "anthropic-version: 2023-06-01" \
        -d "$api_body" \
        "https://api.anthropic.com/v1/messages") \
        || { log "curl failed (network error)"; return 1; }

    http_code=$(echo "$API_RESPONSE" | tail -1)
    API_RESPONSE=$(echo "$API_RESPONSE" | sed '$d')

    echo "$API_RESPONSE" > "${CACHE_DIR}/api-response.json"

    if [[ "$http_code" != "200" ]]; then
        local api_error
        api_error=$(echo "$API_RESPONSE" | jq -r '.error.message // .error.type // "unknown"' 2>/dev/null || echo "unknown")
        log "API returned HTTP ${http_code}: ${api_error}"
        return 1
    fi

    PROFILE_RAW=$(echo "$API_RESPONSE" | jq -r '.content[0].text' 2>/dev/null) \
        || return 1

    INPUT_TOKENS=$(echo "$API_RESPONSE" | jq -r '.usage.input_tokens // 0' 2>/dev/null)
    OUTPUT_TOKENS=$(echo "$API_RESPONSE" | jq -r '.usage.output_tokens // 0' 2>/dev/null)
    # Sonnet 4.5: $3/M input, $15/M output
    COST=$(echo "scale=4; ($INPUT_TOKENS * 3 + $OUTPUT_TOKENS * 15) / 1000000" | bc 2>/dev/null || echo "0")
    log "Tokens: ${INPUT_TOKENS} in / ${OUTPUT_TOKENS} out (~\$${COST})"
    return 0
}

validate_profile_json() {
    # Strip code fences and validate via validate-profile.py. Sets PROFILE_JSON.
    PROFILE_JSON=$(echo "$PROFILE_RAW" | sed '/^```/d' | sed '/^$/d')

    if echo "$PROFILE_JSON" | python3 "$VALIDATOR" >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

# First attempt
TOTAL_COST=0
if ! call_api; then
    log_result "error" "Anthropic API call failed"
    die "Anthropic API call failed (check ANTHROPIC_API_KEY and network)"
fi
TOTAL_COST=$(echo "scale=4; $TOTAL_COST + ${COST:-0}" | bc 2>/dev/null || echo "0")

# Validate
log "Validating profile JSON..."
if ! validate_profile_json; then
    # Retry once on invalid JSON
    log "Invalid JSON from model — retrying once..."
    echo "$PROFILE_RAW" > "${CACHE_DIR}/invalid-response-attempt1.txt"

    if ! call_api; then
        log_result "error" "Anthropic API retry failed" "$TOTAL_COST"
        die "Anthropic API retry failed"
    fi
    TOTAL_COST=$(echo "scale=4; $TOTAL_COST + ${COST:-0}" | bc 2>/dev/null || echo "0")

    if ! validate_profile_json; then
        echo "$PROFILE_RAW" > "${CACHE_DIR}/invalid-response-attempt2.txt"
        log_result "error" "Invalid JSON after 2 attempts" "$TOTAL_COST"
        die "Model returned invalid JSON after 2 attempts. Raw output saved to ${CACHE_DIR}/"
    fi
fi

COST="$TOTAL_COST"

# Get counts for logging
GENRE_COUNT=$(echo "$PROFILE_JSON" | jq '.global_preferences.genre_weights | length' 2>/dev/null || echo 0)
ENERGY_COUNT=$(echo "$PROFILE_JSON" | jq '.energy_profiles | length' 2>/dev/null || echo 0)
DISCOVERY_COUNT=$(echo "$PROFILE_JSON" | jq '.discovery_queries | length' 2>/dev/null || echo 0)

# Save validated profile
echo "$PROFILE_JSON" > "${CACHE_DIR}/validated-profile.json"
log "Profile valid: ${GENRE_COUNT} genres, ${ENERGY_COUNT} energy profiles, ${DISCOVERY_COUNT} discovery queries"
if [[ "$DISCOVERY_COUNT" -eq 0 ]]; then
    log "WARNING: No discovery queries in profile — autopilot discovery will be limited"
fi

# --- Step 5: Push to PiCast (3 retries with backoff) ---
log "Pushing profile to PiCast..."

GENERATED_AT=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

# Build upload payload
UPLOAD_BODY=$(jq -n \
    --argjson profile "$PROFILE_JSON" \
    --arg generated_at "$GENERATED_AT" \
    '{profile: $profile, generated_at: $generated_at}')

UPLOAD_RESPONSE=$(retry_curl 3 2 -sf \
    --connect-timeout 10 \
    --max-time 30 \
    -X POST \
    -H "Content-Type: application/json" \
    -d "$UPLOAD_BODY" \
    "${PICAST_BASE}/api/autopilot/profile") \
    || {
        echo "$PROFILE_JSON" > "${CACHE_DIR}/failed-upload-profile.json"
        log_result "error" "Profile push failed after 3 retries (profile saved locally)" "$COST"
        die "Failed to push profile to PiCast after 3 retries. Saved to ${CACHE_DIR}/failed-upload-profile.json"
    }

# Verify upload succeeded
UPLOAD_OK=$(echo "$UPLOAD_RESPONSE" | jq -r '.ok // false' 2>/dev/null)
if [[ "$UPLOAD_OK" != "true" ]]; then
    UPLOAD_ERR=$(echo "$UPLOAD_RESPONSE" | jq -r '.error // "unknown"' 2>/dev/null)
    log_result "error" "Upload rejected: ${UPLOAD_ERR}" "$COST"
    die "PiCast rejected profile: ${UPLOAD_ERR}"
fi

PROFILE_VERSION=$(echo "$UPLOAD_RESPONSE" | jq -r '.profile.version // "?"' 2>/dev/null)
log "Profile uploaded successfully (v${PROFILE_VERSION})"

# --- Step 6: Log Result ---
log_result "success" "v${PROFILE_VERSION}: ${GENRE_COUNT} genres, ${ENERGY_COUNT} energy profiles, ${DISCOVERY_COUNT} discovery queries" "$COST" "$BASELINE"
log "Done. Cost: ~\$${COST}"
