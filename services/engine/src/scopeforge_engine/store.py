"""Atomic file primitives (Phase 4). Portable files are authoritative evidence;
every mutation is crash-safe: single-line appends or tmp-file + fsync + rename.
Stdlib only.
"""
from __future__ import annotations

import datetime
import json
import os
import time
from pathlib import Path
from typing import Any


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


class StoreError(Exception):
    pass


class CorruptStore(StoreError):
    pass


class FileLock:
    """Mutual exclusion across engine processes (O_EXCL create; stale-lock safe)."""

    def __init__(self, path: str | Path, timeout_s: float = 10.0) -> None:
        self.path = Path(path)
        self.timeout_s = timeout_s
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, str(os.getpid()).encode())
                return
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    age = 0
                if age > 120:  # stale lock from a dead process
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                if time.monotonic() > deadline:
                    raise StoreError(f"lock timeout: {self.path}")
                time.sleep(0.02)

    def release(self) -> None:
        try:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, *args: Any) -> None:
        self.release()


def atomic_write_text(path: str | Path, text: str) -> None:
    """Write + fsync + atomic rename. Readers never see a torn file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def append_jsonl(path: str | Path, obj: dict) -> None:
    """Single-line append (one write() call: atomic vs kill -9 on POSIX)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, separators=(",", ":")) + "\n")


def read_json(path: str | Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (ValueError, OSError) as exc:
        raise CorruptStore(f"unparseable {path}: {exc}") from exc


def read_jsonl(path: str | Path) -> list[dict]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    out: list[dict] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError as exc:
            raise CorruptStore(f"{path}:{lineno}: {exc}") from exc
    return out


def fsync_tree(root: str | Path) -> None:
    """Checkpoint: fsync every regular file so completed work survives kill -9."""
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if name.startswith(".") or name.endswith(".tmp"):
                continue
            try:
                with open(os.path.join(dirpath, name), "rb") as fh:
                    os.fsync(fh.fileno())
            except OSError:
                pass
