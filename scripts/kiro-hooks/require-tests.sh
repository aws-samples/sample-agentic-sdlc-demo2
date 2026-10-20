#!/usr/bin/env bash
# PreToolUse gate (Req 16.3): block submit_implementation unless the project's
# tests pass locally. Deterministic: runs the test command at gate time.
set -uo pipefail
CMD="${AIDLC_TEST_COMMAND:-}"
if [ -z "$CMD" ]; then
  if [ -f package.json ]; then CMD="npm test"
  elif [ -f pyproject.toml ] || [ -f requirements.txt ] || [ -f setup.py ]; then CMD="python3 -m pytest -q"
  else
    echo "No recognized test suite found; allowing submit (set AIDLC_TEST_COMMAND to enforce)." >&2
    exit 0
  fi
fi
echo "Running tests before submit: $CMD" >&2
OUT="$(bash -c "$CMD" 2>&1)"; RC=$?
if [ "$RC" -ne 0 ]; then
  echo "Tests failed (exit $RC) — submit blocked. Fix and retry. Last output:" >&2
  printf '%s\n' "$OUT" | tail -n 20 >&2
  exit 2
fi
exit 0
