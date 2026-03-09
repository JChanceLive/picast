#!/usr/bin/env python3
"""Validate a PiCast AI Autopilot taste profile JSON.

Shared validator for Mac (after Opus generation) and Pi (on upload).
Checks structure, required keys, and value constraints.

Usage:
    ./validate-profile.py profile.json       # Validate a file
    cat profile.json | ./validate-profile.py  # Validate from stdin
    ./validate-profile.py --sample            # Print a minimal valid sample

Exit codes:
    0 = valid profile
    1 = invalid profile (errors printed to stderr)
    2 = usage error (no input)
"""

import json
import sys
from datetime import datetime

REQUIRED_TOP_KEYS = {"version", "generated_at", "global_preferences", "block_strategies"}
REQUIRED_PREFS_KEYS = {"genre_weights"}


def validate(profile: dict) -> list[str]:
    """Validate a taste profile dict. Returns list of error strings (empty = valid)."""
    errors = []

    # Top-level keys
    missing = REQUIRED_TOP_KEYS - set(profile.keys())
    if missing:
        errors.append(f"Missing required keys: {sorted(missing)}")
        return errors  # Can't continue without structure

    # version must be a positive integer
    version = profile["version"]
    if not isinstance(version, int) or version < 1:
        errors.append(f"version must be a positive integer, got: {version!r}")

    # generated_at must be a parseable ISO 8601 timestamp
    generated_at = profile["generated_at"]
    if not isinstance(generated_at, str):
        errors.append(f"generated_at must be a string, got: {type(generated_at).__name__}")
    else:
        try:
            datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"generated_at is not valid ISO 8601: {generated_at!r}")

    # global_preferences must be a dict with genre_weights
    prefs = profile["global_preferences"]
    if not isinstance(prefs, dict):
        errors.append(f"global_preferences must be a dict, got: {type(prefs).__name__}")
    else:
        prefs_missing = REQUIRED_PREFS_KEYS - set(prefs.keys())
        if prefs_missing:
            errors.append(f"global_preferences missing keys: {sorted(prefs_missing)}")
        else:
            gw = prefs["genre_weights"]
            if not isinstance(gw, dict) or len(gw) == 0:
                errors.append("genre_weights must be a non-empty dict")
            else:
                for tag, weight in gw.items():
                    if not isinstance(weight, (int, float)):
                        errors.append(f"genre_weights[{tag!r}] must be numeric, got: {type(weight).__name__}")
                    elif not (0 <= weight <= 10):
                        errors.append(f"genre_weights[{tag!r}] out of range [0,10]: {weight}")

    # block_strategies must be a non-empty dict
    strategies = profile["block_strategies"]
    if not isinstance(strategies, dict):
        errors.append(f"block_strategies must be a dict, got: {type(strategies).__name__}")
    elif len(strategies) == 0:
        errors.append("block_strategies must have at least one block")
    else:
        for block_name, strategy in strategies.items():
            if not isinstance(strategy, dict):
                errors.append(f"block_strategies[{block_name!r}] must be a dict")

    # discovery_queries (optional but must be dict of string lists if present)
    if "discovery_queries" in profile:
        dq = profile["discovery_queries"]
        if not isinstance(dq, dict):
            errors.append(f"discovery_queries must be a dict, got: {type(dq).__name__}")
        else:
            for block_name, queries in dq.items():
                if not isinstance(queries, list):
                    errors.append(f"discovery_queries[{block_name!r}] must be a list")
                elif not all(isinstance(q, str) for q in queries):
                    errors.append(f"discovery_queries[{block_name!r}] must contain only strings")

    return errors


SAMPLE_PROFILE = {
    "version": 1,
    "generated_at": "2026-03-09T06:00:00Z",
    "global_preferences": {
        "genre_weights": {
            "ambient": 3.0,
            "jazz": 2.5,
            "lo-fi": 2.0,
            "classical": 1.5,
        },
        "preferred_duration_range": [1800, 7200],
        "avoid_tags": [],
    },
    "block_strategies": {
        "morning-foundation": {
            "mood": "calm",
            "energy": "low-to-medium",
            "genres": ["ambient", "classical"],
        },
        "creation-stack": {
            "mood": "focused",
            "energy": "medium",
            "genres": ["lo-fi", "jazz"],
        },
    },
    "discovery_queries": {
        "morning-foundation": ["relaxing ambient music", "calm morning playlist"],
        "creation-stack": ["lo-fi focus beats", "jazz for coding"],
    },
}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--sample":
        json.dump(SAMPLE_PROFILE, sys.stdout, indent=2)
        print()
        return 0

    # Read from file argument or stdin
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        try:
            with open(filepath) as f:
                raw = f.read()
        except FileNotFoundError:
            print(f"File not found: {filepath}", file=sys.stderr)
            return 2
        except OSError as e:
            print(f"Cannot read file: {e}", file=sys.stderr)
            return 2
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        print("Usage: validate-profile.py <file.json>", file=sys.stderr)
        print("       cat file.json | validate-profile.py", file=sys.stderr)
        print("       validate-profile.py --sample", file=sys.stderr)
        return 2

    # Parse JSON
    try:
        profile = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        return 1

    if not isinstance(profile, dict):
        print(f"Profile must be a JSON object, got: {type(profile).__name__}", file=sys.stderr)
        return 1

    # Validate
    errors = validate(profile)
    if errors:
        print(f"INVALID — {len(errors)} error(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    # Valid
    version = profile["version"]
    genres = len(profile["global_preferences"]["genre_weights"])
    blocks = len(profile["block_strategies"])
    print(f"VALID — v{version}, {genres} genres, {blocks} blocks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
