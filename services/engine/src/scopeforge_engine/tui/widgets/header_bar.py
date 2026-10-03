"""Header widget inspired by Claude Code / Open Code status line."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label


class HeaderBar(Widget):
    """Top status bar displaying brand, model, mode, scope, active agent, and token usage."""

    DEFAULT_CSS = """
    HeaderBar {
        dock: top;
        height: 3;
        width: 100%;
        background: #161b22;
        border-bottom: heavy #30363d;
        layout: horizontal;
        padding: 0 1;
        align-vertical: middle;
    }
    """

    model_name: reactive[str] = reactive("claude-3-7-sonnet")
    mode: reactive[str] = reactive("plan")
    scope: reactive[str] = reactive("authorized.example")
    active_agent: reactive[str] = reactive("Supervisor")
    tokens: reactive[int] = reactive(0)
    cost: reactive[float] = reactive(0.0)

    def compose(self) -> ComposeResult:
        yield Label("⚡ SCOPEFORGE", id="brand-logo")
        yield Label(f" {self.model_name} ", id="badge-model", classes="header-badge")
        yield Label(f" {self.mode.upper()} ", id=f"badge-mode-{self.mode}", classes="header-badge")
        yield Label(f" {self.scope[:20]} ", id="badge-scope", classes="header-badge")
        yield Label(f" {self.active_agent} ", id="badge-agent", classes="header-badge")
        yield Label(f" {self.tokens} tok ", id="badge-cost", classes="header-badge")

    def watch_model_name(self, value: str):
        try:
            self.query_one("#badge-model", Label).update(f" {value} ")
        except Exception:
            pass

    def watch_mode(self, value: str):
        try:
            lbl = self.query_one("[id^='badge-mode']", Label)
            lbl.id = f"badge-mode-{value}"
            lbl.update(f" {value.upper()} ")
        except Exception:
            pass

    def watch_scope(self, value: str):
        try:
            self.query_one("#badge-scope", Label).update(f" {value[:20]} ")
        except Exception:
            pass

    def watch_active_agent(self, value: str):
        try:
            self.query_one("#badge-agent", Label).update(f" {value.capitalize()} ")
        except Exception:
            pass

    def watch_tokens(self, value: int):
        try:
            self.query_one("#badge-cost", Label).update(f" {value:,} tok ")
        except Exception:
            pass

    def watch_cost(self, value: float):
        try:
            self.query_one("#badge-cost", Label).update(f" {self.tokens:,} tok ")
        except Exception:
            pass
