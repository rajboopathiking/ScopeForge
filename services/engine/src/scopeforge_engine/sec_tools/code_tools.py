"""Claude Code / Open Code style developer and code investigation tools.

Provides view_file, edit_file, write_file, glob_files, grep_search, and git integrations.
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional
from langchain_core.tools import tool


@tool
def view_file(file_path: str, start_line: int = 1, end_line: int = 100) -> str:
    """View the contents of a local file with line numbers and paging metadata.
    Args:
        file_path: Path to the file to inspect.
        start_line: 1-indexed start line number (default 1).
        end_line: 1-indexed end line number (default 100, max 250 per call for safe context management).
    """
    path = Path(file_path).resolve()
    if not path.exists():
        return json.dumps({"error": f"File not found: {file_path}"})
    if not path.is_file():
        return json.dumps({"error": f"Path is not a regular file: {file_path}"})

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        total_lines = len(lines)
        start = max(1, start_line)
        MAX_PAGE_LINES = 250
        requested_end = max(start, end_line)
        end = min(total_lines, min(start + MAX_PAGE_LINES - 1, requested_end))

        formatted_lines = []
        for i in range(start, end + 1):
            formatted_lines.append(f"{i:4d} | {lines[i - 1].rstrip()}")

        has_more = end < total_lines
        next_start = end + 1 if has_more else None

        return json.dumps({
            "file": str(path),
            "total_lines": total_lines,
            "range": f"{start}-{end}",
            "has_more": has_more,
            "next_start_line": next_start,
            "paging_hint": f"Call view_file(file_path='{file_path}', start_line={next_start}, end_line={next_start + 100}) to view remaining content." if has_more else "End of file reached.",
            "content": "\n".join(formatted_lines),
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed reading file: {e}"})


@tool
def edit_file(file_path: str, target_content: str, replacement_content: str) -> str:
    """Perform a surgical search-and-replace modification on an existing file.
    Args:
        file_path: Path to the file to edit.
        target_content: Exact string or block of code to find and replace.
        replacement_content: Replacement text.
    """
    path = Path(file_path).resolve()
    if not path.exists() or not path.is_file():
        return json.dumps({"error": f"File not found: {file_path}"})

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = f.read()

        occurrences = data.count(target_content)
        if occurrences == 0:
            return json.dumps({
                "error": "Target content not found in file. Ensure exact matching whitespace and indentation.",
                "file": str(path),
            })
        if occurrences > 1:
            return json.dumps({
                "error": f"Target content is ambiguous (found {occurrences} occurrences). Provide more surrounding context.",
                "file": str(path),
            })

        new_data = data.replace(target_content, replacement_content, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_data)

        return json.dumps({
            "status": "SUCCESS",
            "file": str(path),
            "message": "Successfully replaced targeted content chunk.",
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed editing file: {e}"})


@tool
def write_file(
    file_path: str,
    content: str,
    overwrite: bool = True,
    append: bool = False,
    chunk_index: int = 0,
    total_chunks: int = 1,
) -> str:
    """Create a new file, overwrite, or incrementally append content without mid-content truncation.
    Args:
        file_path: Path of the file to write.
        content: Code, text, or markdown content to write.
        overwrite: Whether to overwrite existing file (default True, ignored if append is True).
        append: If True, appends content to the end of the file.
        chunk_index: 0-indexed sequence number if writing multi-part content (default 0).
        total_chunks: Expected total number of chunks (default 1).
    """
    path = Path(file_path).resolve()
    if path.exists() and not overwrite and not append and chunk_index == 0:
        return json.dumps({"error": f"File already exists and overwrite is set to False: {file_path}"})

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_mode = "a" if (append or chunk_index > 0) else "w"
        with open(path, file_mode, encoding="utf-8") as f:
            f.write(content)
            f.flush()

        total_bytes = path.stat().st_size
        return json.dumps({
            "status": "SUCCESS",
            "file": str(path),
            "mode": "append" if file_mode == "a" else "write",
            "bytes_written": len(content.encode("utf-8")),
            "total_file_bytes": total_bytes,
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "is_complete": chunk_index + 1 >= total_chunks,
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed writing file: {e}"})


@tool
def append_file(file_path: str, content: str) -> str:
    """Append text content to the end of a file (creates file if not present).
    Designed for progressive generation and streaming storage of large files, reports, and logs without truncation.
    Args:
        file_path: Path to the target file.
        content: Chunk of text or markdown content to append.
    """
    path = Path(file_path).resolve()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
            f.flush()
        return json.dumps({
            "status": "SUCCESS",
            "file": str(path),
            "bytes_appended": len(content.encode("utf-8")),
            "total_file_bytes": path.stat().st_size,
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed appending to file: {e}"})


@tool
def store_large_file(
    file_path: str,
    content_chunk: str,
    chunk_index: int = 0,
    total_chunks: int = 1,
    mode: str = "write",
) -> str:
    """Store large file content using a chunked / streaming strategy to eliminate mid-content truncation.
    Args:
        file_path: Target path for the file.
        content_chunk: The text or markdown content chunk to write or append.
        chunk_index: 0-indexed sequence number of this chunk (default 0).
        total_chunks: Expected total number of chunks (default 1).
        mode: 'write' (overwrite on chunk 0, append thereafter) or 'append' (always append).
    """
    path = Path(file_path).resolve()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_mode = "a" if (mode == "append" or chunk_index > 0) else "w"
        with open(path, file_mode, encoding="utf-8") as f:
            f.write(content_chunk)
            f.flush()
        total_bytes = path.stat().st_size
        return json.dumps({
            "status": "SUCCESS",
            "file": str(path),
            "bytes_chunk_written": len(content_chunk.encode("utf-8")),
            "total_file_bytes": total_bytes,
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "is_complete": chunk_index + 1 >= total_chunks,
            "strategy_note": "Multi-part large file storage preserved on disk without truncation.",
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed storing large file chunk: {e}"})


@tool
def glob_files(pattern: str = "*", directory: str = ".") -> str:
    """Search for files in a directory matching a glob pattern (e.g. '**/*.py', 'src/*.ts').
    Args:
        pattern: Glob pattern to match against filenames.
        directory: Directory to search in (default current directory).
    """
    base = Path(directory).resolve()
    if not base.exists() or not base.is_dir():
        return json.dumps({"error": f"Directory not found: {directory}"})

    matches: List[str] = []
    ignored = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".turbo", "dist", "build"}

    try:
        for p in base.glob(pattern):
            if any(part in ignored for part in p.parts):
                continue
            if p.is_file():
                matches.append(str(p.relative_to(base)))
                if len(matches) >= 100:
                    break

        return json.dumps({
            "directory": str(base),
            "pattern": pattern,
            "count": len(matches),
            "files": sorted(matches),
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Glob failed: {e}"})


@tool
def grep_search(query: str, directory: str = ".", file_pattern: str = "*", case_sensitive: bool = False) -> str:
    """Search for text or regex pattern across files in a directory.
    Args:
        query: Regex or string query to search for.
        directory: Root directory for the search (default current directory).
        file_pattern: Filename filter pattern (default '*').
        case_sensitive: Whether match should be case-sensitive.
    """
    base = Path(directory).resolve()
    if not base.exists() or not base.is_dir():
        return json.dumps({"error": f"Directory not found: {directory}"})

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        regex = re.compile(query, flags)
    except re.error as e:
        return json.dumps({"error": f"Invalid regex query '{query}': {e}"})

    ignored = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".turbo", "dist", "build"}
    matches: List[Dict[str, Any]] = []

    try:
        for p in base.glob(f"**/{file_pattern}"):
            if any(part in ignored for part in p.parts):
                continue
            if not p.is_file():
                continue
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    for idx, line in enumerate(f, start=1):
                        if regex.search(line):
                            matches.append({
                                "file": str(p.relative_to(base)),
                                "line": idx,
                                "match": line.strip()[:150],
                            })
                            if len(matches) >= 60:
                                break
            except Exception:
                continue
            if len(matches) >= 60:
                break

        return json.dumps({
            "query": query,
            "matches_count": len(matches),
            "matches": matches,
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Grep search failed: {e}"})


@tool
def git_diff_tool(staged: bool = False) -> str:
    """Get the git diff for uncommitted changes or staged changes.
    Args:
        staged: If True, inspects staged changes (--cached); otherwise unstaged changes.
    """
    cmd = ["git", "diff", "--cached"] if staged else ["git", "diff"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        output = proc.stdout.strip()
        return json.dumps({
            "command": " ".join(cmd),
            "staged": staged,
            "has_changes": bool(output),
            "diff": output[:4000] if output else "No changes detected.",
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"git diff failed: {e}"})


@tool
def git_status_tool() -> str:
    """Get current git repository status and branch info."""
    try:
        proc = subprocess.run(["git", "status", "--short", "--branch"], capture_output=True, text=True, timeout=10)
        return json.dumps({
            "status": proc.stdout.strip() or "Clean working tree",
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"git status failed: {e}"})


@tool
def git_commit_tool(message: str) -> str:
    """Commit staged changes to git with a commit message.
    Args:
        message: The commit message.
    """
    if not message.strip():
        return json.dumps({"error": "Commit message cannot be empty."})
    try:
        proc = subprocess.run(["git", "commit", "-m", message], capture_output=True, text=True, timeout=15)
        return json.dumps({
            "return_code": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "success": proc.returncode == 0,
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"git commit failed: {e}"})


@tool
def bash_cli(command: str, timeout: int = 120, output_file: Optional[str] = None) -> str:
    """Execute a bash / terminal command in the workspace directory.
    Args:
        command: The terminal command line string to execute.
        timeout: Execution timeout in seconds (default 120).
        output_file: Optional path to save full stdout/stderr directly to disk without truncation.
    """
    forbidden_patterns = ["rm -rf /", "mkfs", "dd if=", ":(){ :|:& };:", "chmod -R 777 /"]
    for fb in forbidden_patterns:
        if fb in command:
            return json.dumps({
                "error": f"Command rejected: matches dangerous destructive pattern '{fb}'",
                "command": command,
                "success": False,
            }, indent=2)

    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True if hasattr(os, "setsid") else False,
        )
        try:
            stdout_data, stderr_data = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if hasattr(os, "killpg") and hasattr(os, "getpgid"):
                try:
                    os.killpg(os.getpgid(proc.pid), 9)
                except Exception:
                    proc.kill()
            else:
                proc.kill()
            stdout_data, stderr_data = proc.communicate()
            return json.dumps({
                "error": f"Command timed out after {timeout} seconds: {command}",
                "command": command,
                "success": False,
            }, indent=2)

        out_clean = stdout_data.strip() if stdout_data else ""
        err_clean = stderr_data.strip() if stderr_data else ""

        # Direct disk output capture strategy
        if output_file:
            out_path = Path(output_file).resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(stdout_data or "")
                if stderr_data:
                    f.write("\n--- STDERR ---\n")
                    f.write(stderr_data)
            return json.dumps({
                "command": command,
                "return_code": proc.returncode,
                "output_file": str(out_path),
                "bytes_written": out_path.stat().st_size,
                "preview": out_clean[:1000] if out_clean else "",
                "success": proc.returncode == 0,
            }, indent=2)

        # Large output auto-spill safeguard: if stdout > 10,000 characters, preserve to disk
        spill_note = None
        if len(out_clean) > 10000:
            spill_dir = Path(".scopeforge/runs/bash_outputs")
            spill_dir.mkdir(parents=True, exist_ok=True)
            import datetime
            spill_file = spill_dir / f"bash_{proc.pid}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            try:
                spill_file.write_text(stdout_data or "", encoding="utf-8")
                spill_note = f"Full untruncated output ({len(out_clean)} chars) preserved on disk at: {spill_file}"
            except Exception:
                pass

        return json.dumps({
            "command": command,
            "return_code": proc.returncode,
            "stdout": out_clean,
            "stderr": err_clean,
            "storage_note": spill_note,
            "success": proc.returncode == 0,
        }, indent=2)
    except Exception as e:
        return json.dumps({
            "error": f"Execution failed: {e}",
            "command": command,
            "success": False,
        }, indent=2)


@tool
def google_web_search(query: str, max_results: int = 5) -> str:
    """Search Google and the web for live technical documentation, code, CVEs, or general info.
    Args:
        query: Search query terms.
        max_results: Maximum number of search results to return (default 5).
    """
    if not query.strip():
        return json.dumps({"error": "Search query cannot be empty."})

    results: List[Dict[str, str]] = []

    # Robust SSL context for macOS and diverse environments
    import ssl
    try:
        import certifi
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ssl_ctx = ssl._create_unverified_context()

    # 1. Tavily API if key is present
    tavily_key = os.getenv("TAVILY_API_KEY")
    if tavily_key:
        try:
            req_data = json.dumps({"query": query, "max_results": max_results}).encode("utf-8")
            t_req = urllib.request.Request(
                "https://api.tavily.com/search",
                data=req_data,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {tavily_key}"},
            )
            with urllib.request.urlopen(t_req, context=ssl_ctx, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for r in data.get("results", [])[:max_results]:
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "snippet": r.get("content", ""),
                    })
        except Exception:
            pass

    # 2. DuckDuckGo live HTML POST scraper (zero-credential fallback)
    if not results:
        try:
            url = "https://html.duckduckgo.com/html/"
            form_data = urllib.parse.urlencode({"q": query}).encode("utf-8")
            ddg_req = urllib.request.Request(
                url,
                data=form_data,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Referer": "https://html.duckduckgo.com/",
                },
            )
            with urllib.request.urlopen(ddg_req, context=ssl_ctx, timeout=8) as resp:
                content = resp.read().decode("utf-8", errors="ignore")

            titles = re.findall(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', content)
            snippets = re.findall(r'<a[^>]+class="result__snippet"[^>]*>([\s\S]*?)</a>', content)

            for i in range(min(len(titles), max_results)):
                href, title_html = titles[i]
                if "uddg=" in href:
                    m = re.search(r"uddg=([^&]+)", href)
                    clean_url = urllib.parse.unquote(m.group(1)) if m else href
                else:
                    clean_url = href
                title = html.unescape(re.sub(r"<[^>]+>", "", title_html).strip())
                snip = html.unescape(re.sub(r"<[^>]+>", "", snippets[i]).strip()) if i < len(snippets) else ""
                if clean_url and title:
                    results.append({
                        "title": title,
                        "url": clean_url,
                        "snippet": snip,
                    })
        except Exception:
            pass

    # 3. Wikipedia API fallback
    if not results:
        try:
            wiki_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(query)}&format=json"
            wiki_req = urllib.request.Request(
                wiki_url,
                headers={"User-Agent": "ScopeForge/1.0 (https://github.com/rajboopathiking/ScopeForge; contact@scopeforge.dev)"},
            )
            with urllib.request.urlopen(wiki_req, context=ssl_ctx, timeout=5) as w_resp:
                w_data = json.loads(w_resp.read().decode("utf-8"))
                for item in w_data.get("query", {}).get("search", [])[:max_results]:
                    results.append({
                        "title": item.get("title", ""),
                        "url": f"https://en.wikipedia.org/wiki/{urllib.parse.quote(item.get('title', ''))}",
                        "snippet": html.unescape(re.sub(r"<[^>]+>", "", item.get("snippet", "")).strip()),
                    })
        except Exception:
            pass

    return json.dumps({
        "query": query,
        "results_count": len(results),
        "results": results,
    }, indent=2)


@tool
def capture_screenshot(url: str, output_file: str, full_page: bool = True, timeout: int = 30000) -> str:
    """Capture a website screenshot to a local PNG file via headless browser.
    Args:
        url: Fully-qualified http(s) URL to capture.
        output_file: Local .png path to write (parent dirs created).
        full_page: Capture full scrollable page (default True).
        timeout: Navigation timeout in ms (default 30000).
    Returns JSON with real filesystem verification (size, file type).
    Never fabricate success: if the browser extra is missing or navigation
    fails, returns success=False with an actionable error.
    """
    out = Path(output_file).expanduser()
    if out.suffix.lower() != ".png":
        return json.dumps({"success": False, "error": "output_file must end with .png", "output_file": str(out)}, indent=2)
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
    except ImportError:
        return json.dumps({
            "success": False,
            "error": "browser capture needs the opt-in extra: `uv sync --extra browser` (playwright not installed). No file was written.",
            "url": url,
            "output_file": str(out),
        }, indent=2)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1920, "height": 1080})
                page.goto(url, timeout=timeout)
                page.screenshot(path=str(out), full_page=full_page)
            finally:
                browser.close()
        if not out.exists():
            return json.dumps({"success": False, "error": "screenshot command returned but file missing", "output_file": str(out)}, indent=2)
        size = out.stat().st_size
        # Minimal PNG magic check, no external `file` dependency
        magic_ok = False
        try:
            with open(out, "rb") as f:
                magic_ok = f.read(8) == b"\x89PNG\r\n\x1a\n"
        except Exception:
            magic_ok = False
        return json.dumps({
            "success": True if (size > 0 and magic_ok) else False,
            "url": url,
            "output_file": str(out),
            "file_size": size,
            "png_magic_ok": magic_ok,
        }, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": f"screenshot failed: {e}", "url": url, "output_file": str(out)}, indent=2)


ALL_CODE_TOOLS = [
    view_file,
    edit_file,
    write_file,
    append_file,
    store_large_file,
    glob_files,
    grep_search,
    git_diff_tool,
    git_status_tool,
    git_commit_tool,
    bash_cli,
    google_web_search,
    capture_screenshot,
]

