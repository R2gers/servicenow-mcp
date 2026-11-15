# servicenow-mcp

A Claude Code MCP server for ServiceNow development. Gives Claude read/write access to ServiceNow tables, Service Portal widgets, script includes, users, roles, and groups -- with hardcoded safety guards.

## Setting up for a user

When a user asks you to install or set up this MCP, follow these steps exactly:

### Step 1 -- Install the package

```bash
pip install git+https://github.com/R2gers/servicenow-mcp.git
```

On macOS/Linux use `pip3` instead of `pip`. If that fails, try `python3 -m pip install ...`.

### Step 2 -- Run setup

```bash
servicenow-mcp-setup
```

This does three things automatically:
1. Registers the MCP server via `claude mcp add` (user-level scope)
2. Installs the `/servicenow` skill to `~/.claude/skills/servicenow-mcp/`
3. Adds `permissions.ask` rules to `~/.claude/settings.json` so every write tool shows the user a confirmation dialog

Run it with `--quick` when you run it for the user: the interactive wizard asks for a password and must be run by the user in their own terminal.

### Step 3 -- The user creates the `.env` file

**NEVER ask the user for their password, and never write it to a file yourself.** Anything typed in chat is stored in the transcript. Instead, tell the user to do one of these themselves:
- run `servicenow-mcp-setup` in their own terminal from the project folder (the wizard hides the password as they type), or
- copy `.env.example` to `.env` in the project folder and fill it in by hand.

The file looks like this:

```env
SERVICENOW_INSTANCE_URL=https://yourinstance.service-now.com
SERVICENOW_AUTH_TYPE=basic
SERVICENOW_USERNAME=your_username
SERVICENOW_PASSWORD=your_password
```

**IMPORTANT:**
- The `.env` MUST go in the project folder, NEVER in `~/.claude/`, `~/.claude.json`, or any global location.
- NEVER pass credentials via `-e` flags in the MCP registration. The server reads `.env` at runtime.
- Do NOT commit this file. It should already be in `.gitignore`.

### Step 4 -- Restart Claude Code

Tell the user: "Restart Claude Code for the MCP server to load. After restart, I'll have access to ServiceNow tools."

## After setup

Once the MCP is loaded, 12 tools become available (prefixed `mcp__servicenow__sn_*`). The `/servicenow` slash command activates the guided workflow.

## Troubleshooting

- **MCP not loading after restart**: Run `claude mcp list` to verify the `servicenow` entry exists. If not, run `servicenow-mcp-setup` again.
- **"No .env found" errors on tool calls**: The `.env` must be in the folder where Claude Code is launched, not in this repo.
- **Python version**: Requires Python 3.10+. Check with `python --version`.
- **Windows PATH issues**: The setup uses the absolute Python path, so PATH shouldn't matter. If it does, run: `claude mcp add servicenow -s user -- C:\full\path\to\python.exe -m servicenow_mcp.server`

## Project structure

```
servicenow_mcp/
  server.py      Main MCP server (tools, safety guards, dispatch)
  setup.py       Setup command (skill, write-confirmation rules, MCP registration)
  __init__.py
pyproject.toml   Package config (pip installable)
test_guards.py   Safety guard tests (run: python test_guards.py)
```

## Safety guards (enforced in server.py)

- Every ServiceNow call goes through `_request()` (timeout, no redirects, ServiceNow's own error text). Do not call `requests` directly.
- Every write goes through `_guard_write()`. Table names and sys_ids are validated by `_table_path()` before they reach a URL.
- A project `guards.json` can only add restrictions (`_merge_guards`). Never make it able to loosen the defaults.
- No delete tool exists
- Blocked tables: `sys_security_acl`, `sys_properties`, `sys_db_object`, `sys_store_app`, etc.
- Restricted tables: `sys_update_set` (only `description` can be updated)
- Protected fields: `sys_id`, `sys_created_by`, `sys_created_on`, `sys_mod_count`, `sys_class_name`
- Anti-wipe: cannot set text fields to empty string
