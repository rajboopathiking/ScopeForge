"""Configuration loader for ScopeForge ScopeGate settings."""
import json
from pathlib import Path
from typing import Dict, List, Optional


class ScopeGateConfig:
    """Container for ScopeGate configuration."""

    def __init__(self, mode: str = "plan", scopes: Optional[List[str]] = None):
        self.mode = mode
        self.scopes = scopes or ["authorized.example", "*.example.com", "localhost"]

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> "ScopeGateConfig":
        """Load ScopeGate config from file or return defaults.

        Args:
            config_path: Path to .scopeforge/scopegate.json. If None, searches
                        current directory and parents.

        Returns:
            ScopeGateConfig instance with loaded or default values.
        """
        if config_path is None:
            # Search for config in current directory and parents
            search_path = Path.cwd()
            for _ in range(5):  # Search up to 5 parent directories
                for candidate_name in ["scopegate.json", "scopegate_config.json"]:
                    candidate = search_path / ".scopeforge" / candidate_name
                    if candidate.exists():
                        config_path = candidate
                        break
                if config_path:
                    break
                search_path = search_path.parent

        if config_path and config_path.exists():
            try:
                with open(config_path, "r") as f:
                    data = json.load(f)
                mode = data.get("mode") or data.get("execution_mode", "plan")
                scopes = data.get("authorized_scopes") or data.get("authorized_targets", [])
                return cls(mode=mode, scopes=scopes)
            except Exception:
                pass

        # Return defaults if no config found or load failed
        return cls()

    def to_dict(self) -> Dict:
        """Serialize config to dictionary."""
        return {"mode": self.mode, "authorized_scopes": self.scopes}
