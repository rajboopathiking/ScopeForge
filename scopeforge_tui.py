#!/usr/bin/env python3
"""Launcher for ScopeForge Claude Code / Open Code style TUI."""
import sys
from pathlib import Path

# Add services/engine/src to path
engine_src = Path(__file__).resolve().parent / "services" / "engine" / "src"
if str(engine_src) not in sys.path:
    sys.path.insert(0, str(engine_src))

from scopeforge_engine.tui.app import main

if __name__ == "__main__":
    main()
