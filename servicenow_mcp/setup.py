"""
servicenow-mcp setup -- installs the skill, write-confirmation rules, and MCP registration into Claude Code.
Run: servicenow-mcp-setup          (interactive or silent)
Run: servicenow-mcp-setup --quick  (skip prompts, just register)
Run: servicenow-mcp-setup --allow-write HOST  (approve one non-production instance for writes)
"""

import json
import os
import sys
import shutil
import getpass
from pathlib import Path

SKILL_DIR_NAME = "servicenow-mcp"

# Data files ship inside the package at servicenow_mcp/data/
_DATA_DIR = Path(__file__).parent / "data"

MCP_KEY = "servicenow"


def _load_skill_content() -> str:
    return (_DATA_DIR / "SKILL.md").read_text(encoding="utf-8")


# Write tools that must always show the developer a confirmation prompt.
WRITE_TOOLS = (
    "sn_update_record",
    "sn_create_record",
    "sn_update_widget",
    "sn_update_script_include",
)
ASK_RULES = [f"mcp__{MCP_KEY}__{tool}" for tool in WRITE_TOOLS]


def _claude_dir() -> Path:
    return Path.home() / ".claude"


def _install_skill():
    skill_dir = _claude_dir() / "skills" / SKILL_DIR_NAME
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(_load_skill_content(), encoding="utf-8")
    print(f"  Skill installed -> {skill_file}")


def _is_own_legacy_hook(entry: dict) -> bool:
    """True for hooks installed by earlier versions of this package."""
    matcher = entry.get("matcher", "")
    if "sn-legacy-mcp" in matcher:
        return True
    if f"mcp__{MCP_KEY}__" not in matcher:
        return False
    # Up to v0.2.0 a "prompt" hook was installed. It is evaluated by a model,
    # never shown to the developer, so it gave no real confirmation.
    return any(h.get("type") in ("prompt", "confirm") for h in entry.get("hooks", []))


def _apply_confirmation_rules(settings: dict) -> tuple[dict, list[str]]:
    """Add `permissions.ask` rules for every write tool and drop our legacy hook.

    An ask rule makes Claude Code show the developer a confirmation dialog, and
    it wins over any allow rule. Returns (settings, list of changes made).
    """
    changes: list[str] = []

    hooks = settings.get("hooks")
    if isinstance(hooks, dict) and isinstance(hooks.get("PreToolUse"), list):
        kept = [h for h in hooks["PreToolUse"] if not (isinstance(h, dict) and _is_own_legacy_hook(h))]
        if len(kept) != len(hooks["PreToolUse"]):
            changes.append("removed legacy servicenow hook (it never asked a human)")
            if kept:
                hooks["PreToolUse"] = kept
            else:
                del hooks["PreToolUse"]
            if not hooks:
                del settings["hooks"]

    permissions = settings.setdefault("permissions", {})
    ask = permissions.setdefault("ask", [])
    for rule in ASK_RULES:
        if rule not in ask:
            ask.append(rule)
            changes.append(f"ask before {rule}")

    return settings, changes


def _install_confirmations():
    settings_file = _claude_dir() / "settings.json"
    settings: dict = {}
    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text(encoding="utf-8"))
        except ValueError as e:
            print(f"  WARNING: {settings_file} is not valid JSON ({e}).")
            print("  Not touching it. Add these rules to permissions.ask by hand:")
            for rule in ASK_RULES:
                print(f"    {rule}")
            return
        if not isinstance(settings, dict):
            print(f"  WARNING: {settings_file} has an unexpected shape; not touching it.")
            return

    before = settings_file.read_text(encoding="utf-8") if settings_file.exists() else None
    settings, changes = _apply_confirmation_rules(settings)
    if not changes:
        print("  Confirmation rules already installed -- skipping")
        return

    settings_file.parent.mkdir(parents=True, exist_ok=True)
    if before is not None:
        # The removed hook may hold text the developer wrote: keep the file as it was.
        backup = settings_file.with_name("settings.json.before-servicenow-mcp")
        backup.write_text(before, encoding="utf-8")
        print(f"  Previous settings kept -> {backup}")
    settings_file.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    for change in changes:
        print(f"  {change}")
    print(f"  Confirmation rules installed -> {settings_file}")


def check() -> dict:
    """Is this machine fully set up? Reads only; `servicenow-mcp-setup --check` prints it as JSON."""
    import copy

    problems: list[str] = []
    settings_file = _claude_dir() / "settings.json"
    settings: object = {}
    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = None
            problems.append(f"{settings_file} could not be read, so write confirmations cannot be verified.")
    if isinstance(settings, dict):
        _, changes = _apply_confirmation_rules(copy.deepcopy(settings))
        if any(change.startswith("removed legacy") for change in changes):
            problems.append(
                "An old review hook from an earlier version is still installed. A model evaluates it, "
                "it is never shown to you, and it can refuse a write without saying why."
            )
        if any(change.startswith("ask before") for change in changes):
            problems.append("Write confirmations are not installed: writes would run without asking you.")

    skill_file = _claude_dir() / "skills" / SKILL_DIR_NAME / "SKILL.md"
    try:
        skill_current = skill_file.read_text(encoding="utf-8") == _load_skill_content()
    except OSError:
        skill_current = False
    if not skill_current:
        problems.append("The /servicenow skill is missing or out of date.")

    registered = False
    try:
        config = json.loads((_claude_dir().parent / ".claude.json").read_text(encoding="utf-8"))
        registered = MCP_KEY in (config.get("mcpServers") or {})
    except (OSError, ValueError, AttributeError):
        pass
    if not registered:
        problems.append("The MCP server is not registered with Claude Code.")

    return {"ok": not problems, "problems": problems}


def _find_python() -> str:
    """Find the absolute path to the Python that has servicenow_mcp installed."""
    return sys.executable


def _install_mcp_server():
    python_path = _find_python()

    # Use `claude mcp add` (the officially supported CLI command).
    # This is safe to run while Claude Code is open -- no race conditions.
    import subprocess

    # shutil.which resolves claude.cmd / claude.exe on Windows; a bare "claude"
    # passed to subprocess raises FileNotFoundError there instead of failing cleanly.
    claude = shutil.which("claude")
    result = None
    if claude:
        # Remove any existing entry first (claude mcp add won't overwrite)
        subprocess.run([claude, "mcp", "remove", MCP_KEY, "-s", "user"], capture_output=True)
        result = subprocess.run(
            [claude, "mcp", "add", MCP_KEY, "-s", "user",
             "--", python_path, "-m", "servicenow_mcp.server"],
            capture_output=True, text=True
        )

    if result is not None and result.returncode == 0:
        print(f"  MCP server registered via `claude mcp add`")
        print(f"  Python: {python_path}")
        print(f"  Module: servicenow_mcp.server")
    else:
        if result is None:
            print(f"  `claude` CLI not found on PATH, falling back to direct config write...")
        else:
            print(f"  `claude mcp add` failed ({(result.stderr or '').strip() or 'no output'}),")
            print(f"  falling back to direct config write...")
        _install_mcp_server_fallback(python_path)

    # Clean up old ~/.claude/mcp.json if it exists (wrong location from v0.1)
    old_mcp = _claude_dir() / "mcp.json"
    if old_mcp.exists():
        old_config = json.loads(old_mcp.read_text(encoding="utf-8"))
        old_servers = old_config.get("mcpServers", {})
        if MCP_KEY in old_servers or "sn-legacy-mcp" in old_servers:
            old_servers.pop(MCP_KEY, None)
            old_servers.pop("sn-legacy-mcp", None)
            if old_servers:
                old_config["mcpServers"] = old_servers
                old_mcp.write_text(json.dumps(old_config, indent=2), encoding="utf-8")
            else:
                old_mcp.unlink()
            print(f"  Cleaned up old config -> {old_mcp}")


def _install_mcp_server_fallback(python_path: str):
    """Direct config write -- used only when `claude` CLI is not on PATH."""
    mcp_file = Path.home() / ".claude.json"
    if mcp_file.exists():
        mcp_config = json.loads(mcp_file.read_text(encoding="utf-8"))
    else:
        mcp_config = {}

    servers = mcp_config.get("mcpServers", {})

    if "sn-legacy-mcp" in servers:
        del servers["sn-legacy-mcp"]
        print("  Removed old sn-legacy-mcp entry")

    servers[MCP_KEY] = {
        "command": python_path,
        "args": ["-m", "servicenow_mcp.server"]
    }
    mcp_config["mcpServers"] = servers
    mcp_file.write_text(json.dumps(mcp_config, indent=2), encoding="utf-8")
    print(f"  MCP server registered -> {mcp_file}")


def _prompt_env_setup():
    """Interactive wizard to create the .env file in the current directory."""
    env_path = Path.cwd() / ".env"

    print()
    print("  ── ServiceNow Connection Setup ──")
    print()

    if env_path.exists():
        answer = input("  .env already exists. Overwrite? [y/N]: ").strip().lower()
        if answer != "y":
            print("  Keeping existing .env")
            return

    print("  How do you authenticate with ServiceNow?")
    print()
    print("    [1] Basic Auth  — username + password (simplest, good for dev/PDI)")
    print("    [2] OAuth        — client credentials + user login (required by some orgs)")
    print()

    choice = ""
    while choice not in ("1", "2"):
        choice = input("  Choose [1/2]: ").strip()
        if not choice:
            choice = "1"

    auth_type = "basic" if choice == "1" else "oauth"
    print(f"\n  → Using {auth_type} authentication")

    print()
    instance_url = input("  Instance URL (e.g. https://mycompany.service-now.com): ").strip()
    if not instance_url:
        print("  ERROR: Instance URL is required.")
        return
    if not instance_url.startswith("https://"):
        instance_url = "https://" + instance_url
    instance_url = instance_url.rstrip("/")

    print()
    username = input("  Username: ").strip()
    if not username:
        print("  ERROR: Username is required.")
        return

    password = getpass.getpass("  Password: ")
    if not password:
        print("  ERROR: Password is required.")
        return

    lines = [
        f"SERVICENOW_INSTANCE_URL={instance_url}",
        f"SERVICENOW_AUTH_TYPE={auth_type}",
        f"SERVICENOW_USERNAME={username}",
        f"SERVICENOW_PASSWORD={password}",
    ]

    if auth_type == "oauth":
        print()
        print("  OAuth additional fields:")
        client_id = input("  Client ID: ").strip()
        if not client_id:
            print("  ERROR: Client ID is required for OAuth.")
            return
        client_secret = getpass.getpass("  Client Secret: ")
        if not client_secret:
            print("  ERROR: Client Secret is required for OAuth.")
            return
        token_url = input(f"  Token URL (default: {instance_url}/oauth_token.do): ").strip()
        token_url = token_url or f"{instance_url}/oauth_token.do"
        lines.extend([
            f"SERVICENOW_CLIENT_ID={client_id}",
            f"SERVICENOW_CLIENT_SECRET={client_secret}",
            f"SERVICENOW_TOKEN_URL={token_url}",
        ])

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _prompt_write_access(instance_url)
    print(f"\n  .env created -> {env_path}")

    gitignore = Path.cwd() / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if ".env" not in content:
            with open(gitignore, "a", encoding="utf-8") as f:
                f.write("\n.env\n")
            print("  Added .env to .gitignore")
    else:
        gitignore.write_text(".env\n", encoding="utf-8")
        print("  Created .gitignore with .env")


def _prompt_write_access(instance_url: str):
    """Ask whether Claude may write to this instance. Unapproved instances stay read-only."""
    from servicenow_mcp import policy

    host = policy.host_of(instance_url)
    state = policy.resolve(instance_url)
    print()
    print("  ── Write access ──")
    print()
    if state["mode"] == policy.WRITE:
        print(f"  Writes are allowed: {state['reason']}")
        return
    if state["mode"] == policy.BLOCKED or state["system_policy"]:
        print(f"  {state['reason']}.")
        print("  This is set by your organisation's policy and cannot be changed here.")
        return

    print(f"  {host} is READ-ONLY for Claude until you approve it.")
    print("  Only approve development, test or sandbox instances. Never production.")
    print()
    answer = input(f"  Is {host} a NON-production instance Claude may write to? [y/N]: ").strip().lower()
    if answer == "y":
        path = policy.allow_write(host)
        print(f"  Write access approved -> {path}")
    else:
        print("  Keeping it read-only. To approve it later, run:")
        print(f"    servicenow-mcp-setup --allow-write {host}")


def _allow_write_command(argv: list) -> int:
    """`servicenow-mcp-setup --allow-write HOST` -- approve one instance for writes, nothing else."""
    from servicenow_mcp import policy

    index = argv.index("--allow-write")
    if index + 1 >= len(argv):
        print("  Usage: servicenow-mcp-setup --allow-write yourinstance.service-now.com")
        return 2
    host = policy.host_of(argv[index + 1])
    system, _ = policy.read_policy_file(policy.system_policy_path())
    if system is not None:
        print(f"  A system policy is installed at {policy.system_policy_path()}.")
        print("  It decides which instances are writable; a user approval has no effect.")
        return 1
    try:
        path = policy.allow_write(host)
    except ValueError as e:
        print(f"  ERROR: {e}")
        return 2
    print(f"  {host} approved for writes -> {path}")
    print("  Restart Claude Code for this to take effect.")
    return 0


def main():
    if "--allow-write" in sys.argv:
        sys.exit(_allow_write_command(sys.argv))
    if "--check" in sys.argv:
        print(json.dumps(check()))
        sys.exit(0)

    quick = "--quick" in sys.argv

    print()
    print("  servicenow-mcp setup v" + _get_version())
    print("  " + "=" * 40)

    print("\n  [1/4] Installing /servicenow skill...")
    _install_skill()

    print("\n  [2/4] Installing write-confirmation rules...")
    _install_confirmations()

    print("\n  [3/4] Registering MCP server...")
    _install_mcp_server()

    if quick:
        print("\n  [4/4] Skipping .env setup (--quick)")
        print()
        print("  " + "=" * 40)
        print("  Setup complete! Restart Claude Code.")
        print()
        print("  Instances are read-only until approved. To let Claude write to a")
        print("  NON-production instance, run:  servicenow-mcp-setup --allow-write <host>")
        print()
        print("  Next: create a .env in your project folder:")
        print("    SERVICENOW_INSTANCE_URL=https://yourinstance.service-now.com")
        print("    SERVICENOW_USERNAME=your_user")
        print("    SERVICENOW_PASSWORD=your_pass")
    else:
        print("\n  [4/4] Configuring ServiceNow connection...")
        _prompt_env_setup()

        print()
        print("  " + "=" * 40)
        print("  Setup complete! Restart Claude Code.")

    print()


def _get_version():
    try:
        from servicenow_mcp import __version__
        return __version__
    except Exception:
        return "unknown"


def update():
    """Entry point for servicenow-mcp-update command."""
    import subprocess

    print()
    print("  servicenow-mcp updater")
    print("  " + "=" * 40)

    current = _get_version()
    print(f"  Current version: {current}")
    print(f"  Updating from GitHub...")
    print()

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade",
         "git+https://github.com/R2gers/servicenow-mcp.git"],
        capture_output=False,
    )

    if result.returncode == 0:
        print()
        print("  " + "=" * 40)
        # Re-import to get new version
        try:
            import importlib
            import servicenow_mcp
            importlib.reload(servicenow_mcp)
            new_version = servicenow_mcp.__version__
        except Exception:
            new_version = "check with: servicenow-mcp-update"
        print(f"  Updated! {current} -> {new_version}")
        print("  Restart Claude Code to pick up changes.")
    else:
        print()
        print("  Update failed. Try manually:")
        print("    pip install --upgrade git+https://github.com/R2gers/servicenow-mcp.git")

    print()


if __name__ == "__main__":
    main()
