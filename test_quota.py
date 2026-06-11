#!/usr/bin/env python3
import httpx
import base64
import json
import urllib.parse
from pathlib import Path

BASE_URL = "http://localhost:8000"

def get_auth_headers(username: str, password: str) -> dict:
    auth = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {auth}"}

def test_1_propfind_quota_properties():
    print("=== Test 1: PROPFIND Quota Properties ===")
    headers = get_auth_headers("admin", "admin123")
    
    body = b"""<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:">
  <D:prop>
    <D:quota-available-bytes/>
    <D:quota-used-bytes/>
  </D:prop>
</D:propfind>"""
    
    r = httpx.request("PROPFIND", f"{BASE_URL}/", headers={
        **headers,
        "Depth": "0",
        "Content-Type": "application/xml",
    }, content=body)
    
    print(f"Status: {r.status_code}")
    print(f"Response:\n{r.text[:800]}")
    assert r.status_code == 207
    assert "quota-available-bytes" in r.text
    assert "quota-used-bytes" in r.text
    
    import re
    avail_match = re.search(r"<D:quota-available-bytes>(\d+)</D:quota-available-bytes>", r.text)
    used_match = re.search(r"<D:quota-used-bytes>(\d+)</D:quota-used-bytes>", r.text)
    assert avail_match is not None
    assert used_match is not None
    
    available = int(avail_match.group(1))
    used = int(used_match.group(1))
    print(f"Available: {available} bytes")
    print(f"Used: {used} bytes")
    assert available > 0
    assert used >= 0
    
    print("PASS\n")

def test_2_put_within_quota():
    print("=== Test 2: PUT Within Quota ===")
    headers = get_auth_headers("user1", "user123")
    
    content = b"A" * 1024
    r = httpx.put(f"{BASE_URL}/quota_test_1kb.txt", headers=headers, content=content)
    print(f"PUT Status: {r.status_code}")
    assert r.status_code in [201, 204]
    print("PASS\n")

def test_3_put_exceed_quota():
    print("=== Test 3: PUT Exceed Quota ===")
    headers = get_auth_headers("user1", "user123")
    
    large_content = b"A" * 60 * 1024 * 1024
    r = httpx.put(f"{BASE_URL}/quota_test_large.txt", headers=headers, content=large_content)
    print(f"PUT Status: {r.status_code}")
    assert r.status_code == 507, f"Expected 507, got {r.status_code}"
    print("PASS\n")

def test_4_delete_releases_quota():
    print("=== Test 4: DELETE Releases Quota ===")
    headers = get_auth_headers("user1", "user123")
    
    body = b"""<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:">
  <D:prop>
    <D:quota-used-bytes/>
  </D:prop>
</D:propfind>"""
    
    r = httpx.request("PROPFIND", f"{BASE_URL}/", headers={
        **headers,
        "Depth": "0",
        "Content-Type": "application/xml",
    }, content=body)
    
    import re
    used_match = re.search(r"<D:quota-used-bytes>(\d+)</D:quota-used-bytes>", r.text)
    used_before = int(used_match.group(1))
    print(f"Used before DELETE: {used_before} bytes")
    
    r = httpx.delete(f"{BASE_URL}/quota_test_1kb.txt", headers=headers)
    print(f"DELETE Status: {r.status_code}")
    assert r.status_code == 204
    
    r = httpx.request("PROPFIND", f"{BASE_URL}/", headers={
        **headers,
        "Depth": "0",
        "Content-Type": "application/xml",
    }, content=body)
    
    used_match = re.search(r"<D:quota-used-bytes>(\d+)</D:quota-used-bytes>", r.text)
    used_after = int(used_match.group(1))
    print(f"Used after DELETE: {used_after} bytes")
    
    assert used_after < used_before
    print("PASS\n")

def test_5_copy_within_quota():
    print("=== Test 5: COPY Within Quota ===")
    headers = get_auth_headers("user1", "user123")
    
    content = b"B" * 2048
    httpx.put(f"{BASE_URL}/copy_src_quota.txt", headers=headers, content=content)
    
    r = httpx.request("COPY", f"{BASE_URL}/copy_src_quota.txt", headers={
        **headers,
        "Destination": f"{BASE_URL}/copy_dst_quota.txt",
    })
    print(f"COPY Status: {r.status_code}")
    assert r.status_code in [201, 204]
    
    r = httpx.get(f"{BASE_URL}/copy_dst_quota.txt", headers=headers)
    assert r.content == content
    print("PASS\n")

def test_6_copy_exceed_quota():
    print("=== Test 6: COPY Exceed Quota ===")
    admin_headers = get_auth_headers("admin", "admin123")
    user1_headers = get_auth_headers("user1", "user123")
    
    large_content = b"C" * 60 * 1024 * 1024
    httpx.put(f"{BASE_URL}/admin_large_file.txt", headers=admin_headers, content=large_content)
    
    r = httpx.request("COPY", f"{BASE_URL}/admin_large_file.txt", headers={
        **user1_headers,
        "Destination": f"{BASE_URL}/user1_copy_large.txt",
    })
    print(f"COPY Status: {r.status_code}")
    assert r.status_code == 507, f"Expected 507, got {r.status_code}"
    print("PASS\n")

def test_7_move_ownership():
    print("=== Test 7: MOVE Ownership Transfer ===")
    admin_headers = get_auth_headers("admin", "admin123")
    user1_headers = get_auth_headers("user1", "user123")
    
    content = b"D" * 512
    httpx.put(f"{BASE_URL}/admin_move_src.txt", headers=admin_headers, content=content)
    
    r = httpx.request("MOVE", f"{BASE_URL}/admin_move_src.txt", headers={
        **user1_headers,
        "Destination": f"{BASE_URL}/user1_move_dst.txt",
    })
    print(f"MOVE Status: {r.status_code}")
    assert r.status_code in [201, 204]
    
    r = httpx.get(f"{BASE_URL}/user1_move_dst.txt", headers=user1_headers)
    assert r.content == content
    
    r = httpx.get(f"{BASE_URL}/admin_move_src.txt", headers=admin_headers)
    assert r.status_code == 404
    print("PASS\n")

def test_8_quota_different_users():
    print("=== Test 8: Quota Per-User Isolation ===")
    admin_headers = get_auth_headers("admin", "admin123")
    user1_headers = get_auth_headers("user1", "user123")
    
    body = b"""<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:">
  <D:prop>
    <D:quota-available-bytes/>
    <D:quota-used-bytes/>
  </D:prop>
</D:propfind>"""
    
    r_admin = httpx.request("PROPFIND", f"{BASE_URL}/", headers={
        **admin_headers,
        "Depth": "0",
        "Content-Type": "application/xml",
    }, content=body)
    
    r_user1 = httpx.request("PROPFIND", f"{BASE_URL}/", headers={
        **user1_headers,
        "Depth": "0",
        "Content-Type": "application/xml",
    }, content=body)
    
    import re
    admin_avail = int(re.search(r"<D:quota-available-bytes>(\d+)</D:quota-available-bytes>", r_admin.text).group(1))
    user1_avail = int(re.search(r"<D:quota-available-bytes>(\d+)</D:quota-available-bytes>", r_user1.text).group(1))
    
    print(f"Admin available: {admin_avail} bytes")
    print(f"User1 available: {user1_avail} bytes")
    
    assert admin_avail != user1_avail, "Different users should have different quota views"
    print("PASS\n")

def test_9_put_overwrite_quota():
    print("=== Test 9: PUT Overwrite Quota Check ===")
    headers = get_auth_headers("user2", "user456")
    
    small_content = b"E" * 100
    r = httpx.put(f"{BASE_URL}/overwrite_test.txt", headers=headers, content=small_content)
    assert r.status_code in [201, 204]
    
    larger_content = b"E" * (30 * 1024 * 1024 + 1)
    r = httpx.put(f"{BASE_URL}/overwrite_test.txt", headers=headers, content=larger_content)
    print(f"PUT overwrite Status: {r.status_code}")
    assert r.status_code == 507, f"Expected 507, got {r.status_code}"
    print("PASS\n")

def cleanup():
    print("=== Cleanup ===")
    admin_headers = get_auth_headers("admin", "admin123")
    
    files_to_delete = [
        "copy_src_quota.txt",
        "copy_dst_quota.txt",
        "admin_large_file.txt",
        "user1_copy_large.txt",
        "admin_move_src.txt",
        "user1_move_dst.txt",
        "overwrite_test.txt",
        "quota_test_1kb.txt",
        "quota_test_large.txt",
    ]
    
    for f in files_to_delete:
        try:
            httpx.delete(f"{BASE_URL}/{f}", headers=admin_headers)
        except:
            pass
    
    ownership_file = Path("/Users/huwenjie/my project/solo/gen-382/storage/.file_ownership.json")
    if ownership_file.exists():
        ownership_file.unlink()
    
    print("Cleanup done\n")

def main():
    print("Starting WebDAV Quota tests...\n")
    
    try:
        httpx.get(f"{BASE_URL}/", headers=get_auth_headers("admin", "admin123"))
    except httpx.ConnectError:
        print("ERROR: Server is not running!")
        print("Please start the server first: python main.py")
        return 1
    
    cleanup()
    
    tests = [
        test_1_propfind_quota_properties,
        test_7_move_ownership,
        test_2_put_within_quota,
        test_3_put_exceed_quota,
        test_4_delete_releases_quota,
        test_5_copy_within_quota,
        test_6_copy_exceed_quota,
        test_8_quota_different_users,
        test_9_put_overwrite_quota,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {e}\n")
            failed += 1
        except Exception as e:
            print(f"ERROR: {e}\n")
            failed += 1
    
    cleanup()
    
    print(f"=== Summary ===")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    exit(main())
