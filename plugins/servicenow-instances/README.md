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
3. Check **Folder for instances**. Each instance gets its own folder under it. Type another path and press Enter to change it.
4. Press **Add instance** (or **Add with OAuth**), fill in the name, instance URL, username and password, then **Create folder and .env**.
5. The pane tests the new instance and shows the result. **Test connection** repeats the test for any listed instance and shows ServiceNow's own error when it fails.
6. Start a new Claude session in the folder it created to work on that instance.

## What it does and does not do

- It writes `<folder for instances>/<name>/.env` and a `.gitignore` holding `.env`. An existing `.env` is never overwritten.
- The password is visible while you type: the plugin UI has no masked field. It is written only to that `.env`. It is not kept in the plugin's state or store and is not added to the conversation.
- It gives the model no tools. Only a person pressing a button creates a folder, runs a test or installs the server.
- It never approves write access. A new instance is read-only for Claude unless it is a Personal Developer Instance; for any other non-production instance the pane shows the command to run yourself: `servicenow-mcp-setup --allow-write <host>`.
- The connection test runs `python -m servicenow_mcp.doctor`, which makes one read-only call. It needs servicenow-mcp 0.3.1 or later.

## Develop

```
claude plugin validate plugins/servicenow-instances
claude plugin test plugins/servicenow-instances
```
