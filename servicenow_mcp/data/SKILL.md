---
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
- Updated script include: MyScriptInclude (sys_id: ghi789)
- Modified widget template: ps-opportunities
```

## Common Pitfalls to Avoid

- **Reference fields**: Always use sys_id values, never display values
- **Encoded queries**: Use ^ for AND, ^OR for OR, CONTAINS not LIKE for partial matches
- **Widget updates**: Never overwrite template/script without reading current version first
- **Bulk operations**: Never update more than 10 records without explicit user OK
- **Deletions**: Suggest deactivation (active=false) instead of deletion
