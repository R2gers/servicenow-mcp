"""
Instance policy -- decides whether an instance may be written to, read only, or not used at all.

The default is deliberately safe: an instance nobody has vouched for is READ-ONLY.
Write access has to be granted from outside the project folder, because the
project folder is where the assistant works and can create files.

Sources, strongest first:

1. System policy  (admin-managed, wins over everything)
     Windows:      %ProgramData%\\servicenow-mcp\\policy.json
     macOS/Linux:  /etc/servicenow-mcp/policy.json
2. User policy    (the developer's own choice, written by `servicenow-mcp-setup`)
     ~/.servicenow-mcp/policy.json
3. Project .env   can only TIGHTEN: SERVICENOW_READ_ONLY=true

Policy file format (host names or globs, matched against the instance host):

    {
      "write_instances":   ["acmedev.service-now.com", "acme-sbx*.service-now.com"],
      "blocked_instances": ["acme.service-now.com"]
    }

Without a system policy, Personal Developer Instances (dev12345.service-now.com)
are writable by default. With a system policy, only what it lists is writable.
"""

import fnmatch
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

WRITE = "write"
READ_ONLY = "read_only"
BLOCKED = "blocked"

# Personal Developer Instances handed out by developer.servicenow.com
_PDI_RE = re.compile(r"^dev\d+\.service-now\.com$")
_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")

_TRUE = {"1", "true", "yes", "on"}


def system_policy_path() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "servicenow-mcp" / "policy.json"
    return Path("/etc/servicenow-mcp/policy.json")


def user_policy_path() -> Path:
    return Path.home() / ".servicenow-mcp" / "policy.json"


def host_of(instance_url: str) -> str:
    """Lower-case host name of an instance URL ('' if it cannot be parsed)."""
    url = (instance_url or "").strip()
    if "://" not in url:
        url = "https://" + url
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def is_pdi(host: str) -> bool:
    return bool(_PDI_RE.fullmatch(host or ""))


def _patterns(values) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(v).strip().lower() for v in values if str(v).strip()]


def read_policy_file(path: Path) -> tuple[dict | None, str | None]:
    """Return (policy, error). policy is None when the file does not exist.

    A file that exists but cannot be parsed returns ({}, error): callers treat
    that as "a policy is present and grants nothing", never as "no policy".
    """
    try:
        if not path.exists():
            return None, None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}, f"{path} is not a JSON object"
        return {
            "write_instances": _patterns(data.get("write_instances")),
            "blocked_instances": _patterns(data.get("blocked_instances")),
        }, None
    except (OSError, ValueError) as e:
        return {}, f"could not read {path}: {e}"


def _matches(host: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        if fnmatch.fnmatchcase(host, pattern):
            return pattern
    return None


def decide(host: str, system: dict | None, user: dict | None, read_only_flag: bool) -> tuple[str, str]:
    """Pure decision function. Returns (mode, human-readable reason)."""
    if not host:
        return BLOCKED, "the instance URL has no valid host name"

    for label, policy in (("system", system), ("user", user)):
        hit = _matches(host, (policy or {}).get("blocked_instances", []))
        if hit:
            return BLOCKED, f"{host} is blocked by the {label} policy (matches '{hit}')"

    if read_only_flag:
        return READ_ONLY, "SERVICENOW_READ_ONLY is set in .env"

    if system is not None:
        hit = _matches(host, system.get("write_instances", []))
        if hit:
            return WRITE, f"{host} is allowed by the system policy (matches '{hit}')"
        return READ_ONLY, (
            f"{host} is not listed in write_instances of the system policy. "
            f"Ask your ServiceNow platform admin to add it"
        )

    hit = _matches(host, (user or {}).get("write_instances", []))
    if hit:
        return WRITE, f"{host} is allowed by your user policy (matches '{hit}')"
    if is_pdi(host):
        return WRITE, f"{host} is a Personal Developer Instance"
    return READ_ONLY, (
        f"{host} has not been approved for writes. If it is a NON-production instance, "
        f"the developer can approve it by running this in their own terminal: "
        f"servicenow-mcp-setup --allow-write {host}"
    )


def resolve(instance_url: str, read_only_env: str | None = None) -> dict:
    """Load the policy files and decide the mode for this instance."""
    host = host_of(instance_url)
    system, system_err = read_policy_file(system_policy_path())
    user, user_err = read_policy_file(user_policy_path())
    for err in (system_err, user_err):
        if err:
            print(f"servicenow-mcp: WARNING - {err}; it grants no write access", file=sys.stderr)
    flag = (read_only_env or "").strip().lower() in _TRUE
    mode, reason = decide(host, system, user, flag)
    return {"host": host, "mode": mode, "reason": reason, "system_policy": system is not None}


def allow_write(host: str, path: Path | None = None) -> Path:
    """Add a host to the user policy's write_instances. Used by the setup command only."""
    host = host_of(host)
    if not host or not _HOST_RE.fullmatch(host):
        raise ValueError(f"'{host}' is not a valid host name. Globs are not accepted here; edit the policy file by hand for those.")
    path = path or user_policy_path()
    data: dict = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not a JSON object; fix or delete it first.")
    writes = data.get("write_instances")
    if not isinstance(writes, list):
        writes = []
    if host not in [str(w).strip().lower() for w in writes]:
        writes.append(host)
    data["write_instances"] = writes
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path
