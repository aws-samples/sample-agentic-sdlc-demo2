"""Shared helpers for the Kiro enforcement-hook scripts.

Reads the PreToolUse/PostToolUse JSON from stdin and locates fields robustly
regardless of the exact envelope Kiro uses (recursively searches for keys), so
the hooks keep working across Kiro versions. Never emits secrets.
"""
import json
import os
import re
import sys
import tempfile


def load_stdin():
    try:
        return json.loads(sys.stdin.read() or "{}")
    except (ValueError, TypeError):
        return {}


def find_key(obj, key):
    """Depth-first search for the first value of ``key`` anywhere in ``obj``."""
    if isinstance(obj, dict):
        if key in obj and obj[key] not in (None, ""):
            return obj[key]
        for v in obj.values():
            found = find_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_key(v, key)
            if found is not None:
                return found
    return None


def state_dir():
    return os.environ.get("AIDLC_STATE_DIR") or os.path.join(
        tempfile.gettempdir(), "aidlc-mcp-state"
    )


def read_marker(project_uuid):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", project_uuid or "unknown")
    path = os.path.join(state_dir(), "projects", f"{safe}.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return {}
