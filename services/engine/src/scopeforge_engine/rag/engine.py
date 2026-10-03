"""LlamaIndex-based Cybersecurity RAG engine for knowledge retrieval and context augmentation."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
import llama_index.core
from llama_index.core import Document, Settings, SummaryIndex
from llama_index.core.schema import NodeWithScore


DEFAULT_SECURITY_KNOWLEDGE = [
    Document(
        text=(
            "OWASP Top 10 - A01:2021 Broken Access Control: "
            "Restrictions on what authenticated users are allowed to do are often not properly enforced. "
            "Flaws typically lead to unauthorized information disclosure, modification, or destruction of all data, "
            "or performing a business function outside the user's limits. Common access control vulnerabilities include "
            "Insecure Direct Object References (IDOR), missing function level access control, CORS misconfigurations, "
            "and forced browsing to authenticated pages as an unauthenticated user."
        ),
        metadata={"category": "OWASP", "topic": "Broken Access Control", "severity": "CRITICAL"},
    ),
    Document(
        text=(
            "OWASP Top 10 - A03:2021 Injection (SQLi, Command Injection, LDAP): "
            "A web application is vulnerable to injection when user-supplied data is not validated, filtered, or sanitized "
            "by the application, dynamic queries or non-parameterized calls without context-aware escaping are used directly "
            "in the interpreter. Remediation requires using a safe API (parameterized queries, stored procedures), "
            "using positive server-side input validation, and escaping special characters."
        ),
        metadata={"category": "OWASP", "topic": "Injection", "severity": "CRITICAL"},
    ),
    Document(
        text=(
            "OWASP Top 10 - A10:2021 Server-Side Request Forgery (SSRF): "
            "SSRF flaws occur whenever a web application is fetching a remote resource without validating the user-supplied URL. "
            "It allows an attacker to coerce the application to send a crafted request to an unexpected destination, "
            "even when protected by a firewall, VPN, or network ACL. Defenses include disabling HTTP redirections, "
            "enforcing URL schemas to HTTP/HTTPS only, and blocking requests to private address spaces (127.0.0.1, 10.0.0.0/8, 169.254.169.254 AWS metadata)."
        ),
        metadata={"category": "OWASP", "topic": "SSRF", "severity": "HIGH"},
    ),
    Document(
        text=(
            "ScopeForge Bug Bounty Methodology: "
            "1. Scoping & RoE verification: Confirm destination domain/IP is within the authorized bounty scope. "
            "2. Passive Reconnaissance: DNS resolution, certificate transparency logs, web archive indexing without touching the origin. "
            "3. Surface Mapping: Fingerprint active services, analyze security headers, inspect robots.txt and sitemaps. "
            "4. Falsifiable Hypotheses: Formulate testable assertions with strict baseline vs changed observable variables. "
            "5. Evidence Gathering: Record exact HTTP request/response pairs, verify reproducibility, and compute CVSS v3.1."
        ),
        metadata={"category": "Methodology", "topic": "ScopeForge RoE", "severity": "INFO"},
    ),
]


class LlamaSecRAG:
    """Llama-based RAG engine specialized for cybersecurity knowledge bases."""

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir or Path(".scopeforge/rag_store")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.documents: List[Document] = list(DEFAULT_SECURITY_KNOWLEDGE)
        self._index: Optional[Any] = None

        self._initialize_index()

    def _initialize_index(self):
        """Build or load the LlamaIndex structure."""
        try:
            # SummaryIndex in LlamaIndex operates without requiring external embedding models
            self._index = SummaryIndex.from_documents(self.documents)
        except Exception:
            self._index = None

    def ingest_text(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Ingest plain text snippet into RAG knowledge."""
        doc = Document(text=text, metadata=metadata or {"source": "manual_ingest"})
        self.documents.append(doc)
        if self._index:
            self._index.insert(doc)
        return f"Ingested document ({len(text)} chars)"

    def ingest_file(self, file_path: str) -> str:
        """Ingest a file from disk into RAG."""
        path = Path(file_path)
        if not path.exists():
            return f"Error: File '{file_path}' does not exist."

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            doc = Document(text=content, metadata={"source": path.name, "path": str(path)})
            self.documents.append(doc)
            if self._index:
                self._index.insert(doc)
            return f"Successfully ingested '{path.name}' ({len(content)} chars) into RAG index."
        except Exception as e:
            return f"Failed to ingest file: {e}"

    def query(self, query_str: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """Retrieve relevant cybersecurity knowledge snippets for a query."""
        results: List[Dict[str, Any]] = []

        q_terms = set(re.findall(r"\w+", query_str.lower()))

        # High-relevance lexical & keyword scoring across documents
        scored_docs = []
        for doc in self.documents:
            text_lower = doc.text.lower()
            score = 0
            for term in q_terms:
                if len(term) > 2:
                    score += text_lower.count(term)
            if score > 0:
                scored_docs.append((score, doc))

        scored_docs.sort(key=lambda x: x[0], reverse=True)

        for score, doc in scored_docs[:top_k]:
            results.append({
                "score": score,
                "text": doc.text,
                "metadata": doc.metadata,
            })

        # Fallback if no direct keyword match
        if not results and self.documents:
            for doc in self.documents[:2]:
                results.append({
                    "score": 0.1,
                    "text": doc.text,
                    "metadata": doc.metadata,
                })

        return results

    def get_context_for_prompt(self, query_str: str) -> str:
        """Format retrieved knowledge for agent prompt injection."""
        hits = self.query(query_str, top_k=2)
        if not hits:
            return ""

        context_blocks = []
        for i, hit in enumerate(hits, 1):
            category = hit["metadata"].get("category", "SecOps")
            context_blocks.append(f"[{i}] ({category}) {hit['text']}")

        return "\n\n### Relevant Security Knowledge (RAG):\n" + "\n".join(context_blocks)
