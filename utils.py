import os
import re
import time
import hashlib
import urllib.parse
from pathlib import Path
from xml.etree import ElementTree as ET
from typing import Optional, Union

DAV_NS = "DAV:"
NS_MAP = {"D": DAV_NS}


def register_dav_namespace():
    register_namespace("D", DAV_NS)


def register_namespace(prefix: str, uri: str):
    ET.register_namespace(prefix, uri)


def safe_resolve_path(base_dir: Path, requested_path: str) -> Path:
    requested_path = urllib.parse.unquote(requested_path)
    requested_path = requested_path.lstrip("/")

    if ".." in requested_path.split("/"):
        raise PermissionError("Path traversal not allowed")

    resolved = (base_dir / requested_path).resolve()
    base_resolved = base_dir.resolve()

    try:
        resolved.relative_to(base_resolved)
    except ValueError:
        raise PermissionError("Path traversal not allowed")

    return resolved


def get_etag(file_path: Path) -> str:
    try:
        stat = file_path.stat()
        etag_str = f"{stat.st_mtime}-{stat.st_size}"
        return f'"{hashlib.md5(etag_str.encode()).hexdigest()}"'
    except OSError:
        return '"0-0"'


def get_content_type(file_path: Path) -> str:
    ext = file_path.suffix.lower()
    types = {
        ".txt": "text/plain",
        ".html": "text/html",
        ".htm": "text/html",
        ".css": "text/css",
        ".js": "application/javascript",
        ".json": "application/json",
        ".xml": "application/xml",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".pdf": "application/pdf",
        ".zip": "application/zip",
        ".tar": "application/x-tar",
        ".gz": "application/gzip",
    }
    return types.get(ext, "application/octet-stream")


def format_http_date(timestamp: float) -> str:
    return time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(timestamp))


def parse_http_date(date_str: str) -> Optional[float]:
    try:
        return time.mktime(time.strptime(date_str, "%a, %d %b %Y %H:%M:%S GMT"))
    except (ValueError, AttributeError):
        return None


def parse_range_header(range_header: str, file_size: int) -> Optional[tuple]:
    if not range_header:
        return None
    match = re.match(r"bytes=(\d*)-(\d*)", range_header)
    if not match:
        return None
    start_str, end_str = match.groups()
    if start_str == "" and end_str == "":
        return None
    if start_str == "":
        suffix_length = int(end_str)
        start = max(0, file_size - suffix_length)
        end = file_size - 1
    elif end_str == "":
        start = int(start_str)
        end = file_size - 1
    else:
        start = int(start_str)
        end = int(end_str)
    if start >= file_size:
        return None
    end = min(end, file_size - 1)
    if start > end:
        return None
    return (start, end)


def el(tag: str, text: Optional[str] = None, attrs: Optional[dict] = None) -> ET.Element:
    elem = ET.Element(f"{{{DAV_NS}}}{tag}", attrs or {})
    if text is not None:
        elem.text = text
    return elem


def subel(parent: ET.Element, tag: str, text: Optional[str] = None, attrs: Optional[dict] = None) -> ET.Element:
    elem = el(tag, text, attrs)
    parent.append(elem)
    return elem


def xml_to_string(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def parse_if_header(if_header: str) -> list:
    if not if_header:
        return []
    tokens = []
    patterns = re.findall(r"<([^>]+)>|(\([^)]+\))", if_header)
    for url_part, condition_part in patterns:
        if condition_part:
            inner = condition_part.strip("()")
            inner_tokens = re.findall(r"<([^>]+)>|(\[[^\]]+\])", inner)
            for token, etag in inner_tokens:
                if token:
                    tokens.append(token.strip())
    return tokens


def parse_lock_request(body: bytes) -> dict:
    result = {"scope": "exclusive", "type": "write", "owner": None, "timeout": None}
    if not body:
        return result
    try:
        root = ET.fromstring(body)
        lockscope = root.find(f"{{{DAV_NS}}}lockscope")
        if lockscope is not None:
            exclusive = lockscope.find(f"{{{DAV_NS}}}exclusive")
            if exclusive is not None:
                result["scope"] = "exclusive"
            shared = lockscope.find(f"{{{DAV_NS}}}shared")
            if shared is not None:
                result["scope"] = "shared"
        locktype = root.find(f"{{{DAV_NS}}}locktype")
        if locktype is not None:
            write = locktype.find(f"{{{DAV_NS}}}write")
            if write is not None:
                result["type"] = "write"
        owner = root.find(f"{{{DAV_NS}}}owner")
        if owner is not None:
            owner_href = owner.find(f"{{{DAV_NS}}}href")
            if owner_href is not None:
                result["owner"] = owner_href.text
            elif owner.text:
                result["owner"] = owner.text
    except ET.ParseError:
        pass
    return result


def parse_propfind_request(body: bytes) -> list:
    if not body:
        return ["getcontentlength", "getcontenttype", "getlastmodified", "resourcetype", "quota-available-bytes", "quota-used-bytes"]
    try:
        root = ET.fromstring(body)
        prop = root.find(f"{{{DAV_NS}}}prop")
        if prop is None:
            allprop = root.find(f"{{{DAV_NS}}}allprop")
            if allprop is not None:
                return ["getcontentlength", "getcontenttype", "getlastmodified", "resourcetype", "getetag", "quota-available-bytes", "quota-used-bytes"]
            return ["getcontentlength", "getcontenttype", "getlastmodified", "resourcetype", "quota-available-bytes", "quota-used-bytes"]
        props = []
        for child in prop:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            props.append(tag.lower())
        return props
    except ET.ParseError:
        return ["getcontentlength", "getcontenttype", "getlastmodified", "resourcetype", "quota-available-bytes", "quota-used-bytes"]


def parse_proppatch_request(body: bytes) -> list:
    operations = []
    if not body:
        return operations
    try:
        root = ET.fromstring(body)
        for parent in root:
            if "set" in parent.tag.lower():
                op = "set"
            elif "remove" in parent.tag.lower():
                op = "remove"
            else:
                continue
            prop_elem = parent.find(f"{{{DAV_NS}}}prop")
            if prop_elem is None:
                continue
            for child in prop_elem:
                tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                ns = child.tag.split("}")[0].strip("{") if "}" in child.tag else ""
                operations.append({
                    "op": op,
                    "name": tag,
                    "ns": ns,
                    "value": child.text
                })
        return operations
    except ET.ParseError:
        return operations


register_dav_namespace()
