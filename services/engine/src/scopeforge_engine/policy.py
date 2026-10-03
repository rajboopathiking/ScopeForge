"""Authorization, scope, and safety kernel (Phase 5, plan.md §7).

PURE functions + small stateful trackers. Prompts and model text NEVER enter
these decisions: `decide()` takes a structured Action only. Unknown means
denied — never unrestricted, never unlimited. Stdlib only.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import posixpath
import re
import socket
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any
# Risk classes (plan §7.5). R6 has no execution path, anywhere.
IMPACT_DEFAULTS = {
    "R0": "allow",   # local read inside run directory
    "R1": "audit",   # local write (audit event)
    "R2": "confirm",  # bounded passive network (exact policy/doc URL)
    "R3": "gate",    # live target read (policy gate + preview)
    "R4": "approve",  # live state change (one-shot approval + fixture proof)
    "R5": "disabled",  # shared/high-impact (explicit isolation only)
    "R6": "prohibited",  # no built-in execution path
}

POLICY_TTL_S = 30 * 24 * 3600  # reconfirm material policy older than 30 days


# -- canonicalization --

@dataclass(frozen=True)
class Target:
    scheme: str
    host: str
    port: int
    path: str

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"


def canonicalize_url(raw: str) -> Target:
    """Parse + normalize. Raises ValueError on anything ambiguous or unsafe."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty target")
    if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", text):
        raise ValueError("target needs an explicit scheme (no guessing)")
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError as exc:
        raise ValueError(f"unparseable target: {exc}") from exc
    if parts.username or parts.password:
        raise ValueError("userinfo in target is forbidden")
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https", "ws", "wss"):
        raise ValueError(f"unsupported scheme {scheme!r}")
    host = parts.hostname or ""
    if not host:
        raise ValueError("missing host")
    try:
        host = host.encode("idna").decode("ascii").lower().rstrip(".")
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"bad hostname {host!r}: {exc}") from exc
    default_port = {"http": 80, "https": 443, "ws": 80, "wss": 443}[scheme]
    try:
        port = parts.port or default_port
    except ValueError as exc:
        raise ValueError(f"bad port: {exc}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"port out of range: {port}")
    path = "/" + posixpath.normpath("/" + (parts.path or "")).lstrip("/")
    if ".." in path.split("/"):
        raise ValueError("path escapes root")
    return Target(scheme=scheme, host=host, port=port, path=path)


# -- scope rules --

@dataclass(frozen=True)
class AssetRule:
    """Structured rule (never bare regex). host: exact, '*.suffix', or CIDR."""
    host: str
    scheme: str = ""
    port: int = 0
    path_prefix: str = "/"
    allow_private: bool = False

    @staticmethod
    def parse(raw: dict) -> AssetRule:
        host = str(raw.get("host", "")).lower().rstrip(".")
        if not host:
            raise ValueError("scope rule needs a host")
        if host.startswith("*") and not host.startswith("*."):
            raise ValueError(f"ambiguous wildcard rejected: {host!r} (use '*.suffix')")
        if host.startswith("*.") and len(host) < 4:
            raise ValueError(f"ambiguous wildcard rejected: {host!r}")
        try:
            port = int(raw.get("port", 0) or 0)
        except (ValueError, TypeError):
            raise ValueError(f"bad port in rule {raw!r}")
        prefix = str(raw.get("path_prefix", "/") or "/")
        if not prefix.startswith("/"):
            raise ValueError(f"path_prefix must be absolute: {prefix!r}")
        return AssetRule(host=host, scheme=str(raw.get("scheme", "")).lower(),
                         port=port, path_prefix=prefix,
                         allow_private=bool(raw.get("allow_private", False)))

    def matches(self, target: Target) -> bool:
        if self.scheme and self.scheme != target.scheme:
            return False
        if self.port and self.port != target.port:
            return False
        if not (target.path == self.path_prefix
                or target.path.startswith(self.path_prefix.rstrip("/") + "/")):
            return False
        host = self.host
        if "/" in host:  # CIDR
            try:
                return ipaddress.ip_address(target.host) in ipaddress.ip_network(host, strict=False)
            except ValueError:
                return False
        if host.startswith("*."):
            suffix = host[2:]
            return target.host != suffix and target.host.endswith("." + suffix)
        return target.host == host


def decide_asset(target: Target, include: list[AssetRule], exclude: list[AssetRule]) -> tuple[bool, str]:
    if any(r.matches(target) for r in exclude):
        return False, f"excluded by scope rule ({target.origin}{target.path})"
    if any(r.matches(target) for r in include):
        return True, "matched included asset"
    return False, "not in included scope (unknown is never unrestricted)"


# -- DNS / IP --

BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"), ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"), ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"), ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fe80::/10"), ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("ff00::/8"),
]
METADATA_IPS = {"169.254.169.254", "100.100.100.200", "fd00:ec2::254"}


def check_addresses(ips: list[str], rule_allows_private: bool) -> tuple[bool, str]:
    for raw in ips:
        if raw in METADATA_IPS:
            return False, f"cloud metadata address blocked: {raw}"
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            return False, f"unresolvable address form: {raw}"
        if any(ip in net for net in BLOCKED_NETWORKS) and not rule_allows_private:
            return False, f"private/link-local address blocked: {raw} (no explicit lab rule)"
    return True, f"{len(ips)} resolved address(es) allowed"


def system_resolver(host: str) -> list[str]:
    """Default resolver. Tests inject fakes; plan/artifact modes never call this."""
    out = set()
    for family, _, _, _, sockaddr in socket.getaddrinfo(host, None):
        out.add(sockaddr[0])
    return sorted(out)


# -- redirects / headers --

SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "proxy-authorization",
                     "x-api-key", "api-key"}


def strip_auth_headers(headers: dict, from_origin: str, to_origin: str) -> dict:
    """Drop credentialed headers on origin change. Never forward across origins."""
    if from_origin == to_origin:
        return dict(headers)
    return {k: v for k, v in headers.items()
            if k.lower() not in SENSITIVE_HEADERS
            and not k.lower().endswith(("-key", "-token", "-secret", "-cookie"))}


# -- policy document --

@dataclass
class Policy:
    program_name: str = "Untitled program"
    policy_source: str = ""
    retrieved_at: str = ""
    mode: str = "plan"
    include: list[AssetRule] = field(default_factory=list)
    exclude: list[AssetRule] = field(default_factory=list)
    methods_allow: list[str] = field(default_factory=lambda: ["GET", "HEAD"])
    methods_deny: list[str] = field(default_factory=list)
    budgets: dict[str, Any] = field(default_factory=dict)
    impact: dict[str, str] = field(default_factory=dict)  # overrides per class
    required_headers: dict[str, str] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)

    @staticmethod
    def load(doc: dict) -> Policy:
        if not isinstance(doc, dict):
            raise ValueError("policy must be an object")
        include, exclude = [], []
        for raw in doc.get("include", []) or []:
            include.append(AssetRule.parse(raw if isinstance(raw, dict) else {"host": str(raw)}))
        for raw in doc.get("exclude", []) or []:
            exclude.append(AssetRule.parse(raw if isinstance(raw, dict) else {"host": str(raw)}))
        unresolved = list(doc.get("unresolved", []) or [])
        if not include:
            unresolved.append("no included assets: scope is UNKNOWN (never unrestricted)")
        budgets = dict(doc.get("budgets", {}) or {})
        for key in ("requests_total", "max_concurrency", "wall_clock_s", "max_bytes",
                    "cost_usd", "per_host_rps", "per_host_burst"):
            if key in budgets and budgets[key] is not None and budgets[key] < 0:
                raise ValueError(f"budget {key} must be >= 0")
        return Policy(
            program_name=str(doc.get("program_name", "Untitled program")),
            policy_source=str(doc.get("policy_source", "")),
            retrieved_at=str(doc.get("retrieved_at", "")),
            mode=str(doc.get("mode", "plan")),
            include=include, exclude=exclude,
            methods_allow=[m.upper() for m in (doc.get("methods_allow") or ["GET", "HEAD"])],
            methods_deny=[m.upper() for m in (doc.get("methods_deny") or [])],
            budgets=budgets,
            impact=dict(doc.get("impact", {}) or {}),
            required_headers=dict(doc.get("required_headers", {}) or {}),
            unresolved=unresolved,
        )

    def impact_decision(self, cls: str) -> str:
        return self.impact.get(cls, IMPACT_DEFAULTS.get(cls, "disabled"))


# -- actions & decisions (the 12-step pipeline) --

@dataclass(frozen=True)
class Action:
    """Structured side-effect proposal. `description` is model text: it NEVER
    influences the decision (prompt-injection boundary)."""
    kind: str  # http.request | browser.navigate | tool.exec | artifact.read | artifact.write
    method: str = "GET"
    url: str = ""
    redirect_chain: tuple[str, ...] = ()
    impact: str = "R3"
    bytes_estimate: int = 0
    cost_estimate: float = 0.0
    actor_ref: str = ""
    fixture_owned: bool = True
    approval: str = "none"  # none | one-shot | session
    description: str = ""
    idempotent: bool = True
    body: str = ""  # request body (exact bytes fingerprinted into capabilities)
    headers: dict = field(default_factory=dict)  # extra headers (credentialed rejected)


@dataclass
class Decision:
    allowed: bool
    reasons: list[str]
    approval_required: bool = False
    approval_preview: dict[str, Any] | None = None
    capability: str | None = None

    def explain(self) -> str:
        verdict = "ALLOW" if self.allowed else "DENY"
        return f"{verdict}: " + "; ".join(self.reasons)


def decide(
    action: Action,
    policy: Policy,
    *,
    resolver=system_resolver,
    budgets: BudgetTracker | None = None,
    approvals: ApprovalLedger | None = None,
    now_s: float | None = None,
) -> Decision:
    reasons: list[str] = []
    deny = lambda why: Decision(allowed=False, reasons=reasons + [why])  # noqa: E731

    # 1. Run mode: plan/artifacts have zero target egress, by construction.
    if action.kind in ("http.request", "browser.navigate", "tool.exec"):
        if policy.mode != "live":
            return deny(f"mode={policy.mode}: target contact disabled (zero egress)")
    else:
        if action.impact in ("R3", "R4", "R5", "R6"):
            return deny(f"local action cannot carry impact {action.impact}")

    # 2. Policy freshness.
    if policy.mode == "live" and action.kind in ("http.request", "browser.navigate", "tool.exec"):
        if not policy.policy_source:
            return deny("no operative policy source recorded")
        if policy.retrieved_at and now_s is not None:
            try:
                import datetime
                ts = datetime.datetime.fromisoformat(
                    policy.retrieved_at.replace("Z", "+00:00")).timestamp()
                if now_s - ts > POLICY_TTL_S:
                    return deny("policy older than 30 days: reconfirm before live use")
            except ValueError:
                return deny("policy retrieved_at is unparseable: reconfirm")

    # 3–5. Asset match, DNS, redirects — every hop independently.
    # Tool wrappers also carry a target_url; same scope matcher applies.
    chain = (action.url, *action.redirect_chain) if action.url else action.redirect_chain
    if action.kind in ("http.request", "browser.navigate", "tool.exec") and chain:
        private_allowed = False
        for hop in chain:
            try:
                target = canonicalize_url(hop)
            except ValueError as exc:
                return deny(f"hop rejected ({hop}): {exc}")
            ok, why = decide_asset(target, policy.include, policy.exclude)
            if not ok:
                return deny(f"hop {target.origin}{target.path}: {why}")
            reasons.append(f"hop {target.host}: {why}")
            matched = [r for r in policy.include if r.matches(target)]
            private_allowed = private_allowed or any(r.allow_private for r in matched)
            try:
                ips = resolver(target.host)
            except OSError as exc:
                return deny(f"DNS failure for {target.host}: {exc}")
            ok_ip, why_ip = check_addresses(ips, private_allowed)
            if not ok_ip:
                return deny(f"hop {target.host}: {why_ip}")
            reasons.append(f"DNS {target.host}: {why_ip}")

    # 6. Method and tool class.
    if action.kind == "http.request" and action.url:
        method = action.method.upper()
        if method in policy.methods_deny:
            return deny(f"method {method} is denied by policy")
        if method not in policy.methods_allow:
            return deny(f"method {method} is not in methods_allow {policy.methods_allow}")
        reasons.append(f"method {method} permitted")

    # 7. Identity and fixture ownership.
    if action.impact in ("R4", "R5") and not action.fixture_owned:
        return deny(f"impact {action.impact} requires researcher-controlled fixtures")
    if action.impact in ("R4", "R5") and not action.actor_ref:
        return deny(f"impact {action.impact} requires a controlled actor identity")

    # 8. Impact class.
    gate = policy.impact_decision(action.impact)
    if gate == "prohibited":
        return deny(f"impact {action.impact} is prohibited: no execution path")
    if gate == "disabled":
        return deny(f"impact {action.impact} is disabled without explicit isolation")

    # 9. Budgets (enforced below the agent, at this layer).
    if budgets is not None:
        ok_b, why_b = budgets.check(action, policy)
        if not ok_b:
            return deny(why_b)
        reasons.append(why_b)

    # 10. Approval.
    approval_required = gate in ("approve",) or action.approval in ("one-shot", "session")
    if action.impact == "R4":
        approval_required = True
    if approval_required:
        scope = ApprovalLedger.scope_for(action)
        if approvals is not None and approvals.granted(scope, action.approval):
            reasons.append(f"approval present ({action.approval})")
        else:
            return Decision(
                allowed=False, approval_required=True,
                reasons=reasons + [f"one-shot approval required for {scope}"],
                approval_preview={
                    "kind": action.kind, "method": action.method,
                    "destination": action.url or "(local)", "impact": action.impact,
                    "bytes_estimate": action.bytes_estimate,
                    "scope": scope,
                    "permission": "policy snapshot + asset/method decision above",
                })

    # 11–12. Capability mint + postcondition are the executor's job (Phase 7);
    # the kernel returns the exact permitted action fingerprint for minting.
    reasons.append(f"impact {action.impact} ({gate})")
    return Decision(allowed=True, reasons=reasons)


# -- engagement bridge --

def asset_entry_to_rule(entry: str) -> dict:
    """One included/excluded asset string -> structured rule dict. URLs become
    exact rules (scheme/port/path preserved); bare strings become host rules."""
    text = str(entry).strip()
    if "://" in text:
        try:
            t = canonicalize_url(text)
            rule: dict[str, Any] = {"host": t.host}
            if (t.scheme == "http" and t.port != 80) or (t.scheme == "https" and t.port != 443):
                rule["port"] = t.port
            rule["scheme"] = t.scheme
            if t.path != "/":
                rule["path_prefix"] = t.path
            return rule
        except ValueError:
            pass
    return {"host": text}


def policy_from_engagement(eng: dict) -> Policy:
    """Build a Policy from a run's engagement.json.

    Structured rules under engagement["policy"] round-trip exactly (scheme,
    ports, prefixes, allow_private). Legacy display strings in
    included_assets/exclusions are parsed the same way as a fallback.
    """
    stored = eng.get("policy", {}) if isinstance(eng.get("policy"), dict) else {}
    if isinstance(stored.get("include"), list) and (
            stored.get("include") or isinstance(stored.get("exclude"), list)):
        include = stored["include"]
        exclude = stored.get("exclude", []) or []
    else:
        include = [asset_entry_to_rule(e) for e in eng.get("included_assets", []) or []
                   if str(e).strip()]
        exclude = [asset_entry_to_rule(e) for e in eng.get("exclusions", []) or []]
    budgets = dict(stored.get("budgets", {}) or {})
    if not budgets:
        budgets = dict(eng.get("budgets", {}) or {})
        if eng.get("request_budget") is not None and "requests_total" not in budgets:
            budgets["requests_total"] = eng["request_budget"]
    doc = {
        "program_name": eng.get("program_name", "Untitled program"),
        "policy_source": eng.get("policy_source", ""),
        "retrieved_at": eng.get("policy_retrieved_at", ""),
        "mode": eng.get("mode", "plan"),
        "include": include, "exclude": exclude,
        "methods_allow": eng.get("permitted_methods", ["GET", "HEAD"]),
        "methods_deny": eng.get("prohibited_methods", []),
        "budgets": budgets,
        "impact": stored.get("impact", {}),
        "required_headers": eng.get("required_headers", {}),
        "unresolved": list(eng.get("unresolved_questions", []) or []),
    }
    return Policy.load(doc)


def static_resolver(mapping: dict[str, list[str]]):
    """Dry-run resolver: answers only from the supplied table (no egress)."""
    def resolve(host: str) -> list[str]:
        if host in mapping and isinstance(mapping[host], list):
            return mapping[host]
        raise OSError(f"no static DNS entry for {host} (supply dns mapping)")
    return resolve


# -- budgets --

class BudgetTracker:
    """Counters enforced at the executor layer. Missing limit = unknown = deny
    live traffic that would need it (never unlimited)."""

    def __init__(self) -> None:
        self.requests_used = 0
        self.bytes_used = 0
        self.cost_used = 0.0
        self.inflight = 0
        self.started_at = time.monotonic()
        self.host_buckets: dict[str, list[float]] = {}
        self.stop_signals: list[str] = []

    def check(self, action: Action, policy: Policy) -> tuple[bool, str]:
        budgets = policy.budgets
        if action.kind in ("http.request", "browser.navigate"):
            limit = budgets.get("requests_total")
            if limit is None:
                return False, "no request budget set (unknown is never unlimited)"
            if self.requests_used >= limit:
                return False, f"request budget exhausted ({self.requests_used}/{limit})"
            max_inflight = budgets.get("max_concurrency", 1)
            if self.inflight >= max_inflight:
                return False, f"concurrency limit reached ({max_inflight})"
            wall = budgets.get("wall_clock_s")
            if wall is not None and time.monotonic() - self.started_at > wall:
                return False, "wall-clock deadline exceeded"
            max_bytes = budgets.get("max_bytes")
            if max_bytes is not None and self.bytes_used + action.bytes_estimate > max_bytes:
                return False, "response byte ceiling would be exceeded"
        cost_limit = budgets.get("cost_usd")
        if cost_limit is not None and self.cost_used + action.cost_estimate > cost_limit:
            return False, "cost ceiling would be exceeded"
        if self.stop_signals:
            return False, f"stop signal active: {self.stop_signals[-1]}"
        return True, "budgets admit this action"

    def consume_request(self, host: str, policy: Policy, *, response_bytes: int = 0) -> None:
        self.requests_used += 1
        self.bytes_used += response_bytes
        now = time.monotonic()
        bucket = self.host_buckets.setdefault(host, [])
        bucket.append(now)
        window = 1.0
        rps = policy.budgets.get("per_host_rps")
        burst = policy.budgets.get("per_host_burst", 5)
        if rps:
            bucket[:] = [t for t in bucket if now - t < max(window, burst / rps)]

    def host_admits(self, host: str, policy: Policy) -> bool:
        rps = policy.budgets.get("per_host_rps")
        if not rps:
            return True
        burst = policy.budgets.get("per_host_burst", 5)
        now = time.monotonic()
        bucket = [t for t in self.host_buckets.get(host, []) if now - t < max(1.0, burst / rps)]
        return len(bucket) < burst

    def stop(self, reason: str) -> None:
        self.stop_signals.append(reason)


class ApprovalLedger:
    """One-shot and session approvals. Session scope is the run; one-shot is
    consumed by a single capability mint (Phase 7 enforces consumption)."""

    def __init__(self) -> None:
        self.session: set[str] = set()
        self.oneshot: set[str] = set()

    @staticmethod
    def scope_for(action: Action) -> str:
        return f"{action.kind}:{action.method.upper()}:{action.url or action.impact}"

    def grant(self, scope: str, kind: str) -> None:
        (self.session if kind == "session" else self.oneshot).add(scope)

    def granted(self, scope: str, kind: str) -> bool:
        if scope in self.session:
            return True
        return kind != "session" and scope in self.oneshot

    def consume(self, scope: str) -> bool:
        """Consume a one-shot grant after use. Returns True if one was spent."""
        if scope in self.oneshot:
            self.oneshot.discard(scope)
            return True
        return False


# -- execution capabilities (short-lived, single-purpose, exact-match) --

class CapabilityMint:
    """HMAC-signed capability tokens. Invalid after restart (fail closed):
    the key lives in memory only. Verified fields: exact action fingerprint,
    expiry (60s), single-use nonce."""

    def __init__(self, key: bytes | None = None) -> None:
        self.key = key or os.urandom(32)
        self.used: set[str] = set()

    @staticmethod
    def fingerprint(action: Action) -> str:
        canonical = json.dumps({
            "kind": action.kind, "method": action.method.upper(), "url": action.url,
            "redirects": list(action.redirect_chain), "impact": action.impact,
            "body_sha256": hashlib.sha256(action.body.encode()).hexdigest(),
            "headers": sorted((k.lower(), v) for k, v in action.headers.items()),
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def mint(self, action: Action, ttl_s: float = 60.0) -> str:
        nonce = os.urandom(12).hex()
        body = json.dumps({"fp": self.fingerprint(action), "exp": time.time() + ttl_s,
                           "nonce": nonce}, separators=(",", ":"))
        sig = hmac.new(self.key, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{sig}"

    def verify(self, token: str, action: Action) -> tuple[bool, str]:
        try:
            body, sig = token.rsplit(".", 1)
            payload = json.loads(body)
        except ValueError:
            return False, "malformed capability"
        if not hmac.compare_digest(hmac.new(self.key, body.encode(), hashlib.sha256).hexdigest(), sig):
            return False, "bad capability signature"
        if payload.get("nonce") in self.used:
            return False, "capability already consumed (no replay)"
        if time.time() > float(payload.get("exp", 0)):
            return False, "capability expired"
        if payload.get("fp") != self.fingerprint(action):
            return False, "capability does not match this exact action (no broadening)"
        self.used.add(payload["nonce"])
        return True, "capability valid"
