"""Test path bootstrap: import the engine package from source without install.

This keeps the CI portability gate (`python -m pytest` on a bare interpreter)
working for stdlib-only modules. Anything needing httpx/anyio still skips via
`pytest.importorskip` in its own module.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
