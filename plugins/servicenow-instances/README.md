# servicenow-instances

A Claude Code plugin for [servicenow-mcp](https://github.com/R2gers/servicenow-mcp). It adds a **ServiceNow instances** pane where you add an instance by filling in a form. The plugin creates the instance folder and its `.env` for you and tests the connection, so nobody has to write a `.env` by hand.

## Install

In the Claude desktop app: click **+** next to the prompt box, then **Plugins**, **Add marketplace** with `R2gers/servicenow-mcp`, then **Add plugin**.

At the prompt of a terminal session:

```
/plugin install servicenow-instances --marketplace R2gers/servicenow-mcp
```

From a shell:

```
claude plugin install servicenow-instances --marketplace R2gers/servicenow-mcp
```

Answer `y` to add the marketplace, then pick the user scope.

The plugin needs a recent Claude Code (it was built on 2.1.326; function-hook plugins are early access and older versions refuse it) and Python 3.10 or later on the machine. It brings the server itself: see step 2 below.

## Use

1. Type `/servicenow-instances` to open the pane.
2. If the first line says the server is not installed, press **Install server**. That runs `pip install` for servicenow-mcp and then `servicenow-mcp-setup --quick`, which registers the MCP server, the `/servicenow` skill and the write confirmations. Restart Claude Code afterwards. The button only shows while the server is missing; an existing install is never replaced (update it with `servicenow-mcp-update`).
3. Press **Settings** to check **Folder for instances**. Each instance gets its own folder under it. Type another path and press Enter to change it.
4. Press **Add instance** (or **Add with OAuth**), fill in the name, instance URL, username and password, then **Create folder and .env**. The pane tests the new instance and shows its card.
5. Each instance is a card: a mark (green connected, red not connected, hollow not tested), its name, whether it is writable or read-only for Claude, its host, user and folder, the test result with ServiceNow's own error when it fails, and its buttons: **Open session**, **Test connection** and, on a read-only instance, **Allow writes**. **Test all** fills in the marks.
6. With more than six instances the cards shrink to one line each, with a **Search** box that filters by name or host; press a name to open its card. A list longer than forty shows the first forty.
7. If a yellow box at the top says setup is incomplete, press **Finish setup**. It installs the write confirmations and the skill, replaces the review hook an early version installed, and keeps your previous settings as `settings.json.before-servicenow-mcp`.
8. Press **Open session** on a card to work on that instance. The Claude desktop app only starts a session on words you send yourself, so the pane writes the request into the chat box: press Enter there and Claude starts a new session in that instance's folder, which begins by reporting the instance, whether it is writable, and your current application and update set. Each instance needs a session of its own because the server reads the `.env` of the folder its session starts in. Outside the desktop app the pane tells you which folder to start a session in yourself.

## If the test says HTTP 401 but the password is right

Newer instances refuse basic auth on the API for a user who can sign in to the UI with the same password. In ServiceNow, open **User Administration > Users**, open the user, and add the role `snc_basic_auth_api_access` in the **Roles** list, then press **Test connection** again. If that role does not exist, or multi-factor authentication is required for the user, add the instance with **Add with OAuth** instead. The pane shows this advice whenever a basic auth test is refused.

## What it does and does not do

- It writes `<folder for instances>/<name>/.env` and a `.gitignore` holding `.env`. An existing `.env` is never overwritten.
- The password is visible while you type: the plugin UI has no masked field. It is written only to that `.env`. It is not kept in the plugin's state or store and is not added to the conversation.
- It gives the model no tools. Only a person pressing a button creates a folder, runs a test, installs the server or allows writes.
- A new instance is read-only for Claude unless it is a Personal Developer Instance. After a successful test, a read-only instance shows **Allow writes**; pressing it asks you to confirm the instance is not production, and only that second press records the approval (it runs `servicenow-mcp-setup --allow-write <host>` for you). Where your organisation has installed a system policy, that policy still decides and the button reports so. Restart any session already open on the instance afterwards.
- The connection test runs `python -m servicenow_mcp.doctor`, which makes one read-only call. It needs servicenow-mcp 0.3.1 or later.

## Develop

```
claude plugin validate plugins/servicenow-instances
claude plugin test plugins/servicenow-instances
```
