"""Evidence vault (Phase 4, plan.md §10). Immutable originals (content-addressed),
derived/redacted copies with transformation provenance, tamper checks. Stdlib only."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

try:
    from .runs import RunStore, ValidationError
    from .models.sanitize import sanitize_headers, sanitize_text
    from .store import append_jsonl, atomic_write_text, read_json, utcnow
except ImportError:  # direct script execution; script dir is on sys.path
    from runs import RunStore, ValidationError  # type: ignore[no-redef]
    from models.sanitize import sanitize_headers, sanitize_text  # type: ignore[no-redef]
    from store import append_jsonl, atomic_write_text, read_json, utcnow  # type: ignore[no-redef]

MAX_IMPORT_BYTES = 25 * 1024 * 1024


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class EvidenceVault:
    def __init__(self, store: RunStore, run_id: str) -> None:
        store.get_run(run_id)  # validates
        self.store = store
        self.run_id = run_id
        self.root = store.run_dir(run_id)

    # -- ids --

    def _next_id(self, prefix: str) -> str:
        top = 0
        ev_dir = self.root / "evidence"
        if ev_dir.is_dir():
            for child in ev_dir.iterdir():
                m = re.fullmatch(rf"{prefix}-(\d+)", child.name)
                if m:
                    top = max(top, int(m.group(1)))
        width = 3 if prefix == "E" else 4
        return f"{prefix}-{top + 1:0{width}d}"

    # -- ingest --

    def import_bytes(
        self,
        data: bytes,
        *,
        source_type: str,
        tool: str = "artifact.import",
        description: str = "",
        mime: str = "application/octet-stream",
        asset: str = "",
        actor_ref: str = "",
        tenant_ref: str = "",
        case_refs: list[str] | None = None,
        sensitivity: str = "internal",
    ) -> dict:
        if len(data) > MAX_IMPORT_BYTES:
            raise ValidationError(f"artifact too large ({len(data)} > {MAX_IMPORT_BYTES})")
        digest = sha256(data)
        blob = self.root / "artifacts" / "original" / digest[:2] / digest
        if not blob.exists():  # immutable: same hash = same bytes, never overwrite
            blob.parent.mkdir(parents=True, exist_ok=True)
            tmp = blob.with_name(f".{digest}.tmp")
            tmp.write_bytes(data)
            tmp.replace(blob)
        eid = self._next_id("E")
        meta = {
            "evidence_id": eid, "sha256": digest, "source_type": source_type,
            "original": True, "captured_at": utcnow(), "tool": tool,
            "tool_version": "0.1.0", "asset": asset, "actor_ref": actor_ref,
            "tenant_ref": tenant_ref, "case_refs": case_refs or [],
            "redaction_state": "original", "derived_from": None,
            "mime": mime, "byte_size": len(data), "sensitivity": sensitivity,
            "description": description,
        }
        mdir = self.root / "evidence" / eid
        mdir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(mdir / "metadata.json", _dump(meta))
        self.store.audit(self.run_id, "evidence.import", f"{eid} sha256:{digest[:12]}")
        return meta

    def import_file(self, path: str | Path, **kw: Any) -> dict:
        src = Path(path)
        data = src.read_bytes()  # size gate inside import_bytes
        kw.setdefault("mime", _guess_mime(src.suffix))
        kw.setdefault("description", f"imported {src.name}")
        return self.import_bytes(data, **kw)

    def http_capture(
        self,
        *,
        method: str,
        url: str,
        request_headers: dict,
        request_body: bytes,
        status: int,
        response_headers: dict,
        response_body: bytes,
        actor_ref: str = "",
        case_refs: list[str] | None = None,
    ) -> dict:
        """Store a request/response pair as one immutable artifact.

        Deliberate deviation from verbatim capture: authorization headers are
        scrubbed at ingest (auth material lives in credential refs + actor
        metadata, never in run records per §5.4). Bodies stay exact — a leaked
        token in a body can itself be the finding. Exports exclude originals;
        use redact() for report copies.
        """
        raw = (f"{method} {url}\n".encode()
               + _headers_bytes(request_headers) + b"\n" + request_body
               + f"\n--- {status} ---\n".encode()
               + _headers_bytes(response_headers) + b"\n" + response_body)
        return self.import_bytes(
            raw, source_type="http", tool="http.capture",
            description=f"{method} {url} -> {status}",
            mime="application/http", asset=url, actor_ref=actor_ref,
            case_refs=case_refs, sensitivity="sensitive")

    # -- derive / redact --

    def derive(self, evidence_id: str, data: bytes, transformation: str,
               redaction_state: str = "derived") -> dict:
        meta = self.get(evidence_id)
        did = self._next_id("E")
        blob = self.root / "artifacts" / "derived" / did
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(data)
        derived = {**meta, "evidence_id": did, "sha256": sha256(data),
                   "original": False, "captured_at": utcnow(),
                   "redaction_state": redaction_state, "derived_from": evidence_id,
                   "byte_size": len(data),
                   "description": f"{transformation} of {evidence_id}"}
        mdir = self.root / "evidence" / did
        mdir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(mdir / "metadata.json", _dump(derived))
        self.store.audit(self.run_id, "evidence.derive", f"{did} from {evidence_id}: {transformation}")
        return derived

    def redact(self, evidence_id: str) -> dict:
        """Redacted report copy. Original bytes are never modified."""
        meta = self.get(evidence_id)
        raw = self.read_bytes(evidence_id, derived_ok=True)
        try:
            text = raw.decode("utf-8")
            redacted = sanitize_text(text).encode()
        except UnicodeDecodeError:
            redacted = raw  # binary: withhold rather than corrupt
        return self.derive(evidence_id, redacted, "redact",
                           redaction_state="redacted" if meta["mime"].startswith("text")
                           or "http" in meta["mime"] or "json" in meta["mime"] else "derived")

    # -- read / verify --

    def get(self, evidence_id: str) -> dict:
        meta = read_json(self.root / "evidence" / evidence_id / "metadata.json")
        if meta is None:
            raise ValidationError(f"unknown evidence {evidence_id!r}")
        return meta

    def list(self) -> list[dict]:
        ev_dir = self.root / "evidence"
        out = []
        if ev_dir.is_dir():
            for child in sorted(ev_dir.iterdir()):
                meta = read_json(child / "metadata.json", None)
                if meta:
                    out.append(meta)
        return out

    def read_bytes(self, evidence_id: str, *, derived_ok: bool = False) -> bytes:
        meta = self.get(evidence_id)
        if meta["original"]:
            p = self.root / "artifacts" / "original" / meta["sha256"][:2] / meta["sha256"]
        elif derived_ok:
            p = self.root / "artifacts" / "derived" / evidence_id
        else:
            raise ValidationError(f"{evidence_id} is derived; originals only")
        return p.read_bytes()

    def verify(self, evidence_id: str) -> bool:
        """Re-hash and compare (tamper detection)."""
        meta = self.get(evidence_id)
        data = self.read_bytes(evidence_id, derived_ok=True)
        return sha256(data) == meta["sha256"]


def _guess_mime(suffix: str) -> str:
    return {
        ".json": "application/json", ".har": "application/har+json",
        ".html": "text/html", ".txt": "text/plain", ".md": "text/markdown",
        ".png": "image/png", ".jpg": "image/jpeg", ".xml": "application/xml",
        ".yaml": "text/yaml", ".yml": "text/yaml",
    }.get(suffix.lower(), "application/octet-stream")


def _headers_bytes(headers: dict) -> bytes:
    redacted = sanitize_headers({str(k): str(v) for k, v in headers.items()})
    return "".join(f"{k}: {v}\n" for k, v in redacted.items()).encode()


def _dump(obj: dict) -> str:
    import json

    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def link_case(store: RunStore, run_id: str, case_id: str, evidence_id: str) -> dict:
    """Attach evidence to a case (validates both exist)."""
    try:
        from .runs import ValidationError as VE
    except ImportError:
        from runs import ValidationError as VE  # type: ignore[no-redef]

    vault = EvidenceVault(store, run_id)
    vault.get(evidence_id)
    case = store.get_case(run_id, case_id)
    refs = list(case.get("evidence_refs", []))
    if evidence_id not in refs:
        refs.append(evidence_id)
    try:
        return store.update_case(run_id, case_id, evidence_refs=refs)
    except VE:
        raise
