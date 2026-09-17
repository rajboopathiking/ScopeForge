"""ScopeForge engine — JSON-RPC 2.0 over NDJSON stdio (protocol v0 spine).

Implements plan.md §5.3 + Issues #2/#4 for Phase 1 exit criteria:
- initialize / capabilities / health / shutdown / run.create / run.cancel /
  event.subscribe (ordered soak hook) / tool.cancel
- per-run monotonic event seq, reconnect replay via last_seen_sequence,
  bounded queues (backpressure), cancellation < 1s, major-version fail-closed,
  structured stderr logs, no target network I/O in this spine.

Sync stdlib only (threading, no asyncio pipe helpers) so the spine runs on
Linux, macOS, and Windows with any Python 3.12+. Pydantic/anyio/httpx enter
in later phases (agent loop, HTTP egress).
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import queue
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from .protocol_generated import (
        EVENT_NAMES,
        METHOD_NAMES,
        PROTOCOL_VERSION,
        assert_compatible,
    )
except ImportError:  # allow `python server.py` direct execution
    from protocol_generated import (  # type: ignore[no-redef]
        EVENT_NAMES,
        METHOD_NAMES,
        PROTOCOL_VERSION,
        assert_compatible,
    )

try:
    from .db import rebuild as db_rebuild
    from .evidence import EvidenceVault, link_case
    from .exporter import ExportError, create_export
    from .reports import render_closeout, render_finding_report
    from .runs import RunStore, ValidationError
    from .store import CorruptStore, StoreError
except ImportError:  # allow `python server.py` direct execution
    from db import rebuild as db_rebuild  # type: ignore[no-redef]
    from evidence import EvidenceVault, link_case  # type: ignore[no-redef]
    from exporter import ExportError, create_export  # type: ignore[no-redef]
    from reports import render_closeout, render_finding_report  # type: ignore[no-redef]
    from runs import RunStore, ValidationError  # type: ignore[no-redef]
    from store import CorruptStore, StoreError  # type: ignore[no-redef]

try:
    from .policy import (
        Action,
        ApprovalLedger,
        BudgetTracker,
        CapabilityMint,
        Policy,
        decide,
        policy_from_engagement,
        static_resolver,
    )
except ImportError:  # allow `python server.py` direct execution
    from policy import (  # type: ignore[no-redef]
        Action,
        ApprovalLedger,
        BudgetTracker,
        CapabilityMint,
        Policy,
        decide,
        policy_from_engagement,
        static_resolver,
    )

try:
    from .proof_gate import gate_result_transition
except ImportError:  # allow `python server.py` direct execution
    from proof_gate import gate_result_transition  # type: ignore[no-redef]

START = time.monotonic()
MAX_SUBSCRIBE = 20000
QUEUE_BOUND = 1000
CHUNK = 250

ERR_METHOD_NOT_FOUND = -32601
ERR_PARSE = -32700
ERR_INVALID = -32600
ERR_BAD_PARAMS = -32602
ERR_ENGINE = -32000
ERR_INCOMPATIBLE = 1001
ERR_NO_RUNTIME = 1003  # provider HTTP runtime (httpx/anyio) unavailable

RUN_STATE_VERSION = 0  # minimal Phase-2 snapshot; Phase 4 extends the run layout


def diff_evidence(a: bytes, b: bytes) -> dict:
    """Baseline/variant diff: structural JSON when both parse, else unified text."""
    try:
        from .parsers import diff_json, diff_text
    except ImportError:
        from parsers import diff_json, diff_text  # type: ignore[no-redef]
    import json as _json

    try:
        return {"kind": "json", "diffs": diff_json(_json.loads(a), _json.loads(b))}
    except (ValueError, UnicodeDecodeError):
        try:
            lines = diff_text(a.decode("utf-8", "replace"), b.decode("utf-8", "replace"))
        except ValueError:
            lines = ["(binary artifacts differ)"]
        return {"kind": "text", "lines": lines}


class _NoRuntime(Exception):
    """Provider HTTP runtime (httpx/anyio) missing on a bare interpreter."""


class CapabilityBlocked(Exception):
    """Model lacks a required capability: visible block, never silent downgrade."""


class CredentialBlocked(Exception):
    """No usable credential reference: configuration error, no values leaked."""

try:
    from .providers import PROVIDER_TYPES, load_provider_config
except ImportError:  # direct script execution
    from providers import PROVIDER_TYPES, load_provider_config  # type: ignore[no-redef]

def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def log(level: str, msg: str, trace_id: str = "-", **kw) -> None:
    rec = {"level": level, "msg": msg, "trace_id": trace_id, "ts": utcnow(), **kw}
    sys.stderr.write(json.dumps(rec) + "\n")
    sys.stderr.flush()


def resp_ok(mid, result, trace_id, run_id=None) -> dict:
    r = {
        "jsonrpc": "2.0",
        "id": mid,
        "result": result,
        "protocol_version": PROTOCOL_VERSION,
        "trace_id": trace_id,
        "ts": utcnow(),
    }
    if run_id is not None:
        r["run_id"] = run_id
    return r


def resp_err(mid, code: int, message: str, trace_id, data=None) -> dict:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {
        "jsonrpc": "2.0",
        "id": mid,
        "error": err,
        "protocol_version": PROTOCOL_VERSION,
        "trace_id": trace_id,
        "ts": utcnow(),
    }


def event(name: str, params: dict, trace_id: str, run_id=None) -> dict:
    e = {
        "jsonrpc": "2.0",
        "method": name,
        "params": params,
        "protocol_version": PROTOCOL_VERSION,
        "trace_id": trace_id,
        "ts": utcnow(),
    }
    if run_id is not None:
        e["run_id"] = run_id
    return e


class Engine:
    def __init__(self, store_root: str | Path | None = None) -> None:
        self.lock = threading.Lock()
        self.seq = 0
        self.seq_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.runs: dict[str, dict] = {}
        self.cancelled_runs: set[str] = set()
        self.cancelled_tools: set[str] = set()
        self.streams: dict[str, threading.Event] = {}
        self.closing = False
        base = store_root or os.environ.get("SCOPEFORGE_HOME") or Path.cwd() / ".scopeforge"
        self.store_root = Path(base)
        self.store = RunStore(self.store_root)  # durable record layer (Phase 4)
        self.budgets: dict[str, BudgetTracker] = {}  # enforced below the agent
        self.approvals: dict[str, ApprovalLedger] = {}
        self.mints: dict[str, CapabilityMint] = {}  # memory-only: restart invalidates
        self._lab_manager = None
        self._load_registry()

    def write(self, msg: dict) -> None:
        line = json.dumps(msg, separators=(",", ":"))
        with self.lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    def next_seq(self) -> int:
        with self.seq_lock:
            self.seq += 1
            return self.seq

    # -- durable run registry (RunStore owns the files; this mirrors status) --

    def _snapshot(self, rid: str) -> None:
        run = self.runs.get(rid)
        if run is None:
            return
        try:
            self.store.update_run(
                rid, status=run.get("status", "draft"), stage=run.get("stage", "scope"),
                events=run.get("events", 0), last_seq=run.get("last_seq", 0),
                cancelled=run.get("cancelled", False))
        except (ValidationError, CorruptStore, StoreError, OSError) as exc:
            log("warn", f"snapshot failed: {exc!r}", run_id=rid)

    def _load_registry(self) -> None:
        try:
            for run in self.store.list_runs():
                rid = run["run_id"]
                self.runs[rid] = run
                if run.get("status") in ("paused", "cancelled"):
                    self.cancelled_runs.add(rid)
            if self.runs:
                # Continue the global sequence across restarts so resumed
                # displays stay ordered (crash-resume gate).
                self.seq = max(
                    [self.seq] + [int(r.get("last_seq", 0)) for r in self.runs.values()]
                )
                log("info", f"loaded {len(self.runs)} run(s)")
        except (CorruptStore, StoreError, OSError) as exc:
            log("warn", f"registry load failed: {exc!r}")

    def flush_all(self) -> None:
        with self.state_lock:
            for rid in list(self.runs):
                self._snapshot(rid)

    @staticmethod
    def _summary(run: dict) -> dict:
        return {
            "run_id": run["run_id"],
            "mode": run["mode"],
            "status": run.get("status", "draft"),
            "updated_at": run.get("updated_at"),
            "events": run.get("events", 0),
            "last_seq": run.get("last_seq", 0),
        }

    @staticmethod
    def _budget() -> dict:
        # No numeric limit is known at this layer: report unknown, never unlimited.
        return {"requests_used": 0, "requests_limit": None,
                "note": "unknown limit — set an explicit operating budget before live use"}

    # -- provider registry (declared; live HTTP is lazily imported) --

    def _resolve_provider(self, name: str) -> tuple[str | None, dict | None]:
        if name in PROVIDER_TYPES:
            return name, None
        cfg = load_provider_config(self.store_root).get(name)
        if cfg is None:
            return None, None
        return cfg["type"], cfg

    def _live_probe(
        self, name: str, ptype: str, model: str, cfg: dict,
    ) -> tuple[bool | None, str]:
        """Returns (verified?, detail). None means the provider runtime is missing.

        No credential configured -> credential_missing WITHOUT any network I/O.
        Uses factory for all provider types (incl. Gemini/Ollama/presets/LiteLLM).
        """
        try:
            try:
                from .models.factory import build_adapter
                from .models.secrets import parse_credential_reference
                from .models.secrets import resolve as resolve_cred
                import anyio
            except ImportError:
                from models.factory import build_adapter  # type: ignore[no-redef]
                from models.secrets import parse_credential_reference  # type: ignore[no-redef]
                from models.secrets import resolve as resolve_cred  # type: ignore[no-redef]
                import anyio  # type: ignore[no-redef]
        except ImportError:
            return None, "provider runtime unavailable (httpx/anyio missing); run `uv sync`"
        # Local transports don't need credentials
        if PROVIDER_TYPES.get(ptype, {}).get("transport") == "local" and ptype in ("mock", "ollama", "vllm", "lmstudio"):
            raw_cred = (cfg.get("credential") or "").strip()
            ref = None
            if raw_cred:
                try:
                    ref = parse_credential_reference(raw_cred)
                    resolve_cred(ref, name)
                except Exception as exc:
                    return False, f"credential error: {exc}"
            base_url = cfg.get("base_url") or PROVIDER_TYPES[ptype].get("default_base_url", "")
            try:
                adapter = build_adapter(ptype, base_url=base_url or "", credential=ref)
            except Exception as exc:
                return False, f"adapter error: {exc}"
            async def check_local() -> bool:
                try:
                    ms = await adapter.list_models()
                    return isinstance(ms, list)
                finally:
                    await adapter.close()
            try:
                ok = anyio.run(check_local)
                return (True, "local endpoint reachable") if ok else (False, "empty model list")
            except Exception as exc:
                kind = getattr(exc, "kind", None) or type(exc).__name__
                return False, f"{kind}: {self._sanitize_probe_error(str(exc))}"
        if ptype == "mock":
            return True, "local (no network)"
        raw_cred = (cfg.get("credential") or "").strip()
        # litellm and some hosted presets may work without explicit credential (env)
        needs_cred = PROVIDER_TYPES.get(ptype, {}).get("transport") == "hosted" and ptype not in ("litellm",)
        if needs_cred and not raw_cred:
            return False, (f"no credential configured for {name!r}; "
                           "run `provider add` with an env:/keychain:/cmd: reference")
        ref = None
        if raw_cred:
            try:
                ref = parse_credential_reference(raw_cred)
                resolve_cred(ref, name)
            except Exception as exc:
                return False, f"credential error: {exc}"
        base_url = cfg.get("base_url") or PROVIDER_TYPES[ptype].get("default_base_url", "")
        # Presets like openrouter/azure use default_base_url; litellm needs no URL
        if ptype not in ("litellm",) and not base_url and PROVIDER_TYPES.get(ptype, {}).get("transport") == "hosted":
            # Allow hosted without URL for generic openai-compat (user must set)
            if ptype in ("openai-compat", "anthropic-compat", "gemini"):
                return False, f"no base_url for {name!r}; set one in providers.toml"
        try:
            adapter = build_adapter(ptype, base_url=base_url or "", credential=ref)
        except Exception as exc:
            return False, f"adapter error: {exc}"

        async def check() -> bool:
            try:
                ms = await adapter.list_models()
                return isinstance(ms, list)
            finally:
                await adapter.close()

        try:
            ok = anyio.run(check)
            return (True, "authenticated endpoint reachable") if ok else (False, "empty model list")
        except Exception as exc:
            kind = getattr(exc, "kind", None) or type(exc).__name__
            return False, f"{kind}: {self._sanitize_probe_error(str(exc))}"

    @staticmethod
    def _sanitize_probe_error(message: str) -> str:
        # Probe errors must never carry secrets: API keys only ever appear in
        # httpx Authorization headers, which we never stringify — but belt first.
        low = message.lower()
        for token in ("bearer ", "sk-", "x-api-key", "api_key"):
            if token in low:
                return "authentication material withheld; check credential reference"
        return message[:300]

    # -- dispatch (called from the single main loop; subscribe emits on a worker) --

    def handle(self, msg: dict):
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        trace = str(msg.get("trace_id") or uuid.uuid4().hex[:12])
        run_id = msg.get("run_id")

        if msg.get("jsonrpc") != "2.0" or not method:
            if mid is None:
                return None
            return resp_err(mid, ERR_INVALID, "invalid JSON-RPC envelope", trace)

        if method == "initialize":
            peer = params.get("protocol_version", "")
            try:
                assert_compatible(peer)
            except Exception:
                log("warn", "incompatible peer", trace, peer=peer)
                if mid is None:
                    return None
                return resp_err(
                    mid, ERR_INCOMPATIBLE,
                    f"Incompatible protocol major: peer={peer} engine={PROTOCOL_VERSION}. "
                    "Upgrade both sides so major versions match.",
                    trace,
                )
            caps = {"methods": METHOD_NAMES, "events": EVENT_NAMES,
                    "modes": ["plan", "artifacts", "live"]}
            res = {"protocol_version": PROTOCOL_VERSION, "engine": "scopeforge-engine/0.1.0",
                   "capabilities": caps}
            log("info", "initialize", trace, peer=peer)
            return resp_ok(mid, res, trace) if mid is not None else None

        if method == "capabilities":
            res = {"protocol": PROTOCOL_VERSION, "methods": METHOD_NAMES,
                   "events": EVENT_NAMES, "modes": ["plan", "artifacts", "live"]}
            return resp_ok(mid, res, trace) if mid is not None else None

        if method == "health":
            res = {"ok": True, "uptime_s": round(time.monotonic() - START, 3), "queue_depth": 0}
            return resp_ok(mid, res, trace) if mid is not None else None

        if method == "shutdown":
            self.closing = True
            log("info", "shutdown", trace)
            return resp_ok(mid, {"ok": True}, trace) if mid is not None else None

        if method == "run.create":
            mode = params.get("mode", "plan")
            if mode not in ("plan", "artifacts", "live"):
                return resp_err(mid, ERR_INVALID, f"unknown mode {mode!r}", trace) if mid is not None else None
            engagement = params.get("engagement") if isinstance(params.get("engagement"), dict) else {}
            if params.get("program") and "program_name" not in engagement:
                engagement = {**engagement, "program_name": params["program"]}
            try:
                with self.state_lock:
                    run = self.store.create_run(mode=mode, engagement=engagement)
                    self.runs[run["run_id"]] = run
            except ValidationError as exc:
                return resp_err(mid, ERR_BAD_PARAMS, str(exc), trace) if mid is not None else None
            except (CorruptStore, StoreError, OSError) as exc:
                return resp_err(mid, ERR_ENGINE, f"store error: {exc}", trace) if mid is not None else None
            log("info", "run.create", trace, run_id=run["run_id"], mode=mode)
            out = {"run_id": run["run_id"], "mode": mode, "status": run["status"]}
            return resp_ok(mid, out, trace, run_id=run["run_id"]) if mid is not None else None

        if method == "run.list":
            with self.state_lock:
                runs = [self._summary(self.runs[k]) for k in sorted(self.runs)]
            return resp_ok(mid, {"runs": runs}, trace) if mid is not None else None

        if method == "run.status":
            rid = params.get("run_id") or run_id
            try:
                run = self.store.get_run(rid or "")
            except ValidationError:
                e = resp_err(mid, ERR_BAD_PARAMS, f"unknown run_id {rid!r}", trace) if mid is not None else None
                return e
            detail = {**run, "budget": self._budget()}
            return resp_ok(mid, detail, trace, run_id=rid) if mid is not None else None

        if method == "run.close":
            rid = params.get("run_id") or run_id
            summary = params.get("summary") if isinstance(params.get("summary"), dict) else {}
            try:
                with self.state_lock:
                    run = self.store.get_run(rid or "")
                    cases = self.store.list_cases(run["run_id"])
                    data = {"run_id": run["run_id"], "mode": run["mode"],
                            "egress": "none" if run["mode"] != "live" else "policy-gated",
                            "cases": cases, **summary}
                    text = render_closeout(data)
                    (self.store.run_dir(run["run_id"]) / "summary.md").write_text(text)
                    closed = self.store.update_run(run["run_id"], status="closed",
                                                   closed_at=utcnow(), stage="closeout")
                    self.runs[run["run_id"]] = closed
            except ValidationError:
                e = resp_err(mid, ERR_BAD_PARAMS, f"unknown run_id {rid!r}", trace) if mid is not None else None
                return e
            except (CorruptStore, StoreError, OSError) as exc:
                return resp_err(mid, ERR_ENGINE, f"store error: {exc}", trace) if mid is not None else None
            log("info", "run.close", trace, run_id=rid)
            return resp_ok(mid, {"run_id": rid, "closed": True}, trace, run_id=rid) if mid is not None else None

        if method == "run.cancel":
            rid = params.get("run_id") or run_id
            with self.state_lock:
                if rid in self.runs:
                    try:
                        updated = self.store.update_run(
                            rid, cancelled=True,
                            status="paused" if self.runs[rid].get("status") != "closed" else "closed")
                        self.runs[rid] = updated
                    except (ValidationError, CorruptStore, StoreError, OSError):
                        self.runs[rid]["cancelled"] = True
            if rid:
                self.cancelled_runs.add(rid)
            for key, stop_ev in list(self.streams.items()):
                if rid is None or key.startswith(f"{rid}:"):
                    stop_ev.set()
            log("info", "run.cancel", trace, run_id=rid or "-")
            out = {"run_id": rid, "cancelled": True}
            return resp_ok(mid, out, trace, run_id=rid) if mid is not None else None

        if method == "run.rebuild":
            try:
                counts = db_rebuild(self.store_root)
            except (CorruptStore, StoreError, OSError) as exc:
                return resp_err(mid, ERR_ENGINE, f"rebuild failed: {exc}", trace) if mid is not None else None
            return resp_ok(mid, {"counts": counts}, trace) if mid is not None else None

        if method == "provider.list":
            configured = load_provider_config(self.store_root)
            providers = [
                {"name": t, "builtin": True,
                 "adapter": PROVIDER_TYPES[t]["adapter"],
                 "endpoint_class": PROVIDER_TYPES[t]["endpoint_class"],
                 "transport": PROVIDER_TYPES[t]["transport"]}
                for t in sorted(PROVIDER_TYPES)
            ]
            for name in sorted(configured):
                cfg = configured[name]
                info = PROVIDER_TYPES[cfg["type"]]
                providers.append({"name": name, "builtin": False, "type": cfg["type"],
                                  "adapter": info["adapter"],
                                  "endpoint_class": info["endpoint_class"],
                                  "transport": info["transport"],
                                  "base_url": cfg["base_url"] or info.get("default_base_url", ""),
                                  # Reference only — values never leave the credential store.
                                  "credential": cfg["credential"]})
            return resp_ok(mid, {"providers": providers}, trace) if mid is not None else None

        if method == "model.list":
            pname = str(params.get("provider", ""))
            ptype, _ = self._resolve_provider(pname)
            if ptype is None:
                e = resp_err(mid, ERR_BAD_PARAMS, f"unknown provider {pname!r}", trace) if mid is not None else None
                return e
            info = PROVIDER_TYPES[ptype]
            return resp_ok(mid, {"provider": pname, "models": info["models"],
                                 "note": info.get("models_note", "")}, trace) if mid is not None else None

        if method == "model.probe":
            pname = str(params.get("provider", ""))
            model = str(params.get("model", ""))
            live = bool(params.get("live", False))
            ptype, cfg = self._resolve_provider(pname)
            if ptype is None:
                e = resp_err(mid, ERR_BAD_PARAMS, f"unknown provider {pname!r}", trace) if mid is not None else None
                return e
            if not model:
                models = PROVIDER_TYPES[ptype]["models"]
                model = models[0] if models else "default"
            caps = dict(PROVIDER_TYPES[ptype]["capabilities"])
            for limited_model, overrides in PROVIDER_TYPES[ptype].get("model_limits", {}).items():
                if model == limited_model:
                    caps.update(overrides)
            result: dict = {
                "provider": pname, "type": ptype, "model": model,
                "adapter": PROVIDER_TYPES[ptype]["adapter"],
                "adapter_version": "0.1.0",
                "endpoint_class": PROVIDER_TYPES[ptype]["endpoint_class"],
                "transport": PROVIDER_TYPES[ptype]["transport"],
                "capabilities": caps, "verified_live": ptype == "mock",
                "credential": (cfg or {}).get("credential", ""),
            }
            if live and ptype != "mock":
                verified, detail = self._live_probe(pname, ptype, model, cfg or {})
                if verified is None:  # runtime missing — actionable error, no crash
                    return resp_err(mid, ERR_NO_RUNTIME, detail, trace) if mid is not None else None
                result["verified_live"] = verified
                result["reachability"] = detail
            elif live:
                result["reachability"] = "local (no network)"
            log("info", "model.probe", trace, provider=pname, model=model, live=live)
            return resp_ok(mid, result, trace) if mid is not None else None

        if method in ("case.create", "case.list", "case.show", "case.update"):
            rid = params.get("run_id") or run_id
            try:
                if method == "case.create":
                    case = params.get("case") if isinstance(params.get("case"), dict) else {}
                    record = self.store.create_case(rid or "", case)
                    with self.state_lock:
                        self.runs[rid or ""] = self.store.get_run(rid or "")
                    self._snapshot(rid or "")
                    return resp_ok(mid, {"case": record}, trace, run_id=rid) if mid is not None else None
                if method == "case.list":
                    cases = self.store.list_cases(rid or "", params.get("result"))
                    return resp_ok(mid, {"cases": cases}, trace, run_id=rid) if mid is not None else None
                if method == "case.show":
                    case = self.store.get_case(rid or "", str(params.get("case_id", "")))
                    return resp_ok(mid, {"case": case}, trace, run_id=rid) if mid is not None else None
                fields = params.get("fields") if isinstance(params.get("fields"), dict) else {}
                case_id = str(params.get("case_id", ""))
                if fields.get("result") == "validated_finding":
                    # Proof gate (Phase 6): prompts cannot promote findings.
                    current = self.store.get_case(rid or "", case_id)
                    trial = {**current, **fields}
                    vault = EvidenceVault(self.store, rid or "")
                    refs = []
                    for ref in trial.get("evidence_refs", []) or []:
                        try:
                            refs.append(vault.get(ref))
                        except ValidationError:
                            continue
                    gate = gate_result_transition(trial, refs, "validated_finding")
                    if not gate["allowed"]:
                        return resp_err(
                            mid, ERR_BAD_PARAMS,
                            f"proof gate refuses validated_finding "
                            f"(stays {gate['verdict']}): {'; '.join(gate['gaps'])}",
                            trace) if mid is not None else None
                updated = self.store.update_case(rid or "", case_id, **fields)
                return resp_ok(mid, {"case": updated}, trace, run_id=rid) if mid is not None else None
            except ValidationError as exc:
                return resp_err(mid, ERR_BAD_PARAMS, str(exc), trace) if mid is not None else None
            except (CorruptStore, StoreError, OSError) as exc:
                return resp_err(mid, ERR_ENGINE, f"store error: {exc}", trace) if mid is not None else None

        if method in ("artifact.import", "evidence.list", "evidence.show", "evidence.redact"):
            rid = params.get("run_id") or run_id
            try:
                vault = EvidenceVault(self.store, rid or "")
                if method == "artifact.import":
                    meta = self._import_artifact(vault, params)
                    self._snapshot(rid or "")
                    return resp_ok(mid, {"evidence_id": meta["evidence_id"],
                                         "sha256": meta["sha256"]}, trace, run_id=rid) if mid is not None else None
                if method == "evidence.list":
                    return resp_ok(mid, {"evidence": vault.list()}, trace, run_id=rid) if mid is not None else None
                eid = str(params.get("evidence_id", ""))
                if method == "evidence.redact":
                    derived = vault.redact(eid)
                    return resp_ok(mid, {"derived": derived}, trace, run_id=rid) if mid is not None else None
                meta = vault.get(eid)
                out_ev: dict = {"evidence": meta}
                if params.get("preview"):
                    try:
                        raw = vault.read_bytes(eid, derived_ok=True)
                        out_ev["preview"] = raw[:4000].decode("utf-8", "replace")
                    except (OSError, ValidationError, ValueError):
                        out_ev["preview"] = "(binary or unreadable)"
                return resp_ok(mid, out_ev, trace, run_id=rid) if mid is not None else None
            except ValidationError as exc:
                return resp_err(mid, ERR_BAD_PARAMS, str(exc), trace) if mid is not None else None
            except (CorruptStore, StoreError, OSError) as exc:
                return resp_err(mid, ERR_ENGINE, f"store error: {exc}", trace) if mid is not None else None

        if method in ("coverage.get", "coverage.set"):
            rid = params.get("run_id") or run_id
            try:
                if method == "coverage.get":
                    cov = self.store.get_coverage(rid or "")
                    return resp_ok(mid, {"coverage": cov}, trace, run_id=rid) if mid is not None else None
                cats = params.get("categories")
                if not isinstance(cats, list):
                    return resp_err(mid, ERR_BAD_PARAMS, "categories must be a list", trace) if mid is not None else None
                cov = self.store.set_coverage(rid or "", cats)
                return resp_ok(mid, {"coverage": cov}, trace, run_id=rid) if mid is not None else None
            except ValidationError as exc:
                return resp_err(mid, ERR_BAD_PARAMS, str(exc), trace) if mid is not None else None
            except (CorruptStore, StoreError, OSError) as exc:
                return resp_err(mid, ERR_ENGINE, f"store error: {exc}", trace) if mid is not None else None

        if method == "report.build":
            rid = params.get("run_id") or run_id
            finding = params.get("finding") if isinstance(params.get("finding"), dict) else {}
            try:
                vault = EvidenceVault(self.store, rid or "")
                for ref in finding.get("evidence", []) if isinstance(finding.get("evidence"), list) else []:
                    vault.get(str(ref))  # every claim links to real evidence
                case_ref = finding.get("case_ref")
                if case_ref:
                    # A report tied to a case cannot outrank the proof gate.
                    case = self.store.get_case(rid or "", str(case_ref))
                    refs = []
                    for ref in case.get("evidence_refs", []) or []:
                        try:
                            refs.append(vault.get(ref))
                        except ValidationError:
                            continue
                    gate = gate_result_transition({**case, "result": "validated_finding"},
                                                  refs, "validated_finding")
                    if not gate["allowed"]:
                        return resp_err(
                            mid, ERR_BAD_PARAMS,
                            f"report refused: case {case_ref} is {gate['verdict']}, "
                            f"gaps: {'; '.join(gate['gaps'])}",
                            trace) if mid is not None else None
                text = render_finding_report(finding)
                fid = self._next_finding_id(rid or "")
                fdir = self.store.run_dir(rid or "") / "findings" / fid
                fdir.mkdir(parents=True, exist_ok=True)
                (fdir / "report.md").write_text(text)
                self.store.audit(rid or "", "report.build", fid)
                return resp_ok(mid, {"finding_id": fid,
                                     "path": str(fdir / "report.md")}, trace, run_id=rid) if mid is not None else None
            except ValidationError as exc:
                return resp_err(mid, ERR_BAD_PARAMS, str(exc), trace) if mid is not None else None
            except ValueError as exc:  # incomplete draft — refuse, list gaps
                return resp_err(mid, ERR_BAD_PARAMS, str(exc), trace) if mid is not None else None
            except (CorruptStore, StoreError, OSError) as exc:
                return resp_err(mid, ERR_ENGINE, f"store error: {exc}", trace) if mid is not None else None

        if method == "export.create":
            rid = params.get("run_id") or run_id
            try:
                result = create_export(self.store, rid or "",
                                       include_originals=bool(params.get("include_originals", False)))
                return resp_ok(mid, result, trace, run_id=rid) if mid is not None else None
            except ValidationError as exc:
                return resp_err(mid, ERR_BAD_PARAMS, str(exc), trace) if mid is not None else None
            except (ExportError, CorruptStore, StoreError, OSError, ValueError) as exc:
                return resp_err(mid, ERR_ENGINE, f"export failed: {exc}", trace) if mid is not None else None

        if method == "policy.import":
            path = str(params.get("file", ""))
            rid = params.get("run_id") or run_id
            inline = params.get("policy") if isinstance(params.get("policy"), dict) else None
            if inline is None:
                if not path:
                    return resp_err(mid, ERR_BAD_PARAMS,
                                    "policy.import needs file or an inline policy document",
                                    trace) if mid is not None else None
                if path.startswith(("http://", "https://")):
                    e = resp_err(mid, ERR_BAD_PARAMS,
                                 "URL policy import needs the Phase-7 transport; save the file first",
                                 trace) if mid is not None else None
                    return e
                try:
                    inline = self._read_policy_file(path)
                except (ValueError, OSError) as exc:
                    return resp_err(mid, ERR_BAD_PARAMS, f"policy rejected: {exc}", trace) if mid is not None else None
            try:
                policy = Policy.load(inline)
            except ValueError as exc:
                return resp_err(mid, ERR_BAD_PARAMS, f"policy rejected: {exc}", trace) if mid is not None else None
            summary = {"program_name": policy.program_name, "mode": policy.mode,
                       "include": len(policy.include), "exclude": len(policy.exclude),
                       "unresolved": policy.unresolved}
            if rid:
                try:
                    eng = self.store.get_engagement(rid)
                    eng.update({
                        "program_name": policy.program_name,
                        "policy_source": policy.policy_source or f"file:{path}",
                        "policy_retrieved_at": policy.retrieved_at or utcnow(),
                        "mode": policy.mode,
                        "included_assets": [self._rule_to_asset(r) for r in policy.include],
                        "exclusions": [self._rule_to_asset(r) for r in policy.exclude],
                        "permitted_methods": policy.methods_allow,
                        "prohibited_methods": policy.methods_deny,
                        "budgets": policy.budgets,
                        "required_headers": policy.required_headers,
                        "unresolved_questions": policy.unresolved,
                        # Structured rules round-trip exactly (ports, prefixes,
                        # allow_private); display strings above are for humans.
                        "policy": {
                            "include": [self._rule_to_dict(r) for r in policy.include],
                            "exclude": [self._rule_to_dict(r) for r in policy.exclude],
                            "budgets": policy.budgets, "impact": policy.impact},
                    })
                    self.store.set_engagement(rid, eng)
                    self.store.audit(rid, "policy.import", path or "inline")
                except ValidationError:
                    e = resp_err(mid, ERR_BAD_PARAMS, f"unknown run_id {rid!r}", trace) if mid is not None else None
                    return e
            log("info", "policy.import", trace, path=path or "inline")
            out_p: dict = {"policy": summary}
            if rid:
                out_p["run_id"] = rid
            return resp_ok(mid, out_p, trace, run_id=rid) if mid is not None else None

        if method == "policy.check":
            rid = params.get("run_id") or run_id
            raw_action = params.get("action") if isinstance(params.get("action"), dict) else {}
            dns_map = params.get("dns") if isinstance(params.get("dns"), dict) else {}
            try:
                if rid:
                    policy = policy_from_engagement(self.store.get_engagement(rid))
                elif isinstance(params.get("policy"), dict):
                    policy = Policy.load(params["policy"])
                else:
                    return resp_err(mid, ERR_BAD_PARAMS,
                                    "policy.check needs run_id or an inline policy", trace) if mid is not None else None
                action = Action(
                    kind=str(raw_action.get("kind", "http.request")),
                    method=str(raw_action.get("method", "GET")),
                    url=str(raw_action.get("url", "")),
                    redirect_chain=tuple(raw_action.get("redirect_chain", []) or ()),
                    impact=str(raw_action.get("impact", "R3")),
                    bytes_estimate=int(raw_action.get("bytes_estimate", 0) or 0),
                    cost_estimate=float(raw_action.get("cost_estimate", 0.0) or 0.0),
                    actor_ref=str(raw_action.get("actor_ref", "")),
                    fixture_owned=bool(raw_action.get("fixture_owned", True)),
                    approval=str(raw_action.get("approval", "none")),
                    description=str(raw_action.get("description", "")),
                    idempotent=bool(raw_action.get("idempotent", True)),
                )
            except (ValidationError, ValueError) as exc:
                return resp_err(mid, ERR_BAD_PARAMS, f"bad check input: {exc}", trace) if mid is not None else None
            budgets = self.budgets.setdefault(rid or "*", BudgetTracker())
            approvals = self.approvals.setdefault(rid or "*", ApprovalLedger())
            decision = decide(action, policy, resolver=static_resolver(dns_map),
                              budgets=budgets, approvals=approvals, now_s=time.time())
            return resp_ok(mid, {"allowed": decision.allowed, "reasons": decision.reasons,
                                 "approval_required": decision.approval_required,
                                 "approval_preview": decision.approval_preview},
                           trace, run_id=rid) if mid is not None else None

        if method == "policy.status":
            rid = params.get("run_id") or run_id
            try:
                eng = self.store.get_engagement(rid or "")
                policy = policy_from_engagement(eng)
            except ValidationError:
                e = resp_err(mid, ERR_BAD_PARAMS, f"unknown run_id {rid!r}", trace) if mid is not None else None
                return e
            budgets = self.budgets.get(rid or "")
            return resp_ok(mid, {
                "run_id": rid, "mode": policy.mode,
                "policy_source": policy.policy_source,
                "unresolved": policy.unresolved,
                "budgets": {"limits": policy.budgets,
                            "used": {"requests": budgets.requests_used if budgets else 0,
                                     "bytes": budgets.bytes_used if budgets else 0,
                                     "cost": budgets.cost_used if budgets else 0.0}},
            }, trace, run_id=rid) if mid is not None else None

        if method == "scope.show":
            rid = params.get("run_id") or run_id
            try:
                eng = self.store.get_engagement(rid or "")
                policy = policy_from_engagement(eng)
            except ValidationError:
                e = resp_err(mid, ERR_BAD_PARAMS, f"unknown run_id {rid!r}", trace) if mid is not None else None
                return e
            return resp_ok(mid, {
                "run_id": rid,
                "program_name": policy.program_name,
                "mode": policy.mode,
                "policy_source": policy.policy_source,
                "retrieved_at": policy.retrieved_at,
                "included_assets": eng.get("included_assets", []),
                "exclusions": eng.get("exclusions", []),
                "methods_allow": policy.methods_allow,
                "methods_deny": policy.methods_deny,
                "unresolved": policy.unresolved,
                "budgets": policy.budgets,
            }, trace, run_id=rid) if mid is not None else None

        if method == "event.subscribe":
            count = max(0, min(int(params.get("count", 10)), MAX_SUBSCRIBE))
            rid = params.get("run_id") or run_id
            if rid and rid not in self.runs:
                e = resp_err(mid, ERR_BAD_PARAMS, f"unknown run_id {rid!r}", trace) if mid is not None else None
                return e
            # Replay anchor accepted; server seq stays globally monotonic, client dedups.
            anchor = params.get("from_seq", params.get("last_seen_sequence"))
            stream_key = f"{rid or '*'}:{trace}"
            stop_ev = threading.Event()
            self.streams[stream_key] = stop_ev
            # Worker thread keeps the main loop free so cancels land within ms.
            threading.Thread(
                target=self._emit, args=(mid, count, trace, rid, stream_key, stop_ev),
                daemon=True,
            ).start()
            log("info", "event.subscribe start", trace, count=count, from_seq=anchor)
            return "async"

        if method == "tool.cancel":
            tid = str(params.get("tool_call_id") or params.get("run_id") or run_id or "*")
            self.cancelled_tools.add(tid)
            for stop_ev in self.streams.values():
                stop_ev.set()
            log("info", "tool.cancel", trace, tool_call_id=tid)
            out = {"cancelled": True}
            return resp_ok(mid, out, trace, run_id=run_id) if mid is not None else None

        if method in ("tool.preview", "tool.execute"):
            rid = params.get("run_id") or run_id
            raw_action = params.get("action") if isinstance(params.get("action"), dict) else {}
            try:
                if not rid:
                    raise ValidationError("run_id is required")
                eng = self.store.get_engagement(rid)
                policy = policy_from_engagement(eng)
                action = self._coerce_action(raw_action)
            except (ValidationError, ValueError) as exc:
                return resp_err(mid, ERR_BAD_PARAMS, f"bad tool input: {exc}", trace) if mid is not None else None
            if method == "tool.preview":
                budgets = self.budgets.setdefault(rid, BudgetTracker())
                approvals = self.approvals.setdefault(rid, ApprovalLedger())
                decision = decide(action, policy, resolver=static_resolver({}),
                                  budgets=budgets, approvals=approvals, now_s=time.time())
                return resp_ok(mid, {"allowed": decision.allowed, "reasons": decision.reasons,
                                     "approval_required": decision.approval_required,
                                     "approval_preview": decision.approval_preview},
                               trace, run_id=rid) if mid is not None else None
            try:
                result = self._run_async(self._execute_action(rid, policy, action, params))
            except _NoRuntime as exc:
                return resp_err(mid, ERR_NO_RUNTIME, str(exc), trace) if mid is not None else None
            except (CorruptStore, StoreError, OSError) as exc:
                return resp_err(mid, ERR_ENGINE, f"execution failed: {exc}", trace) if mid is not None else None
            return resp_ok(mid, result, trace, run_id=rid) if mid is not None else None

        if method == "approval.respond":
            rid = params.get("run_id") or run_id
            scope = str(params.get("scope", ""))
            kind = str(params.get("kind", "one-shot"))
            if not rid or not scope:
                return resp_err(mid, ERR_BAD_PARAMS, "approval.respond needs run_id + scope",
                                trace) if mid is not None else None
            ledger = self.approvals.setdefault(rid, ApprovalLedger())
            if params.get("approved"):
                ledger.grant(scope, kind if kind in ("one-shot", "session") else "one-shot")
                try:
                    self.store.audit(rid, "approval.grant", f"{scope} ({kind})")
                except (ValidationError, CorruptStore, StoreError, OSError):
                    pass
                return resp_ok(mid, {"recorded": True, "approved": True}, trace,
                               run_id=rid) if mid is not None else None
            try:
                self.store.audit(rid, "approval.decline", scope)
            except (ValidationError, CorruptStore, StoreError, OSError):
                pass
            return resp_ok(mid, {"recorded": True, "approved": False}, trace,
                           run_id=rid) if mid is not None else None

        if method in ("lab.list", "lab.launch", "lab.stop", "lab.reset"):
            try:
                from .labs import LabError, LabManager, list_labs
            except ImportError:
                from labs import LabError, LabManager, list_labs  # type: ignore[no-redef]
            try:
                if method == "lab.list":
                    return resp_ok(mid, {"labs": list_labs(
                        params.get("labs_dir"))}, trace) if mid is not None else None
                manager = self._labs()
                if method == "lab.launch":
                    info = manager.launch(str(params.get("lab", "tenancy-demo")),
                                          params.get("labs_dir"))
                    return resp_ok(mid, info, trace) if mid is not None else None
                if method == "lab.stop":
                    stopped = manager.stop(str(params.get("lab", "")))
                    return resp_ok(mid, {"stopped": stopped}, trace) if mid is not None else None
                url = str(params.get("url", "")) or manager.url(str(params.get("lab", "")))
                return resp_ok(mid, manager.reset(url), trace) if mid is not None else None
            except LabError as exc:
                return resp_err(mid, ERR_BAD_PARAMS, str(exc), trace) if mid is not None else None

        if method == "experiment.compare":
            rid = params.get("run_id") or run_id
            if not rid:
                return resp_err(mid, ERR_BAD_PARAMS, "run_id is required",
                                trace) if mid is not None else None
            baseline = params.get("baseline") if isinstance(params.get("baseline"), dict) else {}
            variant = params.get("variant") if isinstance(params.get("variant"), dict) else {}
            case_id = params.get("case_id")
            try:
                result = self._run_async(self._experiment_compare(
                    rid, baseline, variant,
                    str(case_id) if case_id else None))
            except ValidationError as exc:
                return resp_err(mid, ERR_BAD_PARAMS, str(exc), trace) if mid is not None else None
            except _NoRuntime as exc:
                return resp_err(mid, ERR_NO_RUNTIME, str(exc), trace) if mid is not None else None
            except (CorruptStore, StoreError, OSError) as exc:
                return resp_err(mid, ERR_ENGINE, f"experiment failed: {exc}", trace) if mid is not None else None
            return resp_ok(mid, result, trace, run_id=rid) if mid is not None else None

        if method == "evidence.diff":
            rid = params.get("run_id") or run_id
            try:
                vault = EvidenceVault(self.store, rid or "")
                a = vault.read_bytes(str(params.get("baseline_id", "")), derived_ok=True)
                b = vault.read_bytes(str(params.get("variant_id", "")), derived_ok=True)
                result = diff_evidence(a, b)
                result.update({"baseline_id": params.get("baseline_id"),
                               "variant_id": params.get("variant_id")})
                return resp_ok(mid, result, trace, run_id=rid) if mid is not None else None
            except ValidationError as exc:
                return resp_err(mid, ERR_BAD_PARAMS, str(exc), trace) if mid is not None else None
            except (CorruptStore, StoreError, OSError) as exc:
                return resp_err(mid, ERR_ENGINE, f"diff failed: {exc}", trace) if mid is not None else None

        if method in ("chat.submit", "case.execute", "coach.score", "coverage.report"):
            rid = params.get("run_id") or run_id
            try:
                result = self._agent_call(method, params, rid or "", trace)
            except ValidationError as exc:
                return resp_err(mid, ERR_BAD_PARAMS, str(exc), trace) if mid is not None else None
            except _NoRuntime as exc:
                return resp_err(mid, ERR_NO_RUNTIME, str(exc), trace) if mid is not None else None
            except (CapabilityBlocked, CredentialBlocked) as exc:
                return resp_err(mid, ERR_BAD_PARAMS, str(exc), trace) if mid is not None else None
            except (CorruptStore, StoreError, OSError) as exc:
                return resp_err(mid, ERR_ENGINE, f"store error: {exc}", trace) if mid is not None else None
            return resp_ok(mid, result, trace, run_id=rid) if mid is not None else None

        # Phase 8: MCP + tool wrapper + HAR/Burp import
        if method in ("mcp.add", "mcp.list", "mcp.enable", "mcp.disable", "mcp.remove",
                      "tool.list", "tool.invoke", "har.import", "burp.import"):
            try:
                result = self._handle_phase8(method, params, trace)
            except ValueError as exc:
                return resp_err(mid, ERR_BAD_PARAMS, str(exc), trace) if mid is not None else None
            except _NoRuntime as exc:
                return resp_err(mid, ERR_NO_RUNTIME, str(exc), trace) if mid is not None else None
            except PermissionError as exc:
                return resp_err(mid, ERR_BAD_PARAMS, str(exc), trace) if mid is not None else None
            except (CorruptStore, StoreError, OSError) as exc:
                return resp_err(mid, ERR_ENGINE, f"store error: {exc}", trace) if mid is not None else None
            return resp_ok(mid, result, trace) if mid is not None else None

        if mid is None:
            log("warn", "unknown notification", trace, method=method)
            return None
        log("warn", "method not found", trace, method=method)
        return resp_err(mid, ERR_METHOD_NOT_FOUND, f"Method not found: {method}", trace)

    # -- agent runtime RPCs (Phase 6; deterministic loop, policy outside) --

    def _get_adapter(self, run_id: str, params: dict):
        """Build the model adapter for a turn. Local imports keep bare startup lean."""
        try:
            from .agent import resolve_provider
            from .models.errors import CapabilityUnsupported, CredentialMissing
            from .models.factory import build_adapter
            from .models.secrets import parse_credential_reference
        except ImportError:
            from agent import resolve_provider  # type: ignore[no-redef]
            from models.errors import CapabilityUnsupported, CredentialMissing  # type: ignore[no-redef]
            from models.factory import build_adapter  # type: ignore[no-redef]
            from models.secrets import parse_credential_reference  # type: ignore[no-redef]
        try:
            name, ptype, model, kw = resolve_provider(self.store, run_id, params)
        except ValidationError as exc:
            raise CredentialBlocked(str(exc)) from exc
        credential = None
        if ptype != "mock":
            ref = (kw.get("credential_ref") or "").strip()
            if not ref:
                raise CredentialBlocked(
                    f"no credential configured for provider {name!r}; "
                    "run `provider add` with an env:/keychain:/cmd: reference")
            try:
                credential = parse_credential_reference(ref)
            except Exception as exc:
                raise CredentialBlocked(f"bad credential reference: {exc}") from exc
        try:
            adapter = build_adapter(ptype, base_url=kw.get("base_url", ""),
                                    credential=credential)
        except CapabilityUnsupported as exc:
            raise CapabilityBlocked(str(exc)) from exc
        return adapter, model or "mock-turn"

    def _run_async(self, coro):
        import asyncio

        try:
            return asyncio.run(coro)
        except ImportError as exc:
            raise _NoRuntime(f"provider runtime unavailable: {exc}; run `uv sync`") from exc

    def _agent_call(self, method: str, params: dict, run_id: str, trace: str) -> dict:
        try:
            from .agent import coverage_report, run_role_turn, score_case_coach
        except ImportError:
            from agent import coverage_report, run_role_turn, score_case_coach  # type: ignore[no-redef]
        if not run_id:
            raise ValidationError("run_id is required")
        self.store.get_run(run_id)  # validates before any model I/O
        if method == "coach.score":
            case_id = str(params.get("case_id", ""))
            if not case_id:
                raise ValidationError("coach.score needs case_id")
            return score_case_coach(self.store, run_id, case_id)
        if method == "coverage.report":
            return coverage_report(self.store, run_id)
        role = str(params.get("role", "research_assistant" if method == "chat.submit" else ""))
        if method == "case.execute":
            role = "research_assistant"
        case_id = params.get("case_id")
        adapter, model = self._get_adapter(run_id, params)
        try:
            if method == "chat.submit":
                result = self._run_async(run_role_turn(
                    self.store, run_id, role, adapter, model=model,
                    user_message=str(params.get("message", "")),
                    case_id=str(case_id) if case_id else None))
            else:
                if not case_id:
                    raise ValidationError("case.execute needs case_id")
                self.store.get_case(run_id, str(case_id))
                result = self._run_async(run_role_turn(
                    self.store, run_id, "research_assistant", adapter, model=model,
                    user_message="Propose the next bounded action for this case.",
                    case_id=str(case_id)))
                result = {"proposal": result["output"], "gaps": result["gaps"],
                          "decision": result["decision"]}
                if params.get("execute") and isinstance(result["proposal"], dict):
                    # One-shot live execution of the proposal (fully gated).
                    live = self._execute_proposal(run_id, str(case_id), result["proposal"])
                    result["execution"] = live
            return result
        finally:
            try:
                self._run_async(adapter.close())
            except (OSError, ValueError, _NoRuntime):
                pass

    def _execute_proposal(self, run_id: str, case_id: str, proposal: dict) -> dict:
        """Execute one proposed bounded action under the full gate. Only
        http.request proposals execute; anything else returns its dry-run."""
        tool = str(proposal.get("tool", ""))
        args = proposal.get("bounded_args", {}) if isinstance(
            proposal.get("bounded_args"), dict) else {}
        if tool not in ("http.request", "http.preview"):
            return {"executed": False,
                    "reason": f"no live executor for tool {tool!r} (dry-run only)"}
        eng = self.store.get_engagement(run_id)
        policy = policy_from_engagement(eng)
        try:
            action = self._coerce_action({
                "kind": "http.request", "method": args.get("method", "GET"),
                "url": args.get("url", ""), "headers": args.get("headers", {}),
                "body": args.get("body", ""), "actor_ref": args.get("actor_ref", ""),
                "fixture_owned": args.get("fixture_owned", True),
                "description": str(proposal.get("next_action", "")),
            })
        except ValidationError as exc:
            # Model-proposed credential smuggling fails closed, not loud.
            return {"executed": False, "reason": f"proposal refused: {exc}"}
        out = self._run_async(self._execute_action(
            run_id, policy, action, {"case_id": case_id}))
        out["executed"] = bool(out.get("sent"))
        return out

    @staticmethod
    def _import_artifact(vault: EvidenceVault, params: dict) -> dict:
        import base64

        kw: dict[str, Any] = {
            "source_type": str(params.get("source_type", "file")),
            "tool": str(params.get("tool", "artifact.import")),
            "description": str(params.get("description", "")),
            "mime": str(params.get("mime", "")),
            "asset": str(params.get("asset", "")),
            "actor_ref": str(params.get("actor_ref", "")),
            "tenant_ref": str(params.get("tenant_ref", "")),
            "case_refs": params.get("case_refs") if isinstance(params.get("case_refs"), list) else [],
            "sensitivity": str(params.get("sensitivity", "internal")),
        }
        if params.get("content_b64"):
            try:
                data = base64.b64decode(params["content_b64"], validate=True)
            except (ValueError, TypeError):
                raise ValidationError("content_b64 is not valid base64")
            if len(data) > 5 * 1024 * 1024:
                raise ValidationError("inline content exceeds 5 MiB; pass a path instead")
            return vault.import_bytes(data, **kw)
        path = str(params.get("path", ""))
        if not path:
            raise ValidationError("artifact.import needs path or content_b64")
        candidate = Path(path)
        if not candidate.is_file():
            raise ValidationError(f"artifact not found: {path!r}")
        return vault.import_file(candidate, **kw)

    def _next_finding_id(self, run_id: str) -> str:
        fdir = self.store.run_dir(run_id) / "findings"
        top = 0
        if fdir.is_dir():
            for child in fdir.iterdir():
                try:
                    if child.name.startswith("F-"):
                        top = max(top, int(child.name[2:]))
                except ValueError:
                    continue
        return f"F-{top + 1:04d}"

    # -- live execution helpers (Phase 7; sole path to target traffic) --

    def _labs(self):
        try:
            from .labs import LabManager
        except ImportError:
            from labs import LabManager  # type: ignore[no-redef]
        if self._lab_manager is None:
            self._lab_manager = LabManager()
        return self._lab_manager

    def _handle_phase8(self, method: str, params: dict, trace: str) -> dict:
        # Import locally to keep bare-interpreter startup lean
        if method.startswith("mcp."):
            try:
                from .mcp import MCPRegistry
            except ImportError:
                from mcp import MCPRegistry  # type: ignore[no-redef]
            reg = MCPRegistry(self.store_root)
            if method == "mcp.add":
                return reg.add_server(
                    str(params.get("name", "")), str(params.get("command", "")),
                    version=str(params.get("version", "")), hash=str(params.get("hash", "")))
            if method == "mcp.list":
                return {"servers": reg.list_servers()}
            if method == "mcp.enable":
                return reg.enable_server(str(params.get("name", "")),
                                         allowed_tools=params.get("allowed_tools"))
            if method == "mcp.disable":
                return reg.disable_server(str(params.get("name", "")))
            if method == "mcp.remove":
                ok = reg.remove_server(str(params.get("name", "")))
                if not ok:
                    raise ValueError(f"unknown server {params.get('name')!r}")
                return {"removed": True}
        if method == "tool.list":
            try:
                from .tools import NUCLEI_MANIFEST
            except ImportError:
                from tools import NUCLEI_MANIFEST  # type: ignore[no-redef]
            # SDK exemplar + any registered constrained tools (future: registry file)
            return {"tools": [
                {"name": NUCLEI_MANIFEST.name, "description": NUCLEI_MANIFEST.description,
                 "risk_class": NUCLEI_MANIFEST.risk_class, "executable": NUCLEI_MANIFEST.executable,
                 "allowed_args": list(NUCLEI_MANIFEST.allowed_args)},
            ]}
        if method == "tool.invoke":
            try:
                from .tools import ConstrainedToolWrapper, ToolManifest
            except ImportError:
                from tools import ConstrainedToolWrapper, ToolManifest  # type: ignore[no-redef]
            manifest = ToolManifest(
                name=str(params.get("name", "custom")),
                description=str(params.get("description", "")),
                executable=str(params.get("executable", "")),
                allowed_args=tuple(params.get("allowed_args", ())),
                risk_class=str(params.get("risk_class", "R2")),
                timeout_s=float(params.get("timeout_s", 30)),
            )
            wrapper = ConstrainedToolWrapper(manifest)
            # Egress check: tool cannot contact destination absent from its capability
            target = str(params.get("target_url", ""))
            policy = None
            if target:
                # Reuse current run's policy if run_id supplied, else allow (no egress needs)
                run_id = str(params.get("run_id", ""))
                if run_id:
                    try:
                        from .policy import policy_from_engagement
                    except ImportError:
                        from policy import policy_from_engagement  # type: ignore[no-redef]
                    policy = policy_from_engagement(self.store.get_engagement(run_id))
            result = wrapper.execute(
                list(params.get("args", [])), policy=policy, target_url=target)
            return {"tool": result.tool, "exit_code": result.exit_code,
                    "stdout": result.stdout[:4000], "stderr": result.stderr[:1000],
                    "truncated": result.truncated, "parsed": result.parsed}
        if method in ("har.import", "burp.import"):
            run_id = str(params.get("run_id", ""))
            if not run_id:
                raise ValueError(f"{method} needs run_id")
            path = str(params.get("path", ""))
            content_b64 = str(params.get("content_b64", ""))
            import base64

            if content_b64:
                try:
                    raw = base64.b64decode(content_b64, validate=True)
                except Exception:
                    raise ValueError("content_b64 is not valid base64")
                text = raw.decode("utf-8", "replace")
                filename = str(params.get("filename", "upload.har" if method == "har.import" else "burp.xml"))
            else:
                if not path:
                    raise ValueError(f"{method} needs path or content_b64")
                p = Path(path)
                if not p.is_file():
                    raise ValueError(f"file not found: {path!r}")
                text = p.read_text(encoding="utf-8", errors="replace")
                filename = p.name
            # Parse and import each exchange as evidence
            try:
                from .evidence import EvidenceVault as Vault3
                from .parsers import parse_burp_xml, parse_har
            except ImportError:
                from evidence import EvidenceVault as Vault3  # type: ignore[no-redef]
                from parsers import parse_burp_xml, parse_har  # type: ignore[no-redef]
            import json as _json

            if method == "har.import":
                try:
                    obj = _json.loads(text)
                except ValueError as exc:
                    raise ValueError(f"HAR is not JSON: {exc}") from exc
                exchanges = parse_har(obj)
            else:
                exchanges = parse_burp_xml(text)
            vault = Vault3(self.store, run_id)
            ids: list[str] = []
            for ex in exchanges:
                data = _json.dumps(ex).encode()
                meta = vault.import_bytes(data, source_type="har" if method == "har.import" else "burp",
                                          mime="application/json", description=f"imported {filename}")
                ids.append(meta["evidence_id"])
            # Link to case if requested
            case_id = params.get("case_id")
            if case_id and ids:
                try:
                    case = self.store.get_case(run_id, str(case_id))
                    refs = list(case.get("evidence_refs", []) or [])
                    for eid in ids:
                        if eid not in refs:
                            refs.append(eid)
                    self.store.update_case(run_id, str(case_id), evidence_refs=refs)
                except Exception:
                    pass
            return {"imported": len(ids), "evidence_ids": ids, "filename": filename}
        raise ValueError(f"unknown Phase 8 method {method!r}")

    @staticmethod
    def _coerce_action(raw: dict) -> Action:
        if raw.get("kind", "http.request") != "http.request":
            raise ValidationError(
                f"executor handles http.request only (got {raw.get('kind')!r})")
        method = str(raw.get("method", "GET")).upper()
        url = str(raw.get("url", ""))
        if not url:
            raise ValidationError("action needs a url")
        headers = raw.get("headers") if isinstance(raw.get("headers"), dict) else {}
        sensitive = [k for k in headers if k.lower() in (
            "authorization", "cookie", "set-cookie", "x-api-key")]
        if sensitive:
            raise ValidationError(
                f"credentialed headers must come from credential refs, not the action: {sensitive}")
        return Action(
            kind="http.request", method=method, url=url,
            redirect_chain=tuple(raw.get("redirect_chain", []) or ()),
            impact=str(raw.get("impact", "R4" if method not in ("GET", "HEAD", "OPTIONS") else "R3")),
            bytes_estimate=int(raw.get("bytes_estimate", 0) or 0),
            cost_estimate=float(raw.get("cost_estimate", 0.0) or 0.0),
            actor_ref=str(raw.get("actor_ref", "")),
            fixture_owned=bool(raw.get("fixture_owned", True)),
            approval=str(raw.get("approval", "none")),
            description=str(raw.get("description", "")),
            idempotent=bool(raw.get("idempotent", method in ("GET", "HEAD", "OPTIONS"))),
            body=str(raw.get("body", "")),
            headers=headers,
        )

    async def _execute_action(self, run_id: str, policy: Policy, action: Action,
                              params: dict) -> dict:
        try:
            from .egress import Egress, encode_result
            from .evidence import EvidenceVault as Vault
        except ImportError:
            from egress import Egress, encode_result  # type: ignore[no-redef]
            from evidence import EvidenceVault as Vault  # type: ignore[no-redef]
        budgets = self.budgets.setdefault(run_id, BudgetTracker())
        approvals = self.approvals.setdefault(run_id, ApprovalLedger())
        mint = self.mints.setdefault(run_id, CapabilityMint())
        egress = Egress(budgets=budgets, approvals=approvals, mint=mint)
        try:
            result = await egress.execute(action, policy)
        except ImportError as exc:
            raise _NoRuntime(f"live HTTP needs httpx: {exc}; run `uv sync`") from exc
        except Exception as exc:
            # Transport failures are reported, never mistaken for results.
            # Cancellation (BaseException) always propagates past this handler.
            kind = getattr(exc, "kind", None)
            module = type(exc).__module__
            if kind in ("timeout", "network", "rate_limited", "overloaded") or module.startswith(
                    ("httpx", "httpcore", "anyio")):
                return {"sent": False, "allowed": True,
                        "transport_error": f"{kind or type(exc).__name__}: {exc}",
                        "reasons": ["transport failed safely; no partial state assumed"],
                        "requests_made": len(egress.attempt_log)}
            raise
        out = encode_result(result)
        if not result.sent or not result.chain:
            self.store.audit(run_id, "tool.execute",
                             f"denied/preview: {'; '.join(result.reasons)[:200]}")
            return out
        vault = Vault(self.store, run_id)
        evidence_ids = []
        for hop in result.chain:
            meta = vault.http_capture(
                method=hop["method"], url=hop["url"],
                request_headers=hop["request_headers"],
                request_body=hop["request_body"], status=hop["status"],
                response_headers=hop["response_headers"],
                response_body=hop["response_body"],
                actor_ref=action.actor_ref)
            evidence_ids.append(meta["evidence_id"])
        case_id = params.get("case_id")
        if case_id:
            try:
                case = self.store.get_case(run_id, str(case_id))
                refs = list(case.get("evidence_refs", []) or [])
                for eid in evidence_ids:
                    if eid not in refs:
                        refs.append(eid)
                self.store.update_case(run_id, str(case_id), evidence_refs=refs)
            except ValidationError:
                pass
        out["evidence_ids"] = evidence_ids
        self.store.audit(run_id, "tool.execute",
                         f"{action.method} {action.url} -> {result.status} "
                         f"({result.requests_made} request(s), {','.join(evidence_ids)})")
        self._snapshot(run_id)
        return out

    async def _experiment_compare(self, run_id: str, baseline: dict, variant: dict,
                                  case_id: str | None) -> dict:
        try:
            from .evidence import EvidenceVault as Vault2
        except ImportError:
            from evidence import EvidenceVault as Vault2  # type: ignore[no-redef]
        differing = [k for k in ("method", "url", "actor_ref", "body", "headers")
                     if (baseline.get(k) or "") != (variant.get(k) or "")]
        if len(differing) != 1:
            raise ValidationError(
                f"experiment requires exactly one changed variable, "
                f"got {len(differing)}: {differing or ['none']}")
        eng = self.store.get_engagement(run_id)
        policy = policy_from_engagement(eng)
        changed = differing[0]
        stopping = f"one baseline + one variant request (changed: {changed})"
        runs = {}
        for name, spec in (("baseline", baseline), ("variant", variant)):
            action = self._coerce_action({**spec, "description": f"experiment {name}"})
            runs[name] = await self._execute_action(run_id, policy, action, {})
        out: dict = {"changed_variable": changed, "stopping_point": stopping,
                     "baseline": runs["baseline"], "variant": runs["variant"]}
        if runs["baseline"].get("evidence_ids") and runs["variant"].get("evidence_ids"):
            vault = Vault2(self.store, run_id)
            a = vault.read_bytes(runs["baseline"]["evidence_ids"][-1], derived_ok=True)
            b = vault.read_bytes(runs["variant"]["evidence_ids"][-1], derived_ok=True)
            out["diff"] = diff_evidence(a, b)
            if case_id:
                try:
                    case = self.store.get_case(run_id, case_id)
                    refs = list(case.get("evidence_refs", []) or [])
                    for eid in runs["baseline"]["evidence_ids"] + runs["variant"]["evidence_ids"]:
                        if eid not in refs:
                            refs.append(eid)
                    self.store.update_case(run_id, case_id, evidence_refs=refs)
                except ValidationError:
                    pass
        return out

    @staticmethod
    def _read_policy_file(path: str) -> dict:
        import tomllib

        candidate = Path(path)
        if not candidate.is_file():
            raise ValueError(f"policy file not found: {path!r}")
        text = candidate.read_text(encoding="utf-8")
        if candidate.suffix == ".json":
            doc = json.loads(text)
        elif candidate.suffix == ".toml":
            doc = tomllib.loads(text)
        else:
            raise ValueError("policy file must be .toml or .json")
        if not isinstance(doc, dict):
            raise ValueError("policy document must be an object")
        return doc.get("policy", doc) if isinstance(doc.get("policy", doc), dict) else doc

    @staticmethod
    def _rule_to_asset(rule) -> str:
        host = rule.host
        if "/" in host or host.startswith("*."):
            return host
        scheme = rule.scheme or "https"
        port = f":{rule.port}" if rule.port else ""
        path = rule.path_prefix if rule.path_prefix != "/" else ""
        return f"{scheme}://{host}{port}{path}" or host

    @staticmethod
    def _rule_to_dict(rule) -> dict:
        return {"host": rule.host, "scheme": rule.scheme, "port": rule.port,
                "path_prefix": rule.path_prefix, "allow_private": rule.allow_private}

    def _emit(self, mid, count: int, trace: str, run_id, key: str, stop: threading.Event) -> None:
        emitted = 0
        cancelled = False
        i = 0
        while i < count and not self.closing:
            if stop.is_set() or (run_id and run_id in self.cancelled_runs):
                cancelled = True
                break
            for _ in range(min(CHUNK, count - i)):
                if stop.is_set() or (run_id and run_id in self.cancelled_runs):
                    cancelled = True
                    break
                s = self.next_seq()
                self.write(event("token.delta",
                                 {"seq": s, "index": i, "total": count, "text": "tok"},
                                 trace, run_id=run_id))
                emitted += 1
                i += 1
                if run_id and run_id in self.runs:
                    with self.state_lock:
                        run = self.runs.get(run_id)
                        if run is not None:
                            run["events"] = run.get("events", 0) + 1
                            run["last_seq"] = s
                            run["updated_at"] = utcnow()
            # Periodic flush bounds kill -9 counter loss to one chunk.
            if run_id and run_id in self.runs:
                with self.state_lock:
                    self._snapshot(run_id)
        self.streams.pop(key, None)
        if run_id and run_id in self.runs:
            with self.state_lock:
                self._snapshot(run_id)
        if mid is not None:
            self.write(resp_ok(mid, {"received": emitted, "cancelled": cancelled,
                                     "last_seq": self.seq}, trace, run_id=run_id))
        log("info", "event.subscribe done", trace, emitted=emitted, cancelled=cancelled)


def serve() -> int:
    eng = Engine()
    log("info", f"engine start protocol={PROTOCOL_VERSION}")
    incoming: queue.Queue = queue.Queue(maxsize=QUEUE_BOUND)

    def pump() -> None:
        try:
            for line in sys.stdin:
                text = line.strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    eng.write(resp_err(None, ERR_PARSE, "parse error",
                                       uuid.uuid4().hex[:12]))
                    continue
                if incoming.full():
                    log("warn", "queue full; applying backpressure")
                incoming.put(msg)
        finally:
            incoming.put(None)  # EOF sentinel

    threading.Thread(target=pump, daemon=True).start()
    while True:
        msg = incoming.get()
        if msg is None:  # stdin EOF
            break
        try:
            out = eng.handle(msg)
        except Exception as exc:  # never crash the stdio loop on a bad message
            log("error", f"handler error: {exc!r}", str(msg.get("trace_id", "-")))
            mid = msg.get("id")
            out = resp_err(mid, ERR_ENGINE, "engine error",
                           str(msg.get("trace_id", "-"))) if mid is not None else None
        if isinstance(out, dict):
            eng.write(out)
        if eng.closing and not eng.streams:
            break
    for _ in range(100):  # drain active streams briefly on shutdown/EOF
        if not eng.streams:
            break
        time.sleep(0.02)
    eng.flush_all()
    log("info", "engine stop")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(prog="scopeforge-engine")
    ap.add_argument("--stdio", action="store_true", help="serve JSON-RPC over stdio (default)")
    ap.parse_args()
    raise SystemExit(serve())


if __name__ == "__main__":
    main()
