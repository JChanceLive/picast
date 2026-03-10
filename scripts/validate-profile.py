#!/usr/bin/env python3
"""Validate a PiCast AI Autopilot taste profile JSON (v2).

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

REQUIRED_TOP_KEYS = {"version", "generated_at", "global_preferences", "energy_profiles"}
REQUIRED_PREFS_KEYS = {"genre_weights"}
REQUIRED_ENERGY_PROFILES = {"chill", "focus", "vibes"}


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

    # energy_profiles must contain chill, focus, vibes
    profiles = profile["energy_profiles"]
    if not isinstance(profiles, dict):
        errors.append(f"energy_profiles must be a dict, got: {type(profiles).__name__}")
    else:
        ep_missing = REQUIRED_ENERGY_PROFILES - set(profiles.keys())
        if ep_missing:
            errors.append(f"energy_profiles missing required profiles: {sorted(ep_missing)}")
        for ep_name, ep_data in profiles.items():
            if not isinstance(ep_data, dict):
                errors.append(f"energy_profiles[{ep_name!r}] must be a dict")

    # creator_affinity (optional but must be dict of floats if present)
    if "creator_affinity" in profile:
        ca = profile["creator_affinity"]
        if not isinstance(ca, dict):
            errors.append(f"creator_affinity must be a dict, got: {type(ca).__name__}")
        else:
            for creator, weight in ca.items():
                if not isinstance(weight, (int, float)):
                    errors.append(f"creator_affinity[{creator!r}] must be numeric")
                elif not (0 <= weight <= 2):
                    errors.append(f"creator_affinity[{creator!r}] out of range [0,2]: {weight}")

    # avoid_patterns (optional but must be list of strings if present)
    if "avoid_patterns" in profile:
        ap = profile["avoid_patterns"]
        if not isinstance(ap, list):
            errors.append(f"avoid_patterns must be a list, got: {type(ap).__name__}")
        elif not all(isinstance(p, str) for p in ap):
            errors.append("avoid_patterns must contain only strings")

    # discovery_queries (optional but must be list of strings if present)
    if "discovery_queries" in profile:
        dq = profile["discovery_queries"]
        if not isinstance(dq, list):
            errors.append(f"discovery_queries must be a list, got: {type(dq).__name__}")
        elif not all(isinstance(q, str) for q in dq):
            errors.append("discovery_queries must contain only strings")

    return errors


SAMPLE_PROFILE = {
    "version": 2,
    "generated_at": "2026-03-10T06:00:00Z",
    "global_preferences": {
        "genre_weights": {
            "ambient": 0.9,
            "jazz": 0.8,
            "lo-fi": 0.7,
            "classical": 0.6,
        },
        "preferred_duration_range": [1800, 7200],
    },
    "energy_profiles": {
        "chill": {
            "genres": ["ambient", "classical"],
            "max_duration": 7200,
            "tempo": "slow",
            "description": "Relaxing background content for evening wind-down",
        },
        "focus": {
            "genres": ["lo-fi", "jazz"],
            "max_duration": 5400,
            "tempo": "steady",
            "description": "Non-distracting content for deep work sessions",
        },
        "vibes": {
            "genres": ["jazz", "lo-fi", "ambient"],
            "max_duration": 3600,
            "tempo": "any",
            "description": "Engaging variety content for casual browsing",
        },
    },
    "creator_affinity": {
        "Chillhop Music": 1.5,
        "Cafe Music BGM channel": 1.3,
    },
    "avoid_patterns": ["asmr", "mukbang", "prank"],
    "discovery_queries": [
        "relaxing ambient music 2026",
        "lo-fi focus beats for coding",
        "jazz cafe background music",
        "classical piano study music",
    ],
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
    energy = len(profile["energy_profiles"])
    print(f"VALID — v{version}, {genres} genres, {energy} energy profiles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
