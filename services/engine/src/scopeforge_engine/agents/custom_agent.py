"""Custom Agent definitions and loader."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel, Field


class CustomAgentConfig(BaseModel):
    """Specification for user-defined specialized security agents."""
    name: str
    role: str
    description: str
    system_prompt: str
    tools: List[str] = Field(default_factory=list)
    model: Optional[str] = None
    temperature: float = 0.1
    enabled: bool = True


DEFAULT_CUSTOM_AGENTS = [
    CustomAgentConfig(
        name="CloudSecAgent",
        role="Cloud Security & IAM Auditor",
        description="Audits cloud configurations, AWS/GCP IAM roles, and storage bucket access policies.",
        system_prompt=(
            "You are CloudSecAgent, an expert in cloud security posture management. "
            "You examine cloud configurations, IAM policies, and S3/Storage bucket permissions "
            "for excessive privileges, public exposure, and privilege escalation paths."
        ),
        tools=["web_surface_probe", "evidence_recorder"],
    ),
    CustomAgentConfig(
        name="ApiSecAgent",
        role="API & Microservices Security Auditor",
        description="Specializes in REST/GraphQL API security, BOLA/IDOR, and JWT validation flaws.",
        system_prompt=(
            "You are ApiSecAgent, specializing in OWASP API Security Top 10 vulnerabilities. "
            "You scrutinize authentication endpoints, authorization logic, object-level access controls, "
            "and rate limiting protections."
        ),
        tools=["web_surface_probe", "falsifiable_poc_runner", "evidence_recorder"],
    ),
]


class CustomAgentLoader:
    """Manages persistence and loading of custom user-defined agents."""

    def __init__(self, agents_dir: Optional[Path] = None):
        self.agents_dir = agents_dir or Path(".scopeforge/agents")
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        self.agents: Dict[str, CustomAgentConfig] = {}
        self._initialize()

    def _initialize(self):
        # Seed default custom agents if directory is empty
        for ca in DEFAULT_CUSTOM_AGENTS:
            self.agents[ca.name] = ca
            yaml_file = self.agents_dir / f"{ca.name.lower()}.yaml"
            if not yaml_file.exists():
                with open(yaml_file, "w", encoding="utf-8") as f:
                    yaml.safe_dump(ca.model_dump(), f, sort_keys=False)

        # Load any existing yaml files
        for p in self.agents_dir.glob("*.yaml"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                if "name" in data:
                    cfg = CustomAgentConfig(**data)
                    self.agents[cfg.name] = cfg
            except Exception:
                pass

    def list_agents(self) -> List[CustomAgentConfig]:
        return list(self.agents.values())

    def get_agent(self, name: str) -> Optional[CustomAgentConfig]:
        return self.agents.get(name)

    def create_agent(self, config: CustomAgentConfig) -> CustomAgentConfig:
        self.agents[config.name] = config
        yaml_file = self.agents_dir / f"{config.name.lower()}.yaml"
        with open(yaml_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(config.model_dump(), f, sort_keys=False)
        return config
