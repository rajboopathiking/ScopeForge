"""Capability-based routing with approved-boundary fallback (Phase 3, §8.3).

Rules enforced here, visibly:
- candidates must satisfy every required capability (no silent downgrade of
  strict structured output or tool calling);
- fallback never crosses into a provider the user did not approve: an explicit
  allowlist gates cross-provider moves; without one, only same-provider model
  changes are attempted;
- every decision records provider/model/adapter version/mechanism/reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .errors import CapabilityUnsupported, ProviderError
from .types import ModelCapabilities

# Routing capability names -> ModelCapabilities fields.
CAPABILITY_FIELDS = {
    "streaming": "streaming",
    "tools": "tool_calling",
    "tool_calling": "tool_calling",
    "strict_json": "strict_structured_output",
    "strict_structured_output": "strict_structured_output",
    "vision": "vision",
    "reasoning": "reasoning",
}


@dataclass(frozen=True)
class Candidate:
    provider: str
    model: str
    capabilities: ModelCapabilities


@dataclass(frozen=True)
class RouteRequest:
    required: frozenset[str] = frozenset()
    allow: tuple[str, ...] | None = None  # approved providers; None = no cross-provider fallback
    deny: tuple[str, ...] = ()
    local_only: bool = False
    allow_structured_via_tool: bool = True
    require_native_strict: bool = False
    task_class: str = "plan"


@dataclass(frozen=True)
class RoutingDecision:
    provider: str
    model: str
    adapter: str
    adapter_version: str
    structured_via: str  # "" | "native" | "forced-tool"
    fallback_from: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    reason: str = ""


def _satisfies(caps: ModelCapabilities, req: RouteRequest) -> tuple[bool, str, str]:
    """Returns (ok, structured_via, miss_reason)."""
    for name in req.required:
        if name in ("strict_json", "strict_structured_output"):
            if caps.strict_structured_output:
                continue
            if caps.structured_via_tool and req.allow_structured_via_tool and not req.require_native_strict:
                continue
            return False, "", f"{caps.provider}/{caps.model} lacks strict structured output"
        field_name = CAPABILITY_FIELDS.get(name)
        if field_name is None:
            return False, "", f"unknown capability {name!r}"
        if not getattr(caps, field_name):
            return False, "", f"{caps.provider}/{caps.model} lacks {name}"
    via = ""
    if "strict_json" in req.required or "strict_structured_output" in req.required:
        via = "native" if caps.strict_structured_output else "forced-tool"
    return True, via, ""


def _eligible(caps: ModelCapabilities, req: RouteRequest) -> str | None:
    """None if eligible, else the skip reason."""
    if caps.provider in req.deny:
        return "denylisted"
    if req.local_only and caps.transport != "local":
        return "non-local provider under local-only policy"
    return None


def route(candidates: list[Candidate], req: RouteRequest) -> RoutingDecision:
    """Single best candidate (first eligible that satisfies). No fallback."""
    misses: list[str] = []
    for cand in candidates:
        skip = _eligible(cand.capabilities, req)
        if skip:
            misses.append(f"{cand.provider}/{cand.model}: {skip}")
            continue
        ok, via, miss = _satisfies(cand.capabilities, req)
        if not ok:
            misses.append(miss)
            continue
        return RoutingDecision(
            provider=cand.provider, model=cand.model,
            adapter=cand.capabilities.adapter,
            adapter_version=cand.capabilities.adapter_version,
            structured_via=via,
            reason=f"first eligible candidate ({cand.capabilities.endpoint_class})",
        )
    raise CapabilityUnsupported(
        "no candidate satisfies " + (", ".join(sorted(req.required)) or "requirements")
        + (f"; tried: {'; '.join(misses)}" if misses else ""))


def route_with_fallback(
    ordered: list[Candidate], req: RouteRequest, failed: list[str],
) -> RoutingDecision:
    """Next candidate after failures. Cross-provider moves need allowlist approval."""
    skipped: list[str] = []
    failed_providers = {f.split("/")[0] for f in failed}
    failed_ids = set(failed)
    for cand in ordered:
        if f"{cand.provider}/{cand.model}" in failed_ids:
            skipped.append(f"{cand.provider}/{cand.model}: already failed")
            continue
        skip = _eligible(cand.capabilities, req)
        if skip:
            skipped.append(f"{cand.provider}/{cand.model}: {skip}")
            continue
        if failed and cand.provider not in failed_providers:
            if req.allow is None or cand.provider not in req.allow:
                skipped.append(f"{cand.provider}/{cand.model}: cross-provider fallback not approved")
                continue
        ok, via, miss = _satisfies(cand.capabilities, req)
        if not ok:
            skipped.append(miss)
            continue
        return RoutingDecision(
            provider=cand.provider, model=cand.model,
            adapter=cand.capabilities.adapter,
            adapter_version=cand.capabilities.adapter_version,
            structured_via=via, fallback_from=tuple(failed), skipped=tuple(skipped),
            reason="fallback within approved boundary",
        )
    raise ProviderError(kind="unknown", message="fallback exhausted: " + "; ".join(skipped))
