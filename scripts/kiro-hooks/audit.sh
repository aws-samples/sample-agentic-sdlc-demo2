#!/usr/bin/env bash
# PostToolUse audit (Req 16.4 / 14.4): append a non-sensitive record of each
# state-changing platform tool call. Never records tokens or secrets. Non-blocking.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${AIDLC_HOOK_PYTHON:-python3}"
LOG="${AIDLC_AUDIT_LOG:-${AIDLC_STATE_DIR:-$(python3 -c 'import tempfile,os;print(os.path.join(tempfile.gettempdir(),"aidlc-mcp-state"))')}/audit.log}"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
LINE="$(HOOK_DIR="$DIR" "$PY" -c "
import sys,os,json,time
sys.path.insert(0, os.environ['HOOK_DIR'])
import _common as c
d = c.load_stdin()
tool = c.find_key(d,'tool_name') or c.find_key(d,'toolName') or c.find_key(d,'name') or 'unknown'
uuid = c.find_key(d,'project_uuid') or ''
persona = os.environ.get('AIDLC_PERSONA_USERNAME','') or os.environ.get('AIDLC_PERSONA','')
print(json.dumps({'ts':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'tool':tool,'project_uuid':uuid,'persona':persona}))
" 2>/dev/null || true)"
[ -n "$LINE" ] && printf '%s\n' "$LINE" >> "$LOG"
exit 0
