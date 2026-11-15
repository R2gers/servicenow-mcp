---
name: servicenow
description: >
  ServiceNow development workflow skill. Covers business rules, client scripts,
  UI policies, script includes, Service Portal widgets, and general table
  operations with scope/update set verification and session recaps.
trigger: /servicenow
---

# ServiceNow Development Workflow

You are assisting a ServiceNow developer using the servicenow-mcp tools.

## Session Start Protocol

**Before doing ANY work**, run this checklist:
1. Call `sn_get_context`. It returns the instance, whether it is writable, and the developer's current **application (scope)** and **update set**.
2. Tell the developer what it returned, in one or two lines, and ask: "Is this where the work should go?"
3. Wait for confirmation. Do NOT proceed with creates/updates until confirmed.
4. Note the scope and update set for the session recap later.

If `sn_get_context` reports a problem, stop and tell the developer how to fix it in ServiceNow:

| Problem | What the developer does |
|---------|-------------------------|
| Instance is read-only | Nothing, if that is intended. To allow writes on a non-production instance they run `servicenow-mcp-setup --allow-write <host>` in their own terminal and restart Claude Code |
| Update set is Default, or not in progress, or none chosen | Pick or create an in-progress update set in the ServiceNow update set picker |
| Record belongs to another application | Switch application in the ServiceNow application picker |

The server enforces all three. Never try to get around a `BLOCKED:` error, and never edit `.env`, `guards.json` or a policy file to change what you are allowed to do.

After each write, the result's `captured_in` shows the update set and application it landed in. Use that for the session recap.

## Common Development Tables

Use `sn_create_record` and `sn_update_record` with these tables:

| What | Table | Key Fields |
|------|-------|------------|
| Business Rule | `sys_script` | `name`, `table`, `script`, `when` (before/after/async), `order`, `active` |
| Client Script | `sys_script_client` | `name`, `table`, `script`, `type` (onLoad/onChange/onSubmit/onCellEdit), `active` |
| UI Policy | `sys_ui_policy` | `short_description`, `table`, `conditions`, `script_true`, `script_false`, `active` |
| UI Action | `sys_ui_action` | `name`, `table`, `script`, `action_name`, `active`, `order` |
| Script Include | `sys_script_include` | `name`, `script`, `api_name`, `active`, `client_callable` |
| Scheduled Job | `sysauto_script` | `name`, `script`, `run_type`, `active` |
| Fix Script | `sys_script_fix` | `name`, `script`, `description` |
| UI Page | `sys_ui_page` | `name`, `html`, `client_script`, `processing_script` |
| Notification | `sysevent_email_action` | `name`, `event_name`, `subject`, `message_html`, `active` |
| ACL | (blocked) | `sys_security_acl` is always blocked. Ask the developer to make ACL changes in the ServiceNow UI |

### Business Rule Example
```
sn_create_record(table="sys_script", data={
  "name": "Set priority on VIP caller",
  "table": "incident",
  "when": "before",
  "order": 100,
  "active": "true",
  "script": "(function executeRule(current, previous) {\n  if (current.caller_id.vip == 'true') {\n    current.priority = 1;\n  }\n})(current, previous);"
})
```

### Client Script Example
```
sn_create_record(table="sys_script_client", data={
  "name": "Auto-populate assignment group",
  "table": "incident",
  "type": "onChange",
  "field_name": "category",
  "active": "true",
  "script": "function onChange(control, oldValue, newValue, isLoading) {\n  if (isLoading) return;\n  // your logic here\n}"
})
```

## Service Portal: Widget + Page Creation

When placing a widget on a Service Portal page, you MUST create the full hierarchy:

1. Create the **Widget** (`sp_widget`) — `name`, `id`, `template`, `client_script`, `script`, `css`
2. Create the **Page** (`sp_page`) — `title`, `id`
3. Create a **Container** (`sp_container`) — `sp_page`: page sys_id
4. Create a **Row** (`sp_row`) — `sp_container`: container sys_id
5. Create a **Column** (`sp_column`) — `sp_row`: row sys_id, `size_md`: 12
6. Create the **Widget Instance** (`sp_instance`) — `sp_widget`: widget sys_id, `sp_column`: column sys_id

**NEVER skip steps 3-5.** A widget cannot be placed on a page without Container -> Row -> Column.

## Before Updating Anything

1. Always **read first**: use `sn_get_widget`, `sn_get_record`, or `sn_get_script_include` before any update.
2. Show the user what will change (diff the old vs new).
3. Wait for confirmation before applying.

## Configurable Table Guards

The server ships with default guards (blocking ACLs, sys_properties, etc.) that cannot be removed.

A developer can add stricter rules for a project with a `guards.json` next to their `.env`:
```json
{
  "blocked_tables": ["sys_user_has_role"],
  "restricted_tables": {
    "sys_user": ["email", "phone"]
  },
  "blocked_fields": ["sys_scope"]
}
```

Entries are added to the defaults. The file can never unblock a default table or field.

**Never create or edit `guards.json` yourself, and never try to work around a `BLOCKED:` error** (different spelling, another tool, another table). If a guard stops a change the developer needs, tell them and let them make it in the ServiceNow UI.

Every write tool shows the developer a confirmation dialog. Before each write, say in plain words which record will change and how, so the dialog is easy to judge.

## Session End Protocol

When the session is wrapping up or the user says they're done:
1. Compile a recap of everything created/updated this session.
2. Ask: "Want me to add a session recap to your update set description?"
3. If yes, format the recap as a dated entry and append it to the update set's `description` field.

Example recap format:
```
--- Session 2026-11-12 ---
- Created business rule: Set priority on VIP caller (sys_id: abc123)
- Created client script: Auto-populate assignment group (sys_id: def456)
- Updated script include: MyScriptInclude (sys_id: ghi789)
- Created widget: ps-dashboard + placed on page: dashboard
```

## Common Pitfalls to Avoid

- **Reference fields**: Always use sys_id values, never display values
- **Encoded queries**: Use ^ for AND, ^OR for OR, CONTAINS not LIKE for partial matches
- **Script updates**: Never overwrite a script without reading the current version first
- **Bulk operations**: Never update more than 10 records without explicit user OK
- **Deletions**: Suggest deactivation (active=false) instead of deletion
- **Business rule `when`**: Must be `before`, `after`, `async`, or `display`
- **Client script `type`**: Must be `onLoad`, `onChange`, `onSubmit`, or `onCellEdit`
