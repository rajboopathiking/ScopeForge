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
        frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
        if frontmatter_match:
            raw_yaml, body = frontmatter_match.groups()
            try:
                meta = yaml.safe_load(raw_yaml) or {}
                return cls(
                    name=meta.get("name", file_path.parent.name),
                    description=meta.get("description", ""),
                    triggers=meta.get("triggers", []),
                    author=meta.get("author", "ScopeForge"),
                    version=str(meta.get("version", "1.0.0")),
                    instructions=body.strip(),
                    path=str(file_path),
                )
            except Exception:
                pass

        # Fallback if no frontmatter
        return cls(
            name=file_path.parent.name,
            description=content.splitlines()[0][:100] if content else "",
            instructions=content.strip(),
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
