"""Coverage skeleton T01–T12 (Phase 4). Concrete checks arrive with the
Phase-6 catalog; this skeleton gives every run a well-formed coverage.json
with applicability states from day one."""
from __future__ import annotations

CATEGORIES: list[tuple[str, str, str]] = [
    ("T01", "Authentication and identity", "Which flow establishes, recovers, links, or steps up identity?"),
    ("T02", "Authorization and tenancy", "Which actor may perform which action on which object and tenant?"),
    ("T03", "Sessions and tokens", "What is the token purpose and lifecycle?"),
    ("T04", "Business logic and state", "Which server-enforced invariant or transition must hold?"),
    ("T05", "API surface and protocols", "Do methods, versions, schemas, and protocols enforce equivalent rules?"),
    ("T06", "Browser execution", "Can controlled input reach an executable browser context?"),
    ("T07", "Origins and integrations", "Where does trust cross origins, frames, messages, callbacks, or providers?"),
    ("T08", "Server-side input", "Which parser, query, template, or command boundary interprets input?"),
    ("T09", "Files, URLs and processors", "How are files, paths, archives, fetches, and conversions bounded?"),
    ("T10", "Deployment and exposure", "Is exposed behavior reachable, in scope, and practically impactful?"),
    ("T11", "HTTP, proxies and caches", "Do routing, parsing, and cache layers agree, with safe isolation?"),
    ("T12", "Data, errors and cryptography", "What confidentiality or integrity rule protects the data?"),
]

APPLICABILITY = ("not_assessed", "applicable", "not_applicable", "blocked")


def skeleton() -> dict:
    return {"categories": [
        {"id": cid, "name": name, "question": q,
         "applicability": "not_assessed", "checks": []}
        for cid, name, q in CATEGORIES
    ]}


def normalize(coverage: dict) -> dict:
    """Merge stored coverage onto the skeleton (forward-compatible)."""
    base = {c["id"]: c for c in skeleton()["categories"]}
    stored = coverage.get("categories", []) if isinstance(coverage, dict) else []
    for cat in stored:
        cid = cat.get("id")
        if cid in base:
            if cat.get("applicability") in APPLICABILITY:
                base[cid]["applicability"] = cat["applicability"]
            if isinstance(cat.get("checks"), list):
                base[cid]["checks"] = cat["checks"]
    return {"categories": [base[cid] for cid, _, _ in CATEGORIES]}
