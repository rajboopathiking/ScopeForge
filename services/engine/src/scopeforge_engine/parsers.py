"""Artifact parsers + semantic diff (Phase 4). Pure functions, stdlib only.

Guards: entry/size caps everywhere, archive traversal rejection, no network.
"""
from __future__ import annotations

import difflib
import io
import json
import re
import tarfile
import zipfile
from typing import Any

MAX_ENTRIES = 2000
MAX_BODY = 2 * 1024 * 1024
MAX_DIFF_LINES = 500


class ParseError(ValueError):
    pass


def _cap_body(data: bytes | str) -> bytes:
    if isinstance(data, str):
        data = data.encode("utf-8", "replace")
    return data[:MAX_BODY]


def parse_har(obj: dict) -> list[dict]:
    """HAR log -> exchanges. Unknown shapes raise ParseError (never guess)."""
    try:
        entries = obj["log"]["entries"]
    except (KeyError, TypeError):
        raise ParseError("not a HAR log (missing log.entries)")
    if not isinstance(entries, list):
        raise ParseError("HAR entries is not a list")
    out = []
    for entry in entries[:MAX_ENTRIES]:
        try:
            req, resp = entry["request"], entry["response"]
            out.append({
                "method": req.get("method", ""), "url": req.get("url", ""),
                "request_headers": {h["name"]: h.get("value", "") for h in req.get("headers", [])
                                    if isinstance(h, dict) and "name" in h},
                "request_body": (req.get("postData") or {}).get("text", ""),
                "status": resp.get("status", 0),
                "response_headers": {h["name"]: h.get("value", "") for h in resp.get("headers", [])
                                     if isinstance(h, dict) and "name" in h},
                "response_body": (resp.get("content") or {}).get("text", ""),
                "timings": entry.get("timings", {}),
            })
        except (KeyError, TypeError, AttributeError):
            continue
    return out


def parse_raw_http_request(text: str) -> dict:
    """Parse a raw HTTP request (request line + headers + optional body)."""
    head, _, body = text.partition("\r\n\r\n" if "\r\n\r\n" in text else "\n\n")
    lines = head.splitlines()
    if not lines:
        raise ParseError("empty request")
    parts = lines[0].split()
    if len(parts) < 2:
        raise ParseError(f"bad request line: {lines[0]!r}")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        name, sep, value = line.partition(":")
        if not sep:
            raise ParseError(f"bad header line: {line!r}")
        headers[name.strip()] = value.strip()
    return {"method": parts[0], "target": parts[1],
            "version": parts[2] if len(parts) > 2 else "",
            "headers": headers, "body": body}


def parse_raw_http_response(text: str) -> dict:
    head, _, body = text.partition("\r\n\r\n" if "\r\n\r\n" in text else "\n\n")
    lines = head.splitlines()
    if not lines:
        raise ParseError("empty response")
    parts = lines[0].split(None, 2)
    if len(parts) < 2 or not parts[1].isdigit():
        raise ParseError(f"bad status line: {lines[0]!r}")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        name, sep, value = line.partition(":")
        if not sep:
            raise ParseError(f"bad header line: {line!r}")
        headers[name.strip().lower()] = value.strip()
    return {"version": parts[0], "status": int(parts[1]),
            "reason": parts[2] if len(parts) > 2 else "",
            "headers": headers, "body": body}


def parse_openapi(obj: dict) -> list[dict]:
    """OpenAPI document -> API surface list (methods, paths, params)."""
    paths = obj.get("paths")
    if not isinstance(paths, dict):
        raise ParseError("not an OpenAPI document (missing paths)")
    out = []
    for path, item in list(paths.items())[:MAX_ENTRIES]:
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.startswith("x-") or method == "parameters" or not isinstance(op, dict):
                continue
            params = op.get("parameters", []) or []
            out.append({
                "method": method.upper(), "path": path,
                "operation_id": op.get("operationId", ""),
                "params": [p.get("name", "") for p in params if isinstance(p, dict)],
                "auth": bool(op.get("security", obj.get("security", []))),
            })
    return out


def parse_graphql_sdl(text: str) -> dict:
    """GraphQL SDL -> types/queries/mutations (regex-based, no dep)."""
    text = text[:MAX_BODY] if isinstance(text, str) else ""
    types = re.findall(r"(?:type|interface|enum|input|scalar)\s+(\w+)", text)
    queries = re.findall(r"^\s*(\w+)\s*\([^)]*\)\s*:", text, re.M)
    mutations_block = re.search(r"type\s+Mutation\s*\{([^}]*)\}", text, re.S)
    mutations = re.findall(r"^\s*(\w+)\s*\(", mutations_block.group(1), re.M) if mutations_block else []
    return {"types": sorted(set(types))[:500], "queries": sorted(set(queries))[:500],
            "mutations": sorted(set(mutations))[:200]}


def parse_archive(data: bytes, filename: str) -> list[dict]:
    """List archive members. Traversal members are REJECTED (fail closed)."""
    data = _cap_body(data)
    members: list[dict] = []

    def safe(name: str) -> bool:
        return not (name.startswith("/") or name.startswith("\\")
                    or ".." in name.split("/") or ".." in name.split("\\")
                    or len(name) > 512)

    bio = io.BytesIO(data)
    try:
        if filename.endswith(".zip"):
            with zipfile.ZipFile(bio) as zf:
                for info in zf.infolist()[:MAX_ENTRIES]:
                    if not safe(info.filename):
                        raise ParseError(f"unsafe member rejected: {info.filename!r}")
                    members.append({"name": info.filename, "size": info.file_size,
                                    "compressed": info.compress_size})
        elif filename.endswith((".tar", ".tar.gz", ".tgz")):
            with tarfile.open(fileobj=bio, mode="r:*") as tf:
                for member in tf.getmembers()[:MAX_ENTRIES]:
                    if not safe(member.name):
                        raise ParseError(f"unsafe member rejected: {member.name!r}")
                    members.append({"name": member.name, "size": member.size})
        else:
            raise ParseError(f"unsupported archive: {filename}")
    except (zipfile.BadZipFile, tarfile.TarError) as exc:
        raise ParseError(f"unreadable archive: {exc}") from exc
    return members


def parse_burp_xml(text: str) -> list[dict]:
    """Burp Suite XML export -> exchanges (request/response pairs)."""
    import xml.etree.ElementTree as ET

    text = text[: MAX_BODY * 2] if isinstance(text, str) else ""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ParseError(f"not Burp XML: {exc}") from exc
    issues = root.findall(".//issue") or root.findall(".//item") or [root]
    # Also handle <issues><issue> and flat <items>
    if root.tag == "issues":
        issues = root.findall("issue")
    elif root.tag == "items":
        issues = root.findall("item")

    out: list[dict] = []
    for item in issues[:MAX_ENTRIES]:
        url_elem = item.find("url") or item.find("request/url") or item.find("host")
        url = (url_elem.text or "").strip() if url_elem is not None and url_elem.text else ""
        # Try to decode base64 request/response
        req_elem = item.find("request")
        resp_elem = item.find("response")
        req_text = ""
        resp_text = ""
        if req_elem is not None:
            raw = (req_elem.text or "").strip()
            b64 = req_elem.get("base64", "false").lower() == "true"
            if raw:
                try:
                    import base64

                    req_text = base64.b64decode(raw).decode("utf-8", "replace") if b64 else raw
                except Exception:
                    req_text = raw
        if resp_elem is not None:
            raw = (resp_elem.text or "").strip()
            b64 = resp_elem.get("base64", "false").lower() == "true"
            if raw:
                try:
                    import base64

                    resp_text = base64.b64decode(raw).decode("utf-8", "replace") if b64 else raw
                except Exception:
                    resp_text = raw
        if url or req_text or resp_text:
            out.append({"url": url, "request": req_text[:4000], "response": resp_text[:4000],
                        "name": (item.find("name").text or "").strip() if item.find("name") is not None and item.find("name").text else ""})
    if not out:
        raise ParseError("no Burp items found")
    return out


def normalize_json(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: normalize_json(obj[k]) for k in sorted(obj)}
    if isinstance(obj, list):
        return [normalize_json(v) for v in obj]
    return obj


def diff_json(before: Any, after: Any, path: str = "$") -> list[dict]:
    """Structural diff: added/removed/changed with JSON paths."""
    diffs: list[dict] = []

    def walk(b: Any, a: Any, p: str) -> None:
        if len(diffs) > MAX_ENTRIES:
            return
        if isinstance(b, dict) and isinstance(a, dict):
            for key in b:
                if key not in a:
                    diffs.append({"path": f"{p}.{key}", "kind": "removed", "before": b[key]})
                else:
                    walk(b[key], a[key], f"{p}.{key}")
            for key in a:
                if key not in b:
                    diffs.append({"path": f"{p}.{key}", "kind": "added", "after": a[key]})
        elif isinstance(b, list) and isinstance(a, list):
            for i in range(max(len(b), len(a))):
                if i >= len(b):
                    diffs.append({"path": f"{p}[{i}]", "kind": "added", "after": a[i]})
                elif i >= len(a):
                    diffs.append({"path": f"{p}[{i}]", "kind": "removed", "before": b[i]})
                else:
                    walk(b[i], a[i], f"{p}[{i}]")
        elif b != a or type(b) is not type(a):
            diffs.append({"path": p, "kind": "changed", "before": b, "after": a})

    walk(before, after, path)
    return diffs


def diff_text(before: str, after: str, *, context: int = 3) -> list[str]:
    lines = list(difflib.unified_diff(
        before.splitlines(), after.splitlines(), lineterm="", n=context,
        fromfile="baseline", tofile="variant"))
    return lines[:MAX_DIFF_LINES]


def diff_headers(before: dict, after: dict) -> list[dict]:
    b = {str(k).lower(): v for k, v in before.items()}
    a = {str(k).lower(): v for k, v in after.items()}
    out = []
    for key in b:
        if key not in a:
            out.append({"header": key, "kind": "removed"})
        elif b[key] != a[key]:
            entry: dict[str, Any] = {"header": key, "kind": "changed"}
            if "cookie" not in key and "auth" not in key:
                entry["before"], entry["after"] = b[key], a[key]
            out.append(entry)
    for key in a:
        if key not in b:
            out.append({"header": key, "kind": "added"})
    return out
