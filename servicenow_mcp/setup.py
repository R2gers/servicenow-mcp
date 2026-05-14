"""
servicenow-mcp setup -- installs hooks, skill, and MCP registration into Claude Code.
Run: servicenow-mcp-setup
"""

import json
import os
import sys
import shutil
from pathlib import Path

SKILL_DIR_NAME = "servicenow-mcp"

SKILL_CONTENT = r'''---
name: servicenow
description: >
  ServiceNow development workflow skill. Handles widget creation with correct
  page hierarchy (Page->Container->Row->Column->Instance), scope/update set
  verification, and session recaps for update set descriptions.
trigger: /servicenow
---

# ServiceNow Development Workflow

You are assisting a ServiceNow developer using the servicenow-mcp tools.

## Session Start Protocol

**Before doing ANY work**, run this checklist:
1. Greet the developer and ask: "Which **scope** and **update set** are you working in?"
2. Wait for confirmation. Do NOT proceed with creates/updates until confirmed.
3. Note the scope and update set for the session recap later.

## Widget + Page Creation Workflow

When asked to create a widget and place it on a page, follow this EXACT sequence:

### Step 1 -- Create or locate the Widget
```
sn_create_record(table="sp_widget", data={name, id, template, client_script, script, css})
```

### Step 2 -- Create or locate the Page
```
sn_create_record(table="sp_page", data={title, id})
```

### Step 3 -- Create the Container
```
sn_create_record(table="sp_container", data={
  sp_page: "<page_sys_id>",
  order: 100,
  name: "Main"
})
```

### Step 4 -- Create the Row
```
sn_create_record(table="sp_row", data={
  sp_container: "<container_sys_id>",
  order: 100
})
```

### Step 5 -- Create the Column
```
sn_create_record(table="sp_column", data={
  sp_row: "<row_sys_id>",
  order: 100,
  size_md: 12
})
```

### Step 6 -- Create the Widget Instance
```
sn_create_record(table="sp_instance", data={
  sp_widget: "<widget_sys_id>",
  sp_column: "<column_sys_id>",
  order: 100
})
```

**NEVER skip steps 3-5.** A widget cannot be placed on a page without Container -> Row -> Column.

## Before Updating Anything

1. Always **read first**: use `sn_get_widget` or `sn_get_record` before any update.
2. Show the user what will change (diff the old vs new).
3. Wait for confirmation before applying.

## Session End Protocol

When the session is wrapping up or the user says they're done:
1. Compile a recap of everything created/updated this session.
2. Ask: "Want me to add a session recap to your update set description?"
3. If yes, format the recap as a dated entry and append it to the update set's `description` field.

Example recap format:
```
--- Session 2026-05-14 ---
- Created widget: ps-dashboard (sys_id: abc123)
- Created page: dashboard (sys_id: def456)
- Updated script include: PresalesUtils (sys_id: ghi789)
- Modified widget template: ps-opportunities
```

## Common Pitfalls to Avoid

- **Reference fields**: Always use sys_id values, never display values
- **Encoded queries**: Use ^ for AND, ^OR for OR, CONTAINS not LIKE for partial matches
- **Widget updates**: Never overwrite template/script without reading current version first
- **Bulk operations**: Never update more than 10 records without explicit user OK
- **Deletions**: Suggest deactivation (active=false) instead of deletion
'''

# The mcp.json key is "servicenow" so tool calls appear as mcp__servicenow__sn_*
MCP_KEY = "servicenow"

HOOK_CONFIG = {
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "mcp__servicenow__(sn_update_record|sn_create_record|sn_update_widget|sn_update_script_include)",
                "hooks": [
                    {
                        "type": "prompt",
                        "prompt": "This is a ServiceNow write operation. Review the tool arguments and decide: should this proceed? Reply with ALLOW to proceed or DENY with a reason to block it."
                    }
                ]
            }
        ]
    }
}


def _claude_dir() -> Path:
    return Path.home() / ".claude"


def _install_skill():
    skill_dir = _claude_dir() / "skills" / SKILL_DIR_NAME
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(SKILL_CONTENT, encoding="utf-8")
    print(f"  Skill installed -> {skill_file}")


def _install_hooks():
    settings_file = _claude_dir() / "settings.json"
    if not settings_file.exists():
        # Create settings.json with just the hooks
        settings = HOOK_CONFIG
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        print(f"  Hooks installed -> {settings_file}")
        return

    settings = json.loads(settings_file.read_text(encoding="utf-8"))

    existing_hooks = settings.get("hooks", {})
    pre_tool = existing_hooks.get("PreToolUse", [])

    matcher = HOOK_CONFIG["hooks"]["PreToolUse"][0]["matcher"]

    # Clean up old/broken hooks (old name or invalid "confirm" type)
    pre_tool = [h for h in pre_tool
                if "sn-fujidev-mcp" not in h.get("matcher", "")
                and not any(hook.get("type") == "confirm" for hook in h.get("hooks", []))]

    already = any(h.get("matcher") == matcher for h in pre_tool)
    if already:
        print("  Hooks already installed -- skipping")
        return

    pre_tool.extend(HOOK_CONFIG["hooks"]["PreToolUse"])
    existing_hooks["PreToolUse"] = pre_tool
    settings["hooks"] = existing_hooks

    settings_file.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print(f"  Hooks installed -> {settings_file}")


def _find_python() -> str:
    """Find the absolute path to the Python that has servicenow_mcp installed."""
    return sys.executable


def _install_mcp_server():
    mcp_file = _claude_dir() / "mcp.json"
    if mcp_file.exists():
        mcp_config = json.loads(mcp_file.read_text(encoding="utf-8"))
    else:
        mcp_config = {}

    servers = mcp_config.get("mcpServers", {})

    # Clean up old sn-fujidev-mcp entry if present
    if "sn-fujidev-mcp" in servers:
        del servers["sn-fujidev-mcp"]
        print("  Removed old sn-fujidev-mcp entry")

    python_path = _find_python()
    expected = {
        "command": python_path,
        "args": ["-m", "servicenow_mcp.server"]
    }

    current = servers.get(MCP_KEY, {})
    if current == expected:
        print("  MCP server already registered correctly -- skipping")
    else:
        servers[MCP_KEY] = expected
        mcp_config["mcpServers"] = servers
        mcp_file.write_text(json.dumps(mcp_config, indent=2), encoding="utf-8")
        if current:
            print(f"  MCP server UPDATED -> {mcp_file}")
        else:
            print(f"  MCP server registered -> {mcp_file}")
        print(f"  Python: {python_path}")
        print(f"  Module: servicenow_mcp.server")


def main():
    print()
    print("  servicenow-mcp setup")
    print("  " + "=" * 36)

    print("\n  [1/3] Installing /servicenow skill...")
    _install_skill()

    print("\n  [2/3] Installing confirmation hooks...")
    _install_hooks()

    print("\n  [3/3] Registering MCP server...")
    _install_mcp_server()

    print()
    print("  " + "=" * 36)
    print("  Setup complete! Restart Claude Code.")
    print()
    print("  Next: create a .env in your project folder:")
    print("    SERVICENOW_INSTANCE_URL=https://yourinstance.service-now.com")
    print("    SERVICENOW_USERNAME=your_user")
    print("    SERVICENOW_PASSWORD=your_pass")
    print()


if __name__ == "__main__":
    main()
