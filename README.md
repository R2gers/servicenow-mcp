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

### Option A: Let Claude do it

Open Claude Code and say:

> Set up the servicenow-mcp for me: https://github.com/R2gers/servicenow-mcp

Claude will install the package and run setup. Create the `.env` file yourself (or run `servicenow-mcp-setup` in your own terminal) so your password never appears in the chat.

### Option B: Manual install

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

Restart Claude Code after setup.

> **Tip:** If `pip3` is not found, try `python3 -m pip install ...` instead.

### Option C: The plugin (no terminal, no hand-written `.env`)

This repository is also a Claude Code plugin marketplace. The `servicenow-instances` plugin adds a pane that installs the server, creates a folder and `.env` for each instance from a form, and tests the connection.

In the Claude desktop app: click **+** next to the prompt box, then **Plugins**, **Add marketplace** with `R2gers/servicenow-mcp`, then **Add plugin**. In a terminal session:

```
/plugin install servicenow-instances --marketplace R2gers/servicenow-mcp
```

Then type `/servicenow-instances`. It needs a recent Claude Code and Python 3.10+. See [plugins/servicenow-instances](plugins/servicenow-instances/README.md).

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

13 tools covering the core ServiceNow developer workflow:

| Tool | What it does |
|------|-------------|
| **`sn_get_context`** | Show the instance and whether it is writable, plus your current application (scope) and update set |
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

Five layers of protection -- from guidance to hard enforcement:

### 1. MCP Instructions (automatic)

Claude receives mandatory rules on every connection:
- Call `sn_get_context` and have you confirm the application and update set before making changes
- Always read before updating
- Follow the full **Page > Container > Row > Column > Widget Instance** hierarchy
- Suggest update set description recaps at session end

### 2. Human confirmation on every write

Setup adds `permissions.ask` rules to `~/.claude/settings.json` for the four write tools (`sn_update_record`, `sn_create_record`, `sn_update_widget`, `sn_update_script_include`). Claude Code then shows you a confirmation dialog with the exact arguments before each write, and an ask rule wins over any allow rule.

> Versions up to 0.2.0 installed a `prompt` hook instead. That hook was evaluated by a model and never shown to you. Re-run `servicenow-mcp-setup` to replace it.

### 3. Instance policy: read-only unless approved

An instance nobody has vouched for is **read-only**. The write tools are not even offered to Claude, and the server refuses them if called.

| Instance | Default |
|----------|---------|
| Personal Developer Instance (`dev12345.service-now.com`) | Writable |
| Anything else | Read-only until approved |
| Listed under `blocked_instances` | Refused entirely, reads included |

To approve a **non-production** instance, run this yourself in a terminal (the setup wizard also asks):

```bash
servicenow-mcp-setup --allow-write yourdev.service-now.com
```

That writes `~/.servicenow-mcp/policy.json`, outside any project folder. To force read-only for one project, add `SERVICENOW_READ_ONLY=true` to its `.env`.

<details>
<summary><strong>Organisation-wide policy</strong></summary>

An admin can install a system policy that developers cannot override:

- Windows: `%ProgramData%\servicenow-mcp\policy.json`
- macOS / Linux: `/etc/servicenow-mcp/policy.json`

```json
{
  "write_instances":   ["acmedev.service-now.com", "acme-sbx*.service-now.com"],
  "blocked_instances": ["acme.service-now.com"]
}
```

When this file exists, only the hosts it lists are writable (PDIs included), user approvals are ignored, and blocked hosts cannot be used at all. Entries are host names or globs.

</details>

### 4. Scope and update set check

Before changing configuration (any table captured in update sets), the server reads your current application and update set from your ServiceNow user preferences and refuses the write when:

- your current update set is a **Default** update set, is not in progress, or none is chosen for the application
- the record belongs to a **different application** than the one you are currently in
- it cannot read your preferences at all

Successful writes report which update set and application they were captured in. Writes to data tables such as `incident` are not affected. To turn the check off for a project, set `SERVICENOW_UPDATE_SET_CHECK=off` in its `.env`.

> This check reads the same preference records the ServiceNow UI uses. It was built from other tools' source code and tested against a simulated instance, not yet against a live one, so try it on a sandbox first.

### 5. Server-side guards

Enforced in Python before any request is sent, so no prompt or LLM instruction can talk the server out of them:

| Guard | Details |
|-------|---------|
| **Strict identifiers** | Table names must match `[a-z0-9_]+` and sys_ids must be 32 hex characters, so values like `incident/../sys_properties` are rejected instead of reaching a protected table |
| **HTTPS only** | Credentials are never sent to a plain `http://` instance URL |
| **No DELETE** | No delete tool exists. Period. |
| **Protected tables** | `sys_security_acl`, `sys_properties`, `sys_db_object`, `sys_store_app`, and others |
| **Restricted tables** | `sys_update_set` -- only the `description` field can be updated (for session recaps) |
| **Protected fields** | `sys_id`, `sys_created_by`, `sys_created_on`, `sys_mod_count`, `sys_class_name` |
| **Anti-wipe** | Cannot set any text field to an empty string |

A `guards.json` next to your `.env` can **add** blocked tables, blocked fields and restricted tables for that project. It can never remove a default: the file lives in a folder Claude can write to, so loosening from there is ignored and logged as a warning. See `guards.json.example`.

What the guards do not cover: they limit which tables and fields a write may touch, not what the ServiceNow user is otherwise allowed to do. Use an account with least-privilege roles, and do not point this at production.

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

1. **Session start** -- reads your current scope and update set with `sn_get_context` and asks you to confirm them
2. **Widget + page creation** -- follows the correct 6-step hierarchy (never skips Container/Row/Column)
3. **Session end** -- offers to write a recap to your update set description

---

## How it works

```
Your project folder/
  .env                    <-- your ServiceNow credentials (never committed)

~/.claude.json            <-- servicenow-mcp registered here (via `claude mcp add`)
~/.claude/
  settings.json           <-- permissions.ask rules for write tools live here
  skills/servicenow-mcp/  <-- /servicenow skill installed here
```

Claude Code starts the MCP server automatically. The server reads `.env` from your current project folder, connects to your instance, and exposes the tools.

---

## Uninstall

```bash
claude mcp remove servicenow -s user
pip uninstall servicenow-mcp
```

Then remove the `mcp__servicenow__*` entries from `permissions.ask` in `~/.claude/settings.json` and the skill folder `~/.claude/skills/servicenow-mcp/`.

---

## License

MIT
