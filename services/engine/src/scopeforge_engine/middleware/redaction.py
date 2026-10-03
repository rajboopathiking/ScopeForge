"""Redaction Middleware: Scans inputs, outputs, and tool results for credentials and leaks."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from .base import BaseMiddleware

REDACTION_PATTERNS = [
    (re.compile(r"sk-[a-zA-Z0-9]{20,60}"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"sk-ant-[a-zA-Z0-9\-_]{20,80}"), "[REDACTED_ANTHROPIC_KEY]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"ghp_[a-zA-Z0-9]{36}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"eyJ[a-zA-Z0-9_\-]{10,}\.eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]+"), "[REDACTED_JWT_TOKEN]"),
    (re.compile(r"-----BEGIN (RSA|EC|OPENSSH|DSA|PGP)? PRIVATE KEY-----[\s\S]+?-----END \1 PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"(password|passwd|secret|api_key|token)\s*[:=]\s*['\"]([^'\"]{4,})['\"]", re.IGNORECASE), r"\1='[REDACTED_SECRET]'"),
]


class RedactionMiddleware(BaseMiddleware):
    """Scans and masks credentials, API keys, and sensitive tokens."""

    def __init__(self):
        super().__init__(name="Redaction", priority=5)

    def _redact_text(self, text: str) -> str:
        if not isinstance(text, str):
            return text
        redacted = text
        for pattern, replacement in REDACTION_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        return redacted

    def _redact_dict(self, d: Dict[str, Any]) -> Dict[str, Any]:
        result = {}
        for k, v in d.items():
            if isinstance(v, str):
                result[k] = self._redact_text(v)
            elif isinstance(v, dict):
                result[k] = self._redact_dict(v)
            elif isinstance(v, list):
                result[k] = [self._redact_text(x) if isinstance(x, str) else x for x in v]
            else:
                result[k] = v
        return result

    def after_llm(self, response: Any, metadata: Dict[str, Any]) -> Any:
        if hasattr(response, "content") and isinstance(response.content, str):
            response.content = self._redact_text(response.content)
        return response

    def after_tool(self, tool_name: str, result: Any, metadata: Dict[str, Any]) -> Any:
        if isinstance(result, str):
            return self._redact_text(result)
        elif isinstance(result, dict):
            return self._redact_dict(result)
        return result
