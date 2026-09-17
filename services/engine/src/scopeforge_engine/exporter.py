"""Redacted portable export (Phase 4). Defaults to redacted derived artifacts,
never originals. The bundle is secret-scanned before finalize — fail closed.
Stdlib only (tarfile/gzip)."""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any

try:
    from .evidence import EvidenceVault
    from .runs import RunStore, ValidationError
    from .models.sanitize import assert_no_secrets, find_secret_rule, sanitize_json, sanitize_text
    from .store import utcnow
except ImportError:  # direct script execution; script dir is on sys.path
    from evidence import EvidenceVault  # type: ignore[no-redef]
    from runs import RunStore, ValidationError  # type: ignore[no-redef]
    from models.sanitize import assert_no_secrets, find_secret_rule, sanitize_json, sanitize_text  # type: ignore[no-redef]
    from store import utcnow  # type: ignore[no-redef]

TEXT_SUFFIXES = {".json", ".jsonl", ".txt", ".md", ".html", ".har", ".xml",
                 ".yaml", ".yml", ".csv", ".log"}


class ExportError(ValueError):
    pass


def _scrub_record(obj: Any) -> Any:
    return sanitize_json(obj)


def _scrub_text(data: bytes) -> bytes:
    try:
        return sanitize_text(data.decode("utf-8")).encode()
    except UnicodeDecodeError:
        return data


def create_export(store: RunStore, run_id: str, *, include_originals: bool = False) -> dict:
    run = store.get_run(run_id)
    vault = EvidenceVault(store, run_id)
    created = utcnow().replace(":", "").replace("-", "")
    out_path = store.run_dir(run_id) / f"export-{created}.tar.gz"

    files: dict[str, bytes] = {}
    warnings: list[str] = []
    manifest = {"version": 1, "run_id": run_id, "created_at": utcnow(),
                "redacted": True, "include_originals": include_originals,
                "files": [], "warnings": warnings}

    def prescan(location: str, text: str) -> None:
        """Record live secret shapes BEFORE scrubbing so redaction is never silent."""
        try:
            rule = find_secret_rule(text)
        except Exception:
            rule = None
        if rule:
            warnings.append(f"live secret shape ({rule}) redacted from {location}")

    def add(name: str, data: bytes, *, scrub_text_member: bool = False,
            prescan_as: str = "") -> None:
        if prescan_as and Path(name).suffix in TEXT_SUFFIXES:
            try:
                prescan(prescan_as, data.decode("utf-8", "replace"))
            except Exception:
                pass
        if scrub_text_member and Path(name).suffix in TEXT_SUFFIXES:
            data = _scrub_text(data)
        files[name] = data

    engagement = json.loads((store.run_dir(run_id) / "engagement.json").read_text())
    prescan("engagement.json", json.dumps(engagement))
    add("engagement.json", json.dumps(_scrub_record(engagement), indent=2).encode())
    prescan("run-state.json", json.dumps(run))
    add("run-state.json", json.dumps(_scrub_record(run), indent=2).encode())
    coverage = store.get_coverage(run_id)
    prescan("coverage.json", json.dumps(coverage))
    add("coverage.json", json.dumps(_scrub_record(coverage), indent=2).encode())
    cases_path = store.run_dir(run_id) / "cases.jsonl"
    if cases_path.exists():
        raw_cases = cases_path.read_text()
        prescan("cases.jsonl", raw_cases)
        scrubbed = "\n".join(
            json.dumps(_scrub_record(json.loads(line)), separators=(",", ":"))
            for line in raw_cases.splitlines() if line.strip())
        add("cases.jsonl", (scrubbed + "\n").encode() if scrubbed else b"")
    findings_dir = store.run_dir(run_id) / "findings"
    if findings_dir.is_dir():
        for report in sorted(findings_dir.rglob("report.md")):
            add(f"findings/{report.parent.name}/report.md", report.read_bytes(),
                scrub_text_member=True, prescan_as=f"findings/{report.parent.name}")
    summary = store.run_dir(run_id) / "summary.md"
    if summary.exists():
        add("summary.md", summary.read_bytes(),
            scrub_text_member=True, prescan_as="summary.md")

    for meta in vault.list():
        eid = meta["evidence_id"]
        prescan(f"evidence/{eid}/metadata.json", json.dumps(meta))
        add(f"evidence/{eid}/metadata.json",
            json.dumps(_scrub_record(meta), indent=2).encode())
        if meta["original"] and not include_originals:
            continue
        try:
            blob = vault.read_bytes(eid, derived_ok=True)
        except (OSError, ValidationError):
            continue
        kind = "original" if meta["original"] else "derived"
        if _looks_text(blob):
            prescan(f"artifacts/{kind}/{eid}", blob[:65536].decode("utf-8", "replace"))
        data = _scrub_text(blob) if _looks_text(blob) else blob
        add(f"artifacts/{kind}/{eid}", data)

    if include_originals:
        store.audit(run_id, "export.create",
                    "WARNING: originals included by explicit request")

    # Fail closed: scan every text member for secret shapes before finalize.
    # (Post-scrub, so a hit here means a scrub bug — never a silent leak.)
    for name, data in files.items():
        if Path(name).suffix in TEXT_SUFFIXES or name.endswith(".json") or name.endswith(".jsonl"):
            try:
                assert_no_secrets(data.decode("utf-8", "replace"), where=f"export:{name}")
            except Exception as exc:
                raise ExportError(f"export blocked: {exc}") from exc

    manifest["files"] = sorted(files)
    manifest["warnings"] = warnings
    files["manifest.json"] = json.dumps(manifest, indent=2).encode()
    with open(out_path, "wb") as fh:
        with tarfile.open(fileobj=fh, mode="w:gz") as tf:
            for name in sorted(files):
                data = files[name]
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mtime = 0  # reproducible
                tf.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256(out_path.read_bytes()).hexdigest()
    store.audit(run_id, "export.create", f"{out_path.name} sha256:{digest[:12]}")
    return {"path": str(out_path), "sha256": digest, "files": len(files),
            "warnings": warnings}


def _looks_text(blob: bytes) -> bool:
    sample = blob[:4096]
    return b"\x00" not in sample and bool(sample)
