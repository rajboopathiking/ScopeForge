"""Deterministic agent loop (Phase 6, plan.md §9.2). Library, not a server.

One orchestrator, explicit roles: load projection -> select role -> redacted
context -> typed model proposal -> schema validation -> policy dry-run ->
record. Prompts propose; code disposes. No network here beyond the model
provider itself (plan/artifacts modes use mock or user-approved endpoints).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from . import roles as role_profiles
    from .coach import AFTER_CASE_QUESTIONS, record_mastery, score_case
    from .evidence import EvidenceVault
    from .models.errors import CapabilityUnsupported
    from .models.sanitize import sanitize_text
    from .models.types import CanonicalRequest, Message, ResponseFormat, TextBlock
    from .policy import (
        Action,
        ApprovalLedger,
        BudgetTracker,
        decide,
        policy_from_engagement,
        static_resolver,
    )
    from .providers import load_provider_config
    from .models.router import Candidate, RouteRequest, route
    from .runs import RunStore, ValidationError
    from .store import append_jsonl, utcnow
except ImportError:  # direct script execution; script dir is on sys.path
    import roles as role_profiles  # type: ignore[no-redef]
    from coach import AFTER_CASE_QUESTIONS, record_mastery, score_case  # type: ignore[no-redef]
    from evidence import EvidenceVault  # type: ignore[no-redef]
    from models.errors import CapabilityUnsupported  # type: ignore[no-redef]
    from models.sanitize import sanitize_text  # type: ignore[no-redef]
    from models.types import CanonicalRequest, Message, ResponseFormat, TextBlock  # type: ignore[no-redef]
    from policy import (  # type: ignore[no-redef]
        Action, ApprovalLedger, BudgetTracker, decide, policy_from_engagement, static_resolver)
    from providers import load_provider_config  # type: ignore[no-redef]
    from models.router import Candidate, RouteRequest, route  # type: ignore[no-redef]
    from runs import RunStore, ValidationError  # type: ignore[no-redef]
    from store import append_jsonl, utcnow  # type: ignore[no-redef]

TASK_CLASS = {
    "scope_analyst": "extraction", "product_modeler": "plan",
    "test_designer": "plan", "research_assistant": "tool_use",
    "evidence_analyst": "validation", "validator": "validation",
    "report_writer": "report", "coach": "plan",
}


def load_catalog(explicit: str | Path | None = None) -> dict:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    import os

    home = os.environ.get("SCOPEFORGE_HOME")
    if home:
        candidates.append(Path(home) / "catalog.json")
    here = Path.cwd()
    for parent in (here, *here.parents):
        candidates.append(parent / "curriculum" / "coverage-catalog.json")
    for path in candidates:
        try:
            if path.is_file():
                doc = json.loads(path.read_text())
                if isinstance(doc, dict) and "categories" in doc:
                    return doc
        except (OSError, ValueError):
            continue
    return {"version": "0.1.0", "categories": []}


def build_context(store: RunStore, run_id: str, *, case_id: str | None = None,
                  user_message: str = "", event_tail: int = 20) -> dict:
    """Assemble the redacted context package. Secrets never enter: evidence
    contributes metadata + sanitized excerpts only."""
    eng = store.get_engagement(run_id)
    vault = EvidenceVault(store, run_id)
    pinned = {
        "mode": eng.get("mode", "plan"),
        "program": eng.get("program_name", ""),
        "included_assets": eng.get("included_assets", []),
        "exclusions": eng.get("exclusions", []),
        "unresolved": eng.get("unresolved_questions", []),
    }
    case = store.get_case(run_id, case_id) if case_id else None
    evidence_refs = []
    if case:
        for ref in case.get("evidence_refs", []) or []:
            try:
                meta = vault.get(ref)
            except ValidationError:
                continue
            excerpt = ""
            try:
                raw = vault.read_bytes(ref, derived_ok=True)[:800]
                excerpt = sanitize_text(raw.decode("utf-8", "replace"))[:500]
            except (OSError, ValidationError, ValueError):
                excerpt = "(unreadable)"
            evidence_refs.append({
                "evidence_id": meta["evidence_id"], "source_type": meta.get("source_type"),
                "redaction_state": meta.get("redaction_state"), "excerpt": excerpt,
            })
    events = []
    events_path = store.run_dir(run_id) / "events.jsonl"
    if events_path.exists():
        for row in events_path.read_text().splitlines()[-event_tail:]:
            row = row.strip()
            if not row:
                continue
            try:
                kind = json.loads(row).get("method", "event")
            except ValueError:
                kind = "unparseable"
            events.append(kind)
    return {"pinned": pinned, "case": case, "evidence": evidence_refs,
            "recent_event_kinds": events, "user_message": sanitize_text(user_message)[:2000]}


def resolve_provider(store: RunStore, run_id: str, params: dict) -> tuple[str, str, str, dict]:
    """Returns (provider_name, provider_type, model, adapter_kwargs)."""
    eng = store.get_engagement(run_id)
    model_cfg = eng.get("model", {}) if isinstance(eng.get("model"), dict) else {}
    name = params.get("provider") or model_cfg.get("provider") or "mock"
    if name in ("mock",):
        return name, "mock", params.get("model") or model_cfg.get("model") or "mock-turn", {}
    from .server import load_provider_config  # local import: no cycle at module load

    configured = load_provider_config(store.home)
    if name in ("openai-compat", "deepseek", "anthropic-compat"):
        ptype, cfg = name, {}
    elif name in configured:
        cfg = configured[name]
        ptype = cfg["type"]
    else:
        raise ValidationError(f"unknown provider {name!r}")
    base_url = (params.get("base_url") or cfg.get("base_url", "")) if isinstance(cfg, dict) else ""
    return name, ptype, params.get("model") or model_cfg.get("model") or "", {
        "base_url": base_url, "credential_ref": (cfg.get("credential", "") if isinstance(cfg, dict) else "")}


async def run_role_turn(
    store: RunStore,
    run_id: str,
    role: str,
    provider,
    *,
    model: str,
    user_message: str = "",
    case_id: str | None = None,
    catalog_path: str | Path | None = None,
) -> dict[str, Any]:
    """One typed turn. Returns {role, model, output, gaps, structured_via,
    decision, stored} — every step recorded, nothing executed."""
    profile = role_profiles.get_role(role)
    context = build_context(store, run_id, case_id=case_id, user_message=user_message)
    if role == "test_designer" and catalog_path is not None:
        context["catalog_checks"] = [
            c for cat in load_catalog(catalog_path).get("categories", [])
            for c in cat.get("checks", [])]

    caps = await provider.probe(model)
    candidate = Candidate(provider="turn", model=model, capabilities=caps)
    try:
        routing = route([candidate], RouteRequest(
            required=frozenset(profile["requires"]), task_class=TASK_CLASS[role]))
    except CapabilityUnsupported as exc:
        return {"role": role, "model": model, "output": None,
                "gaps": [f"capability blocked: {exc}"], "structured_via": "",
                "decision": None, "stored": False}

    system = (profile["system"]
              + "\nRespond with strict JSON matching the provided schema. "
              + "Available tools (names only, proposals stay proposals): "
              + ", ".join(profile["tools"]) + ".")
    request = CanonicalRequest(
        model=model,
        messages=[Message(role="system", content=[TextBlock(system)]),
                  Message(role="user", content=[TextBlock(json.dumps(context)[:12000])])],
        response_format=ResponseFormat(kind="json_schema", schema=profile["schema"], strict=True),
        task_class=TASK_CLASS[role],
    )
    text_parts: list[str] = []
    tool_payload: Any = None
    structured_via = ""
    usage = {"input_tokens": 0, "output_tokens": 0}
    async for event in provider.stream(request):
        if event.kind == "text.delta":
            text_parts.append(event.text)
        elif event.kind == "tool.done" and event.tool_name == "structured_result":
            tool_payload = event.tool_arguments
            structured_via = "forced-tool"
        elif event.kind == "usage":
            usage = {"input_tokens": event.input_tokens, "output_tokens": event.output_tokens}
        elif event.kind == "error":
            return {"role": role, "model": model, "output": None,
                    "gaps": [f"model error: {event.error}"], "structured_via": "",
                    "decision": None, "stored": False}
    if tool_payload is None:
        structured_via = routing.structured_via or "native"
    output = tool_payload
    gaps: list[str] = []
    if output is None:
        raw = "".join(text_parts).strip()
        try:
            output = json.loads(raw) if raw else None
        except ValueError:
            output = None
        if output is None:
            gaps = ["model returned no parseable strict JSON"]
    if output is not None:
        gaps = role_profiles.validate_output(role, output)

    decision = None
    if role == "research_assistant" and isinstance(output, dict):
        decision = dry_run_proposal(store, run_id, output)

    stored = False
    if output is not None:
        record = {"ts": utcnow(),
                  "method": "agent.turn",
                  "params": {"role": role, "model": model, "gaps": gaps,
                             "structured_via": structured_via, "usage": usage,
                             "decision": decision["allowed"] if decision else None}}
        store.append_event(run_id, record)
        try:
            store.append_usage(run_id, {"role": role, "model": model, **usage})
        except (OSError, ValueError):
            pass
        stored = True
        if role == "coach" and case_id and isinstance(output, dict):
            append_mastery(store, run_id, case_id, output)
    return {"role": role, "model": model, "output": output, "gaps": gaps,
            "structured_via": structured_via or routing.structured_via,
            "decision": decision, "stored": stored}


def dry_run_proposal(store: RunStore, run_id: str, proposal: dict) -> dict:
    """Policy dry-run of a research_assistant proposal (no DNS, no execution)."""
    from .policy import static_resolver

    eng = store.get_engagement(run_id)
    policy = policy_from_engagement(eng)
    tool = str(proposal.get("tool", ""))
    args = proposal.get("bounded_args", {}) if isinstance(proposal.get("bounded_args"), dict) else {}
    kind = {"http.request": "http.request", "http.preview": "http.request",
            "browser.navigate": "browser.navigate"}.get(tool, "tool.exec")
    action = Action(kind=kind, method=str(args.get("method", "GET")),
                    url=str(args.get("url", "")),
                    impact="R3" if kind == "http.request" else "R4",
                    actor_ref=str(args.get("actor_ref", "")),
                    fixture_owned=bool(args.get("fixture_owned", True)),
                    description=str(proposal.get("next_action", "")))
    budgets = BudgetTracker()
    decision = decide(action, policy, resolver=static_resolver({}),
                      budgets=budgets, approvals=ApprovalLedger())
    return {"allowed": decision.allowed, "reasons": decision.reasons,
            "approval_required": decision.approval_required,
            "approval_preview": decision.approval_preview}


def append_mastery(store: RunStore, run_id: str, case_id: str, coach_output: dict) -> None:
    scores = coach_output.get("scores", {}) if isinstance(coach_output, dict) else {}
    mastery_path = store.run_dir(run_id) / "mastery.jsonl"
    history: list[dict] = []
    if mastery_path.exists():
        for line in mastery_path.read_text().splitlines():
            if line.strip():
                try:
                    history.append(json.loads(line))
                except ValueError:
                    continue
    history = record_mastery(history, case_id, scores)
    append_jsonl(mastery_path, history[-1])


def score_case_coach(store: RunStore, run_id: str, case_id: str) -> dict:
    """Deterministic rubric scoring over the actual case record."""
    case = store.get_case(run_id, case_id)
    vault = EvidenceVault(store, run_id)
    linked = 0
    for ref in case.get("evidence_refs", []) or []:
        try:
            vault.get(ref)
            linked += 1
        except ValidationError:
            continue
    scores = score_case(case, evidence_count=linked)
    append_jsonl(store.run_dir(run_id) / "mastery.jsonl",
                 {"case_id": case_id, "scores": scores, "computed": True})
    return {"case_id": case_id, "scores": scores, "questions": list(AFTER_CASE_QUESTIONS)}


def coverage_report(store: RunStore, run_id: str, catalog: dict | None = None) -> dict:
    """Per-check status DERIVED from executed cases. Catalog entries alone are
    never 'executed' — prompts are not coverage."""
    catalog = catalog if catalog is not None else load_catalog()
    by_check: dict[str, list[dict]] = {}
    for case in store.list_cases(run_id):
        by_check.setdefault(str(case.get("check_id", "")), []).append(case)
    executed_results = ("tested_no_issue_observed", "lead", "validated_finding",
                        "false_positive", "blocked", "inconclusive")
    report = []
    for cat in catalog.get("categories", []):
        checks = []
        for check in cat.get("checks", []):
            cid = check["check_id"]
            cases = by_check.get(cid, [])
            if not cases:
                status = "not_executed"
            elif any(c.get("result") == "validated_finding" for c in cases):
                status = "finding"
            elif any(c.get("result") in executed_results for c in cases):
                status = "tested"
            else:
                status = "planned"
            checks.append({"check_id": cid, "title": check.get("title", ""),
                           "status": status,
                           "cases": [c.get("case_id") for c in cases]})
        report.append({"id": cat["id"], "checks": checks})
    return {"run_id": run_id, "categories": report}
