<p align="center">
  <h1 align="center">servicenow-mcp</h1>
  <p align="center">
    <strong>Give Claude direct access to your ServiceNow instance.</strong>
  </p>
  <p align="center">
    <a href="#install">Install</a> &middot;
    <a href="#tools">Tools</a> &middot;
    <a href="#safety">Safety</a> &middot;
    <a href="#skill">Skill</a>
  </p>
</p>

---

A [Claude Code](https://docs.anthropic.com/en/docs/claude-code) MCP server that lets Claude query tables, manage Service Portal widgets, update script includes, create users and roles, and more.

**What makes this different:**
- **Plug and play** -- `pip install` + one setup command, done
- **Multi-instance** -- each project folder has its own `.env`, one install serves all your instances
- **Built-in safety** -- server-side guards that block destructive operations at the code level (not just prompts)
- **Workflow intelligence** -- ships a `/servicenow` skill and mandatory rules so Claude follows SN best practices

<!-- 
## Demo

![servicenow-mcp demo](docs/demo.gif)
-->

---

## Install

**macOS / Linux:**
```bash
pip3 install git+https://github.com/R2gers/servicenow-mcp.git
servicenow-mcp-setup
```

**Windows:**
```bash
pip install git+https://github.com/R2gers/servicenow-mcp.git
servicenow-mcp-setup
```

That's it. The setup command auto-detects your Python path, registers the MCP server, installs the `/servicenow` skill, and adds confirmation hooks. Restart Claude Code after setup.

> **Tip:** If `pip3` is not found, try `python3 -m pip install ...` instead.

### Configure your instance

Create a `.env` file in your **project folder** (where you run Claude Code):

```env
SERVICENOW_INSTANCE_URL=https://yourinstance.service-now.com
SERVICENOW_AUTH_TYPE=basic
SERVICENOW_USERNAME=your_username
SERVICENOW_PASSWORD=your_password
```

> Each project folder can point to a different ServiceNow instance. Just drop a different `.env` in each.

<details>
<summary><strong>OAuth authentication</strong></summary>

```env
SERVICENOW_AUTH_TYPE=oauth
SERVICENOW_CLIENT_ID=your_client_id
SERVICENOW_CLIENT_SECRET=your_client_secret
SERVICENOW_TOKEN_URL=https://yourinstance.service-now.com/oauth_token.do
SERVICENOW_USERNAME=your_username
SERVICENOW_PASSWORD=your_password
```

</details>

---

## Tools

12 tools covering the core ServiceNow developer workflow:

| Tool | What it does |
|------|-------------|
| **`sn_query`** | Query any table with encoded queries, field selection, and limits |
| **`sn_get_record`** | Fetch a single record by sys_id |
| **`sn_create_record`** | Create a record in any table |
| **`sn_update_record`** | Update fields on an existing record |
| **`sn_table_fields`** | List all columns and types of a table |
| **`sn_get_widget`** | Read a Service Portal widget (template, client script, server script, CSS) |
| **`sn_update_widget`** | Update any part of a widget |
| **`sn_list_widgets`** | Search widgets by scope or name |
| **`sn_list_pages`** | List portal pages by portal or scope |
| **`sn_get_page_widgets`** | List all widget instances on a page (walks the full container hierarchy) |
| **`sn_get_script_include`** | Read a Script Include by name or sys_id |
| **`sn_update_script_include`** | Update a Script Include's script body |

---

## Safety

Three layers of protection -- from guidance to hard enforcement:

### 1. MCP Instructions (automatic)

Claude receives mandatory rules on every connection:
- Verify scope and update set before making changes
- Always read before updating
- Follow the full **Page > Container > Row > Column > Widget Instance** hierarchy
- Suggest update set description recaps at session end

### 2. Confirmation hooks

All write operations (`sn_update_*`, `sn_create_*`) trigger a confirmation prompt. You always see what's about to happen.

### 3. Server-side guards (cannot be bypassed)

Hardcoded in Python. No prompt injection or LLM instruction can override these:

| Guard | Details |
|-------|---------|
| **No DELETE** | No delete tool exists. Period. |
| **Protected tables** | `sys_security_acl`, `sys_properties`, `sys_db_object`, `sys_store_app`, and others |
| **Restricted tables** | `sys_update_set` -- only the `description` field can be updated (for session recaps) |
| **Protected fields** | `sys_id`, `sys_created_by`, `sys_created_on`, `sys_mod_count`, `sys_class_name` |
| **Anti-wipe** | Cannot set any text field to an empty string |

<details>
<summary><strong>What IS allowed</strong></summary>

Full read/write access to:
- `sys_user` -- create and update users
- `sys_user_role` -- create and modify roles
- `sys_user_has_role` -- assign roles to users
- `sys_user_group` -- create and modify groups
- `sys_user_grmember` -- add users to groups
- All custom and application-scoped tables
- All Service Portal tables (widgets, pages, containers, etc.)

</details>

---

## Skill

After setup, type `/servicenow` in Claude Code to activate the guided workflow. It enforces:

1. **Session start** -- asks you to confirm scope and update set
2. **Widget + page creation** -- follows the correct 6-step hierarchy (never skips Container/Row/Column)
3. **Session end** -- offers to write a recap to your update set description

---

## How it works

```
Your project folder/
  .env                    <-- your ServiceNow credentials (never committed)

~/.claude.json            <-- servicenow-mcp registered here (user-level MCP config)
~/.claude/
  settings.json           <-- confirmation hooks live here
  skills/servicenow-mcp/  <-- /servicenow skill installed here
```

Claude Code starts the MCP server automatically. The server reads `.env` from your current project folder, connects to your instance, and exposes the tools.

---

## Uninstall

```bash
pip uninstall servicenow-mcp
```

Then remove `servicenow` from `~/.claude/mcp.json` and the hook from `~/.claude/settings.json`.

---

## License

MIT
