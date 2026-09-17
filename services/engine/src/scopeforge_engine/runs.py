"""Run store (Phase 4): portable files authoritative, SQLite rebuildable.

Layout per run: engagement.json, run-state.json, coverage.json, cases.jsonl,
events.jsonl, model-usage.jsonl, audit.jsonl, artifacts/{original,derived}/,
evidence/<id>/metadata.json, findings/<id>/report.md, summary.md.
Stdlib only.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Dual import convention (see docs/development.md): package-relative first so the
# installed distribution works, absolute fallback so `python server.py` direct
# execution keeps working on bare interpreters (CI portability gate).
try:
    from . import coverage_skeleton as coverage_mod
    from .store import (
        CorruptStore,
        FileLock,
        append_jsonl,
        atomic_write_text,
        fsync_tree,
        read_json,
        read_jsonl,
        utcnow,
    )
except ImportError:  # direct script execution; script dir is on sys.path
    import coverage_skeleton as coverage_mod  # type: ignore[no-redef]
    from store import (  # type: ignore[no-redef]
        CorruptStore,
        FileLock,
        append_jsonl,
        atomic_write_text,
        fsync_tree,
        read_json,
        read_jsonl,
        utcnow,
    )

CASE_RESULTS = ("queued", "tested_no_issue_observed", "lead", "validated_finding",
                "false_positive", "blocked", "inconclusive")
CASE_REQUIRED = ("check_id", "asset", "hypothesis", "expected_rule", "baseline",
                 "changed_variable", "observable_result", "stopping_point", "permission_ref")
RUN_VERSION = 1


class ValidationError(ValueError):
    pass


def validate_case(data: dict) -> list[str]:
    errs: list[str] = []
    if data.get("case_id") and not re.fullmatch(r"C-\d{4}", str(data["case_id"])):
        errs.append("case_id must match C-0000")
    if data.get("check_id") and not re.fullmatch(r"T(0[1-9]|1[0-2])\.\d+", str(data["check_id"])):
        errs.append("check_id must match T01.1–T12.n")
    for key in CASE_REQUIRED:
        if not data.get(key) or not str(data[key]).strip():
            errs.append(f"missing {key}")
    if data.get("result") and data["result"] not in CASE_RESULTS:
        errs.append(f"result must be one of {', '.join(CASE_RESULTS)}")
    return errs


class RunStore:
    def __init__(self, home: str | Path) -> None:
        self.home = Path(home)
        (self.home / "runs").mkdir(parents=True, exist_ok=True)

    # -- paths --

    def run_dir(self, run_id: str) -> Path:
        if not re.fullmatch(r"run_\d{4}", run_id):
            raise ValidationError(f"bad run_id {run_id!r}")
        return self.home / "runs" / run_id

    def lock(self, run_id: str) -> FileLock:
        return FileLock(self.run_dir(run_id) / ".lock")

    # -- runs --

    def next_run_id(self) -> str:
        runs = self.home / "runs"
        top = 0
        if runs.is_dir():
            for child in runs.iterdir():
                m = re.fullmatch(r"run_(\d{4})", child.name)
                if m:
                    top = max(top, int(m.group(1)))
        return f"run_{top + 1:04d}"

    def create_run(self, *, mode: str, program: dict | None = None,
                   engagement: dict | None = None) -> dict:
        if mode not in ("plan", "artifacts", "live"):
            raise ValidationError(f"unknown mode {mode!r}")
        eng = dict(engagement or {})
        eng.setdefault("mode", mode)
        if program:
            eng.setdefault("program_name", program if isinstance(program, str) else program.get("program_name", ""))
        for key in ("program_name", "authorization_basis"):
            if not eng.get(key):
                eng[key] = "(unspecified — resolve before live use)" if key == "authorization_basis" else "Untitled program"
        eng.setdefault("included_assets", [])
        eng.setdefault("request_budget", 0 if mode in ("plan", "artifacts") else None)
        run_id = self.next_run_id()
        now = utcnow()
        with self.lock(run_id):
            d = self.run_dir(run_id)
            atomic_write_text(d / "engagement.json", _dump(eng))
            atomic_write_text(d / "run-state.json", _dump({
                "version": RUN_VERSION, "run_id": run_id, "mode": mode,
                "status": "draft", "stage": "scope",
                "created_at": now, "updated_at": now,
                "events": 0, "last_seq": 0, "cancelled": False,
            }))
            atomic_write_text(d / "coverage.json", _dump(coverage_mod.skeleton()))
            for empty in ("cases.jsonl", "events.jsonl", "model-usage.jsonl", "audit.jsonl"):
                (d / empty).touch(exist_ok=True)
            self.audit(run_id, "run.create", f"mode={mode}")
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict:
        state = read_json(self.run_dir(run_id) / "run-state.json")
        if state is None:
            raise ValidationError(f"unknown run_id {run_id!r}")
        return state

    def get_engagement(self, run_id: str) -> dict:
        self.get_run(run_id)
        eng = read_json(self.run_dir(run_id) / "engagement.json", {})
        return eng if isinstance(eng, dict) else {}

    def set_engagement(self, run_id: str, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise ValidationError("engagement patch must be an object")
        with self.lock(run_id):
            eng = self.get_engagement(run_id)
            eng.update(patch)
            atomic_write_text(self.run_dir(run_id) / "engagement.json", _dump(eng))
            self.audit(run_id, "engagement.update", ",".join(sorted(patch)))
            return eng

    def list_runs(self) -> list[dict]:
        out = []
        runs = self.home / "runs"
        if runs.is_dir():
            for child in sorted(runs.iterdir()):
                if re.fullmatch(r"run_\d{4}", child.name):
                    try:
                        out.append(self.get_run(child.name))
                    except (ValidationError, CorruptStore):
                        continue
        return out

    def update_run(self, run_id: str, **fields: Any) -> dict:
        with self.lock(run_id):
            run = self.get_run(run_id)
            run.update(fields)
            run["updated_at"] = utcnow()
            atomic_write_text(self.run_dir(run_id) / "run-state.json", _dump(run))
            return run

    def touch(self, run_id: str, *, events: int = 0, last_seq: int = 0) -> None:
        try:
            with self.lock(run_id):
                run = self.get_run(run_id)
                run["events"] = max(run.get("events", 0), events)
                run["last_seq"] = max(run.get("last_seq", 0), last_seq)
                if run.get("status") == "draft":
                    run["status"] = "active"
                    run["stage"] = "test"
                run["updated_at"] = utcnow()
                atomic_write_text(self.run_dir(run_id) / "run-state.json", _dump(run))
        except (ValidationError, CorruptStore, OSError):
            pass

    # -- cases --

    def _next_case_id(self, run_id: str) -> str:
        top = 0
        for c in self.list_cases(run_id):
            m = re.fullmatch(r"C-(\d{4})", str(c.get("case_id", "")))
            if m:
                top = max(top, int(m.group(1)))
        return f"C-{top + 1:04d}"

    def create_case(self, run_id: str, data: dict) -> dict:
        record = {"result": "queued", "evidence_refs": [], "observed_behavior": None,
                  "inferred_impact": None, **data,
                  "case_id": data.get("case_id") or self._next_case_id(run_id)}
        errs = validate_case(record)
        if errs:
            raise ValidationError("; ".join(errs))
        record["created_at"] = utcnow()
        with self.lock(run_id):
            self.get_run(run_id)
            append_jsonl(self.run_dir(run_id) / "cases.jsonl", record)
            self.audit(run_id, "case.create", record["case_id"])
        return record

    def list_cases(self, run_id: str, result: str | None = None) -> list[dict]:
        self.get_run(run_id)
        cases = read_jsonl(self.run_dir(run_id) / "cases.jsonl")
        return [c for c in cases if not result or c.get("result") == result]

    def get_case(self, run_id: str, case_id: str) -> dict:
        for c in self.list_cases(run_id):
            if c.get("case_id") == case_id:
                return c
        raise ValidationError(f"unknown case {case_id!r}")

    def update_case(self, run_id: str, case_id: str, **fields: Any) -> dict:
        """Executed results require evidence refs; observed_at is stamped."""
        if "result" in fields and fields["result"] not in CASE_RESULTS:
            raise ValidationError(f"bad result {fields['result']!r}")
        with self.lock(run_id):
            cases = self.list_cases(run_id)
            found = False
            out: list[dict] = []
            updated: dict = {}
            for c in cases:
                if c.get("case_id") == case_id:
                    found = True
                    c = {**c, **fields}
                    if c.get("result", "queued") != "queued":
                        if not c.get("evidence_refs"):
                            raise ValidationError("executed results require evidence_refs")
                        c.setdefault("observed_at", utcnow())
                    updated = c
                out.append(c)
            if not found:
                raise ValidationError(f"unknown case {case_id!r}")
            atomic_write_text(self.run_dir(run_id) / "cases.jsonl",
                              "".join(_line(c) for c in out))
            self.audit(run_id, "case.update", f"{case_id} -> {updated.get('result')}")
            return updated

    # -- events / usage / audit --

    def append_event(self, run_id: str, event: dict) -> None:
        append_jsonl(self.run_dir(run_id) / "events.jsonl", event)

    def append_usage(self, run_id: str, usage: dict) -> None:
        append_jsonl(self.run_dir(run_id) / "model-usage.jsonl", usage)

    def audit(self, run_id: str, action: str, detail: str = "") -> None:
        append_jsonl(self.run_dir(run_id) / "audit.jsonl",
                     {"ts": utcnow(), "action": action, "detail": detail})

    def checkpoint(self, run_id: str | None = None) -> None:
        root = self.run_dir(run_id) if run_id else self.home / "runs"
        fsync_tree(root)

    # -- coverage --

    def get_coverage(self, run_id: str) -> dict:
        self.get_run(run_id)
        stored = read_json(self.run_dir(run_id) / "coverage.json", {})
        return coverage_mod.normalize(stored or {})

    def set_coverage(self, run_id: str, categories: list[dict]) -> dict:
        with self.lock(run_id):
            merged = coverage_mod.normalize({"categories": categories})
            atomic_write_text(self.run_dir(run_id) / "coverage.json", _dump(merged))
            self.audit(run_id, "coverage.set", f"{len(categories)} categories")
            return merged


def _dump(obj: dict) -> str:
    import json

    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def _line(obj: dict) -> str:
    import json

    return json.dumps(obj, separators=(",", ":")) + "\n" if obj else ""
