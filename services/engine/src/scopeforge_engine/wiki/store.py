"""LLM Wiki & User Preference Memory Manager.
Stores persistent markdown knowledge, user preferences, target notes, and methodologies across sessions.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional
from langchain_core.tools import tool


DEFAULT_PREFERENCES = """# User Preferences & Security Profile

- **Report Style**: Technical, precise, with reproducible reproduction steps and CVSS v3.1 vector strings.
- **Tone**: Professional, cybersecurity-focused, objective (falsifiable evidence over speculation).
- **Default Execution Mode**: `plan` (Never send active packets without explicit operator approval).
- **Preferred PoC Format**: Python 3 script using `requests` or `httpx` with detailed inline comments.
- **Allowed Scope Boundary**: Strictly adhere to ScopeGate authorized targets.
- **Remediation Priority**: Prioritize architectural fixes and defensive coding over temporary perimeter patches.
"""

DEFAULT_TARGETS = """# Authorized Engagement Targets

- `authorized.example`: Primary authorized staging domain (Wildcard *.example.com enabled).
- `localhost`: Local test services (ports 8080, 8443, 3000).
- `127.0.0.1`: Local loopback interfaces.
"""

DEFAULT_PLAYBOOKS = """# Security Playbooks & Methodology

## Web Application Reconnaissance
1. Enumerate DNS records and subdomains (Passive).
2. Check security headers (CSP, HSTS, X-Frame-Options).
3. Inspect robots.txt, sitemap.xml, and exposed API documentation (/docs, /swagger, /openapi.json).
4. Run SAST audit on supplied code fixtures.

## Vulnerability Validation
1. Formulate falsifiable hypothesis.
2. Establish baseline response (status code, body length, header diff).
3. Send controlled payload and record exact delta.
4. Record SHA256 evidence hash in `.scopeforge/evidence/`.
"""


class SecWiki:
    """Manages the persistent LLM Wiki and User Preference Memory."""

    def __init__(self, wiki_dir: Optional[Path] = None):
        self.wiki_dir = wiki_dir or Path(".scopeforge/wiki")
        self.wiki_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_defaults()

    def _ensure_defaults(self):
        defaults = {
            "preferences.md": DEFAULT_PREFERENCES,
            "targets.md": DEFAULT_TARGETS,
            "playbooks.md": DEFAULT_PLAYBOOKS,
        }
        for filename, content in defaults.items():
            path = self.wiki_dir / filename
            if not path.exists():
                path.write_text(content.strip(), encoding="utf-8")

    def list_pages(self) -> List[str]:
        """List all available wiki pages."""
        return sorted([p.stem for p in self.wiki_dir.glob("*.md")])

    def read_page(self, page_name: str) -> str:
        """Read markdown content of a wiki page."""
        clean_name = page_name.replace(".md", "") + ".md"
        path = self.wiki_dir / clean_name
        if path.exists():
            return path.read_text(encoding="utf-8")
        return f"# Page '{page_name}' does not exist yet."

    def write_page(self, page_name: str, content: str) -> str:
        """Create or update a wiki page."""
        clean_name = page_name.replace(".md", "") + ".md"
        path = self.wiki_dir / clean_name
        path.write_text(content, encoding="utf-8")
        return f"Successfully saved wiki page '{clean_name}' ({len(content)} bytes)."

    def get_user_preferences(self) -> str:
        """Retrieve user preferences text for agent prompt context."""
        return self.read_page("preferences")

    def create_tools(self) -> List[Any]:
        """Create LangChain tools for agent to autonomously read/write the Wiki."""
        wiki = self

        @tool
        def wiki_read(page: str) -> str:
            """Read a page from the user's security knowledge wiki.
            Args:
                page: Name of page (e.g. 'preferences', 'targets', 'playbooks')
            """
            return wiki.read_page(page)

        @tool
        def wiki_write(page: str, content: str) -> str:
            """Write or update a page in the user's security knowledge wiki.
            Args:
                page: Name of page to update
                content: Markdown content to save
            """
            return wiki.write_page(page, content)

        @tool
        def wiki_list() -> str:
            """List all existing pages in the security wiki."""
            return ", ".join(wiki.list_pages())

        return [wiki_read, wiki_write, wiki_list]
