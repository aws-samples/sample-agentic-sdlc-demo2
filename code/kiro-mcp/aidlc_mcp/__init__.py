"""Platform MCP server for the Agentic SDLC Transformation platform.

A local (stdio) MCP server that exposes the platform's control plane (Project
API) and artifact store (S3) as MCP tools, so a persona can drive the platform
directly from the Kiro IDE. See ``.kiro/specs/kiro-native-platform/`` for the
requirements, design, and task plan.

The package is intentionally dependency-light at import time: heavy deps
(``boto3`` for Cognito/S3, the ``mcp`` SDK for the stdio transport) are imported
lazily so the core logic (config, auth policy, API client, tools) is unit
testable with fakes and only stdlib installed.
"""

__version__ = "0.1.0"
