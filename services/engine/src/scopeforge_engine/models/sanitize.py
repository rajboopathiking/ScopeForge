"""Record-time sanitization (Phase 3).

Mirrors the TS display rules (packages/protocol-ts/src/redact.ts) for the
record/replay path: fixtures, logs, and diagnostics must never contain secret
shapes. Raises naming the RULE — never the value.
"""
from __future__ import annotations

import re
from typing import Any

RULES: list[tuple[str, re.Pattern[str]]] = [
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws-secret", re.compile(r"\baws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{30,}['\"]?", re.I)),
    ("github-token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai-key", re.compile(r"\bsk-(proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/=]{12,}\b", re.I)),
    ("basic-auth", re.compile(r"\bBasic\s+[A-Za-z0-9+/=]{12,}\b", re.I)),
    ("private-key", re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\s\S]*?"
        r"-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("credential-assignment", re.compile(
        r"\b(password|passwd|pwd|secret|api[_-]?key|auth[_-]?token|access[_-]?token"
        r"|session[_-]?id|cookie)\s*[:=]\s*(\"[^\"]+\"|'[^']+'|[^\s,;}]{8,})", re.I)),
    ("signed-url-sig", re.compile(r"([?&](?:signature|sig|token|key)=)[^&\s\"']+", re.I)),
]

SECRET_HEADER_NAMES = {
    "authorization", "cookie", "set-cookie", "x-api-key",
    "api-key", "access-token", "auth-token",
}

MARKER = re.compile(r"\[redacted[^\]]*\]")


def _strip_markers(text: str) -> str:
    return MARKER.sub("", text)


def find_secret_rule(text: str) -> str | None:
    """Name of the first matching rule, or None. Idempotent over markers."""
    clean = _strip_markers(text)
    for name, pattern in RULES:
        if pattern.search(clean):
            return name
    return None


def sanitize_text(text: str) -> str:
    out = text
    for _, pattern in RULES:
        out = pattern.sub("[redacted]", out)
    return out


def sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        k: ("[redacted]" if k.lower() in SECRET_HEADER_NAMES
            or k.lower().endswith(("-key", "-token", "-secret")) else v)
        for k, v in headers.items()
    }


def sanitize_json(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        return [sanitize_json(v) for v in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and (
                k.lower() in SECRET_HEADER_NAMES
                or k.lower() in ("key", "api_key", "apikey", "token", "secret", "password")
                or k.lower().endswith(("_key", "-key", "_token", "-token", "_secret", "-secret"))
            ):
                out[k] = "[redacted]"
            else:
                out[k] = sanitize_json(v)
        return out
    return value


class SecretLeak(AssertionError):
    pass


def assert_no_secrets(value: Any, where: str = "") -> None:
    """Raise SecretLeak naming the rule (never the value) on any secret shape."""
    if isinstance(value, str):
        rule = find_secret_rule(value)
        if rule:
            raise SecretLeak(f"secret shape ({rule}) in {where or 'value'}")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in SECRET_HEADER_NAMES:
                raise SecretLeak(f"secret header ({k}) in {where or 'value'}")
            assert_no_secrets(v, where or str(k))
        return
    if isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            assert_no_secrets(v, f"{where}[{i}]")
