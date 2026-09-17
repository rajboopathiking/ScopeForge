"""Credential references (Phase 3, plan.md §8.4).

Secrets are referenced, never stored: `env:VAR`, `keychain:service/account`,
`cmd:program args...`. Values never reach logs, transcripts, or run records —
resolution sites must use `describe()` for anything user-visible.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass

from .errors import CredentialMissing

KINDS = ("env", "keychain", "cmd")


@dataclass(frozen=True)
class CredentialRef:
    kind: str
    target: str

    def describe(self) -> str:
        """Safe public description: kind + target name, never the value."""
        if self.kind == "env":
            return f"env:{self.target} ({'set' if self.target in os.environ else 'missing'})"
        return f"{self.kind}:{self.target}"


def parse_credential_reference(raw: str) -> CredentialRef:
    if not raw or ":" not in raw:
        raise CredentialMissing(f"malformed credential reference (want kind:target, got {raw!r})")
    kind, _, target = raw.partition(":")
    if kind not in KINDS or not target:
        raise CredentialMissing(f"unsupported credential reference {raw!r}; want one of {KINDS}")
    return CredentialRef(kind=kind, target=target)


def resolve(ref: CredentialRef, provider: str = "") -> str:
    """Resolve to the secret value. Raises CredentialMissing without leaking values."""
    if ref.kind == "env":
        try:
            return os.environ[ref.target]
        except KeyError:
            raise CredentialMissing(f"environment variable {ref.target} is not set", provider) from None
    if ref.kind == "keychain":
        try:
            import keyring  # type: ignore[import-not-found]
        except ImportError:
            raise CredentialMissing(
                "keyring backend not installed; `pip install keyring` or use env:/cmd: refs", provider
            ) from None
        service, _, account = ref.target.partition("/")
        if not account:
            raise CredentialMissing(f"malformed keychain ref {ref.target!r}; want service/account", provider)
        value = keyring.get_password(service, account)
        if value is None:
            raise CredentialMissing(f"no keychain entry for {service}/{account}", provider)
        return value
    if ref.kind == "cmd":
        argv = shlex.split(ref.target)
        if not argv:
            raise CredentialMissing("empty cmd: credential reference", provider)
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=10, shell=False, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise CredentialMissing(f"secret command failed to start ({type(exc).__name__})", provider) from None
        if out.returncode != 0:
            raise CredentialMissing("secret command exited non-zero (stderr withheld)", provider)
        value = out.stdout.strip().splitlines()
        if not value or not value[0]:
            raise CredentialMissing("secret command produced no output", provider)
        return value[0]
    raise CredentialMissing(f"unsupported credential kind {ref.kind!r}", provider)
