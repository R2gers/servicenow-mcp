#!/usr/bin/env python3
"""
servicenow-mcp — ServiceNow MCP Server
Exposes generic tools to query, update, and manipulate any ServiceNow table,
widgets, and portal pages — without needing throwaway scripts.

Requires a .env file with ServiceNow credentials in the current working
directory (or passed via --env-file). Will NOT start without valid credentials.
"""

import os
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


_env_loaded = False
_env_error = None  # type: str | None


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


def _require_env():
    """Call at the start of any tool handler. Raises if .env is not loaded."""
    if not _env_loaded:
        raise RuntimeError(
            f"ServiceNow credentials not configured. {_env_error or ''}\n"
            f"Create a .env file in your project folder and restart Claude Code."
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
    )
    resp.raise_for_status()
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
    return os.environ["SERVICENOW_INSTANCE_URL"]


# ── Server Instructions ──────────────────────────────────────────────────────
SERVER_INSTRUCTIONS = """
## ServiceNow MCP — Mandatory Rules

### Session Start Checklist
At the START of every session that will use ServiceNow tools:
1. Ask the developer to confirm their **current scope** and **active update set** before making any changes.
2. Remind them: "Please verify your scope and update set in ServiceNow before we begin."
3. Do NOT proceed with any create/update operations until the developer confirms.

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
    return [
        Tool(
            name="sn_query",
            description=(
                "Query any ServiceNow table using an encoded query. "
                "Returns up to `limit` records with the specified fields."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "table":  {"type": "string", "description": "Table name, e.g. x_fusi_presales_request"},
                    "query":  {"type": "string", "description": "Encoded query, e.g. state=7^category=presales. Leave empty for all."},
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
                    "scope":  {"type": "string", "description": "Scope string e.g. x_fusi_presales"},
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
                    "portal_url_suffix": {"type": "string", "description": "Portal url_suffix e.g. presales"},
                    "scope":             {"type": "string", "description": "Scope e.g. x_fusi_presales"},
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
                    "name":   {"type": "string", "description": "Script include name e.g. PresalesUtils"},
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


def _resolve_widget_sys_id(widget_id: str) -> str:
    if len(widget_id) == 32 and all(c in "0123456789abcdef" for c in widget_id):
        return widget_id
    r = requests.get(
        f"{_base()}/api/now/table/sp_widget",
        headers=_headers(),
        params={"sysparm_query": f"id={widget_id}", "sysparm_fields": "sys_id", "sysparm_limit": 1},
    )
    r.raise_for_status()
    results = r.json().get("result", [])
    if not results:
        raise ValueError(f"No widget found with id='{widget_id}'")
    return results[0]["sys_id"]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        result = _dispatch(name, arguments)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as e:
        return [TextContent(type="text", text=f"ERROR: {e}")]


# ── Hard Safety Guards (code-level, cannot be bypassed by the LLM) ───────────

BLOCKED_TABLES = frozenset({
    "sys_security_acl", "sys_security_acl_role",
    "sys_properties", "sys_db_object", "sys_glide_object",
    "sys_cluster_state", "sys_upgrade_history",
    "sys_update_set",  # prevent accidental update set corruption
    "sys_store_app",
})

BLOCKED_FIELDS = frozenset({
    "sys_id", "sys_created_by", "sys_created_on",
    "sys_mod_count", "sys_class_name",
})


def _guard_table(table: str, operation: str):
    if table in BLOCKED_TABLES:
        raise PermissionError(
            f"BLOCKED: {operation} on protected table '{table}' is not allowed. "
            f"This restriction is enforced at the server level and cannot be overridden."
        )


def _guard_fields(data: dict):
    for field in BLOCKED_FIELDS:
        if field in data:
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


def _dispatch(name: str, args: dict) -> Any:
    _require_env()
    base = _base()
    h    = _headers()

    if name == "sn_query":
        params = {
            "sysparm_limit": min(int(args.get("limit", 20)), 200),
        }
        if args.get("query"):  params["sysparm_query"] = args["query"]
        if args.get("fields"): params["sysparm_fields"] = args["fields"]
        r = requests.get(f"{base}/api/now/table/{args['table']}", headers=h, params=params)
        r.raise_for_status()
        return r.json().get("result", [])

    elif name == "sn_get_record":
        params = {}
        if args.get("fields"): params["sysparm_fields"] = args["fields"]
        r = requests.get(f"{base}/api/now/table/{args['table']}/{args['sys_id']}", headers=h, params=params)
        r.raise_for_status()
        return r.json().get("result", {})

    elif name == "sn_update_record":
        _guard_table(args["table"], "update")
        _guard_fields(args["data"])
        _guard_no_wipe(args["data"], f"{args['table']}/{args['sys_id']}")
        r = requests.patch(f"{base}/api/now/table/{args['table']}/{args['sys_id']}",
                           headers=h, json=args["data"])
        r.raise_for_status()
        return {"status": "updated", "sys_id": args["sys_id"]}

    elif name == "sn_create_record":
        _guard_table(args["table"], "create")
        _guard_fields(args["data"])
        r = requests.post(f"{base}/api/now/table/{args['table']}", headers=h, json=args["data"])
        r.raise_for_status()
        rec = r.json().get("result", {})
        return {"status": "created", "sys_id": rec.get("sys_id"), "record": rec}

    elif name == "sn_get_widget":
        sys_id = _resolve_widget_sys_id(args["widget_id"])
        r = requests.get(f"{base}/api/now/table/sp_widget/{sys_id}", headers=h,
                         params={"sysparm_fields": "name,id,sys_id,template,client_script,script,css"})
        r.raise_for_status()
        return r.json().get("result", {})

    elif name == "sn_update_widget":
        sys_id = _resolve_widget_sys_id(args["widget_id"])
        payload = {k: v for k, v in args.items()
                   if k in ("template", "client_script", "script", "css", "name") and v is not None}
        _guard_no_wipe(payload, f"sp_widget/{sys_id}")
        r = requests.patch(f"{base}/api/now/table/sp_widget/{sys_id}", headers=h, json=payload)
        r.raise_for_status()
        return {"status": "updated", "widget_sys_id": sys_id, "fields_updated": list(payload.keys())}

    elif name == "sn_list_widgets":
        query_parts = []
        if args.get("scope"):  query_parts.append(f"sys_scope.scope={args['scope']}")
        if args.get("search"): query_parts.append(f"nameCONTAINS{args['search']}^ORidCONTAINS{args['search']}")
        params = {
            "sysparm_fields": "name,id,sys_id,sys_scope",
            "sysparm_limit": 100,
        }
        if query_parts: params["sysparm_query"] = "^".join(query_parts)
        r = requests.get(f"{base}/api/now/table/sp_widget", headers=h, params=params)
        r.raise_for_status()
        return r.json().get("result", [])

    elif name == "sn_list_pages":
        query_parts = []
        if args.get("scope"):             query_parts.append(f"sys_scope.scope={args['scope']}")
        if args.get("portal_url_suffix"):
            pr = requests.get(f"{base}/api/now/table/sp_portal", headers=h,
                               params={"sysparm_query": f"url_suffix={args['portal_url_suffix']}",
                                       "sysparm_fields": "sys_id", "sysparm_limit": 1})
            pr.raise_for_status()
            pres = pr.json().get("result", [])
            if pres:
                query_parts.append(f"sp_portal={pres[0]['sys_id']}")
        params = {
            "sysparm_fields": "title,id,sys_id,short_description",
            "sysparm_limit": 100,
        }
        if query_parts: params["sysparm_query"] = "^".join(query_parts)
        r = requests.get(f"{base}/api/now/table/sp_page", headers=h, params=params)
        r.raise_for_status()
        return r.json().get("result", [])

    elif name == "sn_get_page_widgets":
        page_id = args["page_id"]
        if len(page_id) == 32 and all(c in "0123456789abcdef" for c in page_id):
            page_sys_id = page_id
        else:
            pr = requests.get(f"{base}/api/now/table/sp_page", headers=h,
                               params={"sysparm_query": f"id={page_id}", "sysparm_fields": "sys_id", "sysparm_limit": 1})
            pr.raise_for_status()
            pres = pr.json().get("result", [])
            if not pres: raise ValueError(f"No page found with id='{page_id}'")
            page_sys_id = pres[0]["sys_id"]

        out = []
        containers = requests.get(f"{base}/api/now/table/sp_container", headers=h,
            params={"sysparm_query": f"sp_page={page_sys_id}", "sysparm_fields": "sys_id,title,order", "sysparm_limit": 50})
        for c in containers.json().get("result", []):
            rows = requests.get(f"{base}/api/now/table/sp_row", headers=h,
                params={"sysparm_query": f"sp_container={c['sys_id']}", "sysparm_fields": "sys_id,order", "sysparm_limit": 50})
            for row in rows.json().get("result", []):
                cols = requests.get(f"{base}/api/now/table/sp_column", headers=h,
                    params={"sysparm_query": f"sp_row={row['sys_id']}", "sysparm_fields": "sys_id,order,size_md", "sysparm_limit": 50})
                for col in cols.json().get("result", []):
                    instances = requests.get(f"{base}/api/now/table/sp_instance", headers=h,
                        params={"sysparm_query": f"sp_column={col['sys_id']}", "sysparm_fields": "sys_id,sp_widget,order", "sysparm_limit": 50})
                    for wi in instances.json().get("result", []):
                        widget_ref = wi.get("sp_widget", {})
                        widget_sys_id = widget_ref.get("value", "") if isinstance(widget_ref, dict) else widget_ref
                        widget_info = {}
                        if widget_sys_id:
                            wr = requests.get(f"{base}/api/now/table/sp_widget/{widget_sys_id}", headers=h,
                                params={"sysparm_fields": "name,id,sys_id"})
                            if wr.ok: widget_info = wr.json().get("result", {})
                        out.append({
                            "instance_sys_id": wi["sys_id"],
                            "widget_sys_id":   widget_sys_id,
                            "widget_name":     widget_info.get("name", ""),
                            "widget_id":       widget_info.get("id", ""),
                        })
        return out

    elif name == "sn_get_script_include":
        if args.get("sys_id"):
            r = requests.get(f"{base}/api/now/table/sys_script_include/{args['sys_id']}", headers=h)
        else:
            r = requests.get(f"{base}/api/now/table/sys_script_include", headers=h,
                params={"sysparm_query": f"name={args.get('name','')}", "sysparm_limit": 1})
        r.raise_for_status()
        res = r.json().get("result", {})
        if isinstance(res, list): res = res[0] if res else {}
        return res

    elif name == "sn_update_script_include":
        _guard_no_wipe({"script": args["script"]}, f"sys_script_include/{args['sys_id']}")
        r = requests.patch(f"{base}/api/now/table/sys_script_include/{args['sys_id']}",
                           headers=h, json={"script": args["script"]})
        r.raise_for_status()
        return {"status": "updated", "sys_id": args["sys_id"]}

    elif name == "sn_table_fields":
        r = requests.get(f"{base}/api/now/table/sys_dictionary", headers=h,
            params={
                "sysparm_query": f"name={args['table']}^element!=NULL",
                "sysparm_fields": "element,column_label,internal_type,default_value",
                "sysparm_limit": 200,
            })
        r.raise_for_status()
        return sorted(r.json().get("result", []), key=lambda x: x["element"])

    else:
        raise ValueError(f"Unknown tool: {name}")


async def main():
    _load_env()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main_sync():
    """Synchronous entry point for console_scripts."""
    asyncio.run(main())


if __name__ == "__main__":
    main_sync()
