"""Skills model and parser following Claude Code & Open Code SKILL.md specification."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel, ConfigDict, Field


class Skill(BaseModel):
    """Encapsulates an agent skill defined via SKILL.md with YAML frontmatter."""
    model_config = ConfigDict(frozen=False)

    name: str
    description: str
    triggers: List[str] = Field(default_factory=list)
    author: str = "ScopeForge"
    version: str = "1.0.0"
    instructions: str = ""
    path: Optional[str] = None
    enabled: bool = True

    @classmethod
    def from_skill_file(cls, file_path: Path) -> Optional[Skill]:
        """Parse SKILL.md file with YAML frontmatter."""
        if not file_path.exists():
            return None

        content = file_path.read_text(encoding="utf-8")
        name = file_path.parent.name
        description = ""
        triggers = []
        author = "ScopeForge"
        version = "1.0.0"
        instructions = content.strip()

        frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
        if frontmatter_match:
            raw_yaml, body = frontmatter_match.groups()
            instructions = body.strip()
            try:
                meta = yaml.safe_load(raw_yaml) or {}
                name = meta.get("name", file_path.parent.name)
                description = str(meta.get("description", "") or "").strip()
                raw_triggers = meta.get("triggers", [])
                if isinstance(raw_triggers, str):
                    triggers = [t.strip() for t in raw_triggers.split(",") if t.strip()]
                elif isinstance(raw_triggers, list):
                    triggers = [str(t).strip() for t in raw_triggers if t]
                author = meta.get("author", "ScopeForge")
                version = str(meta.get("version", "1.0.0"))
            except Exception:
                pass
        else:
            description = content.splitlines()[0][:100] if content else ""

        # Auto-infer triggers if not explicitly defined in frontmatter (e.g. Claude Code skills)
        if not triggers:
            derived = set()
            clean_name = name.lower()
            derived.add(clean_name)
            for part in clean_name.replace("_", "-").split("-"):
                if len(part) >= 2:
                    derived.add(part)
            if clean_name.startswith("li-"):
                derived.add("linkedin")
            # Extract common action/topic keywords from description
            desc_lower = description.lower()
            for kw in ("linkedin", "post", "hook", "carousel", "profile", "comment", "inbox", "dm", "message", "reply", "plan", "audit", "repurpose", "human"):
                if kw in desc_lower:
                    derived.add(kw)
            triggers = sorted(list(derived))

        return cls(
            name=name,
            description=description,
            triggers=triggers,
            author=author,
            version=version,
            instructions=instructions,
            path=str(file_path),
        )

    def to_markdown(self) -> str:
        """Serialize skill back to SKILL.md format."""
        meta = {
            "name": self.name,
            "description": self.description,
            "triggers": self.triggers,
            "author": self.author,
            "version": self.version,
        }
        yaml_front = yaml.safe_dump(meta, sort_keys=False)
        return f"---\n{yaml_front}---\n\n{self.instructions}\n"
