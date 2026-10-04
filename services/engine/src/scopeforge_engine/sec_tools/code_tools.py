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
    """View the contents of a local file with line numbers.
    Args:
        file_path: Path to the file to inspect.
        start_line: 1-indexed start line number (default 1).
        end_line: 1-indexed end line number (default 100).
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
        end = min(total_lines, max(start, end_line))
        
        formatted_lines = []
        for i in range(start, end + 1):
            formatted_lines.append(f"{i:4d} | {lines[i - 1].rstrip()}")

        return json.dumps({
            "file": str(path),
            "total_lines": total_lines,
            "range": f"{start}-{end}",
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
def write_file(file_path: str, content: str, overwrite: bool = True) -> str:
    """Create a new file or overwrite an existing file with provided content.
    Args:
        file_path: Path of the file to write.
        content: Code or text content to write.
        overwrite: Whether to overwrite existing file (default True).
    """
    path = Path(file_path).resolve()
    if path.exists() and not overwrite:
        return json.dumps({"error": f"File already exists and overwrite is set to False: {file_path}"})

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return json.dumps({
            "status": "SUCCESS",
            "file": str(path),
            "bytes_written": len(content.encode("utf-8")),
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed writing file: {e}"})


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
def bash_cli(command: str, timeout: int = 30) -> str:
    """Execute a bash / terminal command in the workspace directory.
    Args:
        command: The terminal command line string to execute.
        timeout: Execution timeout in seconds (default 30).
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
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return json.dumps({
            "command": command,
            "return_code": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "success": proc.returncode == 0,
        }, indent=2)
    except subprocess.TimeoutExpired:
        return json.dumps({
            "error": f"Command timed out after {timeout} seconds: {command}",
            "command": command,
            "success": False,
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
            with urllib.request.urlopen(t_req, timeout=8) as resp:
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
            with urllib.request.urlopen(ddg_req, timeout=8) as resp:
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
            wiki_req = urllib.request.Request(wiki_url, headers={"User-Agent": "ScopeForge/1.0"})
            with urllib.request.urlopen(wiki_req, timeout=5) as w_resp:
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


ALL_CODE_TOOLS = [
    view_file,
    edit_file,
    write_file,
    glob_files,
    grep_search,
    git_diff_tool,
    git_status_tool,
    git_commit_tool,
    bash_cli,
    google_web_search,
]

