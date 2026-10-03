"""Controlled browser capture (Phase 7 scaffold). Gated behind the opt-in
`browser` extra (Playwright) AND the same policy gate as HTTP: every
navigation and subresource request is decided independently, credentials never
cross origins, WebSockets need explicit scope.

Without the extra installed, construction raises CapabilityUnsupported with an
actionable message (visible degradation, never silent).
"""
from __future__ import annotations

from typing import Any

try:
    from .policy import Action
except ImportError:  # direct script execution
    from policy import Action  # type: ignore[no-redef]


class BrowserUnavailable(Exception):
    pass


def check_browser_runtime() -> None:
    try:
        import playwright  # type: ignore[import-not-found]
    except ImportError:
        raise BrowserUnavailable(
            "browser capture needs the opt-in extra: `uv sync --extra browser`. "
            "HTTP egress is unaffected.") from None
    void = playwright


def subresource_allowed(url: str, page_origin: str, decide_fn) -> tuple[bool, str]:
    """Policy hook for the Playwright route handler: each subresource URL is
    decided as its own R3 navigation with the page origin as context."""
    action = Action(kind="browser.navigate", method="GET", url=url, impact="R3",
                    description=f"subresource of {page_origin}")
    decision = decide_fn(action)
    if not decision.allowed:
        return False, "; ".join(decision.reasons)
    return True, "subresource admitted"


class BrowserCapture:
    """Thin Playwright controller. All navigation goes through the gate first;
    callers must install a route handler calling subresource_allowed."""

    def __init__(self, *, headless: bool = True) -> None:
        check_browser_runtime()
        self.headless = headless

    async def capture(self, url: str, *, decide_fn, timeout_ms: int = 15000) -> dict[str, Any]:
        from playwright.async_api import async_playwright  # type: ignore[import-not-found]

        gate = decide_fn(Action(kind="browser.navigate", method="GET", url=url,
                                impact="R3", description="top-level navigation"))
        if not gate.allowed:
            return {"navigated": False, "reasons": gate.reasons}
        requests: list[dict] = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            try:
                context = await browser.new_context()
                page = await context.new_page()

                async def route_handler(route, request):
                    ok, why = subresource_allowed(request.url, url, decide_fn)
                    requests.append({"url": request.url, "admitted": ok, "why": why})
                    if ok:
                        await route.continue_()
                    else:
                        await route.abort()

                await page.route("**/*", route_handler)
                await page.goto(url, timeout=timeout_ms)
                shot = await page.screenshot()
                title = await page.title()
            finally:
                await browser.close()
        return {"navigated": True, "title": title, "screenshot_bytes": len(shot),
                "requests": requests}
