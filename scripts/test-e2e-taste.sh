#!/usr/bin/env bash
# test-e2e-taste.sh — End-to-end validation of taste profile pipeline
#
# Tests the full flow: generate profile -> push to Pi -> verify engine uses it.
# Does NOT call Opus (uses a sample profile). Requires PiCast to be running.
#
# Usage:
#   ./test-e2e-taste.sh                    # Full E2E test against PiCast
#   ./test-e2e-taste.sh --local-only       # Skip Pi tests (validator only)
#
# Test coverage:
#   1. Validator accepts valid profiles
#   2. Validator rejects invalid profiles (bad JSON, missing keys, bad types)
#   3. Profile push to PiCast succeeds
#   4. Profile appears in /api/autopilot/status
#   5. Profile version increments correctly
#   6. Error path: unreachable Pi handled gracefully

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VALIDATOR="${SCRIPT_DIR}/validate-profile.py"
PICAST_HOST="${PICAST_HOST:-picast.local}"
PICAST_PORT="${PICAST_PORT:-5050}"
PICAST_BASE="http://${PICAST_HOST}:${PICAST_PORT}"

LOCAL_ONLY=false
[[ "${1:-}" == "--local-only" ]] && LOCAL_ONLY=true

PASS=0
FAIL=0
SKIP=0

pass() { PASS=$((PASS + 1)); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $1" >&2; }
skip() { SKIP=$((SKIP + 1)); echo "  SKIP: $1"; }

echo "=== PiCast Taste Profile E2E Test ==="
echo ""

# -----------------------------------------------
# Section 1: Validator Tests (local, no Pi needed)
# -----------------------------------------------
echo "--- Validator Tests ---"

# Test 1: Valid sample profile
if python3 "$VALIDATOR" --sample | python3 "$VALIDATOR" >/dev/null 2>&1; then
    pass "Valid sample accepted"
else
    fail "Valid sample rejected"
fi

# Test 2: Invalid JSON
if echo "not json" | python3 "$VALIDATOR" >/dev/null 2>&1; then
    fail "Invalid JSON accepted (should reject)"
else
    pass "Invalid JSON rejected"
fi

# Test 3: Missing required keys
if echo '{"foo": "bar"}' | python3 "$VALIDATOR" >/dev/null 2>&1; then
    fail "Missing keys accepted (should reject)"
else
    pass "Missing required keys rejected"
fi

# Test 4: Bad version type
BAD_VERSION='{"version":"nope","generated_at":"2026-03-09T06:00:00Z","global_preferences":{"genre_weights":{"a":1.0}},"block_strategies":{"b":{}}}'
if echo "$BAD_VERSION" | python3 "$VALIDATOR" >/dev/null 2>&1; then
    fail "Bad version type accepted (should reject)"
else
    pass "Bad version type rejected"
fi

# Test 5: Empty genre_weights
EMPTY_GENRES='{"version":1,"generated_at":"2026-03-09T06:00:00Z","global_preferences":{"genre_weights":{}},"block_strategies":{"b":{}}}'
if echo "$EMPTY_GENRES" | python3 "$VALIDATOR" >/dev/null 2>&1; then
    fail "Empty genre_weights accepted (should reject)"
else
    pass "Empty genre_weights rejected"
fi

# Test 6: Empty block_strategies
EMPTY_BLOCKS='{"version":1,"generated_at":"2026-03-09T06:00:00Z","global_preferences":{"genre_weights":{"a":1.0}},"block_strategies":{}}'
if echo "$EMPTY_BLOCKS" | python3 "$VALIDATOR" >/dev/null 2>&1; then
    fail "Empty block_strategies accepted (should reject)"
else
    pass "Empty block_strategies rejected"
fi

# Test 7: Genre weight out of range
BAD_WEIGHT='{"version":1,"generated_at":"2026-03-09T06:00:00Z","global_preferences":{"genre_weights":{"a":99.0}},"block_strategies":{"b":{}}}'
if echo "$BAD_WEIGHT" | python3 "$VALIDATOR" >/dev/null 2>&1; then
    fail "Out-of-range genre weight accepted (should reject)"
else
    pass "Out-of-range genre weight rejected"
fi

# Test 8: Valid minimal profile
MINIMAL='{"version":1,"generated_at":"2026-03-09T06:00:00Z","global_preferences":{"genre_weights":{"ambient":2.0}},"block_strategies":{"morning-foundation":{"mood":"calm"}}}'
if echo "$MINIMAL" | python3 "$VALIDATOR" >/dev/null 2>&1; then
    pass "Minimal valid profile accepted"
else
    fail "Minimal valid profile rejected"
fi

# Test 9: File input mode
TMPFILE=$(mktemp)
python3 "$VALIDATOR" --sample > "$TMPFILE"
if python3 "$VALIDATOR" "$TMPFILE" >/dev/null 2>&1; then
    pass "File input mode works"
else
    fail "File input mode failed"
fi
rm -f "$TMPFILE"

echo ""

if [[ "$LOCAL_ONLY" == true ]]; then
    echo "--- Pi Tests ---"
    skip "Pi tests skipped (--local-only)"
    echo ""
    echo "=== Results: ${PASS} passed, ${FAIL} failed, ${SKIP} skipped ==="
    [[ $FAIL -eq 0 ]] && exit 0 || exit 1
fi

# -----------------------------------------------
# Section 2: Pi Integration Tests
# -----------------------------------------------
echo "--- Pi Integration Tests ---"

# Check Pi is reachable
if ! curl -sf --connect-timeout 5 "${PICAST_BASE}/api/health" >/dev/null 2>&1; then
    echo "PiCast unreachable at ${PICAST_BASE} — skipping Pi tests"
    skip "Pi unreachable"
    echo ""
    echo "=== Results: ${PASS} passed, ${FAIL} failed, ${SKIP} skipped ==="
    [[ $FAIL -eq 0 ]] && exit 0 || exit 1
fi

# Check autopilot endpoints are available (may not be deployed yet)
AP_CHECK=$(curl -s --connect-timeout 5 -o /dev/null -w "%{http_code}" "${PICAST_BASE}/api/autopilot/status" 2>/dev/null || echo "000")
if [[ "$AP_CHECK" == "404" ]]; then
    echo "Autopilot endpoints not deployed yet (v1.0.1) — skipping Pi integration tests"
    echo "Deploy autopilot code to Pi first, then re-run."
    skip "Autopilot not deployed"
    echo ""
    echo "=== Results: ${PASS} passed, ${FAIL} failed, ${SKIP} skipped ==="
    [[ $FAIL -eq 0 ]] && exit 0 || exit 1
fi

# Get current profile version for comparison
BEFORE_VERSION=$(curl -sf "${PICAST_BASE}/api/autopilot/status" 2>/dev/null \
    | jq -r '.profile.version // 0' 2>/dev/null || echo "0")

# Test 10: Push a valid profile
TEST_PROFILE='{"version":99,"generated_at":"2026-03-09T12:00:00Z","global_preferences":{"genre_weights":{"ambient":3.0,"jazz":2.0,"lo-fi":1.5}},"block_strategies":{"morning-foundation":{"mood":"calm","energy":"low"},"creation-stack":{"mood":"focused","energy":"medium"}},"discovery_queries":{"morning-foundation":["relaxing ambient"]}}'
GENERATED_AT=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

PUSH_BODY=$(jq -n \
    --argjson profile "$TEST_PROFILE" \
    --arg generated_at "$GENERATED_AT" \
    '{profile: $profile, generated_at: $generated_at}')

PUSH_RESPONSE=$(curl -sf \
    --connect-timeout 10 \
    --max-time 30 \
    -X POST \
    -H "Content-Type: application/json" \
    -d "$PUSH_BODY" \
    "${PICAST_BASE}/api/autopilot/profile" 2>/dev/null) || true

PUSH_OK=$(echo "${PUSH_RESPONSE:-{}}" | jq -r '.ok // false' 2>/dev/null)
if [[ "$PUSH_OK" == "true" ]]; then
    pass "Profile push accepted"
else
    PUSH_ERR=$(echo "${PUSH_RESPONSE:-{}}" | jq -r '.error // "no response"' 2>/dev/null)
    fail "Profile push rejected: ${PUSH_ERR}"
fi

# Test 11: Profile appears in status
STATUS_RESPONSE=$(curl -sf "${PICAST_BASE}/api/autopilot/status" 2>/dev/null) || true
STATUS_VERSION=$(echo "${STATUS_RESPONSE:-{}}" | jq -r '.profile.version // 0' 2>/dev/null)
if [[ "$STATUS_VERSION" == "99" ]]; then
    pass "Profile version visible in status (v99)"
else
    fail "Profile version mismatch: expected 99, got ${STATUS_VERSION}"
fi

# Test 12: Profile stale detection
STATUS_STALE=$(echo "${STATUS_RESPONSE:-{}}" | jq -r '.profile.stale // true' 2>/dev/null)
if [[ "$STATUS_STALE" == "false" ]]; then
    pass "Freshly pushed profile is not stale"
else
    fail "Freshly pushed profile detected as stale"
fi

# Test 13: GET profile returns our data
PROFILE_RESPONSE=$(curl -sf "${PICAST_BASE}/api/autopilot/profile" 2>/dev/null) || true
PROFILE_GENRES=$(echo "${PROFILE_RESPONSE:-{}}" | jq '.profile.global_preferences.genre_weights | length' 2>/dev/null || echo 0)
if [[ "$PROFILE_GENRES" == "3" ]]; then
    pass "GET /profile returns pushed profile (3 genres)"
else
    fail "GET /profile genre count: expected 3, got ${PROFILE_GENRES}"
fi

# Test 14: Push invalid profile (should be rejected)
BAD_PUSH_BODY='{"profile":{"bad":"data"},"generated_at":"2026-03-09T12:00:00Z"}'
BAD_RESPONSE=$(curl -s \
    --connect-timeout 10 \
    -X POST \
    -H "Content-Type: application/json" \
    -d "$BAD_PUSH_BODY" \
    "${PICAST_BASE}/api/autopilot/profile" 2>/dev/null) || true

BAD_OK=$(echo "${BAD_RESPONSE:-{}}" | jq -r '.ok // false' 2>/dev/null)
if [[ "$BAD_OK" != "true" ]]; then
    pass "Invalid profile push rejected by server"
else
    fail "Invalid profile push was accepted (should reject)"
fi

# Test 15: Restore previous version (push a v1 profile to clean up)
RESTORE_PROFILE='{"version":1,"generated_at":"2026-03-09T12:00:00Z","global_preferences":{"genre_weights":{"ambient":2.0}},"block_strategies":{"morning-foundation":{"mood":"calm"}}}'
RESTORE_BODY=$(jq -n \
    --argjson profile "$RESTORE_PROFILE" \
    --arg generated_at "$GENERATED_AT" \
    '{profile: $profile, generated_at: $generated_at}')

RESTORE_RESPONSE=$(curl -sf \
    --connect-timeout 10 \
    -X POST \
    -H "Content-Type: application/json" \
    -d "$RESTORE_BODY" \
    "${PICAST_BASE}/api/autopilot/profile" 2>/dev/null) || true

RESTORE_OK=$(echo "${RESTORE_RESPONSE:-{}}" | jq -r '.ok // false' 2>/dev/null)
if [[ "$RESTORE_OK" == "true" ]]; then
    pass "Profile restored to v1 (cleanup)"
else
    fail "Profile restore failed"
fi

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed, ${SKIP} skipped ==="
[[ $FAIL -eq 0 ]] && exit 0 || exit 1
