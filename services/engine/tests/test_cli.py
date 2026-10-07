"""Tests for Claude Code & AGY style interactive CLI and slash commands."""
import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from prompt_toolkit.document import Document
from scopeforge_engine.cli import ScopeForgeCLI, ScopeForgeSlashCompleter
from scopeforge_engine.middleware.approval import ApprovalGateMiddleware
from scopeforge_engine.middleware.base import ToolApprovalRequest
from scopeforge_engine.middleware.pipeline import create_default_pipeline


def test_cli_slash_completer():
    """Verify slash command auto-completer produces completions and metadata."""
    completer = ScopeForgeSlashCompleter()
    doc = Document("/mo")
    completions = list(completer.get_completions(doc, None))
    cmds = [c.text for c in completions]
    assert "/mode" in cmds
    assert "/model" in cmds
    # Check display metadata is populated
    mode_comp = next(c for c in completions if c.text == "/mode")
    assert "ScopeGate" in mode_comp.display_meta_text


def test_pipeline_get_middleware_by_name_and_type():
    """Verify get_middleware retrieves interceptors by name or class type."""
    pipe = create_default_pipeline()
    gate_by_name = pipe.get_middleware("ApprovalGate")
    assert gate_by_name is not None
    assert isinstance(gate_by_name, ApprovalGateMiddleware)

    gate_by_type = pipe.get_middleware(ApprovalGateMiddleware)
    assert gate_by_type is not None
    assert gate_by_type is gate_by_name

    assert pipe.get_middleware("NonExistent") is None


def test_cli_initialization_and_banner():
    """Verify CLI initializes subsystems and renders banner without errors."""
    cli = ScopeForgeCLI(mode="redteam", scope=["target.com", "*.target.com"])
    assert cli.sec_mode == "redteam"
    assert "target.com" in cli.current_scope
    assert cli.pipeline.get_scope_gate().mode == "redteam"

    # Verify banner prints cleanly
    cli.print_banner()

    # Verify prompt HTML tag contains active mode
    prompt_html = cli.get_prompt_text()
    assert "REDTEAM" in prompt_html.formatted_text[0][1] or any("REDTEAM" in part[1] for part in prompt_html.formatted_text)


def test_cli_slash_commands(tmp_path: Path, monkeypatch):
    """Verify all CLI slash commands execute cleanly without exceptions."""
    monkeypatch.chdir(tmp_path)
    cli = ScopeForgeCLI()

    # /help
    assert cli.handle_slash_command("/help") is True

    # /mode
    assert cli.handle_slash_command("/mode audit") is True
    assert cli.sec_mode == "audit"

    # /scope
    assert cli.handle_slash_command("/scope newtarget.com") is True
    assert "newtarget.com" in cli.current_scope

    # /agent
    assert cli.handle_slash_command("/agent recon") is True
    assert cli.pending_agent == "recon"

    # /cost
    assert cli.handle_slash_command("/cost") is True

    # /doctor
    assert cli.handle_slash_command("/doctor") is True

    # /init
    assert cli.handle_slash_command("/init") is True
    assert (tmp_path / "SCOPEFORGE.md").exists()
    # Second init warns without overwrite
    assert cli.handle_slash_command("/init") is True

    # /diff & /commit
    assert cli.handle_slash_command("/diff") is True
    assert cli.handle_slash_command("/commit test commit") is True

    # /compact
    assert cli.handle_slash_command("/compact") is True

    # /wiki & /rag
    assert cli.handle_slash_command("/wiki") is True
    assert cli.handle_slash_command("/rag test query") is True

    # /exit returns False to break REPL loop
    assert cli.handle_slash_command("/exit") is False


def test_cli_model_switching():
    """Verify model selection via /model slash command."""
    cli = ScopeForgeCLI()
    initial_name = cli.provider_mgr.active_provider_name

    # List models
    assert cli.handle_slash_command("/model") is True

    # Query with nonexistent model
    assert cli.handle_slash_command("/model nonexistent-model-xyz") is True
    assert cli.provider_mgr.active_provider_name == initial_name

    # Query with matching keyword if available
    providers = cli.provider_mgr.list_providers()
    if providers:
        target_name = providers[0].name
        assert cli.handle_slash_command(f"/model {target_name}") is True
        assert cli.provider_mgr.active_provider_name == target_name

        # Query by 1-based number index (e.g. /model 1)
        assert cli.handle_slash_command("/model 1") is True
        assert cli.provider_mgr.active_provider_name == providers[0].name


def test_cli_routes_to_tui_on_subcommand(monkeypatch):
    """Verify running `scopeforge tui` routes to Textual dashboard instead of executing mission 'tui'."""
    from scopeforge_engine.cli import main
    import sys

    monkeypatch.setattr(sys, "argv", ["scopeforge", "tui"])
    mock_run = MagicMock()
    monkeypatch.setattr("scopeforge_engine.tui.app.ScopeForgeTUIApp.run", mock_run)
    main()
    assert mock_run.called


def test_cli_terminal_approval_callback():
    """Verify HITL terminal approval handles auto-approve and user confirmation."""
    cli = ScopeForgeCLI(auto_approve=True)
    req = ToolApprovalRequest(
        request_id="apr_test",
        agent_name="ReconAgent",
        tool_name="bash_security_exec",
        tool_args={"command": "curl -I https://example.com"},
        reason="Testing",
    )
    # Auto-approve active
    assert cli._terminal_approval_callback(req) is True

    # Manual mode: user says yes
    cli.auto_approve = False
    with patch("builtins.input", return_value="y"):
        assert cli._terminal_approval_callback(req) is True

    # Manual mode: user says no
    with patch("builtins.input", return_value="n"):
        assert cli._terminal_approval_callback(req) is False

    # Manual mode: user says always
    with patch("builtins.input", return_value="a"):
        assert cli._terminal_approval_callback(req) is True
        assert cli.auto_approve is True


@pytest.mark.asyncio
async def test_cli_one_shot_execution():
    """Verify one-shot mission execution through CLI."""
    cli = ScopeForgeCLI(mode="plan")

    # Mock orchestrator run to return predictable final state
    mock_state = {
        "active_agent": "Supervisor",
        "messages": [
            MagicMock(content="### Security Assessment Complete\nTarget verified.")
        ],
        "findings": [
            {
                "id": "SF-001",
                "title": "CORS Wildcard Header",
                "severity": "HIGH",
                "cvss_score": 8.1,
                "target": "target.com",
                "description": "Access-Control-Allow-Origin: * was returned.",
            }
        ],
    }

    with patch.object(cli.orchestrator, "run", return_value=mock_state):
        await cli.execute_mission("audit target.com")
        assert len(cli.chat_history) > 0
        assert cli.total_tokens > 0


def test_cli_model_add_positional(tmp_path: Path):
    """Verify adding custom model using positional slash command syntax."""
    cli = ScopeForgeCLI()
    res = cli.handle_slash_command(
        "/model add my-deepseek deepseek-chat https://api.deepseek.com/v1 sk-test-key custom"
    )
    assert res is True
    active = cli.provider_mgr.get_active_config()
    assert active.name == "my-deepseek"
    assert active.model == "deepseek-chat"
    assert active.api_base == "https://api.deepseek.com/v1"
    assert active.api_key == "sk-test-key"


def test_cli_model_add_interactive_wizard():
    """Verify adding custom model using interactive wizard prompts."""
    cli = ScopeForgeCLI()
    wizard_inputs = [
        "wizard-claude",                       # 1. Model Name
        "anthropic",                           # 2. Provider Type
        "claude-3-7-sonnet-20250219",          # 3. Model ID
        "https://api.justwoker.icu",           # 4. Base URL
        "sk-wizard-key",                       # 5. API Key
    ]
    with patch("builtins.input", side_effect=wizard_inputs):
        res = cli.handle_slash_command("/model add")
        assert res is True

    active = cli.provider_mgr.get_active_config()
    assert active.name == "wizard-claude"
    assert active.model == "claude-3-7-sonnet-20250219"
    assert active.api_base == "https://api.justwoker.icu"
    assert active.api_key == "sk-wizard-key"


def test_cli_config_commands():
    """Verify /config inspections and /config set updates."""
    cli = ScopeForgeCLI()
    # View config
    assert cli.handle_slash_command("/config") is True

    # Set key, model, base
    assert cli.handle_slash_command("/config set key sk-new-custom-key") is True
    assert cli.provider_mgr.get_active_config().api_key == "sk-new-custom-key"

    assert cli.handle_slash_command("/config set model gpt-4o-custom") is True
    assert cli.provider_mgr.get_active_config().model == "gpt-4o-custom"

    assert cli.handle_slash_command("/config set base https://proxy.custom.io/v1") is True
    assert cli.provider_mgr.get_active_config().api_base == "https://proxy.custom.io/v1"

