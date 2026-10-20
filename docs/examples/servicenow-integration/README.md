# ServiceNow integration — example templates (guidance only)

These files show how a persona could integrate a third-party service that has
**no ready-made MCP server** (ServiceNow here; the same shape works for any REST
API). They are **forward-looking templates for reference**, not wired into the
deployed stack.

> **Note:** the platform no longer uses an AgentCore Gateway to host MCP
> targets (AgentCore was removed). In this architecture you extend a persona by **composing
> an additional MCP server in Kiro** alongside the `aidlc-platform` MCP server —
> the persona agent then has both toolsets. This example is framed as *an optional
> extra MCP server*, not a Gateway target. It still mirrors the existing Atlassian
> integration (`cdk/custom-resources/atlassian_mcp_proxy.py`), a dependency-free
> Lambda proxy that calls a vendor REST API directly.

| File | What it is |
| --- | --- |
| `servicenow_mcp_proxy.py` | A dependency-free Lambda that calls the ServiceNow Table API. Front it with a small MCP server (or run it as an MCP server directly) so a persona agent in Kiro can call each function as an MCP tool. |
| `SKILL.md` | A Kiro Skill (knowledge/procedure) that teaches a persona *when and how* to use those tools. A skill is not a connector — it drives the tools the MCP server provides. |

## How the pieces fit

```
Persona agent (Kiro)
        │  composes two MCP servers:
        ├── aidlc-platform  (control plane + get_stage_brief/submit_artifact/complete_stage)
        └── servicenow      (this example — additional MCP server)
                        │  tool: servicenow_create_incident ...
                        ▼
             servicenow_mcp_proxy.py  (Lambda / MCP tool implementation)
                        │  Basic auth, per-project creds
                        ▼
             ServiceNow Table API  (https://{instance}.service-now.com)

  Credentials: Secrets Manager  aidlc/projects/{project_id}/servicenow
               (global fallback: env SERVICENOW_SECRET_ARN)
```

The **Skill** supplies the know-how; the **MCP server** supplies the reach. A
persona agent that has both the `aidlc-platform` MCP server and this ServiceNow
MCP server configured can call either from the same Kiro session.

## Wiring it up (outline — not done here)

1. **Store credentials** in Secrets Manager at `aidlc/projects/{project_id}/servicenow`
   (per-project) and/or a global secret referenced by `SERVICENOW_SECRET_ARN`:
   ```json
   { "instance": "dev12345", "username": "svc_integration", "password": "..." }
   ```
   Use an integration/API user (ideally OAuth) with least privilege — never a human admin.

2. **Deploy the Lambda** and grant it `secretsmanager:GetSecretValue` on
   `aidlc/projects/*/servicenow-*` (and the global ARN if used), mirroring the
   Atlassian proxy's IAM in `cdk/lib/aidlc-platform-agents.ts`.

3. **Expose it as an MCP server.** Front the proxy with a thin MCP server (stdio
   or HTTP) that lists the five tools (`servicenow_create_incident`,
   `servicenow_get_incident`, `servicenow_update_incident`,
   `servicenow_add_comment`, `servicenow_list_incidents`). The handler already
   supports MCP JSON-RPC `tools/call`, so it can back the MCP server directly.

4. **Add it to the persona agents** that should use it: configure the ServiceNow
   MCP server in Kiro alongside `aidlc-platform`, and add its tools to the
   relevant persona agents' `tools` / `allowedTools`.

5. **Install the Skill:** copy `SKILL.md` to `.kiro/skills/servicenow-incidents/SKILL.md`
   (project scope) or `~/.kiro/skills/servicenow-incidents/SKILL.md` (global), and
   reference it from the personas that should use it.

## Notes

- **No extra dependencies:** the proxy uses only `urllib` + `boto3`, so it needs
  no bundling — same as the Atlassian proxy.
- **Invocation:** the handler supports MCP JSON-RPC (`tools/call`); it can also be
  invoked directly as a Lambda with the tool name supplied by the caller.
- **Credential routing:** `project_id` selects the per-project secret; a global
  secret is the fallback — the same pattern the platform uses for Atlassian and
  Git tokens.
- **Alternative (no infra):** for a quick interactive-only path, skip the Lambda
  and let the Skill drive the ServiceNow official CLI or a generic HTTP MCP tool
  from Kiro.
