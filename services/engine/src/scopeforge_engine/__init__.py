"""ScopeForge engine package (Phase 1 spine)."""

__version__ = "0.1.16"

# Defensive compatibility guard: Some environments have buggy Cython (e.g. 3.2.x)
# that crashes on SQLAlchemy's cython.declare(cython.const[cython.int], 1).
try:
    import Cython.Shadow  # type: ignore

    _orig_declare = Cython.Shadow.declare

    def _safe_declare(t, value=None):
        try:
            return _orig_declare(t, value)
        except ValueError:
            return value

    Cython.Shadow.declare = _safe_declare
except Exception:
    pass
