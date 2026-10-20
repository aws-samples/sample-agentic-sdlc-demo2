#!/bin/bash

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)

set -e

cd "$SCRIPT_DIR"/../code

assets_dir="$SCRIPT_DIR"/../assets

# Make sure the assets directory exists
mkdir -p "$assets_dir"

# ── UI Frontend removed: Kiro IDE is the only frontend (no React build) ──

# ── Workflow Registry (single source of truth) ───────────────
# `workflow_registry.json` is the single canonical Persona/Workflow Registry
# (design Section 3a) and `workflow_registry.py` is its shared loader/validator
# module (design Section 3a-i). With the AgentCore Runtime removed
# (kiro-native-platform design Section 3.9), the Project API Lambda asset
# (cdk/custom-resources/) now holds the SOLE Python copy — it is the canonical
# source, so there is no runtime image to sync from anymore. (The former React/TS
# mirror in code/ui-frontend was removed with the web UI; Kiro IDE is the only
# frontend, and its MCP server keeps its own copy at
# code/kiro-mcp/aidlc_mcp/workflow_registry.json.)

# With AgentCore removed, the gateway tool Lambdas are gone and the
# Project API / webhook Lambdas bundle directly from cdk/custom-resources/ via
# CDK `Code.fromAsset`, so there is nothing left for this script to zip.
echo "package-code: nothing to package (Lambdas bundle from cdk/custom-resources)."
