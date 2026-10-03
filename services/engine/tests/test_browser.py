"""Browser gate scaffold (stdlib-only). Real capture needs the opt-in extra."""
from types import SimpleNamespace

import pytest

from scopeforge_engine.browser import (
    BrowserUnavailable,
    check_browser_runtime,
    subresource_allowed,
)


def test_missing_extra_degrades_visibly():
    try:
        import playwright  # noqa: F401
    except ImportError:
        with pytest.raises(BrowserUnavailable, match="opt-in extra"):
            check_browser_runtime()
    else:
        pytest.skip("playwright installed; live capture is a maintainer op")


def test_subresource_gate_per_request():
    seen = []

    def decide(action):
        seen.append(action)
        if "evil" in action.url:
            return SimpleNamespace(allowed=False, reasons=["not in included scope"])
        return SimpleNamespace(allowed=True, reasons=["ok"])

    ok, _ = subresource_allowed("https://authorized.example/app.js",
                                "https://authorized.example/", decide)
    assert ok is True
    denied, why = subresource_allowed("https://evil.example/x.js",
                                      "https://authorized.example/", decide)
    assert denied is False and "not in included scope" in why
    assert all(a.kind == "browser.navigate" and a.impact == "R3" for a in seen)
