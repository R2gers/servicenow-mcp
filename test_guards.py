"""Quick test to verify the server-side guards work correctly."""
import sys
sys.path.insert(0, ".")

from servicenow_mcp.server import (
    _guard_table, _guard_fields, _guard_no_wipe, _guard_restricted_fields,
    BLOCKED_TABLES, RESTRICTED_TABLES
)

passed = 0
failed = 0

def test(name, fn, expect_error=True):
    global passed, failed
    try:
        fn()
        if expect_error:
            print(f"  FAIL: {name} - expected error but none raised")
            failed += 1
        else:
            print(f"  PASS: {name}")
            passed += 1
    except (PermissionError, ValueError) as e:
        if expect_error:
            print(f"  PASS: {name} - blocked with: {e}")
            passed += 1
        else:
            print(f"  FAIL: {name} - unexpected error: {e}")
            failed += 1

print("=" * 60)
print("Testing server-side guards")
print("=" * 60)

print("\n--- Blocked tables ---")
test("Block sys_security_acl update",
     lambda: _guard_table("sys_security_acl", "update"))
test("Block sys_properties create",
     lambda: _guard_table("sys_properties", "create"))
test("Block sys_store_app create",
     lambda: _guard_table("sys_store_app", "create"))

print("\n--- Allowed tables ---")
test("Allow sys_user create",
     lambda: _guard_table("sys_user", "create"), expect_error=False)
test("Allow sys_user_role create",
     lambda: _guard_table("sys_user_role", "create"), expect_error=False)
test("Allow sys_user_has_role create",
     lambda: _guard_table("sys_user_has_role", "create"), expect_error=False)
test("Allow sys_user_group create",
     lambda: _guard_table("sys_user_group", "create"), expect_error=False)
test("Allow sys_user_grmember create",
     lambda: _guard_table("sys_user_grmember", "create"), expect_error=False)
test("Allow incident create",
     lambda: _guard_table("incident", "create"), expect_error=False)
test("Allow sp_widget update",
     lambda: _guard_table("sp_widget", "update"), expect_error=False)
test("Allow custom table",
     lambda: _guard_table("x_myapp_custom_table", "update"), expect_error=False)

print("\n--- Restricted tables (sys_update_set) ---")
test("Block sys_update_set create",
     lambda: _guard_table("sys_update_set", "create"))
test("Allow sys_update_set update (table level)",
     lambda: _guard_table("sys_update_set", "update"), expect_error=False)
test("Allow description update on sys_update_set",
     lambda: _guard_restricted_fields("sys_update_set", {"description": "Session recap"}), expect_error=False)
test("Block name update on sys_update_set",
     lambda: _guard_restricted_fields("sys_update_set", {"name": "Hacked"}))
test("Block state update on sys_update_set",
     lambda: _guard_restricted_fields("sys_update_set", {"state": "complete"}))
test("Block mixed fields on sys_update_set (description + name)",
     lambda: _guard_restricted_fields("sys_update_set", {"description": "ok", "name": "bad"}))
test("No restriction on normal tables",
     lambda: _guard_restricted_fields("incident", {"name": "test", "state": "1"}), expect_error=False)

print("\n--- Blocked fields ---")
test("Block sys_id modification",
     lambda: _guard_fields({"sys_id": "abc123"}))
test("Block sys_created_by modification",
     lambda: _guard_fields({"sys_created_by": "admin"}))
test("Block sys_created_on modification",
     lambda: _guard_fields({"sys_created_on": "2024-01-01"}))

print("\n--- Allowed fields ---")
test("Allow name field",
     lambda: _guard_fields({"name": "Test"}), expect_error=False)
test("Allow active field",
     lambda: _guard_fields({"active": "true"}), expect_error=False)
test("Allow description field",
     lambda: _guard_fields({"description": "Hello"}), expect_error=False)

print("\n--- Anti-wipe protection ---")
test("Block empty string wipe",
     lambda: _guard_no_wipe({"template": ""}, "sp_widget/abc"))
test("Allow whitespace (not truly empty)",
     lambda: _guard_no_wipe({"script": "   "}, "sp_widget/abc"), expect_error=False)
test("Block multiple field wipe",
     lambda: _guard_no_wipe({"template": "", "css": ""}, "sp_widget/abc"))

print("\n--- Allowed values ---")
test("Allow normal text",
     lambda: _guard_no_wipe({"template": "<div>Hello</div>"}, "sp_widget/abc"), expect_error=False)
test("Allow single space (explicit clear)",
     lambda: _guard_no_wipe({"css": " "}, "sp_widget/abc"), expect_error=False)

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  PASS: {name}")
        passed += 1
    else:
        print(f"  FAIL: {name} {detail}")
        failed += 1


# ── Everything below runs against a fake HTTP layer: no network is used. ────
import servicenow_mcp.server as srv
from servicenow_mcp.server import _merge_guards, _valid_table, _valid_sys_id, _error_message
from servicenow_mcp.setup import _apply_confirmation_rules, ASK_RULES

SYS_ID = "0123456789abcdef0123456789abcdef"

print("\n--- Table name validation (path traversal / guard bypass) ---")
for bad in ["incident/../sys_properties", "sys_properties ", " sys_properties", "Sys_Properties",
            "sys_properties?x=1", "sys_properties/", "sys_properties#", "sys%5fproperties",
            "incident\n", "", "..", None, 42]:
    test(f"Reject table name {bad!r}", lambda b=bad: _valid_table(b))
    test(f"Guard rejects table name {bad!r}", lambda b=bad: _guard_table(b, "update"))
test("Accept incident", lambda: _valid_table("incident"), expect_error=False)
test("Accept scoped table", lambda: _valid_table("x_myapp_custom_table"), expect_error=False)

print("\n--- sys_id validation ---")
for bad in ["../sys_properties/" + SYS_ID, SYS_ID + "/..", SYS_ID.upper(), "abc", "", None,
            SYS_ID + "?sysparm_x=1"]:
    test(f"Reject sys_id {bad!r}", lambda b=bad: _valid_sys_id(b))
test("Accept valid sys_id", lambda: _valid_sys_id(SYS_ID), expect_error=False)


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None, text=None):
        self.status_code = status
        self._body = {"result": {}} if body is None else body
        self._text = text
        self.headers = headers or {}

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        if self._text is not None:
            raise ValueError("not json")
        return self._body


calls = []
next_response = [FakeResponse()]
next_exception = [None]


def fake_request(method, url, **kwargs):
    calls.append({"method": method, "url": url, **kwargs})
    if next_exception[0]:
        raise next_exception[0]
    return next_response[0]


srv.requests.request = fake_request
srv._env_loaded = True
srv.os.environ["SERVICENOW_INSTANCE_URL"] = "https://unit-test.invalid/"
srv.os.environ["SERVICENOW_AUTH_TYPE"] = "basic"
srv.os.environ["SERVICENOW_USERNAME"] = "u"
srv.os.environ["SERVICENOW_PASSWORD"] = "p"

# The guard tests below need a writable instance; the instance policy has its own section.
WRITABLE = {"host": "unit-test.invalid", "mode": "write", "reason": "unit test", "system_policy": False}
srv._instance_policy = dict(WRITABLE)
# ...and no update-set lookups; that check has its own section too.
srv.os.environ["SERVICENOW_UPDATE_SET_CHECK"] = "off"


def dispatch_blocked(name, args, exc=(PermissionError, ValueError)):
    """True when the tool call is refused AND no HTTP request was sent."""
    calls.clear()
    try:
        srv._dispatch(name, args)
    except exc:
        return len(calls) == 0
    return False


print("\n--- End to end: blocked writes never reach the network ---")
check("Traversal in table on update",
      dispatch_blocked("sn_update_record", {"table": "incident/../sys_properties", "sys_id": SYS_ID, "data": {"value": "x"}}))
check("Traversal in sys_id on update",
      dispatch_blocked("sn_update_record", {"table": "incident", "sys_id": "../sys_properties/" + SYS_ID, "data": {"value": "x"}}))
check("Traversal in table on create",
      dispatch_blocked("sn_create_record", {"table": "incident/../sys_security_acl", "data": {"name": "x"}}))
check("Traversal in table on read",
      dispatch_blocked("sn_query", {"table": "incident/../sys_properties"}))
check("Traversal in sys_id on read",
      dispatch_blocked("sn_get_record", {"table": "incident", "sys_id": "../../x"}))
check("Blocked table on update",
      dispatch_blocked("sn_update_record", {"table": "sys_properties", "sys_id": SYS_ID, "data": {"value": "x"}}))
check("Blocked field with different case",
      dispatch_blocked("sn_update_record", {"table": "incident", "sys_id": SYS_ID, "data": {"SYS_ID": "x"}}))
check("Field name with path characters",
      dispatch_blocked("sn_update_record", {"table": "incident", "sys_id": SYS_ID, "data": {"a/b": "x"}}))
check("Empty data on update",
      dispatch_blocked("sn_update_record", {"table": "incident", "sys_id": SYS_ID, "data": {}}))
check("Widget update with nothing to change",
      dispatch_blocked("sn_update_widget", {"widget_id": SYS_ID}))
check("Script include wipe",
      dispatch_blocked("sn_update_script_include", {"sys_id": SYS_ID, "script": ""}))
check("Script include with bad sys_id",
      dispatch_blocked("sn_update_script_include", {"sys_id": "../x", "script": "var a;"}))

print("\n--- End to end: allowed calls build the expected request ---")
calls.clear()
next_response[0] = FakeResponse(200, {"result": {"sys_id": SYS_ID}})
out = srv._dispatch("sn_update_record", {"table": "incident", "sys_id": SYS_ID, "data": {"short_description": "x"}})
check("Update goes to the exact table URL",
      len(calls) == 1 and calls[0]["url"] == f"https://unit-test.invalid/api/now/table/incident/{SYS_ID}"
      and calls[0]["method"] == "PATCH", str(calls))
check("Every request has a timeout", calls[0].get("timeout", 0) > 0)
check("Redirects are not followed", calls[0].get("allow_redirects") is False)
check("Update returns status", out == {"status": "updated", "sys_id": SYS_ID})

print("\n--- Error reporting ---")


def dispatch_error(args=None):
    calls.clear()
    try:
        srv._dispatch("sn_get_record", args or {"table": "incident", "sys_id": SYS_ID})
    except RuntimeError as e:
        return str(e)
    return ""


next_response[0] = FakeResponse(403, {"error": {"message": "Operation Failed", "detail": "ACL Exception Update Failed due to security constraints"}})
msg = dispatch_error()
check("403 includes ServiceNow message and detail",
      "403" in msg and "Operation Failed" in msg and "security constraints" in msg, msg)
next_response[0] = FakeResponse(429, {"error": {"message": "Rate limit exceeded"}}, headers={"Retry-After": "60"})
msg = dispatch_error()
check("429 includes Retry-After", "429" in msg and "60" in msg, msg)
next_response[0] = FakeResponse(302, text="<html>login</html>")
msg = dispatch_error()
check("Redirect is an error, not data", "302" in msg and "redirect" in msg.lower(), msg)
next_response[0] = FakeResponse(200, text="<html>Instance Hibernating</html>")
msg = dispatch_error()
check("HTML body is an error, not data", "non-JSON" in msg, msg)
next_exception[0] = srv.requests.Timeout("slow")
msg = dispatch_error()
check("Timeout gives a clear message", "did not answer" in msg, msg)
next_exception[0] = None
next_response[0] = FakeResponse()
srv.os.environ["SERVICENOW_INSTANCE_URL"] = "http://unit-test.invalid"
msg = dispatch_error()
check("Plain http instance URL is refused", "https://" in msg and len(calls) == 0, msg)
srv.os.environ["SERVICENOW_INSTANCE_URL"] = "https://unit-test.invalid"

print("\n--- Project guards.json can only tighten ---")
defaults = {
    "blocked_tables": ["sys_security_acl", "sys_properties"],
    "restricted_tables": {"sys_update_set": ["description"]},
    "blocked_fields": ["sys_id", "sys_created_by"],
}
g, w = _merge_guards(defaults, {"blocked_tables": []})
check("Empty blocked_tables does not unblock defaults",
      set(g["blocked_tables"]) == {"sys_security_acl", "sys_properties"} and w, str(g))
g, w = _merge_guards(defaults, {"blocked_tables": ["sys_user_has_role"]})
check("Project can add a blocked table",
      set(g["blocked_tables"]) == {"sys_security_acl", "sys_properties", "sys_user_has_role"}, str(g))
g, w = _merge_guards(defaults, {"blocked_fields": []})
check("Empty blocked_fields does not unblock defaults", set(g["blocked_fields"]) == {"sys_id", "sys_created_by"} and w)
g, w = _merge_guards(defaults, {"restricted_tables": {}})
check("Empty restricted_tables keeps defaults", g["restricted_tables"] == {"sys_update_set": ["description"]} and w)
g, w = _merge_guards(defaults, {"restricted_tables": {"sys_update_set": ["description", "state", "name"]}})
check("Cannot widen a restricted table", g["restricted_tables"]["sys_update_set"] == ["description"] and w)
g, w = _merge_guards(defaults, {"restricted_tables": {"sys_update_set": []}})
check("Can narrow a restricted table to nothing", g["restricted_tables"]["sys_update_set"] == [] and not w, str(w))
g, w = _merge_guards(defaults, {"restricted_tables": {"sys_update_set": ["description"], "sys_user": ["email"]}})
check("Can add a restricted table", g["restricted_tables"].get("sys_user") == ["email"] and not w)
g, w = _merge_guards(defaults, ["not", "a", "dict"])
check("Malformed override is ignored", set(g["blocked_tables"]) == {"sys_security_acl", "sys_properties"} and w)
g, w = _merge_guards(defaults, {})
check("No override means defaults, no warnings", g["restricted_tables"] == {"sys_update_set": ["description"]} and not w)

print("\n--- Setup: confirmation rules ---")
legacy = {
    "permissions": {"allow": ["Bash(ls)"]},
    "hooks": {"PreToolUse": [
        {"matcher": "mcp__servicenow__(sn_update_record|sn_create_record)", "hooks": [{"type": "prompt", "prompt": "p"}]},
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]},
    ]},
}
s, changes = _apply_confirmation_rules(legacy)
check("All write tools get an ask rule", all(r in s["permissions"]["ask"] for r in ASK_RULES) and len(ASK_RULES) == 4)
check("Legacy prompt hook is removed", all("servicenow" not in h["matcher"] for h in s["hooks"]["PreToolUse"]))
check("Unrelated hooks and allow rules are kept",
      s["hooks"]["PreToolUse"] == [{"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}]
      and s["permissions"]["allow"] == ["Bash(ls)"])
check("Second run changes nothing", _apply_confirmation_rules(s)[1] == [])
s, changes = _apply_confirmation_rules({})
check("Works on empty settings", s == {"permissions": {"ask": ASK_RULES}})

print("\n--- Instance policy: decision ---")
import asyncio
import tempfile
from pathlib import Path
from servicenow_mcp import policy

SYSTEM = {"write_instances": ["acmedev.service-now.com", "acme-sbx*.service-now.com"],
          "blocked_instances": ["acme.service-now.com"]}
USER_ALLOWS_PROD = {"write_instances": ["acme.service-now.com", "acmetest.service-now.com"], "blocked_instances": []}


def mode(host, system=None, user=None, flag=False):
    return policy.decide(host, system, user, flag)[0]


check("Unknown instance is read-only by default", mode("acme.service-now.com") == policy.READ_ONLY)
check("PDI is writable with no policy", mode("dev12345.service-now.com") == policy.WRITE)
check("PDI look-alike is not a PDI", mode("dev12345.service-now.com.evil.example") == policy.READ_ONLY
      and mode("xdev12345.service-now.com") == policy.READ_ONLY)
check("User policy can approve an instance", mode("acmetest.service-now.com", user=USER_ALLOWS_PROD) == policy.WRITE)
check("System policy approves listed host", mode("acmedev.service-now.com", SYSTEM) == policy.WRITE)
check("System policy approves glob", mode("acme-sbx2.service-now.com", SYSTEM) == policy.WRITE)
check("System policy: unlisted host is read-only", mode("other.service-now.com", SYSTEM) == policy.READ_ONLY)
check("System policy: PDI is not auto-approved", mode("dev12345.service-now.com", SYSTEM) == policy.READ_ONLY)
check("User policy cannot override system policy",
      mode("acmetest.service-now.com", SYSTEM, USER_ALLOWS_PROD) == policy.READ_ONLY)
check("System block beats user approval", mode("acme.service-now.com", SYSTEM, USER_ALLOWS_PROD) == policy.BLOCKED)
check("User block beats PDI default",
      mode("dev12345.service-now.com", user={"blocked_instances": ["dev12345.service-now.com"]}) == policy.BLOCKED)
check("Unreadable system policy grants nothing", mode("acmedev.service-now.com", {}) == policy.READ_ONLY)
check("SERVICENOW_READ_ONLY tightens an approved instance",
      mode("acmedev.service-now.com", SYSTEM, flag=True) == policy.READ_ONLY)
check("SERVICENOW_READ_ONLY does not unblock", mode("acme.service-now.com", SYSTEM, flag=True) == policy.BLOCKED)
check("Empty host is blocked", mode("") == policy.BLOCKED)
check("Host is parsed and lower-cased", policy.host_of("https://AcmeDev.Service-Now.com/nav") == "acmedev.service-now.com"
      and policy.host_of("acme.service-now.com") == "acme.service-now.com")

print("\n--- Instance policy: files ---")
with tempfile.TemporaryDirectory() as tmp:
    pfile = Path(tmp) / "nested" / "policy.json"
    check("Missing file means no policy", policy.read_policy_file(pfile) == (None, None))
    policy.allow_write("https://AcmeDev.service-now.com/", pfile)
    policy.allow_write("acmedev.service-now.com", pfile)
    loaded, err = policy.read_policy_file(pfile)
    check("allow_write stores the host once", loaded["write_instances"] == ["acmedev.service-now.com"] and err is None)
    test("allow_write refuses globs", lambda: policy.allow_write("*.service-now.com", pfile))
    test("allow_write refuses junk", lambda: policy.allow_write("a b;c", pfile))
    pfile.write_text("{not json", encoding="utf-8")
    loaded, err = policy.read_policy_file(pfile)
    check("Corrupt file is a present policy that grants nothing", loaded == {} and err)
    pfile.write_text('["a"]', encoding="utf-8")
    check("Non-object file grants nothing", policy.read_policy_file(pfile)[0] == {})

print("\n--- Instance policy: enforcement in the server ---")
WRITE_CALLS = [
    ("sn_update_record", {"table": "incident", "sys_id": SYS_ID, "data": {"short_description": "x"}}),
    ("sn_create_record", {"table": "incident", "data": {"short_description": "x"}}),
    ("sn_update_widget", {"widget_id": SYS_ID, "css": "a{}"}),
    ("sn_update_script_include", {"sys_id": SYS_ID, "script": "var a;"}),
]
check("WRITE_TOOLS matches the setup ask rules",
      {f"mcp__servicenow__{t}" for t in srv.WRITE_TOOLS} == set(ASK_RULES))

srv._instance_policy = {"host": "acme.service-now.com", "mode": "read_only", "reason": "not approved", "system_policy": False}
for tool, tool_args in WRITE_CALLS:
    check(f"Read-only instance refuses {tool} without a request",
          dispatch_blocked(tool, tool_args, exc=(PermissionError,)))
calls.clear()
next_response[0] = FakeResponse(200, {"result": []})
srv._dispatch("sn_query", {"table": "incident"})
check("Read-only instance still allows reads", len(calls) == 1 and calls[0]["method"] == "GET")
listed = {t.name for t in asyncio.run(srv.list_tools())}
check("Read-only instance hides the write tools", not (listed & srv.WRITE_TOOLS) and "sn_query" in listed, str(listed))

srv._instance_policy = {"host": "acme.service-now.com", "mode": "blocked", "reason": "blocked by policy", "system_policy": True}
check("Blocked instance refuses reads without a request",
      dispatch_blocked("sn_query", {"table": "incident"}, exc=(PermissionError,)))
check("Blocked instance refuses writes without a request",
      dispatch_blocked(*WRITE_CALLS[0], exc=(PermissionError,)))

srv._instance_policy = dict(WRITABLE)
listed = {t.name for t in asyncio.run(srv.list_tools())}
check("Writable instance offers the write tools", srv.WRITE_TOOLS <= listed)

print("\n--- Scope and update set: simulated instance ---")
USER_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
APP_ID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
OTHER_APP = "cccccccccccccccccccccccccccccccc"
US_GOOD = "11111111111111111111111111111111"
US_DEFAULT = "22222222222222222222222222222222"
US_DONE = "33333333333333333333333333333333"
US_GLOBAL = "44444444444444444444444444444444"
REC = "dddddddddddddddddddddddddddddddd"


class FakeInstance:
    """Answers Table API GETs from in-memory rows; records every write."""

    def __init__(self, prefs, record_scope=APP_ID):
        self.prefs = prefs
        self.record_scope = record_scope
        self.writes = []
        self.fail_tables = set()
        self.parents = {"sys_script": "sys_metadata", "sp_widget": "sys_metadata",
                        "sys_script_include": "sys_metadata", "incident": "task", "task": "",
                        "sys_metadata": "", "u_synced": "", "sys_user": ""}
        self.update_sets = {
            US_GOOD: {"sys_id": US_GOOD, "name": "STRY001 portal work", "state": "in progress", "is_default": "false",
                      "application": {"link": "x", "value": APP_ID}},
            US_DEFAULT: {"sys_id": US_DEFAULT, "name": "Default", "state": "in progress", "is_default": "true",
                         "application": {"link": "x", "value": APP_ID}},
            US_DONE: {"sys_id": US_DONE, "name": "STRY000 old", "state": "complete", "is_default": "false",
                      "application": {"link": "x", "value": APP_ID}},
            US_GLOBAL: {"sys_id": US_GLOBAL, "name": "Global work", "state": "in progress", "is_default": "false",
                        "application": "global"},
        }

    def __call__(self, method, url, **kw):
        calls.append({"method": method, "url": url, **kw})
        path = url.split("/api/now/table/", 1)[1]
        table, _, sys_id = path.partition("/")
        params = kw.get("params") or {}
        query = params.get("sysparm_query", "")
        if table in self.fail_tables:
            return FakeResponse(403, {"error": {"message": "ACL", "detail": "denied"}})
        if method != "GET":
            self.writes.append((method, table, sys_id))
            return FakeResponse(200, {"result": {"sys_id": sys_id or REC}})
        if table == "sys_user":
            return FakeResponse(200, {"result": [{"sys_id": USER_ID, "user_name": "u", "name": "Unit Tester"}]})
        if table == "sys_user_preference":
            return FakeResponse(200, {"result": [{"name": k, "value": v} for k, v in self.prefs.items()]})
        if table == "sys_scope":
            return FakeResponse(200, {"result": {"name": "My App", "scope": "x_acme_myapp"}})
        if table == "sys_update_set":
            return FakeResponse(200, {"result": self.update_sets.get(sys_id, {})})
        if table == "sys_db_object":
            name = query.split("name=", 1)[1]
            if name not in self.parents:
                return FakeResponse(200, {"result": []})
            return FakeResponse(200, {"result": [{"name": name, "super_class.name": self.parents[name]}]})
        if table == "sys_dictionary":
            return FakeResponse(200, {"result": [{"name": "u_synced"}] if "u_synced" in query else []})
        if sys_id:
            scope = self.record_scope
            return FakeResponse(200, {"result": {"sys_scope": scope if scope == "global" else {"link": "x", "value": scope}}})
        return FakeResponse(200, {"result": []})


def use_instance(prefs, **kw):
    inst = FakeInstance(prefs, **kw)
    srv.requests.request = inst
    srv._user_cache.clear()
    srv._captured_cache.clear()
    calls.clear()
    return inst


def write_outcome(inst, tool, tool_args):
    """('ok', result) or ('blocked', message). Blocked must mean no write was sent."""
    try:
        return "ok", srv._dispatch(tool, tool_args)
    except PermissionError as e:
        return ("blocked" if not inst.writes else "LEAKED"), str(e)


srv.os.environ["SERVICENOW_UPDATE_SET_CHECK"] = "on"
GOOD_PREFS = {"apps.current_app": APP_ID, f"updateSetForScope{APP_ID}": US_GOOD, "sys_update_set": US_GOOD}
BR_UPDATE = ("sn_update_record", {"table": "sys_script", "sys_id": REC, "data": {"script": "x"}})
BR_CREATE = ("sn_create_record", {"table": "sys_script", "data": {"name": "x"}})

inst = use_instance(GOOD_PREFS)
ctx = srv._dispatch("sn_get_context", {})
check("Context reports application and update set",
      ctx["application"] == {"sys_id": APP_ID, "name": "My App", "scope": "x_acme_myapp"}
      and ctx["update_set"]["name"] == "STRY001 portal work" and ctx["config_writes_ok"] and not ctx["problems"], str(ctx))
check("Context reports instance access mode", ctx["instance"]["access_mode"] == "write" and ctx["update_set_check"] == "on")
check("Context only reads", not inst.writes and all(c["method"] == "GET" for c in calls))
check("Context does not use the side-effecting UI endpoint", all("concoursepicker" not in c["url"] for c in calls))

print("\n--- Scope and update set: writes ---")
inst = use_instance(GOOD_PREFS)
state, out = write_outcome(inst, *BR_UPDATE)
check("Config update allowed in a real update set",
      state == "ok" and inst.writes == [("PATCH", "sys_script", REC)]
      and out["captured_in"] == {"update_set": "STRY001 portal work", "update_set_sys_id": US_GOOD, "application": "My App"},
      str(out))
inst = use_instance(GOOD_PREFS)
state, out = write_outcome(inst, *BR_CREATE)
check("Config create allowed in a real update set", state == "ok" and "captured_in" in out, str(out))

for label, prefs in [
    ("Default update set", {"apps.current_app": APP_ID, f"updateSetForScope{APP_ID}": US_DEFAULT}),
    ("completed update set", {"apps.current_app": APP_ID, f"updateSetForScope{APP_ID}": US_DONE}),
    ("no update set chosen", {"apps.current_app": APP_ID}),
    ("update set of another application", {"apps.current_app": APP_ID, "sys_update_set": US_GLOBAL}),
    ("no preferences at all", {}),
]:
    for tool, tool_args in (BR_UPDATE, BR_CREATE,
                            ("sn_update_widget", {"widget_id": REC, "css": "a{}"}),
                            ("sn_update_script_include", {"sys_id": REC, "script": "var a;"})):
        inst = use_instance(prefs, record_scope=prefs.get("apps.current_app", "global"))
        state, out = write_outcome(inst, tool, tool_args)
        check(f"{tool} blocked with {label}", state == "blocked", f"{state}: {out}")

inst = use_instance({})
ctx = srv._dispatch("sn_get_context", {})
check("No preferences means Global and a reported problem",
      ctx["application"]["sys_id"] == "global" and not ctx["config_writes_ok"] and ctx["problems"], str(ctx))

inst = use_instance({"apps.current_app": "global", "updateSetForScopeglobal": US_GLOBAL}, record_scope="global")
state, out = write_outcome(inst, *BR_UPDATE)
check("Global scope with a real update set is allowed", state == "ok" and out["captured_in"]["application"] == "Global", str(out))

inst = use_instance(GOOD_PREFS, record_scope=OTHER_APP)
state, out = write_outcome(inst, *BR_UPDATE)
check("Update to a record of another application is blocked", state == "blocked" and "different application" in out, out)
inst = use_instance(GOOD_PREFS, record_scope="global")
state, out = write_outcome(inst, *BR_UPDATE)
check("Update to a global record from a scoped app is blocked", state == "blocked", out)

DEFAULT_PREFS = {"apps.current_app": APP_ID, f"updateSetForScope{APP_ID}": US_DEFAULT}
inst = use_instance(DEFAULT_PREFS)
state, out = write_outcome(inst, "sn_update_record", {"table": "incident", "sys_id": REC, "data": {"state": "2"}})
check("Data table (incident) is not affected by the update set check",
      state == "ok" and "captured_in" not in out and inst.writes == [("PATCH", "incident", REC)], str(out))
inst = use_instance(DEFAULT_PREFS)
state, out = write_outcome(inst, "sn_update_record", {"table": "u_synced", "sys_id": REC, "data": {"a": "b"}})
check("Table with update_synch attribute is treated as captured", state == "blocked", str(out))
inst = use_instance(DEFAULT_PREFS)
state, out = write_outcome(inst, "sn_update_record", {"table": "u_unknown", "sys_id": REC, "data": {"a": "b"}})
check("Unknown table is treated as captured (safe side)", state == "blocked", str(out))

inst = use_instance(GOOD_PREFS)
inst.fail_tables = {"sys_user_preference"}
state, out = write_outcome(inst, *BR_UPDATE)
check("Unreadable preferences block the write (fail closed)", state == "blocked" and "could not verify" in out, out)

srv.os.environ["SERVICENOW_UPDATE_SET_CHECK"] = "off"
inst = use_instance(DEFAULT_PREFS)
state, out = write_outcome(inst, *BR_UPDATE)
check("Check can be switched off by the developer", state == "ok" and len(calls) == 1, str(calls))
srv.os.environ["SERVICENOW_UPDATE_SET_CHECK"] = "on"

for name, expected in [("Default", True), ("Default 1", True), ("default 12", True),
                       ("Default settings rework", False), ("STRY Default", False)]:
    check(f"Name backstop: {name!r} -> {expected}",
          srv._is_default_update_set({"name": name, "is_default": "false"}) is expected)
check("is_default flag wins over the name", srv._is_default_update_set({"name": "Anything", "is_default": "true"}))

print("\n--- Connection check (doctor) ---")
from servicenow_mcp import doctor

use_instance(GOOD_PREFS)
srv._instance_policy = dict(WRITABLE)
out = doctor.report(load_env=False)
check("Doctor reports a working connection", out["ok"] and out["user"] == "u" and out["access_mode"] == "write", str(out))
check("Doctor only reads", all(c["method"] == "GET" for c in calls), str(calls))
check("Doctor output holds no password", "SERVICENOW_PASSWORD" not in str(out) and srv.os.environ["SERVICENOW_PASSWORD"] not in out.values(), str(out))

inst = use_instance(GOOD_PREFS)
inst.fail_tables = {"sys_user"}
out = doctor.report(load_env=False)
check("Doctor reports ServiceNow's own error", not out["ok"] and "HTTP 403" in out["error"], str(out))

srv._instance_policy = {"host": "prod.example", "mode": "blocked", "reason": "blocked by policy", "system_policy": True}
use_instance(GOOD_PREFS)
out = doctor.report(load_env=False)
check("Doctor makes no request to a blocked instance", not out["ok"] and not calls, str(out))
srv._instance_policy = dict(WRITABLE)

srv._env_loaded = False
srv._env_error = "No .env file found"
out = doctor.report(load_env=False)
check("Doctor reports a missing .env", not out["ok"] and ".env" in out["error"], str(out))
srv._env_loaded = True
srv._env_error = None

print("\n" + "=" * 60)
print(f"Results: {passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("All guards working correctly!")
