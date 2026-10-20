#!/usr/bin/env bash
# PreToolUse gate (Req 16.2): block a forward advance/handoff until the persona
# has reviewed the project's artifacts, but ONLY for states where a persona
# approves upstream work (config: review-required-states.txt). Never matches
# return_for_changes (Req 16.5). Recoverable: read artifacts, then retry.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${AIDLC_HOOK_PYTHON:-python3}"

INPUT="$(cat)"
UUID="$(printf '%s' "$INPUT" | HOOK_DIR="$DIR" "$PY" -c "import sys,os;sys.path.insert(0,os.environ['HOOK_DIR']);import _common as c;print(c.find_key(c.load_stdin(),'project_uuid') or '')" 2>/dev/null || true)"

if [ -z "$UUID" ]; then
  # Can't identify the project; server-side RBAC remains authoritative. Allow.
  exit 0
fi

STATE="$(HOOK_DIR="$DIR" "$PY" - "$UUID" <<'PYIN' 2>/dev/null || true
import os, sys
sys.path.insert(0, os.environ["HOOK_DIR"])
import _common as c
m = c.read_marker(sys.argv[1])
print((m.get("handoff_state") or "") + "|" + (m.get("artifacts_reviewed_at") or ""))
PYIN
)"
HANDOFF="${STATE%%|*}"
REVIEWED="${STATE#*|}"

REQUIRED_FILE="$DIR/review-required-states.txt"
NEEDS_REVIEW="no"
if [ -f "$REQUIRED_FILE" ] && [ -n "$HANDOFF" ]; then
  while IFS= read -r line; do
    line="${line%%#*}"; line="$(printf '%s' "$line" | tr -d '[:space:]')"
    [ -z "$line" ] && continue
    [ "$line" = "$HANDOFF" ] && NEEDS_REVIEW="yes"
  done < "$REQUIRED_FILE"
fi

if [ "$NEEDS_REVIEW" = "yes" ] && [ -z "$REVIEWED" ]; then
  echo "Review required before approving: read this project's artifacts (list_artifacts / get_artifact) before advancing from '$HANDOFF', then retry." >&2
  exit 2
fi
exit 0
