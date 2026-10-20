"""MCP server wiring (design Section 2a, task 8).

Builds the Platform MCP server: loads and validates config (fail-fast), builds
the pluggable credential provider, the Project API client, the S3 artifact
reader, the session state store, and the registry mirror, then exposes the
:class:`~aidlc_mcp.tools.PlatformTools` methods as MCP tools over stdio.

Tool selection in Kiro is LLM-driven, so each tool's name and docstring is the
contract the model reasons over — keep them clear and action-oriented.
"""

from __future__ import annotations

from typing import Optional

from .auth import build_credential_provider
from .config import Config, load_config
from .project_api import ProjectApiClient
from .registry import load_registry
from .s3_artifacts import S3ArtifactReader
from .state_store import StateStore
from .tools import PlatformTools


def build_platform_tools(config: Optional[Config] = None) -> PlatformTools:
    """Construct PlatformTools from config/env (raises ConfigError on missing values)."""
    cfg = config or load_config()
    creds = build_credential_provider(cfg)
    api = ProjectApiClient(cfg.project_api_url, creds)
    s3 = S3ArtifactReader(cfg.files_bucket, cfg.aws_region)
    state = StateStore(cfg.state_dir)
    registry = load_registry()
    return PlatformTools(api, s3, creds, state, registry)


def create_server(platform: Optional[PlatformTools] = None):
    """Create the FastMCP server with every platform tool registered."""
    from mcp.server.fastmcp import FastMCP  # lazy: only needed to actually serve

    tools = platform or build_platform_tools()
    mcp = FastMCP("aidlc-platform")

    @mcp.tool()
    def list_projects() -> dict:
        """List the projects you can see, each tagged with whether it is assigned
        to you now (ownership: mine / awaiting_me / awaiting_other / owned_by_other),
        its current handoff state, and repo/QA status. Use this for
        "show me my projects" / "what am I working on"."""
        return tools.list_projects()

    @mcp.tool()
    def get_project(project_uuid: str) -> dict:
        """Show one project's details, including assignment history and, if it was
        returned for changes, the reviewer's feedback (latest_change_request)."""
        return tools.get_project(project_uuid)

    @mcp.tool()
    def list_artifacts(project_uuid: str) -> dict:
        """List the generated artifacts (documents) for a project so you can review them."""
        return tools.list_artifacts(project_uuid)

    @mcp.tool()
    def get_artifact(project_uuid: str, path: str) -> dict:
        """Read one project artifact's text content (e.g. requirements.md, stories.md,
        the design, or the construction brief). Reviewing artifacts here satisfies the
        review-before-approve gate."""
        return tools.get_artifact(project_uuid, path)

    @mcp.tool()
    def create_project(name: str, description: str = "", type: str = "development") -> dict:
        """Create a new project (Business Analyst)."""
        return tools.create_project(name, description, type)

    @mcp.tool()
    def delete_project(project_uuid: str) -> dict:
        """Delete a project you own. Destructive."""
        return tools.delete_project(project_uuid)

    @mcp.tool()
    def handoff_project(project_uuid: str, note: str = "") -> dict:
        """Hand the project off to the next persona, advancing as many steps as needed
        until ownership transfers (e.g. Business Analyst -> Solution Architect)."""
        return tools.handoff_project(project_uuid, note or None)

    @mcp.tool()
    def return_for_changes(project_uuid: str, comments: str) -> dict:
        """Send the project back to the previous persona for changes, with your comments
        (e.g. a Solution Architect returning work to the Business Analyst with architecture
        feedback). Always available; comments are required and reach the receiving persona."""
        return tools.return_for_changes(project_uuid, comments)

    @mcp.tool()
    def link_repo(project_uuid: str, repo_url: str, branch: str = "", git_token: str = "") -> dict:  # nosec B107 empty default, not a secret
        """Link your pushed GitHub/GitLab repository to the project (Software Developer).
        Provide git_token only for a private repo; it is stored securely and never echoed."""
        return tools.link_repo(project_uuid, repo_url, branch or None, git_token or None)

    @mcp.tool()
    def submit_implementation(project_uuid: str, note: str = "") -> dict:
        """Submit your linked implementation for QA (Software Developer). Requires a linked
        repo; local tests must pass (enforced by a hook)."""
        return tools.submit_implementation(project_uuid, note or None)

    @mcp.tool()
    def run_tests(project_uuid: str) -> dict:
        """Start the QA test-runner for a project (QA Engineer). Results arrive
        asynchronously and appear via get_project."""
        return tools.run_tests(project_uuid)

    @mcp.tool()
    def upload_requirements(project_uuid: str, file_path: str) -> dict:
        """Upload a requirements document (.md/.txt/.pdf/.docx) for a project (Business
        Analyst)."""
        return tools.upload_requirements(project_uuid, file_path)

    @mcp.tool()
    def get_stage_brief(project_uuid: str) -> dict:
        """Fetch the current stage's brief for a project so you can GENERATE its
        deliverable in Kiro: what to produce (with required flags + acceptance rules),
        what inputs to read (consumes), and the guiding skill. Owner-gated by the
        server (only available when it is your turn)."""
        return tools.get_stage_brief(project_uuid)

    @mcp.tool()
    def submit_artifact(project_uuid: str, path: str, content: str) -> dict:
        """Submit a Kiro-generated artifact for the active stage by uploading its
        content to the project's shared storage. The path must be one the current
        stage is declared to produce; the server owner-gates and constrains the
        upload to that contract."""
        return tools.submit_artifact(project_uuid, path, content)

    @mcp.tool()
    def complete_stage(project_uuid: str, note: str = "") -> dict:
        """Mark your current stage complete once you have submitted its artifacts.
        The server validates acceptance and advances the workflow to the next stage
        (a missing required artifact blocks; other issues return as warnings). This
        is the primary way to progress your own work; use handoff_project to transfer
        the project to the next persona."""
        return tools.complete_stage(project_uuid, note or None)

    return mcp
