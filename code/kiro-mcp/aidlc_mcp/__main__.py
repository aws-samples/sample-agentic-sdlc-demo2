"""Stdio entrypoint: ``python -m aidlc_mcp``.

Fails fast with a clear, non-sensitive message when configuration is missing or
authentication cannot be established, then serves the MCP tools over stdio.
"""

from __future__ import annotations

import sys

from .errors import AuthError, ConfigError


def main() -> int:
    try:
        from .server import create_server

        server = create_server()
    except ConfigError as e:
        print(f"[aidlc-mcp] configuration error: {e}", file=sys.stderr)
        return 2
    except AuthError as e:
        print(f"[aidlc-mcp] authentication error: {e}", file=sys.stderr)
        return 3
    except ModuleNotFoundError as e:
        print(f"[aidlc-mcp] missing dependency: {e}. Install with "
              f"'pip install mcp boto3'.", file=sys.stderr)
        return 4
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
