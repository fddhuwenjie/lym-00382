import os
import urllib.parse
import shutil
from pathlib import Path
from typing import List, Optional
from xml.etree import ElementTree as ET

from fastapi import FastAPI, Request, Response, HTTPException, status
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse

from config import (
    get_user_home,
    LOCK_TIMEOUT,
    get_user_quota,
    get_user_usage,
    get_available_quota,
    check_quota,
    set_file_owner,
    get_file_owner,
    remove_file_owner,
    transfer_file_ownership,
)
from auth import authenticate
from lock_manager import lock_manager
from utils import (
    safe_resolve_path,
    get_etag,
    get_content_type,
    format_http_date,
    parse_range_header,
    parse_if_header,
    parse_lock_request,
    parse_propfind_request,
    parse_proppatch_request,
    el,
    subel,
    xml_to_string,
    DAV_NS,
)

app = FastAPI()


def get_href_url(request: Request, path: str) -> str:
    path = path.lstrip("/")
    return f"{request.url.scheme}://{request.url.netloc}/{urllib.parse.quote(path)}"


def get_prop_element(prop_name: str, fs_path: Path, href_path: str, username: str = ""):
    prop_name = prop_name.lower()
    try:
        if prop_name == "getcontentlength":
            if fs_path.is_file():
                return el("getcontentlength", str(fs_path.stat().st_size))
            return el("getcontentlength", "0")
        elif prop_name == "getcontenttype":
            if fs_path.is_dir():
                return el("getcontenttype", "httpd/unix-directory")
            return el("getcontenttype", get_content_type(fs_path))
        elif prop_name == "getlastmodified":
            mtime = fs_path.stat().st_mtime
            return el("getlastmodified", format_http_date(mtime))
        elif prop_name == "resourcetype":
            rt = el("resourcetype")
            if fs_path.is_dir():
                subel(rt, "collection")
            return rt
        elif prop_name == "getetag":
            return el("getetag", get_etag(fs_path))
        elif prop_name == "getcontentlanguage":
            return el("getcontentlanguage", "")
        elif prop_name == "creationdate":
            ctime = fs_path.stat().st_ctime
            return el("creationdate", format_http_date(ctime))
        elif prop_name == "displayname":
            return el("displayname", fs_path.name)
        elif prop_name == "supportedlock":
            sl = el("supportedlock")
            le1 = subel(sl, "lockentry")
            subel(le1, "lockscope", None, {"exclusive": ""})
            subel(le1, "locktype", None, {"write": ""})
            le2 = subel(sl, "lockentry")
            subel(le2, "lockscope", None, {"shared": ""})
            subel(le2, "locktype", None, {"write": ""})
            return sl
        elif prop_name == "lockdiscovery":
            ld = el("lockdiscovery")
            locks = lock_manager.get_locks_for_path(str(fs_path))
            for lock in locks:
                active = subel(ld, "activelock")
                scope = subel(active, "lockscope")
                if lock.scope == "exclusive":
                    subel(scope, "exclusive")
                else:
                    subel(scope, "shared")
                ltype = subel(active, "locktype")
                subel(ltype, "write")
                depth_el = subel(active, "depth")
                depth_el.text = "infinity"
                owner = subel(active, "owner")
                if lock.owner:
                    href = subel(owner, "href")
                    href.text = lock.owner
                timeout_el = subel(active, "timeout")
                timeout_el.text = f"Second-{LOCK_TIMEOUT}"
                token_el = subel(active, "locktoken")
                href_token = subel(token_el, "href")
                href_token.text = lock.token
            return ld
        elif prop_name == "quota-available-bytes":
            if username:
                available = get_available_quota(username)
                return el("quota-available-bytes", str(available))
            elem = el("quota-available-bytes")
            elem.set("xmlns", DAV_NS)
            return elem
        elif prop_name == "quota-used-bytes":
            if username:
                used = get_user_usage(username)
                return el("quota-used-bytes", str(used))
            elem = el("quota-used-bytes")
            elem.set("xmlns", DAV_NS)
            return elem
        else:
            elem = el(prop_name)
            elem.set("xmlns", DAV_NS)
            return elem
    except OSError:
        elem = el(prop_name)
        elem.set("xmlns", DAV_NS)
        return elem


def build_propstat_response(request: Request, fs_path: Path, href_path: str, requested_props: List[str], username: str = "") -> ET.Element:
    propstat = el("propstat")
    prop = el("prop")
    
    for prop_name in requested_props:
        elem = get_prop_element(prop_name, fs_path, href_path, username)
        prop.append(elem)
    
    propstat.append(prop)
    status = subel(propstat, "status")
    status.text = "HTTP/1.1 200 OK"
    
    return propstat


def build_multistatus_response(request: Request, resources: List[tuple], requested_props: List[str], username: str = "") -> bytes:
    multistatus = el("multistatus")
    
    for fs_path, href_path in resources:
        response = el("response")
        href = subel(response, "href")
        href.text = get_href_url(request, href_path)
        
        if not fs_path.exists():
            propstat = el("propstat")
            prop = el("prop")
            for prop_name in requested_props:
                elem = el(prop_name)
                elem.set("xmlns", DAV_NS)
                prop.append(elem)
            propstat.append(prop)
            status = subel(propstat, "status")
            status.text = "HTTP/1.1 404 Not Found"
            response.append(propstat)
        else:
            propstat = build_propstat_response(request, fs_path, href_path, requested_props, username)
            response.append(propstat)
        
        multistatus.append(response)
    
    return xml_to_string(multistatus)


def generate_directory_html(base_dir: Path, fs_path: Path, request: Request, rel_path: str) -> str:
    items = []
    parent = str(Path(rel_path).parent)
    if parent == ".":
        parent = ""
    
    if rel_path:
        items.append(f'<tr><td colspan="4"><a href="/{urllib.parse.quote(parent)}">..</a></td></tr>')
    
    try:
        for entry in sorted(fs_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            name = entry.name
            encoded_name = urllib.parse.quote(name)
            entry_rel = str(Path(rel_path) / name) if rel_path else name
            href = f"/{urllib.parse.quote(entry_rel)}"
            
            if entry.is_dir():
                size = "-"
                icon = "📁"
            else:
                try:
                    size = str(entry.stat().st_size)
                except OSError:
                    size = "0"
                icon = "📄"
            
            try:
                mtime = format_http_date(entry.stat().st_mtime)
            except OSError:
                mtime = "-"
            
            items.append(
                f'<tr>'
                f'<td>{icon}</td>'
                f'<td><a href="{href}">{name}</a></td>'
                f'<td style="text-align: right; padding: 0 1em;">{size}</td>'
                f'<td style="padding: 0 1em;">{mtime}</td>'
                f'</tr>'
            )
    except OSError:
        pass
    
    items_html = "\n".join(items)
    display_path = "/" + rel_path if rel_path else "/"
    
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Index of {display_path}</title>
    <style>
        body {{ font-family: monospace; padding: 1em; }}
        h1 {{ margin: 0 0 1em 0; }}
        table {{ border-collapse: collapse; width: 100%; }}
        tr:hover {{ background: #f5f5f5; }}
        a {{ text-decoration: none; color: #0366d6; }}
        a:hover {{ text-decoration: underline; }}
        .header {{ font-weight: bold; border-bottom: 2px solid #ddd; }}
    </style>
</head>
<body>
    <h1>Index of {display_path}</h1>
    <table>
        <tr class="header">
            <th></th>
            <th style="text-align: left;">Name</th>
            <th style="text-align: right;">Size</th>
            <th style="text-align: left;">Last Modified</th>
        </tr>
        {items_html}
    </table>
    <hr>
    <p><em>WebDAV Server (RFC 4918)</em></p>
</body>
</html>"""


def check_lock(request: Request, fs_path: str, method: str) -> None:
    if_header = request.headers.get("If", "")
    tokens = parse_if_header(if_header)
    if not lock_manager.validate_lock(fs_path, tokens, method):
        raise HTTPException(
            status_code=423,
            detail="Locked"
        )


def check_etag(request: Request, fs_path: Path) -> Optional[Response]:
    if_match = request.headers.get("If-Match")
    if_none_match = request.headers.get("If-None-Match")
    method = request.method
    
    if if_match and if_match != "*":
        current_etag = get_etag(fs_path)
        matches = [m.strip() for m in if_match.split(",")]
        if current_etag not in matches:
            raise HTTPException(status_code=412, detail="Precondition Failed")
    
    if if_none_match:
        current_etag = get_etag(fs_path)
        matches = [m.strip() for m in if_none_match.split(",")]
        if current_etag in matches or "*" in matches:
            if method in ["GET", "HEAD"]:
                return Response(status_code=304)
            else:
                raise HTTPException(status_code=412, detail="Precondition Failed")
    
    return None


async def get_request_body(request: Request) -> bytes:
    try:
        return await request.body()
    except Exception:
        return b""


def collect_resources(base_dir: Path, root_fs_path: Path, root_href_path: str, depth: str) -> List[tuple]:
    resources = [(root_fs_path, root_href_path)]
    
    if not root_fs_path.is_dir():
        return resources
    
    if depth == "0":
        return resources
    
    def walk(current_fs: Path, current_href: str, depth_infinite: bool):
        try:
            entries = sorted(current_fs.iterdir())
        except OSError:
            return
        
        for entry in entries:
            entry_href = str(Path(current_href) / entry.name) if current_href else entry.name
            resources.append((entry, entry_href))
            
            if depth_infinite and entry.is_dir():
                walk(entry, entry_href, True)
    
    if depth == "1":
        try:
            entries = sorted(root_fs_path.iterdir())
        except OSError:
            entries = []
        for entry in entries:
            entry_href = str(Path(root_href_path) / entry.name) if root_href_path else entry.name
            resources.append((entry, entry_href))
    elif depth == "infinity":
        walk(root_fs_path, root_href_path, True)
    
    return resources


@app.api_route("/{path:path}", methods=["GET", "HEAD"])
async def handle_get(request: Request, path: str = ""):
    username = await authenticate(request)
    user_home = get_user_home(username)
    
    try:
        fs_path = safe_resolve_path(user_home, path)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    if not fs_path.exists():
        raise HTTPException(status_code=404, detail="Not Found")
    
    etag_response = check_etag(request, fs_path)
    if etag_response is not None:
        return etag_response
    
    if fs_path.is_dir():
        html = generate_directory_html(user_home, fs_path, request, path)
        return HTMLResponse(content=html)
    
    file_size = fs_path.stat().st_size
    range_header = request.headers.get("Range")
    etag = get_etag(fs_path)
    content_type = get_content_type(fs_path)
    last_modified = format_http_date(fs_path.stat().st_mtime)
    
    headers = {
        "ETag": etag,
        "Last-Modified": last_modified,
        "Accept-Ranges": "bytes",
        "Content-Type": content_type,
    }
    
    range_result = parse_range_header(range_header, file_size) if range_header else None
    
    if range_result:
        start, end = range_result
        length = end - start + 1
        
        def file_iterator():
            with open(fs_path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk_size = min(64 * 1024, remaining)
                    data = f.read(chunk_size)
                    if not data:
                        break
                    yield data
                    remaining -= len(data)
        
        return StreamingResponse(
            file_iterator(),
            status_code=206,
            headers={
                **headers,
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": str(length),
            },
            media_type=content_type,
        )
    
    if request.method == "HEAD":
        return Response(
            content=b"",
            headers={
                **headers,
                "Content-Length": str(file_size),
            },
        )
    
    return FileResponse(
        path=str(fs_path),
        headers=headers,
        media_type=content_type,
    )


@app.api_route("/{path:path}", methods=["PUT"])
async def handle_put(request: Request, path: str = ""):
    username = await authenticate(request)
    user_home = get_user_home(username)
    
    try:
        fs_path = safe_resolve_path(user_home, path)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    check_lock(request, str(fs_path), "PUT")
    
    file_existed = fs_path.exists()
    old_size = 0
    old_owner = None
    
    if file_existed:
        etag_response = check_etag(request, fs_path)
        if etag_response is not None:
            return etag_response
        old_size = fs_path.stat().st_size
        old_owner = get_file_owner(fs_path)
    
    if fs_path.is_dir():
        raise HTTPException(status_code=409, detail="Conflict: Cannot PUT to a directory")
    
    body = await get_request_body(request)
    new_size = len(body)
    
    if old_owner == username:
        additional_bytes = new_size - old_size
        if additional_bytes > 0 and not check_quota(username, additional_bytes):
            raise HTTPException(status_code=507, detail="Insufficient Storage")
    elif old_owner is not None and old_owner != username:
        if not check_quota(username, new_size):
            raise HTTPException(status_code=507, detail="Insufficient Storage")
    else:
        if not check_quota(username, new_size):
            raise HTTPException(status_code=507, detail="Insufficient Storage")
    
    fs_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(fs_path, "wb") as f:
        f.write(body)
    
    set_file_owner(fs_path, username)
    
    new_etag = get_etag(fs_path)
    
    return Response(
        status_code=201 if not file_existed else 204,
        headers={
            "ETag": new_etag,
            "Last-Modified": format_http_date(fs_path.stat().st_mtime),
        },
    )


def _collect_all_paths(path: Path) -> List[Path]:
    paths = [path]
    if path.is_dir():
        try:
            for entry in path.iterdir():
                paths.extend(_collect_all_paths(entry))
        except OSError:
            pass
    return paths


def _calculate_total_size(path: Path) -> int:
    total = 0
    if path.is_file():
        try:
            total = path.stat().st_size
        except OSError:
            pass
    elif path.is_dir():
        try:
            for entry in path.iterdir():
                total += _calculate_total_size(entry)
        except OSError:
            pass
    return total


def _collect_file_paths_with_size(path: Path) -> List[tuple]:
    results = []
    if path.is_file():
        try:
            results.append((path, path.stat().st_size))
        except OSError:
            pass
    elif path.is_dir():
        results.append((path, 0))
        try:
            for entry in path.iterdir():
                results.extend(_collect_file_paths_with_size(entry))
        except OSError:
            pass
    return results


@app.api_route("/{path:path}", methods=["DELETE"])
async def handle_delete(request: Request, path: str = ""):
    username = await authenticate(request)
    user_home = get_user_home(username)
    
    try:
        fs_path = safe_resolve_path(user_home, path)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    if not fs_path.exists():
        raise HTTPException(status_code=404, detail="Not Found")
    
    check_lock(request, str(fs_path), "DELETE")
    
    all_paths = _collect_all_paths(fs_path)
    
    if fs_path.is_dir():
        shutil.rmtree(fs_path)
    else:
        fs_path.unlink()
    
    for p in all_paths:
        remove_file_owner(p)
    
    return Response(status_code=204)


@app.api_route("/{path:path}", methods=["MKCOL"])
async def handle_mkcol(request: Request, path: str = ""):
    username = await authenticate(request)
    user_home = get_user_home(username)
    
    try:
        fs_path = safe_resolve_path(user_home, path)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    if fs_path.exists():
        raise HTTPException(status_code=405, detail="Method Not Allowed: Already exists")
    
    parent = fs_path.parent
    if not parent.exists():
        raise HTTPException(status_code=409, detail="Conflict: Parent directory does not exist")
    
    check_lock(request, str(fs_path), "MKCOL")
    
    fs_path.mkdir(parents=False)
    set_file_owner(fs_path, username)
    
    return Response(status_code=201)


@app.api_route("/{path:path}", methods=["PROPFIND"])
async def handle_propfind(request: Request, path: str = ""):
    username = await authenticate(request)
    user_home = get_user_home(username)
    
    try:
        fs_path = safe_resolve_path(user_home, path)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    if not fs_path.exists():
        raise HTTPException(status_code=404, detail="Not Found")
    
    depth = request.headers.get("Depth", "1")
    if depth.lower() == "infinity":
        depth = "infinity"
    
    body = await get_request_body(request)
    requested_props = parse_propfind_request(body)
    
    href_path = path
    resources = collect_resources(user_home, fs_path, href_path, depth)
    
    xml_content = build_multistatus_response(request, resources, requested_props, username)
    
    return Response(
        content=xml_content,
        status_code=207,
        media_type="application/xml; charset=utf-8",
        headers={"DAV": "1, 2"},
    )


@app.api_route("/{path:path}", methods=["PROPPATCH"])
async def handle_proppatch(request: Request, path: str = ""):
    username = await authenticate(request)
    user_home = get_user_home(username)
    
    try:
        fs_path = safe_resolve_path(user_home, path)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    if not fs_path.exists():
        raise HTTPException(status_code=404, detail="Not Found")
    
    check_lock(request, str(fs_path), "PROPPATCH")
    
    body = await get_request_body(request)
    operations = parse_proppatch_request(body)
    
    multistatus = el("multistatus")
    response = el("response")
    href = subel(response, "href")
    href.text = get_href_url(request, path)
    
    for op in operations:
        propstat = el("propstat")
        prop = el("prop")
        elem = el(op["name"])
        if op["ns"]:
            elem.set("xmlns", op["ns"])
        prop.append(elem)
        propstat.append(prop)
        status = subel(propstat, "status")
        status.text = "HTTP/1.1 200 OK"
        response.append(propstat)
    
    multistatus.append(response)
    
    return Response(
        content=xml_to_string(multistatus),
        status_code=207,
        media_type="application/xml; charset=utf-8",
    )


def get_destination_path(request: Request, user_home: Path) -> Path:
    dest_header = request.headers.get("Destination")
    if not dest_header:
        raise HTTPException(status_code=400, detail="Missing Destination header")
    
    try:
        if "://" in dest_header:
            dest_path = dest_header.split("://", 1)[1].split("/", 1)[1]
        else:
            dest_path = dest_header.lstrip("/")
        
        dest_path = urllib.parse.unquote(dest_path)
        return safe_resolve_path(user_home, dest_path), dest_path
    except (IndexError, PermissionError):
        raise HTTPException(status_code=403, detail="Invalid Destination")


@app.api_route("/{path:path}", methods=["COPY"])
async def handle_copy(request: Request, path: str = ""):
    username = await authenticate(request)
    user_home = get_user_home(username)
    
    try:
        src_path = safe_resolve_path(user_home, path)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    if not src_path.exists():
        raise HTTPException(status_code=404, detail="Not Found")
    
    dest_path, dest_href = get_destination_path(request, user_home)
    depth = request.headers.get("Depth", "infinity")
    overwrite = request.headers.get("Overwrite", "T").upper() == "T"
    
    dest_existed = dest_path.exists()
    
    if dest_existed and not overwrite:
        raise HTTPException(status_code=412, detail="Precondition Failed")
    
    src_files = _collect_file_paths_with_size(src_path)
    
    if depth != "infinity" and src_path.is_dir():
        src_files = [(src_path, 0)]
    
    total_src_size = sum(size for _, size in src_files)
    
    old_dest_size = 0
    old_dest_owned_size = 0
    old_dest_files = []
    if dest_existed:
        old_dest_files = _collect_file_paths_with_size(dest_path)
        old_dest_size = sum(size for _, size in old_dest_files)
        for p, size in old_dest_files:
            if get_file_owner(p) == username:
                old_dest_owned_size += size
    
    if old_dest_owned_size > 0:
        additional_bytes = total_src_size - old_dest_owned_size
    else:
        additional_bytes = total_src_size
    
    if additional_bytes > 0 and not check_quota(username, additional_bytes):
        raise HTTPException(status_code=507, detail="Insufficient Storage")
    
    if src_path.is_dir():
        if dest_existed:
            for p, _ in old_dest_files:
                remove_file_owner(p)
            if dest_path.is_file():
                dest_path.unlink()
            else:
                shutil.rmtree(dest_path)
        
        if depth == "infinity":
            shutil.copytree(src_path, dest_path)
        else:
            dest_path.mkdir(parents=True)
    else:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if dest_existed:
            for p, _ in old_dest_files:
                remove_file_owner(p)
            if dest_path.is_dir():
                shutil.rmtree(dest_path)
            else:
                dest_path.unlink()
        shutil.copy2(src_path, dest_path)
    
    if src_path.is_dir() and depth == "infinity":
        for src_file, _ in src_files:
            rel = src_file.relative_to(src_path)
            dest_file = dest_path / rel
            set_file_owner(dest_file, username)
    elif src_path.is_dir():
        set_file_owner(dest_path, username)
    else:
        set_file_owner(dest_path, username)
    
    return Response(status_code=204 if dest_existed else 201)


@app.api_route("/{path:path}", methods=["MOVE"])
async def handle_move(request: Request, path: str = ""):
    username = await authenticate(request)
    user_home = get_user_home(username)
    
    try:
        src_path = safe_resolve_path(user_home, path)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    if not src_path.exists():
        raise HTTPException(status_code=404, detail="Not Found")
    
    check_lock(request, str(src_path), "MOVE")
    
    dest_path, dest_href = get_destination_path(request, user_home)
    overwrite = request.headers.get("Overwrite", "T").upper() == "T"
    
    dest_existed = dest_path.exists()
    
    if dest_existed and not overwrite:
        raise HTTPException(status_code=412, detail="Precondition Failed")
    
    src_files = _collect_file_paths_with_size(src_path)
    total_src_size = sum(size for _, size in src_files)
    src_owner = get_file_owner(src_path)
    
    old_dest_size = 0
    old_dest_owned_size = 0
    old_dest_files = []
    if dest_existed:
        old_dest_files = _collect_file_paths_with_size(dest_path)
        old_dest_size = sum(size for _, size in old_dest_files)
        for p, size in old_dest_files:
            if get_file_owner(p) == username:
                old_dest_owned_size += size
    
    if src_owner == username:
        if old_dest_owned_size > 0:
            additional_bytes = total_src_size - old_dest_owned_size
        else:
            additional_bytes = 0
    else:
        if old_dest_owned_size > 0:
            additional_bytes = total_src_size - old_dest_owned_size
        else:
            additional_bytes = total_src_size
    
    if additional_bytes > 0 and not check_quota(username, additional_bytes):
        raise HTTPException(status_code=507, detail="Insufficient Storage")
    
    if dest_existed:
        for p, _ in old_dest_files:
            remove_file_owner(p)
        if dest_path.is_dir():
            shutil.rmtree(dest_path)
        else:
            dest_path.unlink()
    
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    
    for src_file, _ in src_files:
        rel = src_file.relative_to(src_path)
        dest_file = dest_path / rel
        transfer_file_ownership(src_file, dest_file, username)
    
    shutil.move(str(src_path), str(dest_path))
    
    return Response(status_code=204 if dest_existed else 201)


@app.api_route("/{path:path}", methods=["LOCK"])
async def handle_lock(request: Request, path: str = ""):
    username = await authenticate(request)
    user_home = get_user_home(username)
    
    try:
        fs_path = safe_resolve_path(user_home, path)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    if_header = request.headers.get("If", "")
    tokens = parse_if_header(if_header)
    
    if tokens:
        for token in tokens:
            refreshed = lock_manager.refresh_lock(token)
            if refreshed:
                prop = el("prop")
                ld = subel(prop, "lockdiscovery")
                active = subel(ld, "activelock")
                scope = subel(active, "lockscope")
                if refreshed.scope == "exclusive":
                    subel(scope, "exclusive")
                else:
                    subel(scope, "shared")
                ltype = subel(active, "locktype")
                subel(ltype, "write")
                depth_el = subel(active, "depth")
                depth_el.text = "infinity"
                owner = subel(active, "owner")
                if refreshed.owner:
                    href = subel(owner, "href")
                    href.text = refreshed.owner
                timeout_el = subel(active, "timeout")
                timeout_el.text = f"Second-{LOCK_TIMEOUT}"
                token_el = subel(active, "locktoken")
                href_token = subel(token_el, "href")
                href_token.text = refreshed.token
                
                propstat = el("propstat")
                propstat.append(prop)
                status = subel(propstat, "status")
                status.text = "HTTP/1.1 200 OK"
                
                multistatus = el("multistatus")
                response = el("response")
                href = subel(response, "href")
                href.text = get_href_url(request, path)
                response.append(propstat)
                multistatus.append(response)
                
                return Response(
                    content=xml_to_string(multistatus),
                    status_code=207,
                    media_type="application/xml; charset=utf-8",
                    headers={"Lock-Token": f"<{refreshed.token}>"},
                )
    
    body = await get_request_body(request)
    lock_info = parse_lock_request(body)
    
    try:
        lock = lock_manager.create_lock(
            path=str(fs_path),
            scope=lock_info["scope"],
            lock_type=lock_info["type"],
            owner=lock_info["owner"],
            username=username,
        )
    except Exception as e:
        raise HTTPException(status_code=423, detail=str(e))
    
    prop = el("prop")
    ld = subel(prop, "lockdiscovery")
    active = subel(ld, "activelock")
    scope = subel(active, "lockscope")
    if lock.scope == "exclusive":
        subel(scope, "exclusive")
    else:
        subel(scope, "shared")
    ltype = subel(active, "locktype")
    subel(ltype, "write")
    depth_el = subel(active, "depth")
    depth_el.text = "infinity"
    owner = subel(active, "owner")
    if lock.owner:
        href = subel(owner, "href")
        href.text = lock.owner
    timeout_el = subel(active, "timeout")
    timeout_el.text = f"Second-{LOCK_TIMEOUT}"
    token_el = subel(active, "locktoken")
    href_token = subel(token_el, "href")
    href_token.text = lock.token
    
    return Response(
        content=xml_to_string(prop),
        status_code=200,
        media_type="application/xml; charset=utf-8",
        headers={"Lock-Token": f"<{lock.token}>"},
    )


@app.api_route("/{path:path}", methods=["UNLOCK"])
async def handle_unlock(request: Request, path: str = ""):
    username = await authenticate(request)
    user_home = get_user_home(username)
    
    try:
        fs_path = safe_resolve_path(user_home, path)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    lock_token_header = request.headers.get("Lock-Token", "")
    if not lock_token_header:
        raise HTTPException(status_code=400, detail="Missing Lock-Token header")
    
    match = [t.strip("<> ") for t in lock_token_header.split(",")]
    for token in match:
        lock = lock_manager.get_lock(token)
        if lock and lock.username == username:
            lock_manager.release_lock(token)
    
    return Response(status_code=204)


@app.api_route("/{path:path}", methods=["OPTIONS"])
async def handle_options(request: Request, path: str = ""):
    return Response(
        status_code=200,
        headers={
            "DAV": "1, 2",
            "Allow": "OPTIONS, GET, HEAD, PUT, DELETE, MKCOL, COPY, MOVE, PROPFIND, PROPPATCH, LOCK, UNLOCK",
            "Content-Length": "0",
        },
    )


@app.middleware("http")
async def check_path_traversal(request: Request, call_next):
    raw_path = request.url.path
    path_parts = urllib.parse.unquote(raw_path).split("/")
    if ".." in path_parts:
        return Response(status_code=403, content="Forbidden")
    response = await call_next(request)
    return response


@app.middleware("http")
async def add_dav_header(request: Request, call_next):
    response = await call_next(request)
    if "DAV" not in response.headers:
        response.headers["DAV"] = "1, 2"
    return response
