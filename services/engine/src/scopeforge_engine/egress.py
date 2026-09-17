"""Policy-gated live HTTP executor (Phase 7). The ONLY path to target traffic.

Every request: structured Action -> policy.decide (all 12 steps, real DNS) ->
approval gate -> short-lived capability -> single send -> evidence capture ->
budget postcondition. Redirects re-decided hop-by-hop with credential
stripping. Non-idempotent methods never auto-retry. Cancellation propagates.

Needs httpx (uv env); import lazily so bare interpreters keep working.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

try:
    from .policy import (
        Action,
        ApprovalLedger,
        BudgetTracker,
        CapabilityMint,
        Policy,
        canonicalize_url,
        decide,
        strip_auth_headers,
        system_resolver,
    )
except ImportError:  # direct script execution; script dir is on sys.path
    from policy import (  # type: ignore[no-redef]
        Action,
        ApprovalLedger,
        BudgetTracker,
        CapabilityMint,
        Policy,
        canonicalize_url,
        decide,
        strip_auth_headers,
        system_resolver,
    )

MAX_REDIRECTS = 5
MAX_BODY = 2 * 1024 * 1024
REQUEST_TIMEOUT_S = 10.0
IDEMPOTENT = {"GET", "HEAD", "OPTIONS"}


@dataclass
class ExecResult:
    sent: bool
    allowed: bool = False
    reasons: list[str] = field(default_factory=list)
    approval_required: bool = False
    approval_preview: dict | None = None
    status: int = 0
    headers: dict = field(default_factory=dict)
    body_preview: str = ""
    body_truncated: bool = False
    evidence_id: str = ""
    requests_made: int = 0
    chain: list[dict] = field(default_factory=list)  # full hop record for evidence


class Egress:
    def __init__(self, *, budgets: BudgetTracker, approvals: ApprovalLedger,
                 mint: CapabilityMint, client=None) -> None:
        self.budgets = budgets
        self.approvals = approvals
        self.mint = mint
        self.client = client  # injectable httpx.AsyncClient (tests use real one)
        self.cookies: dict[str, dict[str, str]] = {}  # origin -> name -> value
        self.attempt_log: list[dict] = []  # audit: every attempted send

    async def _client_for(self):
        if self.client is not None:
            return self.client, False
        httpx = __import__("httpx")
        return httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S), True

    def _cookie_header(self, origin: str) -> str:
        jar = self.cookies.get(origin, {})
        return "; ".join(f"{k}={v}" for k, v in jar.items())

    def _store_cookies(self, origin: str, headers: Any) -> None:
        jar = self.cookies.setdefault(origin, {})
        raw = headers.get_list("set-cookie") if hasattr(headers, "get_list") else []
        for entry in raw:
            pair = entry.split(";", 1)[0]
            if "=" in pair:
                name, _, value = pair.partition("=")
                jar[name.strip()] = value.strip()

    async def execute(self, action: Action, policy: Policy) -> ExecResult:
        decision = decide(action, policy, resolver=system_resolver,
                          budgets=self.budgets, approvals=self.approvals,
                          now_s=time.time())
        if not decision.allowed:
            if decision.approval_required:
                return ExecResult(sent=False, allowed=False, reasons=decision.reasons,
                                  approval_required=True,
                                  approval_preview=decision.approval_preview)
            return ExecResult(sent=False, allowed=False, reasons=decision.reasons)
        capability = self.mint.mint(action)
        # Single verify lives inside _send_with_capability (nonces are single-use).
        result = await self._send_with_capability(action, policy, capability)
        if result.sent:
            # One-shot approvals are spent by use; sessions persist.
            self.approvals.consume(ApprovalLedger.scope_for(action))
        return result

    async def _send_with_capability(self, action: Action, policy: Policy,
                                    capability: str) -> ExecResult:
        ok, why = self.mint.verify(capability, action)
        if not ok:
            return ExecResult(sent=False, allowed=False, reasons=[f"capability refused: {why}"])
        # Re-decide at send time (budgets/approvals may have changed since preview).
        decision = decide(action, policy, resolver=system_resolver,
                          budgets=self.budgets, approvals=self.approvals,
                          now_s=time.time())
        if not decision.allowed:
            return ExecResult(sent=False, allowed=False, reasons=decision.reasons,
                              approval_required=decision.approval_required,
                              approval_preview=decision.approval_preview)
        history: list[str] = [action.url]
        headers = dict(policy.required_headers)
        headers.update(action_headers(action))
        body = action_body(action)
        method = action.method.upper()
        hops = 0
        status, resp_headers, resp_body = 0, {}, b""
        chain: list[dict] = []
        client, owned = await self._client_for()
        try:
            while True:
                target = canonicalize_url(history[-1])
                if not self.budgets.host_admits(target.host, policy):
                    return ExecResult(sent=False, allowed=False,
                                      reasons=[f"rate limit: host {target.host}"],
                                      requests_made=hops, chain=chain)
                self.budgets.inflight += 1
                try:
                    req_headers = dict(headers)
                    cookie = self._cookie_header(target.origin)
                    if cookie:
                        req_headers["Cookie"] = cookie
                    self.attempt_log.append({"method": method, "url": history[-1]})
                    resp = await self._send_once(client, method, history[-1],
                                                 req_headers, body)
                finally:
                    self.budgets.inflight -= 1
                hops += 1
                status = resp["status"]
                resp_headers = resp["headers"]
                resp_body = resp["body"]
                chain.append({"method": method, "url": history[-1], "status": status,
                              "request_headers": dict(req_headers),
                              "request_body": body,
                              "response_headers": dict(resp["headers"]),
                              "response_body": resp_body})
                self._store_cookies(target.origin, resp["raw_headers"])
                self.budgets.consume_request(target.host, policy,
                                             response_bytes=len(resp_body))
                location = resp_headers.get("location", "")
                if status in (301, 302, 303, 307, 308) and location and hops <= MAX_REDIRECTS:
                    nxt = resolve_location(history[-1], location)
                    hop_action = Action(kind=action.kind, method=method, url=nxt,
                                        redirect_chain=tuple(history),
                                        impact=action.impact, actor_ref=action.actor_ref,
                                        fixture_owned=action.fixture_owned)
                    hop = decide(hop_action, policy, resolver=system_resolver,
                                 budgets=self.budgets, approvals=self.approvals,
                                 now_s=time.time())
                    if not hop.allowed:
                        return ExecResult(sent=True, allowed=False,
                                          reasons=[f"redirect hop denied: {'; '.join(hop.reasons)}"],
                                          status=status, requests_made=hops, chain=chain)
                    prev_origin = target.origin
                    history.append(nxt)
                    new_target = canonicalize_url(nxt)
                    headers = strip_auth_headers(headers, prev_origin, new_target.origin)
                    if status == 303 or (status in (301, 302) and method == "POST"):
                        method, body = "GET", b""
                    continue
                break
        finally:
            if owned:
                await client.aclose()
        preview = resp_body[:4000].decode("utf-8", "replace")
        return ExecResult(sent=True, allowed=True, reasons=[f"completed in {hops} request(s)"],
                          status=status, headers=dict(resp_headers),
                          body_preview=preview, body_truncated=len(resp_body) > 4000,
                          requests_made=hops, chain=chain)

    async def _send_once(self, client, method: str, url: str,
                         headers: dict, body: bytes) -> dict:
        """Single send. Idempotent methods get one retry on timeout/5xx;
        non-idempotent methods NEVER auto-retry."""
        tries = 2 if method in IDEMPOTENT else 1
        last: Any = None
        for attempt in range(tries):
            try:
                resp = await client.request(method, url, headers=headers,
                                            content=body or None,
                                            follow_redirects=False)
                chunks = []
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    if sum(map(len, chunks)) > MAX_BODY:
                        break
                data = b"".join(chunks)
                await resp.aclose()
                if resp.status_code >= 500 and method in IDEMPOTENT and attempt < tries - 1:
                    last = resp.status_code
                    continue
                return {"status": resp.status_code, "headers": dict(resp.headers),
                        "raw_headers": resp.headers, "body": data}
            except Exception as exc:  # timeout/network: retry only if idempotent
                last = exc
                if method not in IDEMPOTENT:
                    raise
        if isinstance(last, Exception):
            raise last
        return {"status": last, "headers": {}, "raw_headers": {}, "body": b""}


def action_headers(action: Action) -> dict:
    headers = {}
    actor = action.actor_ref
    if actor:
        headers["X-Actor"] = actor
    return headers


def action_body(action: Action) -> bytes:
    return action.body.encode("utf-8") if action.body else b""


def resolve_location(base: str, location: str) -> str:
    if "://" in location:
        return location
    parts = urlsplit(base)
    if location.startswith("/"):
        return f"{parts.scheme}://{parts.netloc}{location}"
    base_path = parts.path.rsplit("/", 1)[0]
    return f"{parts.scheme}://{parts.netloc}{base_path}/{location}"


def encode_result(result: ExecResult) -> dict:
    return {"sent": result.sent, "allowed": result.allowed, "reasons": result.reasons,
            "approval_required": result.approval_required,
            "approval_preview": result.approval_preview, "status": result.status,
            "headers": result.headers, "body_preview": result.body_preview,
            "body_truncated": result.body_truncated, "evidence_id": result.evidence_id,
            "requests_made": result.requests_made}
