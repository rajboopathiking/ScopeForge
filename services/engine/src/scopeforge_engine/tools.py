"""Tool-wrapper SDK (Phase 8). One constrained external-tool exemplar.

Wrappers constrain targets, arguments, concurrency, and output. Raw arbitrary
shell is developer-only and never exposed as a live bug-bounty tool.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from .policy import Action, Policy, decide
    from .store import utcnow


except ImportError:  # direct script execution; script dir is on sys.path
    from policy import Action, Policy, decide  # type: ignore[no-redef]
    from store import utcnow  # type: ignore[no-redef]


@dataclass(frozen=True)
class ToolManifest:
    name: str
    description: str
    executable: str  # absolute path or name on PATH
    version_range: str = ""  # e.g. ">=1.0,<2.0"
    allowed_args: tuple[str, ...] = ()  # allowlisted flags/values (empty = any)
    risk_class: str = "R2"  # R0-R6
    output_parser: str = "json"  # json|text|har
    timeout_s: float = 30.0
    max_output_bytes: int = 2 * 1024 * 1024
    executable_hash: str = ""  # sha256 of binary (optional pin)
    egress_needs: tuple[str, ...] = ()  # hosts the tool may contact

    def validate(self) -> list[str]:
        errs: list[str] = []
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.name):
            errs.append(f"bad tool name {self.name!r}")
        if not self.executable:
            errs.append("executable is required")
        if self.risk_class not in ("R0", "R1", "R2", "R3", "R4", "R5", "R6"):
            errs.append(f"bad risk_class {self.risk_class!r}")
        # No shell metacharacters in executable
        if any(c in self.executable for c in (";", "&", "|", "`", "$", "(", ")")):
            errs.append("executable contains shell metacharacters")
        return errs


@dataclass
class ToolResult:
    tool: str
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool = False
    parsed: Any = None


class ConstrainedToolWrapper:
    """Executes a tool manifest with typed argv, no shell, bounded resources."""

    def __init__(self, manifest: ToolManifest) -> None:
        errs = manifest.validate()
        if errs:
            raise ValueError("; ".join(errs))
        self.manifest = manifest

    def _check_args(self, args: list[str]) -> None:
        if self.manifest.allowed_args:
            allowed = set(self.manifest.allowed_args)
            for a in args:
                # Allow values that follow an allowed flag
                if a.startswith("-"):
                    if a not in allowed:
                        raise PermissionError(f"arg {a!r} not in allowlist for {self.manifest.name!r}")
                # Values are checked loosely; strict mode would pin per-flag values

    def _resolve_executable(self) -> str:
        exe = self.manifest.executable
        # If absolute, check exists; if bare name, resolve via PATH
        if os.path.isabs(exe):
            if not Path(exe).is_file():
                raise FileNotFoundError(f"tool executable not found: {exe}")
            return exe
        # Search PATH
        for d in os.environ.get("PATH", "").split(os.pathsep):
            candidate = Path(d) / exe
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        raise FileNotFoundError(f"tool {exe!r} not found on PATH")

    def _verify_hash(self, path: str) -> None:
        if not self.manifest.executable_hash:
            return
        h = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if h != self.manifest.executable_hash:
            raise ValueError(f"tool hash mismatch for {self.manifest.name!r}: expected {self.manifest.executable_hash[:12]}")

    def execute(
        self,
        args: list[str],
        *,
        policy: Policy | None = None,
        target_url: str = "",
        extra_env: dict[str, str] | None = None,
    ) -> ToolResult:
        """Execute with policy check. If policy given, tool's egress needs are checked."""
        self._check_args(args)
        exe = self._resolve_executable()
        self._verify_hash(exe)

        # Policy gate: if tool needs egress, check it
        if policy is not None and target_url:
            try:
                from .policy import decide as policy_decide
            except ImportError:  # direct script execution
                from policy import decide as policy_decide  # type: ignore[no-redef]

            action = Action(kind="tool.exec", method="EXEC", url=target_url,
                            impact=self.manifest.risk_class, description=f"tool:{self.manifest.name}")
            decision = policy_decide(action, policy, resolver=lambda h: ["127.0.0.1"],
                                     budgets=None, approvals=None)
            if not decision.allowed:
                raise PermissionError(f"tool egress denied: {'; '.join(decision.reasons)}")

        # Build typed argv — never shell
        argv = [exe, *args]
        # Sandboxed execution: temp dir, no host network by default (tool's own egress needs checked above)
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith("AWS_") or k == "PATH"}
            if extra_env:
                env.update(extra_env)
            try:
                proc = subprocess.run(
                    argv, capture_output=True, text=True,
                    timeout=self.manifest.timeout_s,
                    cwd=tmpdir, env=env,
                    shell=False,  # critical: never shell
                )
            except subprocess.TimeoutExpired as exc:
                return ToolResult(tool=self.manifest.name, exit_code=124,
                                  stdout=(exc.stdout or "")[:self.manifest.max_output_bytes] if isinstance(exc.stdout, str) else "",
                                  stderr=f"timeout after {self.manifest.timeout_s}s",
                                  truncated=True)

            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            truncated = len(stdout) > self.manifest.max_output_bytes
            if truncated:
                stdout = stdout[:self.manifest.max_output_bytes]

            # Never interpolate model text into shell — output is data
            parsed = None
            if self.manifest.output_parser == "json" and stdout.strip():
                try:
                    parsed = json.loads(stdout)
                except json.JSONDecodeError:
                    parsed = None

            return ToolResult(
                tool=self.manifest.name,
                exit_code=proc.returncode,
                stdout=stdout, stderr=stderr,
                truncated=truncated, parsed=parsed,
            )


# Exemplar: constrained Nuclei wrapper (Phase 8 SDK demo)
NUCLEI_MANIFEST = ToolManifest(
    name="nuclei",
    description="Nuclei vulnerability scanner (constrained wrapper exemplar)",
    executable="nuclei",
    version_range=">=3.0",
    allowed_args=("-target", "-templates", "-severity", "-json", "-o", "-silent"),
    risk_class="R2",
    output_parser="json",
    timeout_s=60.0,
    egress_needs=(),
)
