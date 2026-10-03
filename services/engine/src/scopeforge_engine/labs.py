"""Local lab lifecycle (Phase 7). Lab servers are engine-local tooling
(subprocesses bound to 127.0.0.1), audit-logged, never target contact.
Stdlib only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any


class LabError(Exception):
    pass


def find_labs_dir(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("SCOPEFORGE_LABS")
    if env:
        return Path(env)
    here = Path.cwd()
    for parent in (here, *here.parents):
        candidate = parent / "labs"
        if (candidate / "tenancy-demo" / "manifest.json").is_file():
            return candidate
    raise LabError("labs directory not found (set SCOPEFORGE_LABS)")


def list_labs(labs_dir: str | Path | None = None) -> list[dict]:
    root = find_labs_dir(labs_dir)
    out = []
    for child in sorted(root.iterdir()):
        manifest = child / "manifest.json"
        if manifest.is_file():
            try:
                doc = json.loads(manifest.read_text())
                out.append({"name": child.name, "objective": doc.get("objective", ""),
                            "budgets": doc.get("budgets", {})})
            except (OSError, ValueError):
                continue
    return out


class LabManager:
    """Launch/stop/reset lab subprocesses. One lab per run in Phase 7."""

    def __init__(self) -> None:
        self.labs: dict[str, subprocess.Popen] = {}

    def launch(self, name: str, labs_dir: str | Path | None = None) -> dict[str, Any]:
        if name in self.labs:
            raise LabError(f"lab {name!r} already running")
        root = find_labs_dir(labs_dir)
        server = root / name / "server.py"
        if not server.is_file():
            raise LabError(f"unknown lab {name!r}")
        proc = subprocess.Popen(
            [sys.executable, str(server), "--port", "0"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        port = 0
        deadline = time.time() + 15
        try:
            while time.time() < deadline:
                line = proc.stdout.readline() if proc.stdout else ""
                if line.startswith("READY port="):
                    port = int(line.strip().rsplit("=", 1)[1])
                    break
                if proc.poll() is not None:
                    break
        except ValueError:
            pass
        if not port:
            proc.kill()
            raise LabError(f"lab {name!r} failed to start")
        url = f"http://127.0.0.1:{port}"
        self.labs[name] = {"proc": proc, "url": url}
        # Readiness gate: reset endpoint must answer before use.
        self.reset(url)
        manifest = json.loads((root / name / "manifest.json").read_text())
        return {"name": name, "url": url, "manifest": manifest}

    def url(self, name: str) -> str:
        try:
            return self.labs[name]["url"]
        except KeyError:
            raise LabError(f"lab {name!r} is not running") from None

    def reset(self, url: str) -> dict:
        req = urllib.request.Request(f"{url}/reset", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except OSError as exc:
            raise LabError(f"lab reset failed: {exc}") from exc

    def stop(self, name: str) -> bool:
        entry = self.labs.pop(name, None)
        if entry is None:
            return False
        proc = entry["proc"]
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return True

    def stop_all(self) -> None:
        for name in list(self.labs):
            self.stop(name)
