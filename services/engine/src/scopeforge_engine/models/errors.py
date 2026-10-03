"""Provider error taxonomy (Phase 3). Retryability is explicit, never guessed."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderError(Exception):
    kind: str  # auth|rate_limited|overloaded|timeout|cancelled|bad_request|
    # server|network|capability_unsupported|credential_missing|unknown
    message: str
    provider: str = ""
    status: int | None = None
    retryable: bool = False
    code: str = ""

    def __str__(self) -> str:  # never include secrets; messages are built sanitized
        where = f" [{self.provider}]" if self.provider else ""
        return f"{self.kind}{where}: {self.message}"


class CapabilityUnsupported(ProviderError):
    def __init__(self, message: str, provider: str = "") -> None:
        super().__init__(kind="capability_unsupported", message=message,
                         provider=provider, retryable=False)


class CredentialMissing(ProviderError):
    def __init__(self, message: str, provider: str = "") -> None:
        super().__init__(kind="credential_missing", message=message,
                         provider=provider, retryable=False)
