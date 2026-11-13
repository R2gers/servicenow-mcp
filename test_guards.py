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

print("\n" + "=" * 60)
print(f"Results: {passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("All guards working correctly!")
