"""System clipboard integration for ScopeForge TUI.

Provides cross-platform copy and paste functionality with native macOS
`pbcopy`/`pbpaste` support, Linux `wl-copy`/`xclip`/`xsel` support,
Windows `clip`/`powershell` support, and Textual fallback.
"""
from __future__ import annotations

import logging
import platform
import shutil
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


def copy_to_system_clipboard(text: str) -> bool:
    """Copy text to the OS system clipboard.

    On macOS, uses native `pbcopy` so the content is immediately available to
    all external applications (browsers, IDEs, Slack, etc.).
    On Linux, uses `wl-copy` (Wayland), `xclip`, or `xsel` (X11).
    On Windows, uses `clip.exe` or PowerShell Set-Clipboard.
    """
    if not text:
        return False

    system = platform.system().lower()

    # 1. macOS (Darwin) - native pbcopy
    if system == "darwin" and shutil.which("pbcopy"):
        try:
            res = subprocess.run(
                ["pbcopy"],
                input=text.encode("utf-8"),
                check=True,
                timeout=2,
            )
            return res.returncode == 0
        except Exception as e:
            logger.debug("pbcopy failed: %s", e)

    # 2. Linux / BSD - Wayland or X11
    if system in ("linux", "freebsd", "openbsd"):
        # Wayland
        if shutil.which("wl-copy"):
            try:
                res = subprocess.run(
                    ["wl-copy"],
                    input=text.encode("utf-8"),
                    check=True,
                    timeout=2,
                )
                return res.returncode == 0
            except Exception as e:
                logger.debug("wl-copy failed: %s", e)

        # X11 with xclip
        if shutil.which("xclip"):
            try:
                res = subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=text.encode("utf-8"),
                    check=True,
                    timeout=2,
                )
                return res.returncode == 0
            except Exception as e:
                logger.debug("xclip failed: %s", e)

        # X11 with xsel
        if shutil.which("xsel"):
            try:
                res = subprocess.run(
                    ["xsel", "--clipboard", "--input"],
                    input=text.encode("utf-8"),
                    check=True,
                    timeout=2,
                )
                return res.returncode == 0
            except Exception as e:
                logger.debug("xsel failed: %s", e)

    # 3. Windows
    if system == "windows":
        if shutil.which("clip"):
            try:
                res = subprocess.run(
                    ["clip"],
                    input=text.encode("utf-16le"),
                    check=True,
                    timeout=2,
                )
                return res.returncode == 0
            except Exception as e:
                logger.debug("clip failed: %s", e)

        if shutil.which("powershell"):
            try:
                res = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "Set-Clipboard", "-Value", "$input"],
                    input=text.encode("utf-8"),
                    check=True,
                    timeout=2,
                )
                return res.returncode == 0
            except Exception as e:
                logger.debug("powershell Set-Clipboard failed: %s", e)

    # 4. Try pyperclip if installed
    try:
        import pyperclip  # type: ignore

        pyperclip.copy(text)
        return True
    except Exception:
        pass

    # 5. Try tkinter if available
    try:
        import tkinter as tk  # type: ignore

        r = tk.Tk()
        r.withdraw()
        r.clipboard_clear()
        r.clipboard_append(text)
        r.update()
        r.destroy()
        return True
    except Exception:
        pass

    return False


def paste_from_system_clipboard() -> str:
    """Retrieve text from the OS system clipboard.

    On macOS, uses native `pbpaste`.
    On Linux, uses `wl-paste`, `xclip`, or `xsel`.
    On Windows, uses PowerShell Get-Clipboard.
    """
    system = platform.system().lower()

    # 1. macOS (Darwin) - native pbpaste
    if system == "darwin" and shutil.which("pbpaste"):
        try:
            res = subprocess.run(
                ["pbpaste"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0 and res.stdout is not None:
                return res.stdout
        except Exception as e:
            logger.debug("pbpaste failed: %s", e)

    # 2. Linux / BSD - Wayland or X11
    if system in ("linux", "freebsd", "openbsd"):
        # Wayland
        if shutil.which("wl-paste"):
            try:
                res = subprocess.run(
                    ["wl-paste", "--no-newline"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if res.returncode == 0 and res.stdout is not None:
                    return res.stdout
            except Exception as e:
                logger.debug("wl-paste failed: %s", e)

        # X11 with xclip
        if shutil.which("xclip"):
            try:
                res = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-o"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if res.returncode == 0 and res.stdout is not None:
                    return res.stdout
            except Exception as e:
                logger.debug("xclip -o failed: %s", e)

        # X11 with xsel
        if shutil.which("xsel"):
            try:
                res = subprocess.run(
                    ["xsel", "--clipboard", "--output"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if res.returncode == 0 and res.stdout is not None:
                    return res.stdout
            except Exception as e:
                logger.debug("xsel --output failed: %s", e)

    # 3. Windows
    if system == "windows" and shutil.which("powershell"):
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0 and res.stdout is not None:
                return res.stdout
        except Exception as e:
            logger.debug("powershell Get-Clipboard failed: %s", e)

    # 4. Try pyperclip if installed
    try:
        import pyperclip  # type: ignore

        val = pyperclip.paste()
        if isinstance(val, str):
            return val
    except Exception:
        pass

    # 5. Try tkinter if available
    try:
        import tkinter as tk  # type: ignore

        r = tk.Tk()
        r.withdraw()
        val = r.clipboard_get()
        r.destroy()
        if isinstance(val, str):
            return val
    except Exception:
        pass

    return ""


def deduplicate_doubled_text(val: str) -> str:
    """If terminal or clipboard duplicate-pasted string into doubled halves (e.g. keykey or urlurl), collapse it."""
    if not val or len(val) < 6:
        return val
    s = val.strip()
    if len(s) % 2 == 0:
        half = len(s) // 2
        first, second = s[:half], s[half:]
        # If both halves are identical and not just a repeated single character like '------'
        if first == second and len(set(first)) > 1:
            return first
    return val


def clean_pasted_text(text: str, single_line: bool = False) -> str:
    """Normalize, sanitize, and de-duplicate pasted text.

    If `single_line` is True, multiple lines are converted into spaces
    so that multi-line pastes into single-line inputs (e.g. prompt bars, API keys)
    are not truncated or lost.
    """
    if not text:
        return ""
    # Strip null bytes and control codes (preserve newlines and tabs)
    text = "".join(ch for ch in text if ch in "\n\r\t" or ord(ch) >= 32)
    if single_line:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        res = " ".join(lines)
    else:
        res = text.strip("\r\n")
    return deduplicate_doubled_text(res)
