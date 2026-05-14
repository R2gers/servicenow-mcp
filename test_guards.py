"""Quick test to verify the server-side guards work correctly."""
import sys
sys.path.insert(0, ".")

from servicenow_mcp.server import (
    _guard_table, _guard_fields, _guard_no_wipe,
    BLOCKED_TABLES
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
test("Block sys_update_set update",
     lambda: _guard_table("sys_update_set", "update"))
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
     lambda: _guard_table("x_fusi_presales_request", "update"), expect_error=False)

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

print("\n" + "=" * 60)
print(f"Results: {passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("All guards working correctly!")
