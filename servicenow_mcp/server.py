#!/usr/bin/env python3
"""
servicenow-mcp — ServiceNow MCP Server
Exposes generic tools to query, update, and manipulate any ServiceNow table,
widgets, and portal pages — without needing throwaway scripts.

Requires a .env file with ServiceNow credentials in the current working
directory (or passed via --env-file). Will NOT start without valid credentials.
"""

import os
import re
import sys
import json
import argparse
import asyncio
import requests
from typing import Any
from dotenv import load_dotenv

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from servicenow_mcp import policy


_env_loaded = False
_env_error = None  # type: str | None


def _check_for_updates():
    """Non-blocking version check against GitHub. Prints a notice if outdated."""
    try:
        from servicenow_mcp import __version__, __repo__
        import urllib.request
        import json as _json

        api_url = "https://raw.githubusercontent.com/R2gers/servicenow-mcp/master/servicenow_mcp/__init__.py"
        req = urllib.request.Request(api_url, headers={"User-Agent": "servicenow-mcp"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            content = resp.read().decode("utf-8")

        remote_version = None
        for line in content.splitlines():
            if line.startswith("__version__"):
                remote_version = line.split("=")[1].strip().strip('"').strip("'")
                break

        if remote_version and remote_version != __version__:
            def _parse_ver(v):
                try:
                    return tuple(int(x) for x in v.split("."))
                except Exception:
                    return (0,)
            is_newer = _parse_ver(remote_version) > _parse_ver(__version__)
            if is_newer:
                print(
                    f"servicenow-mcp: UPDATE AVAILABLE {__version__} -> {remote_version}  "
                    f"Run: servicenow-mcp-update",
                    file=sys.stderr,
                )
            else:
                print(f"servicenow-mcp: v{__version__} (up to date)", file=sys.stderr)
        else:
            print(f"servicenow-mcp: v{__version__} (up to date)", file=sys.stderr)
    except Exception:
        pass


def _load_env():
    """Resolve and load .env. Never crashes — sets _env_error if something is wrong."""
    global _env_loaded, _env_error

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--env-file", default=None)
    args, _ = parser.parse_known_args()

    cwd_env = os.path.join(os.getcwd(), ".env")
    env_file = args.env_file or cwd_env

    if not os.path.exists(env_file):
        _env_error = (
            f"No .env file found at {env_file}. "
            f"Create a .env in your project folder with:\n"
            f"  SERVICENOW_INSTANCE_URL=https://yourinstance.service-now.com\n"
            f"  SERVICENOW_USERNAME=your_user\n"
            f"  SERVICENOW_PASSWORD=your_pass"
        )
        print(f"servicenow-mcp: WARNING - {_env_error}", file=sys.stderr)
        print("servicenow-mcp: Server will start but tools will return errors until .env is configured.", file=sys.stderr)
        return

    load_dotenv(dotenv_path=env_file, override=True)

    required = ["SERVICENOW_INSTANCE_URL", "SERVICENOW_USERNAME", "SERVICENOW_PASSWORD"]
    missing = [v for v in required if not os.environ.get(v)]
    placeholder = os.environ.get("SERVICENOW_INSTANCE_URL", "")
    if "YOURINSTANCE" in placeholder.upper():
        missing.append("SERVICENOW_INSTANCE_URL (still has placeholder value)")
    if missing:
        _env_error = f"Missing or invalid env vars in {env_file}: {', '.join(missing)}"
        print(f"servicenow-mcp: WARNING - {_env_error}", file=sys.stderr)
        return

    _env_loaded = True
    print(f"servicenow-mcp: loaded credentials from {env_file}", file=sys.stderr)
    print(f"servicenow-mcp: target instance -> {os.environ['SERVICENOW_INSTANCE_URL']}", file=sys.stderr)
    p = _policy()
    print(f"servicenow-mcp: access mode -> {p['mode']} ({p['reason']})", file=sys.stderr)


def _require_env():
    """Call at the start of any tool handler. Raises if .env is not loaded."""
    if not _env_loaded:
        raise RuntimeError(
            f"ServiceNow credentials not configured. {_env_error or ''}\n"
            f"Create a .env file in your project folder and restart Claude Code."
        )


# ── Instance policy ─────────────────────────────────────────────────────────
# Which instance may be written to is decided by servicenow_mcp/policy.py from
# files OUTSIDE the project folder. An instance nobody approved is read-only.

WRITE_TOOLS = frozenset({
    "sn_update_record",
    "sn_create_record",
    "sn_update_widget",
    "sn_update_script_include",
})

_instance_policy = None  # type: dict | None


def _policy() -> dict:
    global _instance_policy
    if _instance_policy is None:
        _instance_policy = policy.resolve(
            os.environ.get("SERVICENOW_INSTANCE_URL", ""),
            os.environ.get("SERVICENOW_READ_ONLY"),
        )
    return _instance_policy


def _guard_instance(tool: str):
    """Refuse everything on a blocked instance, and writes on a read-only one."""
    p = _policy()
    if p["mode"] == policy.BLOCKED:
        raise PermissionError(
            f"BLOCKED: this instance may not be used with servicenow-mcp: {p['reason']}. "
            f"This is enforced at the server level. Do not try to work around it."
        )
    if tool in WRITE_TOOLS and p["mode"] != policy.WRITE:
        raise PermissionError(
            f"BLOCKED: {tool} is not available because this instance is read-only: {p['reason']}. "
            f"This is enforced at the server level. Do not edit policy files or .env to change it; "
            f"tell the developer and let them decide."
        )


# ── Auth ────────────────────────────────────────────────────────────────────
_token_cache: dict = {}


def _auth_type() -> str:
    return os.environ.get("SERVICENOW_AUTH_TYPE", "basic").lower()


def _get_token() -> str:
    import time
    now = time.time()
    if _token_cache.get("token") and now < _token_cache.get("expires", 0) - 30:
        return _token_cache["token"]
    resp = requests.post(
        os.environ["SERVICENOW_TOKEN_URL"],
        data={
            "grant_type":    "password",
            "client_id":     os.environ["SERVICENOW_CLIENT_ID"],
            "client_secret": os.environ["SERVICENOW_CLIENT_SECRET"],
            "username":      os.environ["SERVICENOW_USERNAME"],
            "password":      os.environ["SERVICENOW_PASSWORD"],
        },
        timeout=15,
        allow_redirects=False,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"OAuth token request failed. {_error_message(resp)}")
    data = resp.json()
    _token_cache["token"]   = data["access_token"]
    _token_cache["expires"] = now + data.get("expires_in", 1800)
    return _token_cache["token"]


def _headers() -> dict:
    if _auth_type() == "oauth":
        return {
            "Authorization": f"Bearer {_get_token()}",
            "Accept":        "application/json",
            "Content-Type":  "application/json",
        }
    import base64
    creds = base64.b64encode(
        f"{os.environ['SERVICENOW_USERNAME']}:{os.environ['SERVICENOW_PASSWORD']}".encode()
    ).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Accept":        "application/json",
        "Content-Type":  "application/json",
    }


def _base() -> str:
    base = os.environ["SERVICENOW_INSTANCE_URL"].strip().rstrip("/")
    if not base.lower().startswith("https://"):
        raise RuntimeError(
            "SERVICENOW_INSTANCE_URL must start with https:// -- refusing to send credentials over plain HTTP."
        )
    return base


# ── Server Instructions ──────────────────────────────────────────────────────
SERVER_INSTRUCTIONS = """
## ServiceNow MCP — Mandatory Rules

### Session Start Checklist
At the START of every session that will use ServiceNow tools:
1. Call `sn_get_context`. It reports the instance, whether it is writable, and the developer's current **application (scope)** and **update set**.
2. Tell the developer what it returned and ask them to confirm that is where the work should go.
3. Do NOT proceed with any create/update operations until the developer confirms.
4. If it reports problems (Default update set, update set not in progress, read-only instance), tell the developer and let them fix it in ServiceNow. Never try to work around a `BLOCKED:` error, and never edit `.env`, `guards.json` or policy files yourself.

The server enforces this too: configuration writes are refused when they would land in a Default update set or in a different application than the record belongs to.

### Service Portal Page + Widget Creation (CRITICAL)
When placing a widget on a Service Portal page, you MUST create the full hierarchy:
1. Create the **Page** (`sp_page`)
2. Create a **Container** (`sp_container`) linked to the page
3. Create a **Row** (`sp_row`) linked to the container
4. Create a **Column** (`sp_column`) linked to the row
5. Create the **Widget Instance** (`sp_instance`) linked to the column, referencing the widget

NEVER try to link a widget directly to a page — it will silently fail.
The correct chain is always: Page → Container → Row → Column → Widget Instance.

### Before Any Update or Create
- Always read the existing record first (sn_get_record or sn_get_widget) before updating.
- Always check the table's fields (sn_table_fields) before querying an unfamiliar table.
- For widgets: always read the current template/script before overwriting.

### Safety Guardrails
- NEVER bulk-update more than 10 records without explicit user confirmation.
- NEVER delete records — ServiceNow records should be deactivated, not deleted.
- Always confirm with the user before running sn_update_record or sn_update_widget.
- When updating widgets, show a diff of what will change before applying.

### Session End — Update Set Description
At the END of every session (or when the user is done making changes), suggest:
"Would you like me to update your update set description with a recap of what we changed this session?"
If yes, compile a summary of all creates/updates made and offer to write it to the update set's description field.

### Reference Field Handling
- Reference fields return sys_id values by default. When displaying to users, use display_value=true or query the referenced table.
- When creating records with reference fields, always use the sys_id, not the display value.

### Encoded Query Syntax Reminders
- Use ^ for AND, ^OR for OR
- Operators: = != LIKE CONTAINS STARTSWITH ENDSWITH > < >= <=
- Date format: javascript:gs.dateGenerate('YYYY-MM-DD')
- NULL check: fieldISEMPTY / fieldISNOTEMPTY
"""

# ── Server ───────────────────────────────────────────────────────────────────
server = Server("servicenow-mcp", instructions=SERVER_INSTRUCTIONS)


@server.list_tools()
async def list_tools() -> list[Tool]:
    tools = _all_tools()
    # On a read-only or blocked instance the write tools are not even offered.
    if _env_loaded and _policy()["mode"] != policy.WRITE:
        tools = [t for t in tools if t.name not in WRITE_TOOLS]
    return tools


def _all_tools() -> list[Tool]:
    return [
        Tool(
            name="sn_get_context",
            description=(
                "Read-only. Returns where changes will land: the instance and whether it is writable, "
                "the API user, their current application (scope) and current update set, and any problem "
                "that would block configuration writes (e.g. the update set is Default or not in progress). "
                "Call this at the start of a session and before the first write."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="sn_query",
            description=(
                "Query any ServiceNow table using an encoded query. "
                "Returns up to `limit` records with the specified fields."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "table":  {"type": "string", "description": "Table name, e.g. incident"},
                    "query":  {"type": "string", "description": "Encoded query, e.g. state=7^category=hardware. Leave empty for all."},
                    "fields": {"type": "string", "description": "Comma-separated field names. Leave empty for defaults."},
                    "limit":  {"type": "integer", "description": "Max records to return (default 20, max 200)", "default": 20},
                },
                "required": ["table"],
            },
        ),
        Tool(
            name="sn_get_record",
            description="Get a single ServiceNow record by sys_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "table":  {"type": "string"},
                    "sys_id": {"type": "string"},
                    "fields": {"type": "string", "description": "Comma-separated fields. Leave empty for all."},
                },
                "required": ["table", "sys_id"],
            },
        ),
        Tool(
            name="sn_update_record",
            description="Update any field(s) on a ServiceNow record by sys_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "table":  {"type": "string"},
                    "sys_id": {"type": "string"},
                    "data":   {"type": "object", "description": "Key/value pairs of fields to update"},
                },
                "required": ["table", "sys_id", "data"],
            },
        ),
        Tool(
            name="sn_create_record",
            description="Create a new record in any ServiceNow table.",
            inputSchema={
                "type": "object",
                "properties": {
                    "table": {"type": "string"},
                    "data":  {"type": "object", "description": "Field key/value pairs"},
                },
                "required": ["table", "data"],
            },
        ),
        Tool(
            name="sn_get_widget",
            description="Read a Service Portal widget's template, client script, server script, and CSS by widget sys_id or id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "widget_id": {"type": "string", "description": "Widget sys_id (GUID) or widget id string like 'ps-opportunities'"},
                },
                "required": ["widget_id"],
            },
        ),
        Tool(
            name="sn_update_widget",
            description="Update a Service Portal widget's template, client_script, server script (script), or CSS. Only pass the fields you want to change.",
            inputSchema={
                "type": "object",
                "properties": {
                    "widget_id":     {"type": "string", "description": "Widget sys_id or id string"},
                    "template":      {"type": "string"},
                    "client_script": {"type": "string"},
                    "script":        {"type": "string", "description": "Server-side script"},
                    "css":           {"type": "string"},
                    "name":          {"type": "string"},
                },
                "required": ["widget_id"],
            },
        ),
        Tool(
            name="sn_list_widgets",
            description="List Service Portal widgets, optionally filtered by scope or name search.",
            inputSchema={
                "type": "object",
                "properties": {
                    "scope":  {"type": "string", "description": "Scope string e.g. x_myapp_custom"},
                    "search": {"type": "string", "description": "Text to search in name or id"},
                },
            },
        ),
        Tool(
            name="sn_list_pages",
            description="List Service Portal pages for a given portal or scope.",
            inputSchema={
                "type": "object",
                "properties": {
                    "portal_url_suffix": {"type": "string", "description": "Portal url_suffix e.g. sp"},
                    "scope":             {"type": "string", "description": "Scope e.g. x_myapp_custom"},
                },
            },
        ),
        Tool(
            name="sn_get_page_widgets",
            description="List all widget instances placed on a Service Portal page.",
            inputSchema={
                "type": "object",
                "properties": {
                    "page_id": {"type": "string", "description": "Page id string e.g. opportunities, or page sys_id"},
                },
                "required": ["page_id"],
            },
        ),
        Tool(
            name="sn_get_script_include",
            description="Read a Script Include by name or sys_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name":   {"type": "string", "description": "Script include name e.g. MyScriptInclude"},
                    "sys_id": {"type": "string"},
                },
            },
        ),
        Tool(
            name="sn_update_script_include",
            description="Update a Script Include's script body.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sys_id": {"type": "string"},
                    "script": {"type": "string"},
                },
                "required": ["sys_id", "script"],
            },
        ),
        Tool(
            name="sn_table_fields",
            description="List all columns/fields of a ServiceNow table.",
            inputSchema={
                "type": "object",
                "properties": {
                    "table": {"type": "string"},
                },
                "required": ["table"],
            },
        ),
    ]


# ── Input validation ─────────────────────────────────────────────────────────
# Table names and sys_ids are placed in the URL path, so they are validated
# strictly BEFORE any guard runs. Without this, a value such as
# "incident/../sys_properties" passes the guard and is then normalised by the
# HTTP library into a request against the protected table.

_TABLE_RE = re.compile(r"^[a-z0-9_]{1,80}$")
_SYS_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _valid_table(table: Any) -> str:
    if not isinstance(table, str) or not _TABLE_RE.fullmatch(table):
        raise ValueError(
            f"BLOCKED: invalid table name {table!r}. Table names may only contain "
            f"lowercase letters, digits and underscores (e.g. 'incident', 'x_myapp_task')."
        )
    return table


def _valid_sys_id(sys_id: Any) -> str:
    if not isinstance(sys_id, str) or not _SYS_ID_RE.fullmatch(sys_id):
        raise ValueError(
            f"BLOCKED: invalid sys_id {sys_id!r}. A sys_id is exactly 32 lowercase hex characters."
        )
    return sys_id


def _is_sys_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_SYS_ID_RE.fullmatch(value))


def _table_path(table: str, sys_id: str | None = None) -> str:
    path = f"/api/now/table/{_valid_table(table)}"
    if sys_id is not None:
        path += f"/{_valid_sys_id(sys_id)}"
    return path


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _timeout() -> float:
    try:
        return max(1.0, float(os.environ.get("SERVICENOW_TIMEOUT", "30")))
    except ValueError:
        return 30.0


_STATUS_HINTS = {
    401: "Authentication failed. Check the credentials in .env.",
    403: "The ServiceNow user is not allowed to do this (ACL or cross-scope access policy).",
    404: "Not found. The table or record does not exist, or an ACL hides it from this user.",
    429: "Rate limit reached on the instance. Wait before retrying.",
}


def _error_message(resp) -> str:
    """Build a useful message from a failed ServiceNow response."""
    parts = [f"ServiceNow returned HTTP {resp.status_code}"]
    try:
        err = resp.json().get("error", {})
        if isinstance(err, dict):
            if err.get("message"): parts.append(str(err["message"]))
            if err.get("detail"):  parts.append(str(err["detail"]))
        elif err:
            parts.append(str(err))
    except Exception:
        if 300 <= resp.status_code < 400:
            parts.append("Unexpected redirect (SSO login page?). Basic auth may be disabled for this user.")
    hint = _STATUS_HINTS.get(resp.status_code)
    if hint:
        parts.append(hint)
    if resp.status_code == 429 and resp.headers.get("Retry-After"):
        parts.append(f"Retry-After: {resp.headers['Retry-After']}s")
    return " | ".join(parts)


def _request(method: str, path: str, *, params: dict | None = None, json_body: dict | None = None) -> Any:
    """Single entry point for every ServiceNow API call: timeout, no redirects, real errors."""
    try:
        resp = requests.request(
            method, f"{_base()}{path}",
            headers=_headers(), params=params, json=json_body,
            timeout=_timeout(), allow_redirects=False,
        )
    except requests.Timeout:
        raise RuntimeError(
            f"ServiceNow did not answer within {_timeout():g}s ({method} {path}). "
            f"Narrow the query or raise SERVICENOW_TIMEOUT in .env."
        ) from None
    except requests.RequestException as e:
        raise RuntimeError(f"Could not reach ServiceNow ({type(e).__name__}): {e}") from None

    if not resp.ok or 300 <= resp.status_code < 400:
        raise RuntimeError(_error_message(resp))
    try:
        return resp.json().get("result")
    except ValueError:
        raise RuntimeError(
            "ServiceNow returned a non-JSON response. The instance may be hibernating, "
            "or the request was sent to a login page."
        ) from None


def _resolve_widget_sys_id(widget_id: str) -> str:
    if _is_sys_id(widget_id):
        return widget_id
    results = _request("GET", _table_path("sp_widget"),
                       params={"sysparm_query": f"id={widget_id}", "sysparm_fields": "sys_id", "sysparm_limit": 1})
    if not results:
        raise ValueError(f"No widget found with id='{widget_id}'")
    return results[0]["sys_id"]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    # Exceptions propagate: the MCP SDK turns them into a result flagged isError,
    # so the client can tell a failure from data. requests is blocking, so the
    # work runs in a thread to keep the event loop responsive.
    result = await asyncio.to_thread(_dispatch, name, arguments)
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


# ── Safety Guards ────────────────────────────────────────────────────────────
# Package defaults live in data/guards.json. A project-level guards.json can
# ADD restrictions but can never remove a default one: that file sits in a
# folder the assistant can write to, so it must not be able to loosen guards.

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def _norm_list(values: Any) -> set:
    if not isinstance(values, list):
        return set()
    return {str(v).strip().lower() for v in values if str(v).strip()}


def _merge_guards(defaults: dict, overrides: dict) -> tuple[dict, list[str]]:
    """Combine default guards with project overrides, tighten-only.

    Returns (guards, warnings). Warnings list every attempt to loosen a default.
    """
    warnings: list[str] = []
    if not isinstance(overrides, dict):
        overrides = {}
        warnings.append("project guards.json is not a JSON object; ignored")

    blocked_tables = _norm_list(defaults.get("blocked_tables"))
    blocked_fields = _norm_list(defaults.get("blocked_fields"))
    restricted = {str(t).strip().lower(): _norm_list(f)
                  for t, f in (defaults.get("restricted_tables") or {}).items()}

    for key, current in (("blocked_tables", blocked_tables), ("blocked_fields", blocked_fields)):
        if key in overrides:
            extra = _norm_list(overrides[key])
            dropped = current - extra
            if dropped:
                warnings.append(
                    f"{key}: project guards.json omits default entries {sorted(dropped)}; "
                    f"they stay blocked (project guards can only add restrictions)"
                )
            current |= extra

    over_restricted = overrides.get("restricted_tables")
    if isinstance(over_restricted, dict):
        given = {str(t).strip().lower() for t in over_restricted}
        dropped = set(restricted) - given
        if dropped:
            warnings.append(
                f"restricted_tables: project guards.json omits default entries {sorted(dropped)}; "
                f"they stay restricted"
            )
        for table, fields in over_restricted.items():
            table = str(table).strip().lower()
            wanted = _norm_list(fields)
            if table in restricted:
                widened = wanted - restricted[table]
                if widened:
                    warnings.append(
                        f"restricted_tables.{table}: cannot allow extra fields {sorted(widened)}; ignored"
                    )
                restricted[table] &= wanted
            else:
                restricted[table] = wanted

    return ({
        "blocked_tables": sorted(blocked_tables),
        "blocked_fields": sorted(blocked_fields),
        "restricted_tables": {t: sorted(f) for t, f in restricted.items()},
    }, warnings)


def _load_guards():
    """Load package defaults, then tighten with a project-level guards.json if present."""
    with open(os.path.join(_DATA_DIR, "guards.json"), encoding="utf-8") as f:
        defaults = json.load(f)

    overrides: dict = {}
    project_path = os.path.join(os.getcwd(), "guards.json")
    if os.path.exists(project_path):
        try:
            with open(project_path, encoding="utf-8") as f:
                overrides = json.load(f)
            print(f"servicenow-mcp: loaded project guards from {project_path}", file=sys.stderr)
        except (OSError, ValueError) as e:
            print(f"servicenow-mcp: WARNING - could not read {project_path} ({e}); using default guards",
                  file=sys.stderr)
            overrides = {}

    guards, warnings = _merge_guards(defaults, overrides)
    for w in warnings:
        print(f"servicenow-mcp: WARNING - {w}", file=sys.stderr)
    return guards


_guards = _load_guards()
BLOCKED_TABLES = frozenset(_guards["blocked_tables"])
RESTRICTED_TABLES = {
    table: frozenset(fields) for table, fields in _guards.get("restricted_tables", {}).items()
}
BLOCKED_FIELDS = frozenset(_guards["blocked_fields"])


def _guard_table(table: str, operation: str):
    _valid_table(table)
    if table in BLOCKED_TABLES:
        raise PermissionError(
            f"BLOCKED: {operation} on protected table '{table}' is not allowed. "
            f"This restriction is enforced at the server level and cannot be overridden."
        )
    if table in RESTRICTED_TABLES and operation == "create":
        raise PermissionError(
            f"BLOCKED: create on restricted table '{table}' is not allowed. "
            f"Only updates to {sorted(RESTRICTED_TABLES[table])} are permitted. "
            f"This restriction is enforced at the server level and cannot be overridden."
        )


def _guard_data(data: Any):
    """Write payloads must be a non-empty object with plain field names."""
    if not isinstance(data, dict) or not data:
        raise ValueError("BLOCKED: 'data' must be a non-empty object of field/value pairs.")
    bad = [k for k in data if not isinstance(k, str) or not _TABLE_RE.fullmatch(k)]
    if bad:
        raise ValueError(
            f"BLOCKED: invalid field names {bad}. Field names may only contain "
            f"lowercase letters, digits and underscores."
        )


def _guard_restricted_fields(table: str, data: dict):
    """For restricted tables, only allow the whitelisted fields."""
    if table not in RESTRICTED_TABLES:
        return
    allowed = RESTRICTED_TABLES[table]
    forbidden = set(data.keys()) - allowed
    if forbidden:
        raise PermissionError(
            f"BLOCKED: Cannot modify fields {sorted(forbidden)} on restricted table '{table}'. "
            f"Only {sorted(allowed)} may be updated. "
            f"This restriction is enforced at the server level."
        )


def _guard_fields(data: dict):
    for field in data:
        if str(field).strip().lower() in BLOCKED_FIELDS:
            raise PermissionError(
                f"BLOCKED: Cannot modify system field '{field}'. "
                f"This restriction is enforced at the server level."
            )


def _guard_no_wipe(data: dict, context: str):
    """Reject updates that set text fields to empty strings (accidental wipe)."""
    wiped = [k for k, v in data.items() if isinstance(v, str) and len(v) == 0]
    if wiped:
        raise ValueError(
            f"BLOCKED: Refusing to wipe fields {wiped} on {context}. "
            f"To clear a field, set it to a single space or an explicit placeholder. "
            f"Empty-string writes are blocked to prevent accidental data loss."
        )


def _guard_write(table: str, operation: str, data: Any, context: str):
    """Every write goes through here, whichever tool it came from."""
    _guard_table(table, operation)
    _guard_data(data)
    _guard_restricted_fields(table, data)
    _guard_fields(data)
    if operation == "update":
        _guard_no_wipe(data, context)


# ── Scope and update set context ─────────────────────────────────────────────
# ServiceNow stores a developer's current application and update set as rows
# in sys_user_preference. A Table API call starts a fresh session that reads
# those rows, so they decide where a configuration change is captured:
#
#   apps.current_app               -> sys_id of the application, or "global"
#   updateSetForScope<app sys_id>  -> sys_id of the update set chosen for that app
#   sys_update_set                 -> sys_id of the last update set chosen
#
# Reading the rows through the Table API has no side effects. (The UI's own
# /api/now/ui/concoursepicker/current endpoint can rewrite preferences and
# create Default update sets, so it is deliberately not used here.)

GLOBAL_SCOPE = "global"

_user_cache: dict = {}
_captured_cache: dict = {}


def _ref_value(value: Any) -> str:
    """Reference fields come back as {'link':..., 'value':...} or as a bare string."""
    if isinstance(value, dict):
        return str(value.get("value") or "")
    return str(value or "")


def _is_true(value: Any) -> bool:
    return str(value).strip().lower() in ("true", "1")


def _current_user() -> dict:
    if not _user_cache:
        username = os.environ["SERVICENOW_USERNAME"]
        rows = _request("GET", _table_path("sys_user"),
                        params={"sysparm_query": f"user_name={username}",
                                "sysparm_fields": "sys_id,user_name,name", "sysparm_limit": 1})
        if not rows:
            raise RuntimeError(
                f"Could not read the sys_user record for '{username}', so the current "
                f"application and update set cannot be determined."
            )
        _user_cache.update(rows[0])
    return dict(_user_cache)


def _is_default_update_set(update_set: dict) -> bool:
    if _is_true(update_set.get("is_default")):
        return True
    # Backstop for the name: ServiceNow names them "Default", "Default 1", ...
    name = str(update_set.get("name") or "").strip().lower()
    return name == "default" or (name.startswith("default ") and name[8:].strip().isdigit())


def _get_context() -> dict:
    """Read the API user's current application and update set. Always fetched fresh."""
    user = _current_user()
    prefs = _request("GET", _table_path("sys_user_preference"),
                     params={"sysparm_query": (f"user={user['sys_id']}^name=apps.current_app"
                                               f"^ORname=sys_update_set^ORnameSTARTSWITHupdateSetForScope"),
                             "sysparm_fields": "name,value", "sysparm_limit": 200}) or []
    pref = {p.get("name"): str(p.get("value") or "").strip() for p in prefs}

    app_id = pref.get("apps.current_app") or GLOBAL_SCOPE
    application = {"sys_id": app_id, "name": "Global", "scope": GLOBAL_SCOPE}
    if app_id != GLOBAL_SCOPE:
        if not _is_sys_id(app_id):
            raise RuntimeError(f"Unexpected value for the apps.current_app preference: {app_id!r}")
        app = _request("GET", _table_path("sys_scope", app_id),
                       params={"sysparm_fields": "name,scope"}) or {}
        application.update(name=app.get("name") or app_id, scope=app.get("scope") or "")

    problems: list[str] = []
    update_set: dict = {}
    source = f"updateSetForScope{app_id}"
    us_id = pref.get(source)
    if not us_id:
        source, us_id = "sys_update_set", pref.get("sys_update_set")

    if us_id and _is_sys_id(us_id):
        row = _request("GET", _table_path("sys_update_set", us_id),
                       params={"sysparm_fields": "sys_id,name,state,is_default,application"}) or {}
        if row:
            update_set = {
                "sys_id": row.get("sys_id") or us_id,
                "name": row.get("name") or "",
                "state": str(row.get("state") or "").lower(),
                "is_default": _is_default_update_set(row),
                "application": _ref_value(row.get("application")),
                "from_preference": source,
            }
            # The fallback preference may point at a set that belongs to another application.
            if source == "sys_update_set" and update_set["application"] not in ("", app_id):
                problems.append(
                    f"No update set has been chosen for application '{application['name']}'. "
                    f"ServiceNow will capture changes in that application's Default update set."
                )
                update_set = {}
    if not update_set and not problems:
        problems.append(
            f"No update set has been chosen for application '{application['name']}'. "
            f"ServiceNow will capture changes in its Default update set."
        )
    if update_set:
        if update_set["is_default"]:
            problems.append(f"The current update set '{update_set['name']}' is a Default update set.")
        if update_set["state"] != "in progress":
            problems.append(
                f"The current update set '{update_set['name']}' is '{update_set['state']}', not in progress. "
                f"ServiceNow will capture changes in the Default update set instead."
            )

    return {
        "user": {"sys_id": user.get("sys_id"), "user_name": user.get("user_name"), "name": user.get("name")},
        "application": application,
        "update_set": update_set or None,
        "config_writes_ok": not problems,
        "problems": problems,
    }


def _update_set_check_enabled() -> bool:
    return os.environ.get("SERVICENOW_UPDATE_SET_CHECK", "on").strip().lower() not in ("off", "false", "0", "no")


def _is_captured(table: str) -> bool:
    """True when changes to this table are captured in update sets.

    A table is captured when it extends sys_metadata, or when it (or a parent)
    has the update_synch dictionary attribute. If this cannot be worked out,
    the table is treated as captured so the check errs on the safe side.
    """
    if table in _captured_cache:
        return _captured_cache[table]
    chain: list[str] = []
    name = table
    try:
        for _ in range(12):
            rows = _request("GET", _table_path("sys_db_object"),
                            params={"sysparm_query": f"name={name}",
                                    "sysparm_fields": "name,super_class.name", "sysparm_limit": 1})
            if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
                break
            chain.append(name)
            parent = str(rows[0].get("super_class.name") or "").strip()
            if not parent or parent in chain or not _TABLE_RE.fullmatch(parent):
                break
            name = parent
        if not chain:
            return True  # unknown table (or hidden by ACL): assume captured, do not cache
        captured = "sys_metadata" in chain
        if not captured:
            rows = _request("GET", _table_path("sys_dictionary"),
                            params={"sysparm_query": (f"nameIN{','.join(chain)}^internal_type=collection"
                                                      f"^attributesLIKEupdate_synch=true"),
                                    "sysparm_fields": "name", "sysparm_limit": 1})
            captured = bool(rows)
    except RuntimeError:
        return True
    _captured_cache[table] = captured
    return captured


_HOW_TO_DISABLE = (
    "This check is enforced at the server level. Do not work around it: ask the developer to fix it "
    "in ServiceNow. (A developer who really wants it off can set SERVICENOW_UPDATE_SET_CHECK=off in .env.)"
)


def _guard_capture(table: str, sys_id: str | None = None) -> dict:
    """Before a configuration write: make sure it will land in a real update set.

    Returns details to include in the tool result, or {} when the table is not captured.
    """
    if not _update_set_check_enabled() or not _is_captured(table):
        return {}
    try:
        ctx = _get_context()
    except RuntimeError as e:
        raise PermissionError(
            f"BLOCKED: could not verify the current application and update set before writing to "
            f"'{table}' ({e}). {_HOW_TO_DISABLE}"
        ) from None

    app = ctx["application"]
    if sys_id is not None:
        rec = _request("GET", _table_path(table, sys_id), params={"sysparm_fields": "sys_scope"}) or {}
        rec_scope = _ref_value(rec.get("sys_scope"))
        if rec_scope and rec_scope != app["sys_id"]:
            raise PermissionError(
                f"BLOCKED: this {table} record belongs to a different application (sys_scope {rec_scope}) "
                f"than the developer's current application '{app['name']}' ({app['sys_id']}). "
                f"Ask the developer to switch application in ServiceNow first. {_HOW_TO_DISABLE}"
            )
    if not ctx["config_writes_ok"]:
        raise PermissionError(
            f"BLOCKED: refusing to change '{table}' because the change would not be captured in a "
            f"proper update set. {' '.join(ctx['problems'])} Ask the developer to select an "
            f"in-progress, non-Default update set for application '{app['name']}'. {_HOW_TO_DISABLE}"
        )
    return {
        "captured_in": {
            "update_set": ctx["update_set"]["name"],
            "update_set_sys_id": ctx["update_set"]["sys_id"],
            "application": app["name"],
        }
    }


def _dispatch(name: str, args: dict) -> Any:
    _require_env()
    _guard_instance(name)

    if name == "sn_get_context":
        p = _policy()
        ctx = _get_context()
        return {
            "instance": {"host": p["host"], "access_mode": p["mode"], "access_reason": p["reason"]},
            **ctx,
            "update_set_check": "on" if _update_set_check_enabled() else "off",
        }

    elif name == "sn_query":
        params = {
            "sysparm_limit": max(1, min(int(args.get("limit", 20)), 200)),
        }
        if args.get("query"):  params["sysparm_query"] = args["query"]
        if args.get("fields"): params["sysparm_fields"] = args["fields"]
        return _request("GET", _table_path(args["table"]), params=params) or []

    elif name == "sn_get_record":
        params = {}
        if args.get("fields"): params["sysparm_fields"] = args["fields"]
        return _request("GET", _table_path(args["table"], args["sys_id"]), params=params) or {}

    elif name == "sn_update_record":
        path = _table_path(args["table"], args["sys_id"])
        _guard_write(args["table"], "update", args.get("data"), f"{args['table']}/{args['sys_id']}")
        info = _guard_capture(args["table"], args["sys_id"])
        _request("PATCH", path, json_body=args["data"])
        return {"status": "updated", "sys_id": args["sys_id"], **info}

    elif name == "sn_create_record":
        path = _table_path(args["table"])
        _guard_write(args["table"], "create", args.get("data"), args["table"])
        info = _guard_capture(args["table"])
        rec = _request("POST", path, json_body=args["data"]) or {}
        return {"status": "created", "sys_id": rec.get("sys_id"), **info, "record": rec}

    elif name == "sn_get_widget":
        sys_id = _resolve_widget_sys_id(args["widget_id"])
        return _request("GET", _table_path("sp_widget", sys_id),
                        params={"sysparm_fields": "name,id,sys_id,template,client_script,script,css"}) or {}

    elif name == "sn_update_widget":
        sys_id = _resolve_widget_sys_id(args["widget_id"])
        payload = {k: v for k, v in args.items()
                   if k in ("template", "client_script", "script", "css", "name") and v is not None}
        _guard_write("sp_widget", "update", payload, f"sp_widget/{sys_id}")
        info = _guard_capture("sp_widget", sys_id)
        _request("PATCH", _table_path("sp_widget", sys_id), json_body=payload)
        return {"status": "updated", "widget_sys_id": sys_id, "fields_updated": list(payload.keys()), **info}

    elif name == "sn_list_widgets":
        query_parts = []
        if args.get("scope"):  query_parts.append(f"sys_scope.scope={args['scope']}")
        if args.get("search"): query_parts.append(f"nameCONTAINS{args['search']}^ORidCONTAINS{args['search']}")
        params = {
            "sysparm_fields": "name,id,sys_id,sys_scope",
            "sysparm_limit": 100,
        }
        if query_parts: params["sysparm_query"] = "^".join(query_parts)
        return _request("GET", _table_path("sp_widget"), params=params) or []

    elif name == "sn_list_pages":
        query_parts = []
        if args.get("scope"):             query_parts.append(f"sys_scope.scope={args['scope']}")
        if args.get("portal_url_suffix"):
            pres = _request("GET", _table_path("sp_portal"),
                            params={"sysparm_query": f"url_suffix={args['portal_url_suffix']}",
                                    "sysparm_fields": "sys_id", "sysparm_limit": 1})
            if pres:
                query_parts.append(f"sp_portal={pres[0]['sys_id']}")
        params = {
            "sysparm_fields": "title,id,sys_id,short_description",
            "sysparm_limit": 100,
        }
        if query_parts: params["sysparm_query"] = "^".join(query_parts)
        return _request("GET", _table_path("sp_page"), params=params) or []

    elif name == "sn_get_page_widgets":
        page_id = args["page_id"]
        if _is_sys_id(page_id):
            page_sys_id = page_id
        else:
            pres = _request("GET", _table_path("sp_page"),
                            params={"sysparm_query": f"id={page_id}", "sysparm_fields": "sys_id", "sysparm_limit": 1})
            if not pres: raise ValueError(f"No page found with id='{page_id}'")
            page_sys_id = pres[0]["sys_id"]

        def _children(table: str, query: str, fields: str) -> list:
            return _request("GET", _table_path(table),
                            params={"sysparm_query": query, "sysparm_fields": fields, "sysparm_limit": 50}) or []

        out = []
        for c in _children("sp_container", f"sp_page={page_sys_id}", "sys_id,title,order"):
            for row in _children("sp_row", f"sp_container={c['sys_id']}", "sys_id,order"):
                for col in _children("sp_column", f"sp_row={row['sys_id']}", "sys_id,order,size_md"):
                    for wi in _children("sp_instance", f"sp_column={col['sys_id']}", "sys_id,sp_widget,order"):
                        widget_ref = wi.get("sp_widget", {})
                        widget_sys_id = widget_ref.get("value", "") if isinstance(widget_ref, dict) else widget_ref
                        widget_info = {}
                        if _is_sys_id(widget_sys_id):
                            try:
                                widget_info = _request("GET", _table_path("sp_widget", widget_sys_id),
                                                       params={"sysparm_fields": "name,id,sys_id"}) or {}
                            except RuntimeError:
                                widget_info = {}  # widget not readable by this user; keep the instance row
                        out.append({
                            "instance_sys_id": wi["sys_id"],
                            "widget_sys_id":   widget_sys_id,
                            "widget_name":     widget_info.get("name", ""),
                            "widget_id":       widget_info.get("id", ""),
                        })
        return out

    elif name == "sn_get_script_include":
        if args.get("sys_id"):
            res = _request("GET", _table_path("sys_script_include", args["sys_id"]))
        elif args.get("name"):
            res = _request("GET", _table_path("sys_script_include"),
                           params={"sysparm_query": f"name={args['name']}", "sysparm_limit": 1})
        else:
            raise ValueError("Provide either 'name' or 'sys_id'.")
        if isinstance(res, list): res = res[0] if res else {}
        return res or {}

    elif name == "sn_update_script_include":
        path = _table_path("sys_script_include", args["sys_id"])
        payload = {"script": args["script"]}
        _guard_write("sys_script_include", "update", payload, f"sys_script_include/{args['sys_id']}")
        info = _guard_capture("sys_script_include", args["sys_id"])
        _request("PATCH", path, json_body=payload)
        return {"status": "updated", "sys_id": args["sys_id"], **info}

    elif name == "sn_table_fields":
        table = _valid_table(args["table"])
        res = _request("GET", _table_path("sys_dictionary"),
                       params={
                           "sysparm_query": f"name={table}^element!=NULL",
                           "sysparm_fields": "element,column_label,internal_type,default_value",
                           "sysparm_limit": 200,
                       }) or []
        return sorted(res, key=lambda x: x["element"])

    else:
        raise ValueError(f"Unknown tool: {name}")


async def main():
    _load_env()
    _check_for_updates()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main_sync():
    """Synchronous entry point for console_scripts."""
    asyncio.run(main())


if __name__ == "__main__":
    main_sync()
