"""
Connection check -- `python -m servicenow_mcp.doctor [--env-file PATH]`.

Loads the .env exactly as the server does, makes one read-only call and prints
a single JSON object on stdout. Credentials are never printed.
"""

import json
import sys


def report(load_env: bool = True) -> dict:
    from servicenow_mcp import __version__, policy, server

    out: dict = {"ok": False, "version": __version__}
    if load_env:
        server._load_env()
    if not server._env_loaded:
        out["error"] = server._env_error or "ServiceNow credentials are not configured."
        return out

    p = server._policy()
    out.update(host=p["host"], access_mode=p["mode"], access_reason=p["reason"])
    if p["mode"] == policy.BLOCKED:
        out["error"] = f"This instance may not be used: {p['reason']}"
        return out

    try:
        user = server._current_user()
    except KeyError as e:
        out["error"] = f"{e.args[0]} is missing from .env (required for OAuth)."
        return out
    except Exception as e:
        out["error"] = str(e) or type(e).__name__
        return out

    out.update(ok=True, user=user.get("user_name"), user_display_name=user.get("name"))
    return out


def main():
    result = report()
    print(json.dumps(result))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
