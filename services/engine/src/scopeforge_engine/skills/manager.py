"""SkillManager for discovery, activation, and prompt augmentation."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional
from .models import Skill


DEFAULT_SKILLS = [
    {
        "name": "subdomain-recon",
        "description": "Passive OSINT and Certificate Transparency reconnaissance for authorized asset discovery.",
        "triggers": ["subdomain", "dns", "crt.sh", "ct logs", "asset discovery", "recon"],
        "instructions": (
            "### Subdomain & Asset Discovery Methodology\n\n"
            "1. **Passive First**: Query Certificate Transparency logs (crt.sh) and public DNS records before active probing.\n"
            "2. **Boundary Validation**: Check every discovered hostname against the ScopeGate authorized target list.\n"
            "3. **CNAME Mapping**: Check for dangling DNS records (e.g. GitHub Pages, S3, Heroku) pointing to unallocated resources (Subdomain Takeover risk).\n"
            "4. **Telemetry Preservation**: Save discovered domain lists into `.scopeforge/evidence/`."
        ),
    },
    {
        "name": "api-idor-audit",
        "description": "Techniques for detecting Broken Object Level Authorization (BOLA/IDOR) in REST and GraphQL APIs.",
        "triggers": ["idor", "bola", "api", "graphql", "authorization bypass", "object level"],
        "instructions": (
            "### API BOLA / IDOR Testing Playbook\n\n"
            "1. **Endpoint Identification**: Locate endpoints featuring resource identifiers (e.g. `/api/v1/orders/{order_id}`).\n"
            "2. **Differential Testing**: Submit request with User A's auth token requesting User B's resource ID.\n"
            "3. **Falsifiable Assertion**: Baseline (User A accesses Resource A -> 200 OK). Test (User A accesses Resource B -> Expected 403 Forbidden).\n"
            "4. **Defensive Verification**: Confirm if tenant isolation is enforced at the database query layer."
        ),
    },
    {
        "name": "cve-triage",
        "description": "Systematic CVE vulnerability severity validation and CVSS v3.1 scoring.",
        "triggers": ["cve", "cvss", "triage", "severity", "exploit-db", "nvd"],
        "instructions": (
            "### CVE Triage & False-Positive Elimination\n\n"
            "1. **Version Verification**: Confirm exact installed version and whether the vulnerable module/feature is actually compiled or enabled.\n"
            "2. **Reachability Analysis**: Determine if the vulnerable code path can be triggered from an untrusted network input.\n"
            "3. **CVSS v3.1 Metrics**: Calculate Vector String (Attack Vector, Complexity, Privileges Required, User Interaction, Scope, Confidentiality, Integrity, Availability).\n"
            "4. **Remediation**: Prioritize official vendor patches over workarounds."
        ),
    },
    {
        "name": "reverse-proxy-bypass",
        "description": "Auditing HTTP reverse proxy header rewrites, hop-by-hop headers, and path normalizations.",
        "triggers": ["proxy", "hop-by-hop", "x-forwarded", "waf bypass", "path traversal", "nginx"],
        "instructions": (
            "### Reverse Proxy & Gateway Audit Playbook\n\n"
            "1. **Header Normalization**: Test header casing and duplicate header behavior (e.g. `Transfer-Encoding`, `Host`).\n"
            "2. **Hop-by-Hop Headers**: Check if `Connection: close, X-Forwarded-For` strips downstream authentication headers.\n"
            "3. **Path Traversal Deltas**: Compare origin behavior for URI encoded sequences (`%2e%2e%2f`, `..;/`).\n"
            "4. **ScopeGate Check**: Verify origin target remains within authorized engagement boundaries."
        ),
    },
]


class SkillManager:
    """Manages skill discovery, loading, and automatic prompt injection."""

    def __init__(self, skills_dir: Optional[Path] = None):
        self.skills_dir = skills_dir or Path(".scopeforge/skills")
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.skills: Dict[str, Skill] = {}
        self.active_skills: set[str] = set()

        self._seed_defaults()
        self._load_all()

    def _seed_defaults(self):
        """Seed default cybersecurity skills if directory is empty."""
        for d in DEFAULT_SKILLS:
            skill_folder = self.skills_dir / d["name"]
            skill_file = skill_folder / "SKILL.md"
            if not skill_file.exists():
                skill_folder.mkdir(parents=True, exist_ok=True)
                skill = Skill(
                    name=d["name"],
                    description=d["description"],
                    triggers=d["triggers"],
                    instructions=d["instructions"],
                    path=str(skill_file),
                )
                skill_file.write_text(skill.to_markdown(), encoding="utf-8")

    def _load_all(self):
        """Scan skills directories (.scopeforge/skills, root skills/, and ~/.scopeforge/skills) and load all valid SKILL.md files."""
        self.skills.clear()
        search_dirs = [
            self.skills_dir,
            Path("skills"),
            Path.home() / ".scopeforge" / "skills",
        ]
        for s_dir in search_dirs:
            if s_dir.exists():
                for p in s_dir.glob("*/SKILL.md"):
                    skill = Skill.from_skill_file(p)
                    if skill and skill.name not in self.skills:
                        self.skills[skill.name] = skill

    def list_skills(self) -> List[Skill]:
        """Return all discovered skills."""
        return list(self.skills.values())

    def get_skill(self, name: str) -> Optional[Skill]:
        """Get skill by name."""
        return self.skills.get(name)

    def activate_skill(self, name: str) -> bool:
        """Explicitly activate a skill for subsequent agent turns."""
        if name in self.skills:
            self.active_skills.add(name)
            return True
        return False

    def deactivate_skill(self, name: str) -> bool:
        if name in self.active_skills:
            self.active_skills.remove(name)
            return True
        return False

    def auto_match_skills(self, query: str) -> List[Skill]:
        """Automatically match skills against user query terms, ranked by relevance."""
        q_lower = query.lower()
        scored: List[tuple[int, Skill]] = []
        for s in self.skills.values():
            score = 0
            if s.name.lower() in q_lower:
                score += 10
            for t in s.triggers:
                t_low = t.lower()
                if t_low in q_lower:
                    score += 3 if len(t_low) > 3 else 1
            if s.name in self.active_skills:
                score += 5
            if score > 0:
                scored.append((score, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:3]]

    def install_skill_from_repo(self, repo_url: str) -> tuple[bool, str, List[str]]:
        """Clone a git repository and install all skills found within it into ScopeForge."""
        import shutil
        import subprocess
        import tempfile

        repo_url = repo_url.strip()
        if not repo_url:
            return False, "Repository URL cannot be empty.", []

        temp_dir = Path(tempfile.mkdtemp(prefix="scopeforge_skill_"))
        try:
            res = subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, str(temp_dir)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if res.returncode != 0:
                return False, f"git clone failed: {res.stderr.strip() or res.stdout.strip()}", []

            installed: List[str] = []
            candidates: List[Path] = []
            if (temp_dir / "skills").exists() and (temp_dir / "skills").is_dir():
                candidates.extend((temp_dir / "skills").glob("*"))
            else:
                candidates.extend(temp_dir.glob("*"))

            target_dirs = [self.skills_dir, Path.home() / ".scopeforge" / "skills"]
            for cand in candidates:
                if cand.is_dir() and (cand / "SKILL.md").exists():
                    s_name = cand.name
                    for t_dir in target_dirs:
                        t_dir.mkdir(parents=True, exist_ok=True)
                        dest = t_dir / s_name
                        if dest.exists():
                            shutil.rmtree(dest)
                        shutil.copytree(cand, dest)
                    installed.append(s_name)

            if not installed and (temp_dir / "SKILL.md").exists():
                s_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
                for t_dir in target_dirs:
                    dest = t_dir / s_name
                    dest.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(temp_dir / "SKILL.md", dest / "SKILL.md")
                installed.append(s_name)

            self._load_all()
            if installed:
                return True, f"Successfully installed {len(installed)} skills: {', '.join(installed)}", installed
            return False, "No valid SKILL.md skills found in the repository.", []
        except Exception as e:
            return False, f"Failed to install skill: {e}", []
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def get_prompt_instructions(self, query: str = "") -> str:
        """Generate formatted skill instructions to augment the LLM system prompt."""
        matched = self.auto_match_skills(query) if query else [self.skills[k] for k in self.active_skills if k in self.skills]
        if not matched:
            return ""

        blocks = []
        for s in matched:
            blocks.append(f"#### Active Skill: [{s.name}]\n{s.description}\n\n{s.instructions}")

        return "\n\n### Activated Specialized Skills:\n" + "\n\n---\n\n".join(blocks)

    def create_skill(
        self,
        name: str,
        description: str,
        triggers: List[str],
        instructions: str,
    ) -> Skill:
        """Create a new custom skill with SKILL.md on disk."""
        skill_folder = self.skills_dir / name
        skill_folder.mkdir(parents=True, exist_ok=True)
        skill_file = skill_folder / "SKILL.md"
        skill = Skill(
            name=name,
            description=description,
            triggers=triggers,
            instructions=instructions,
            path=str(skill_file),
        )
        skill_file.write_text(skill.to_markdown(), encoding="utf-8")
        self.skills[name] = skill
        return skill
