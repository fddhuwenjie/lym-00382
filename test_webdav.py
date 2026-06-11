#!/usr/bin/env python3
import httpx
import base64
import io
import hashlib
import os
import time
import urllib.parse
import subprocess
from pathlib import Path

BASE_URL = "http://localhost:8000"
USERNAME = "admin"
PASSWORD = "admin123"
AUTH = base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
HEADERS = {"Authorization": f"Basic {AUTH}"}


def test_1_options():
    print("=== Test 1: OPTIONS ===")
    r = httpx.options(f"{BASE_URL}/", headers=HEADERS)
    print(f"Status: {r.status_code}")
    print(f"DAV: {r.headers.get('DAV')}")
    print(f"Allow: {r.headers.get('Allow')}")
    assert r.status_code == 200
    assert "1" in r.headers.get("DAV", "")
    print("PASS\n")


def test_2_put_get():
    print("=== Test 2: PUT and GET ===")
    content = b"Hello, WebDAV!"
    r = httpx.put(f"{BASE_URL}/test.txt", headers=HEADERS, content=content)
    print(f"PUT Status: {r.status_code}")
    assert r.status_code in [201, 204]
    etag = r.headers.get("ETag")
    print(f"ETag: {etag}")
    
    r = httpx.get(f"{BASE_URL}/test.txt", headers=HEADERS)
    print(f"GET Status: {r.status_code}")
    print(f"Content: {r.content}")
    assert r.status_code == 200
    assert r.content == content
    assert r.headers.get("ETag") == etag
    print("PASS\n")


def test_3_range_request():
    print("=== Test 3: Range Request ===")
    content = b"0123456789ABCDEF"
    httpx.put(f"{BASE_URL}/range_test.txt", headers=HEADERS, content=content)
    
    r = httpx.get(f"{BASE_URL}/range_test.txt", headers={
        **HEADERS,
        "Range": "bytes=0-4"
    })
    print(f"Range Status: {r.status_code}")
    print(f"Content-Range: {r.headers.get('Content-Range')}")
    print(f"Content: {r.content}")
    assert r.status_code == 206
    assert r.content == b"01234"
    
    r = httpx.get(f"{BASE_URL}/range_test.txt", headers={
        **HEADERS,
        "Range": "bytes=10-"
    })
    print(f"Range (suffix) Status: {r.status_code}")
    print(f"Content: {r.content}")
    assert r.status_code == 206
    assert r.content == b"ABCDEF"
    
    r = httpx.get(f"{BASE_URL}/range_test.txt", headers={
        **HEADERS,
        "Range": "bytes=-5"
    })
    print(f"Range (last 5) Status: {r.status_code}")
    print(f"Content: {r.content}")
    assert r.status_code == 206
    assert r.content == b"BCDEF"
    print("PASS\n")


def test_4_propfind():
    print("=== Test 4: PROPFIND ===")
    body = b"""<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:">
  <D:prop>
    <D:getcontentlength/>
    <D:getcontenttype/>
    <D:getlastmodified/>
    <D:resourcetype/>
    <D:getetag/>
  </D:prop>
</D:propfind>"""
    
    r = httpx.request("PROPFIND", f"{BASE_URL}/test.txt", headers={
        **HEADERS,
        "Depth": "0",
        "Content-Type": "application/xml",
    }, content=body)
    
    print(f"Status: {r.status_code}")
    print(f"Content-Type: {r.headers.get('Content-Type')}")
    print(f"Response:\n{r.text[:500]}")
    assert r.status_code == 207
    assert "multistatus" in r.text
    assert "getcontentlength" in r.text
    assert "getcontenttype" in r.text
    assert "resourcetype" in r.text
    print("PASS\n")


def test_5_mkcol_and_delete():
    print("=== Test 5: MKCOL and DELETE ===")
    r = httpx.request("MKCOL", f"{BASE_URL}/testdir", headers=HEADERS)
    print(f"MKCOL Status: {r.status_code}")
    assert r.status_code == 201
    
    r = httpx.get(f"{BASE_URL}/testdir/", headers=HEADERS)
    print(f"GET dir Status: {r.status_code}")
    assert r.status_code == 200
    assert "testdir" in r.text or "Index of" in r.text
    
    httpx.put(f"{BASE_URL}/testdir/file1.txt", headers=HEADERS, content=b"test")
    
    r = httpx.delete(f"{BASE_URL}/testdir", headers=HEADERS)
    print(f"DELETE dir Status: {r.status_code}")
    assert r.status_code == 204
    
    r = httpx.get(f"{BASE_URL}/testdir", headers=HEADERS)
    print(f"GET deleted dir Status: {r.status_code}")
    assert r.status_code == 404
    print("PASS\n")


def test_6_copy_move():
    print("=== Test 6: COPY and MOVE ===")
    content = b"Copy test content"
    httpx.put(f"{BASE_URL}/copy_src.txt", headers=HEADERS, content=content)
    
    r = httpx.request("COPY", f"{BASE_URL}/copy_src.txt", headers={
        **HEADERS,
        "Destination": f"{BASE_URL}/copy_dst.txt",
    })
    print(f"COPY Status: {r.status_code}")
    assert r.status_code in [201, 204]
    
    r = httpx.get(f"{BASE_URL}/copy_dst.txt", headers=HEADERS)
    assert r.content == content
    print(f"COPY verify: OK")
    
    r = httpx.request("MOVE", f"{BASE_URL}/copy_src.txt", headers={
        **HEADERS,
        "Destination": f"{BASE_URL}/move_dst.txt",
    })
    print(f"MOVE Status: {r.status_code}")
    assert r.status_code in [201, 204]
    
    r = httpx.get(f"{BASE_URL}/copy_src.txt", headers=HEADERS)
    assert r.status_code == 404
    r = httpx.get(f"{BASE_URL}/move_dst.txt", headers=HEADERS)
    assert r.content == content
    print(f"MOVE verify: OK")
    print("PASS\n")


def test_7_lock_unlock():
    print("=== Test 7: LOCK and UNLOCK ===")
    httpx.put(f"{BASE_URL}/lock_test.txt", headers=HEADERS, content=b"locked file")
    
    lock_body = b"""<?xml version="1.0" encoding="utf-8"?>
<D:lockinfo xmlns:D="DAV:">
  <D:lockscope><D:exclusive/></D:lockscope>
  <D:locktype><D:write/></D:locktype>
  <D:owner><D:href>mailto:test@example.com</D:href></D:owner>
</D:lockinfo>"""
    
    r = httpx.request("LOCK", f"{BASE_URL}/lock_test.txt", headers={
        **HEADERS,
        "Content-Type": "application/xml",
        "Timeout": "Second-3600",
    }, content=lock_body)
    
    print(f"LOCK Status: {r.status_code}")
    lock_token = r.headers.get("Lock-Token")
    print(f"Lock-Token: {lock_token}")
    print(f"Response:\n{r.text[:400]}")
    assert r.status_code == 200
    assert lock_token is not None
    assert "lockdiscovery" in r.text
    
    r = httpx.put(f"{BASE_URL}/lock_test.txt", headers=HEADERS, content=b"modified without lock")
    print(f"PUT without lock Status: {r.status_code}")
    assert r.status_code == 423
    
    r = httpx.put(f"{BASE_URL}/lock_test.txt", headers={
        **HEADERS,
        "If": f"(<{lock_token.strip('<>')}>)",
    }, content=b"modified with lock")
    print(f"PUT with lock Status: {r.status_code}")
    assert r.status_code in [200, 201, 204]
    
    r = httpx.request("UNLOCK", f"{BASE_URL}/lock_test.txt", headers={
        **HEADERS,
        "Lock-Token": lock_token,
    })
    print(f"UNLOCK Status: {r.status_code}")
    assert r.status_code == 204
    
    r = httpx.put(f"{BASE_URL}/lock_test.txt", headers=HEADERS, content=b"modified after unlock")
    print(f"PUT after unlock Status: {r.status_code}")
    assert r.status_code in [200, 201, 204]
    print("PASS\n")


def test_8_if_match():
    print("=== Test 8: If-Match / If-None-Match ===")
    content = b"ETag test v1"
    r = httpx.put(f"{BASE_URL}/etag_test.txt", headers=HEADERS, content=content)
    etag = r.headers.get("ETag")
    print(f"ETag: {etag}")
    
    r = httpx.get(f"{BASE_URL}/etag_test.txt", headers={
        **HEADERS,
        "If-None-Match": etag,
    })
    print(f"GET If-None-Match Status: {r.status_code}")
    assert r.status_code == 304, f"Expected 304 for GET, got {r.status_code}"
    
    r = httpx.put(f"{BASE_URL}/etag_test2.txt", headers=HEADERS, content=b"new")
    r = httpx.put(f"{BASE_URL}/etag_test2.txt", headers={
        **HEADERS,
        "If-None-Match": "*",
    }, content=b"should fail")
    print(f"PUT If-None-Match: * Status: {r.status_code}")
    assert r.status_code == 412, f"Expected 412 for PUT, got {r.status_code}"
    
    r = httpx.put(f"{BASE_URL}/etag_test.txt", headers={
        **HEADERS,
        "If-Match": '"wrong-etag"',
    }, content=b"v2")
    print(f"If-Match (wrong) Status: {r.status_code}")
    assert r.status_code == 412
    
    r = httpx.put(f"{BASE_URL}/etag_test.txt", headers={
        **HEADERS,
        "If-Match": etag,
    }, content=b"v2")
    print(f"If-Match (correct) Status: {r.status_code}")
    assert r.status_code in [200, 201, 204]
    print("PASS\n")


def test_9_path_traversal():
    print("=== Test 9: Path Traversal Protection ===")
    
    auth_b64 = base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
    
    result = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--path-as-is",
         "-H", f"Authorization: Basic {auth_b64}",
         f"{BASE_URL}/../etc/passwd"],
        capture_output=True, text=True
    )
    status = result.stdout.strip()
    print(f"Path traversal Status: {status}")
    assert status == "403", f"Expected 403, got {status}"
    
    result2 = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--path-as-is",
         "-H", f"Authorization: Basic {auth_b64}",
         f"{BASE_URL}/%2e%2e%2fetc%2fpasswd"],
        capture_output=True, text=True
    )
    status2 = result2.stdout.strip()
    print(f"Encoded path traversal Status: {status2}")
    assert status2 == "403", f"Expected 403, got {status2}"
    print("PASS\n")


def test_10_unicode_filename():
    print("=== Test 10: Unicode Filename ===")
    content = b"Unicode test"
    filename = "中文文件.txt"
    encoded = urllib.parse.quote(filename)
    
    r = httpx.put(f"{BASE_URL}/{encoded}", headers=HEADERS, content=content)
    print(f"PUT unicode Status: {r.status_code}")
    assert r.status_code in [201, 204]
    
    r = httpx.get(f"{BASE_URL}/{encoded}", headers=HEADERS)
    print(f"GET unicode Status: {r.status_code}")
    assert r.content == content
    assert r.status_code == 200
    print("PASS\n")


def test_11_copy_large_directory():
    print("=== Test 11: Copy Large Directory ===")
    
    httpx.request("MKCOL", f"{BASE_URL}/src_dir", headers=HEADERS)
    httpx.request("MKCOL", f"{BASE_URL}/src_dir/sub1", headers=HEADERS)
    httpx.request("MKCOL", f"{BASE_URL}/src_dir/sub1/sub2", headers=HEADERS)
    
    src_hashes = {}
    for i in range(5):
        content = f"file content {i}".encode() * 100
        path = f"src_dir/file{i}.txt" if i < 2 else f"src_dir/sub1/file{i}.txt"
        if i == 4:
            path = f"src_dir/sub1/sub2/file{i}.txt"
        httpx.put(f"{BASE_URL}/{path}", headers=HEADERS, content=content)
        src_hashes[path] = hashlib.md5(content).hexdigest()
    
    r = httpx.request("COPY", f"{BASE_URL}/src_dir", headers={
        **HEADERS,
        "Destination": f"{BASE_URL}/dst_dir",
        "Depth": "infinity",
    })
    print(f"COPY dir Status: {r.status_code}")
    assert r.status_code in [201, 204]
    
    for path, expected_hash in src_hashes.items():
        dst_path = path.replace("src_dir", "dst_dir")
        r = httpx.get(f"{BASE_URL}/{dst_path}", headers=HEADERS)
        actual_hash = hashlib.md5(r.content).hexdigest()
        print(f"  {dst_path}: {expected_hash == actual_hash}")
        assert actual_hash == expected_hash
    
    print("PASS\n")


def test_12_cross_user_lock():
    print("=== Test 12: Cross-User Lock Protection ===")
    
    admin_auth = base64.b64encode(f"admin:{PASSWORD}".encode()).decode()
    admin_headers = {"Authorization": f"Basic {admin_auth}"}
    
    user1_auth = base64.b64encode(f"user1:user123".encode()).decode()
    user1_headers = {"Authorization": f"Basic {user1_auth}"}
    
    httpx.put(f"{BASE_URL}/shared_test.txt", headers=admin_headers, content=b"admin content")
    
    lock_body = b"""<?xml version="1.0" encoding="utf-8"?>
<D:lockinfo xmlns:D="DAV:">
  <D:lockscope><D:exclusive/></D:lockscope>
  <D:locktype><D:write/></D:locktype>
  <D:owner><D:href>mailto:admin@example.com</D:href></D:owner>
</D:lockinfo>"""
    
    r = httpx.request("LOCK", f"{BASE_URL}/shared_test.txt", headers={
        **admin_headers,
        "Content-Type": "application/xml",
    }, content=lock_body)
    
    print(f"admin LOCK Status: {r.status_code}")
    assert r.status_code == 200
    lock_token = r.headers.get("Lock-Token")
    print(f"Lock-Token: {lock_token}")
    
    r = httpx.put(f"{BASE_URL}/shared_test.txt", headers=user1_headers, content=b"user1 modified")
    print(f"user1 PUT without lock Status: {r.status_code}")
    assert r.status_code == 423, f"Expected 423 Locked, got {r.status_code}"
    
    httpx.request("UNLOCK", f"{BASE_URL}/shared_test.txt", headers={
        **admin_headers,
        "Lock-Token": lock_token,
    })
    
    r = httpx.put(f"{BASE_URL}/shared_test.txt", headers=user1_headers, content=b"user1 modified after unlock")
    print(f"user1 PUT after unlock Status: {r.status_code}")
    assert r.status_code in [200, 201, 204]
    
    print("PASS\n")


def test_13_304_not_modified():
    print("=== Test 13: 304 Not Modified for GET If-None-Match ===")
    
    content = b"304 test content"
    r = httpx.put(f"{BASE_URL}/304_test.txt", headers=HEADERS, content=content)
    etag = r.headers.get("ETag")
    print(f"ETag: {etag}")
    
    r = httpx.get(f"{BASE_URL}/304_test.txt", headers={
        **HEADERS,
        "If-None-Match": etag,
    })
    print(f"GET with If-None-Match Status: {r.status_code}")
    assert r.status_code == 304, f"Expected 304 Not Modified, got {r.status_code}"
    assert len(r.content) == 0
    
    r = httpx.get(f"{BASE_URL}/304_test.txt", headers={
        **HEADERS,
        "If-None-Match": '"wrong-etag"',
    })
    print(f"GET with wrong If-None-Match Status: {r.status_code}")
    assert r.status_code == 200
    assert r.content == content
    
    r = httpx.put(f"{BASE_URL}/304_test.txt", headers={
        **HEADERS,
        "If-None-Match": "*",
    }, content=b"new content")
    print(f"PUT with If-None-Match: * Status: {r.status_code}")
    assert r.status_code == 412, f"Expected 412 for PUT, got {r.status_code}"
    
    print("PASS\n")


def test_14_copy_move_status_codes():
    print("=== Test 14: COPY/MOVE Status Codes ===")
    import time
    unique = str(int(time.time()))
    
    httpx.put(f"{BASE_URL}/cm_src_{unique}.txt", headers=HEADERS, content=b"source")
    
    r = httpx.request("COPY", f"{BASE_URL}/cm_src_{unique}.txt", headers={
        **HEADERS,
        "Destination": f"{BASE_URL}/cm_new_{unique}.txt",
    })
    print(f"COPY to new Status: {r.status_code}")
    assert r.status_code == 201, f"Expected 201 Created, got {r.status_code}"
    
    r = httpx.request("COPY", f"{BASE_URL}/cm_src_{unique}.txt", headers={
        **HEADERS,
        "Destination": f"{BASE_URL}/cm_new_{unique}.txt",
        "Overwrite": "T",
    })
    print(f"COPY overwrite Status: {r.status_code}")
    assert r.status_code == 204, f"Expected 204 No Content, got {r.status_code}"
    
    httpx.put(f"{BASE_URL}/cm_move_src_{unique}.txt", headers=HEADERS, content=b"move source")
    
    r = httpx.request("MOVE", f"{BASE_URL}/cm_move_src_{unique}.txt", headers={
        **HEADERS,
        "Destination": f"{BASE_URL}/cm_move_new_{unique}.txt",
    })
    print(f"MOVE to new Status: {r.status_code}")
    assert r.status_code == 201, f"Expected 201 Created, got {r.status_code}"
    
    httpx.put(f"{BASE_URL}/cm_move_src2_{unique}.txt", headers=HEADERS, content=b"move source 2")
    httpx.put(f"{BASE_URL}/cm_move_exist_{unique}.txt", headers=HEADERS, content=b"existing")
    
    r = httpx.request("MOVE", f"{BASE_URL}/cm_move_src2_{unique}.txt", headers={
        **HEADERS,
        "Destination": f"{BASE_URL}/cm_move_exist_{unique}.txt",
        "Overwrite": "T",
    })
    print(f"MOVE overwrite Status: {r.status_code}")
    assert r.status_code == 204, f"Expected 204 No Content, got {r.status_code}"
    
    r = httpx.put(f"{BASE_URL}/put_new_{unique}.txt", headers=HEADERS, content=b"new file")
    print(f"PUT new Status: {r.status_code}")
    assert r.status_code == 201, f"Expected 201 Created, got {r.status_code}"
    
    r = httpx.put(f"{BASE_URL}/put_new_{unique}.txt", headers=HEADERS, content=b"overwrite")
    print(f"PUT overwrite Status: {r.status_code}")
    assert r.status_code == 204, f"Expected 204 No Content, got {r.status_code}"
    
    print("PASS\n")


def main():
    print("Starting WebDAV tests...\n")
    
    try:
        httpx.get(f"{BASE_URL}/", headers=HEADERS)
    except httpx.ConnectError:
        print("ERROR: Server is not running!")
        print("Please start the server first: python main.py")
        return
    
    tests = [
        test_1_options,
        test_2_put_get,
        test_3_range_request,
        test_4_propfind,
        test_5_mkcol_and_delete,
        test_6_copy_move,
        test_7_lock_unlock,
        test_8_if_match,
        test_9_path_traversal,
        test_10_unicode_filename,
        test_11_copy_large_directory,
        test_12_cross_user_lock,
        test_13_304_not_modified,
        test_14_copy_move_status_codes,
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
    
    print(f"=== Summary ===")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit(main())
