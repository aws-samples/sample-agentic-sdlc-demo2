"""ServiceNow REST API Proxy Lambda — EXAMPLE / TEMPLATE.

Modeled on cdk/custom-resources/atlassian_mcp_proxy.py. This is a *guidance*
template showing how to integrate a service that has NO ready-made MCP server:
you register this Lambda as an AgentCore Gateway target with a static tool
schema, and the Gateway exposes each function to the agents as an MCP tool
named ``servicenow___<tool>``. The Lambda translates those tool calls into
ServiceNow Table API (REST) calls, using credentials from Secrets Manager.

It is deliberately dependency-free (stdlib ``urllib`` + ``boto3``) so it needs
no bundling, exactly like the Atlassian proxy.

Credential resolution (per-project, with a global fallback):
  * Per project:  Secrets Manager secret ``aidlc/projects/{project_id}/servicenow``
  * Global:       the ARN in env ``SERVICENOW_SECRET_ARN``
  Secret JSON shape:
      {"instance": "dev12345", "username": "svc_user", "password": "..."}
  (``instance`` -> https://{instance}.service-now.com). Use an integration
  user + OAuth or a scoped API user in production; never a human admin.

NOTE: template only. Wire it up per docs/examples/servicenow-integration/README.md.
"""

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

import boto3

_secrets_client = None
_credential_cache: dict[str, dict] = {}
_current_project_id: str | None = None


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
def _get_credentials(project_id: str | None = None) -> dict:
    """Fetch ServiceNow credentials from Secrets Manager (cached per project_id).

    Returns a dict with ``base_url`` and an ``authorization`` header value, or
    an ``{"error": ...}`` dict when credentials cannot be resolved.
    """
    global _secrets_client, _credential_cache

    if project_id is not None:
        cache_key = project_id
        secret_name = f"aidlc/projects/{project_id}/servicenow"
    else:
        cache_key = "__global__"
        secret_name = os.environ.get("SERVICENOW_SECRET_ARN", "")
        if not secret_name:
            return {"error": "No ServiceNow secret configured (SERVICENOW_SECRET_ARN unset)"}

    if cache_key in _credential_cache:
        return _credential_cache[cache_key]

    if _secrets_client is None:
        _secrets_client = boto3.client(
            "secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )

    try:
        response = _secrets_client.get_secret_value(SecretId=secret_name)
    except _secrets_client.exceptions.ResourceNotFoundException:
        return {"error": f"ServiceNow credentials not configured for project {project_id}"}

    secret = json.loads(response["SecretString"])
    instance = secret.get("instance")
    username = secret.get("username")
    password = secret.get("password")
    if not instance or not username or not password:
        return {"error": f"Invalid ServiceNow secret format for project {project_id}"}

    secret["base_url"] = f"https://{instance}.service-now.com"
    secret["authorization"] = "Basic " + base64.b64encode(
        f"{username}:{password}".encode()
    ).decode()
    _credential_cache[cache_key] = secret
    return secret


def _sn_request(method: str, path: str, body: dict | None = None,
                query: dict | None = None, project_id: str | None = None) -> dict:
    """Make an authenticated request to the ServiceNow REST API.

    ``path`` is relative to the instance base URL, e.g.
    ``/api/now/table/incident``.
    """
    effective_project_id = project_id if project_id is not None else _current_project_id
    creds = _get_credentials(effective_project_id)
    if "error" in creds:
        return {"success": False, **creds}

    url = creds["base_url"] + path
    if query:
        url += "?" + urllib.parse.urlencode(query)

    data = json.dumps(body).encode("utf-8") if body else None
    headers = {
        "Authorization": creds["authorization"],
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    print(f"[SN-PROXY] REST {method} {url}")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
            raw = resp.read().decode("utf-8")
            parsed = json.loads(raw) if raw else {}
            print(f"[SN-PROXY] status={resp.status} preview={str(parsed)[:200]}")
            return {"success": True, "status": resp.status, "body": parsed}
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8") if e.fp else str(e)
        print(f"[SN-PROXY] HTTP {e.code}: {err[:500]}")
        return {"success": False, "status": e.code, "error": err}
    except Exception as e:  # noqa: BLE001
        print(f"[SN-PROXY] request error: {e}")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Tool implementations (ServiceNow Table API — incident table)
# ---------------------------------------------------------------------------
_INCIDENT = "/api/now/table/incident"


def servicenow_create_incident(short_description: str, description: str = "",
                               urgency: str = "3", impact: str = "3",
                               assignment_group: str = "", caller_id: str = "") -> str:
    """Create an incident. Returns the new incident number + sys_id."""
    fields = {
        "short_description": short_description,
        "description": description,
        "urgency": urgency,
        "impact": impact,
    }
    if assignment_group:
        fields["assignment_group"] = assignment_group
    if caller_id:
        fields["caller_id"] = caller_id

    result = _sn_request("POST", _INCIDENT, body=fields)
    if result["success"]:
        rec = result["body"].get("result", {})
        return (f"Created incident {rec.get('number')} "
                f"(sys_id {rec.get('sys_id')}): {short_description}")
    return f"Error creating incident: {result.get('error', result.get('status'))}"


def servicenow_get_incident(number: str) -> str:
    """Fetch an incident by number (e.g. INC0012345). Returns JSON."""
    result = _sn_request("GET", _INCIDENT, query={
        "sysparm_query": f"number={number}",
        "sysparm_limit": "1",
        "sysparm_display_value": "true",
    })
    if result["success"]:
        rows = result["body"].get("result", [])
        if not rows:
            return f"No incident found with number {number}"
        r = rows[0]
        return json.dumps({
            "number": r.get("number"),
            "sys_id": r.get("sys_id"),
            "state": r.get("state"),
            "short_description": r.get("short_description"),
            "assignment_group": r.get("assignment_group"),
        })
    return f"Error fetching incident: {result.get('error', result.get('status'))}"


def servicenow_update_incident(sys_id: str, fields: dict | None = None,
                               state: str = "", assignment_group: str = "") -> str:
    """Update an incident by sys_id. Pass any subset of fields."""
    payload = dict(fields or {})
    if state:
        payload["state"] = state
    if assignment_group:
        payload["assignment_group"] = assignment_group
    if not payload:
        return "No fields to update"

    result = _sn_request("PATCH", f"{_INCIDENT}/{sys_id}", body=payload)
    if result["success"]:
        return f"Updated incident {result['body'].get('result', {}).get('number', sys_id)}"
    return f"Error updating incident: {result.get('error', result.get('status'))}"


def servicenow_add_comment(sys_id: str, comment: str, work_note: bool = False) -> str:
    """Add a customer-visible comment (or an internal work note) to an incident."""
    field = "work_notes" if work_note else "comments"
    result = _sn_request("PATCH", f"{_INCIDENT}/{sys_id}", body={field: comment})
    if result["success"]:
        return f"Added {'work note' if work_note else 'comment'} to incident {sys_id}"
    return f"Error adding comment: {result.get('error', result.get('status'))}"


def servicenow_list_incidents(query: str = "", limit: int = 10) -> str:
    """List incidents matching an encoded ServiceNow query. Returns JSON."""
    params = {"sysparm_limit": str(limit), "sysparm_display_value": "true"}
    if query:
        params["sysparm_query"] = query
    result = _sn_request("GET", _INCIDENT, query=params)
    if result["success"]:
        rows = [{
            "number": r.get("number"),
            "sys_id": r.get("sys_id"),
            "state": r.get("state"),
            "short_description": r.get("short_description"),
        } for r in result["body"].get("result", [])]
        return json.dumps({"incidents": rows, "count": len(rows)})
    return f"Error listing incidents: {result.get('error', result.get('status'))}"


# ---------------------------------------------------------------------------
# MCP tool dispatch
# ---------------------------------------------------------------------------
_TOOL_HANDLERS = {
    "servicenow_create_incident": lambda a: servicenow_create_incident(**a),
    "servicenow_get_incident": lambda a: servicenow_get_incident(**a),
    "servicenow_update_incident": lambda a: servicenow_update_incident(**a),
    "servicenow_add_comment": lambda a: servicenow_add_comment(**a),
    "servicenow_list_incidents": lambda a: servicenow_list_incidents(**a),
}


def _handle_mcp_request(mcp_request: dict) -> dict:
    """Handle an MCP JSON-RPC request (tools/list, tools/call)."""
    method = mcp_request.get("method", "")
    req_id = mcp_request.get("id")
    params = mcp_request.get("params", {})

    if method == "tools/list":
        tools = [
            {"name": name, "description": f"ServiceNow {name}", "inputSchema": {"type": "object"}}
            for name in _TOOL_HANDLERS
        ]
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = {k: v for k, v in params.get("arguments", {}).items() if k != "project_id"}
        handler_fn = _TOOL_HANDLERS.get(tool_name)
        if not handler_fn:
            return {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}}
        try:
            text = handler_fn(tool_args)
            return {"jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{"type": "text", "text": text}]}}
        except Exception as e:  # noqa: BLE001
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(e)}}

    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"Unknown method: {method}"}}


def _extract_project_id(event: dict, context) -> str | None:
    """project_id priority: tool args > session context > env fallback > None."""
    project_id = event.get("project_id")
    if project_id is None:
        project_id = event.get("params", {}).get("arguments", {}).get("project_id")
    if project_id is None:
        try:
            project_id = context.client_context.custom.get("project_id")
        except (AttributeError, TypeError):
            pass
    if project_id is None:
        project_id = os.environ.get("DEFAULT_PROJECT_ID")
    return project_id


def handler(event, context):
    """Lambda entrypoint. Gateway invokes either as MCP JSON-RPC or as a direct
    tool call with the tool name in context.client_context.custom
    ["bedrockAgentCoreToolName"] (scheme: ``servicenow___<tool>``).
    """
    print(f"[SN-PROXY] received: {json.dumps(event)[:500]}")

    gateway_tool_name = None
    try:
        custom = context.client_context.custom
        if custom and "bedrockAgentCoreToolName" in custom:
            full = custom["bedrockAgentCoreToolName"]
            gateway_tool_name = full.split("___", 1)[1] if "___" in full else full
    except (AttributeError, TypeError, KeyError):
        pass

    if isinstance(event, list):
        return [_handle_event(req, gateway_tool_name, context) for req in event]
    return _handle_event(event, gateway_tool_name, context)


def _handle_event(event: dict, gateway_tool_name: str | None = None, context=None) -> dict:
    global _current_project_id
    extracted = _extract_project_id(event, context)
    if extracted is not None:
        _current_project_id = extracted

    # MCP JSON-RPC path
    if "method" in event:
        return _handle_mcp_request(event)

    # Direct Gateway Lambda-target invocation
    tool_name = gateway_tool_name or event.get("_tool_name") or event.get("tool_name")
    if not tool_name:
        return {"error": f"Cannot determine tool from event keys: {list(event.keys())}"}

    handler_fn = _TOOL_HANDLERS.get(tool_name)
    if not handler_fn:
        return {"error": f"Unknown tool: {tool_name}"}

    args = {k: v for k, v in event.items() if not k.startswith("_") and k != "project_id"}
    try:
        return {"result": handler_fn(args)}
    except Exception as e:  # noqa: BLE001
        print(f"[SN-PROXY] tool error: {e}")
        return {"error": str(e)}
