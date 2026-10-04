"""Comprehensive test suite for the new ScopeForge LangGraph, Textual, RAG, Wiki, MCP, and A2A suite."""
import json
import pytest
from scopeforge_engine.a2a.bus import A2ABus
from scopeforge_engine.a2a.protocol import A2AIntent, A2AMessage
from scopeforge_engine.agents.custom_agent import CustomAgentConfig, CustomAgentLoader
from scopeforge_engine.agents.graph import MultiAgentSecOpsOrchestrator
from scopeforge_engine.llm_providers.manager import ProviderManager
from scopeforge_engine.llm_providers.models import ProviderType
from scopeforge_engine.middleware.audit import AuditLoggingMiddleware
from scopeforge_engine.middleware.pipeline import MiddlewarePipeline
from scopeforge_engine.middleware.redaction import RedactionMiddleware
from scopeforge_engine.middleware.scope_gate import ScopeGateMiddleware
from scopeforge_engine.rag.engine import LlamaSecRAG
from scopeforge_engine.sec_tools.tools import (
    cve_advisory_search,
    evidence_recorder,
    falsifiable_poc_runner,
    recon_port_scan,
    sast_code_audit,
    web_surface_probe,
)
from scopeforge_engine.wiki.store import SecWiki


def test_provider_manager():
    mgr = ProviderManager()
    providers = mgr.list_providers()
    assert len(providers) >= 6
    assert mgr.set_active_provider("mock-secops")
    assert mgr.active_provider_name == "mock-secops"
    chat_model = mgr.get_chat_model()
    assert chat_model is not None


def test_custom_provider_normalization_and_deduplication():
    # Test key sanitization and deduplication
    doubled = "apx_live_test12345apx_live_test12345"
    assert ProviderManager._sanitize_api_key(doubled) == "apx_live_test12345"

    bearer_paste = "Bearer apx_live_test67890"
    assert ProviderManager._sanitize_api_key(bearer_paste) == "apx_live_test67890"

    curl_paste = "curl -H 'Authorization: Bearer sk-ant-secret123' https://api.anthropic.com"
    assert ProviderManager._sanitize_api_key(curl_paste) == "sk-ant-secret123"

    # Test base URL normalization (for CUSTOM: no path appending, directly use that api_base)
    assert ProviderManager._normalize_base_for_provider(ProviderType.CUSTOM, "https://api.apmix.ai") == "https://api.apmix.ai"
    assert ProviderManager._normalize_base_for_provider(ProviderType.CUSTOM, "https://api.apmix.ai/v1") == "https://api.apmix.ai/v1"
    assert ProviderManager._normalize_base_for_provider(ProviderType.CUSTOM, "https://api.apmix.ai/v1/chat/completions") == "https://api.apmix.ai/v1"
    assert ProviderManager._normalize_base_for_provider(ProviderType.OPENROUTER, "https://openrouter.ai") == "https://openrouter.ai/api/v1"
    assert ProviderManager._normalize_base_for_provider(ProviderType.OLLAMA, "http://localhost:11434") == "http://localhost:11434/v1"

    # Test add_custom_provider with third-party proxy for Claude
    mgr = ProviderManager()
    cfg = mgr.add_custom_provider(
        name="test-apmix-claude",
        model="claude-sonnet-4-6-free",
        api_key="apx_live_testkeyapx_live_testkey",
        api_base="https://api.apmix.ai/v1",
        set_active=False,
    )
    assert cfg.provider == ProviderType.CUSTOM
    assert cfg.api_base == "https://api.apmix.ai/v1"
    assert cfg.api_key == "apx_live_testkey"

    # Test self-healing migration of legacy misconfigured proxy
    from scopeforge_engine.llm_providers.models import LLMConfig
    legacy_cfg = LLMConfig(
        name="test-legacy-claude",
        provider=ProviderType.ANTHROPIC,
        model="claude-sonnet-4-6-free",
        api_key="apx_live_heal123apx_live_heal123",
        api_base="https://api.apmix.ai",
    )
    healed = ProviderManager._migrate_config(legacy_cfg)
    assert healed.provider == ProviderType.CUSTOM
    # Custom directly uses configured api_base without path appending
    assert healed.api_base == "https://api.apmix.ai"
    assert healed.api_key == "apx_live_heal123"

    # Clean up test provider
    mgr.providers.pop("test-apmix-claude", None)
    mgr.save_config()


def test_scopegate_middleware():
    gate = ScopeGateMiddleware(authorized_scopes=["authorized.example"], mode="plan")
    
    # In plan mode, network tools must be rejected
    proceed, reason, _ = gate.before_tool("recon_port_scan", {"target": "authorized.example"}, {})
    assert not proceed
    assert "PLAN" in reason

    # Switch to live mode
    gate.set_mode("live")
    proceed, _, _ = gate.before_tool("recon_port_scan", {"target": "authorized.example"}, {})
    assert proceed

    # Out of scope target must be rejected
    proceed, reason, _ = gate.before_tool("recon_port_scan", {"target": "unauthorized.badsite.com"}, {})
    assert not proceed
    assert "NOT in authorized scope" in reason


def test_redaction_middleware():
    redact = RedactionMiddleware()
    text = "Authorization: Bearer sk-1234567890abcdef1234567890 and secret='super_secret_password'"
    cleaned = redact._redact_text(text)
    assert "sk-12345" not in cleaned
    assert "[REDACTED" in cleaned


def test_sec_tools():
    # Recon tool
    res = recon_port_scan.invoke({"target": "authorized.example", "ports": "80,443"})
    data = json.loads(res)
    assert data["target"] == "authorized.example"

    # SAST tool
    code = "query = f'SELECT * FROM accounts WHERE id = {user_id}'"
    sast_res = sast_code_audit.invoke({"code_snippet_or_file": code})
    sast_data = json.loads(sast_res)
    assert sast_data["findings_count"] > 0
    assert "SQL Injection" in sast_data["findings"][0]["title"]

    # CVE tool
    cve_res = cve_advisory_search.invoke({"query": "CVE-2024-3400"})
    cve_data = json.loads(cve_res)
    assert cve_data["results_count"] > 0

    # PoC tool
    poc_res = falsifiable_poc_runner.invoke({
        "hypothesis": "Header reflection vulnerability",
        "target": "authorized.example",
        "payload_type": "header_reflection",
    })
    poc_data = json.loads(poc_res)
    assert "CONFIRMED" in poc_data["verdict"]


def test_llama_rag():
    rag = LlamaSecRAG()
    hits = rag.query("OWASP Injection SQLi", top_k=2)
    assert len(hits) > 0
    prompt_context = rag.get_context_for_prompt("SSRF vulnerability")
    assert "SSRF" in prompt_context


def test_sec_wiki():
    wiki = SecWiki()
    pages = wiki.list_pages()
    assert "preferences" in pages
    assert "targets" in pages
    prefs = wiki.get_user_preferences()
    assert "Preferences" in prefs


def test_a2a_bus_and_protocol():
    bus = A2ABus()
    received = []
    bus.subscribe(lambda m: received.append(m))

    msg = bus.send(
        sender="ReconAgent",
        recipient="AuditAgent",
        intent=A2AIntent.TASK_DELEGATION,
        payload={"target": "authorized.example"},
    )
    assert msg.verify()
    assert len(received) == 1
    assert received[0].sender == "ReconAgent"


@pytest.mark.asyncio
async def test_multiagent_orchestrator():
    orchestrator = MultiAgentSecOpsOrchestrator()
    state = await orchestrator.run("Perform a security audit and port scan on authorized.example", mode="live")
    assert len(state["messages"]) >= 2
    assert len(state["a2a_log"]) >= 1
    assert state["active_agent"] == "recon"


@pytest.mark.asyncio
async def test_tui_rendering_and_interaction():
    from scopeforge_engine.tui.app import ScopeForgeTUIApp
    app = ScopeForgeTUIApp()
    async with app.run_test(size=(120, 36)) as pilot:
        # Verify focused on prompt-input
        assert pilot.app.focused is not None
        # Verify header rendered
        header = pilot.app.query_one("#header-bar")
        assert header.region.height == 3
        # Verify chat and sidebar rendered
        chat = pilot.app.query_one("#chat-stream")
        assert chat.region.height > 10
        sidebar = pilot.app.query_one("#sidebar")
        assert sidebar.region.height > 10
        # Verify prompt bar rendered
        prompt = pilot.app.query_one("#prompt-bar")
        assert prompt.region.height == 4
        # Test keyboard cycle F3
        await pilot.press("f3")
        await pilot.pause()
        assert pilot.app.sec_mode == "artifacts"

        # Test slash commands
        pilot.app.handle_user_input("/skill list")
        await pilot.pause()
        pilot.app.handle_user_input("/config")
        await pilot.pause()
        pilot.app.handle_user_input("/cost")
        await pilot.pause()
        pilot.app.handle_user_input("/status")
        await pilot.pause()


def test_skills_manager():
    from scopeforge_engine.skills.manager import SkillManager
    mgr = SkillManager()
    skills = mgr.list_skills()
    assert len(skills) >= 4
    names = [s.name for s in skills]
    assert "subdomain-recon" in names
    assert "api-idor-audit" in names

    # Test auto matching
    matches = mgr.auto_match_skills("Let us test subdomain enumeration and dns")
    assert len(matches) > 0
    assert matches[0].name == "subdomain-recon"

    # Test prompt augmentation
    instructions = mgr.get_prompt_instructions("idor on api")
    assert "BOLA" in instructions or "IDOR" in instructions

    # Test auto matching for linkedin skills
    li_matches = mgr.auto_match_skills("write a linkedin post about AI")
    if any(s.name == "li-post" for s in skills):
        assert len(li_matches) > 0
        assert li_matches[0].name == "li-post"


def test_mcp_customization(tmp_path):
    from scopeforge_engine.mcp_bridge import MCPBridge
    mcp = MCPBridge(home=tmp_path)
    servers = mcp.list_servers()
    assert len(servers) >= 2

    # Add a new custom MCP server
    mcp.registry.add_server("test-custom-mcp", "python -m custom_server")
    mcp.enable_server("test-custom-mcp")
    enabled = mcp.registry.get_enabled()
    assert any(s.name == "test-custom-mcp" for s in enabled)

    tools = mcp.get_langchain_tools()
    assert len(tools) >= 1


def test_claude_code_developer_tools(tmp_path):
    from scopeforge_engine.sec_tools import (
        view_file,
        edit_file,
        write_file,
        glob_files,
        grep_search,
        git_diff_tool,
        git_status_tool,
    )

    test_file = tmp_path / "hello.py"
    # Write tool
    res = write_file.invoke({"file_path": str(test_file), "content": "def hello():\n    print('world')\n"})
    data = json.loads(res)
    assert data["status"] == "SUCCESS"

    # View tool
    res = view_file.invoke({"file_path": str(test_file), "start_line": 1, "end_line": 2})
    data = json.loads(res)
    assert "hello()" in data["content"]
    assert data["total_lines"] == 2

    # Edit tool
    res = edit_file.invoke({
        "file_path": str(test_file),
        "target_content": "print('world')",
        "replacement_content": "print('universe')",
    })
    data = json.loads(res)
    assert data["status"] == "SUCCESS"
    assert "universe" in test_file.read_text()

    # Glob tool
    res = glob_files.invoke({"pattern": "*.py", "directory": str(tmp_path)})
    data = json.loads(res)
    assert "hello.py" in data["files"]

    # Grep tool
    res = grep_search.invoke({"query": "universe", "directory": str(tmp_path)})
    data = json.loads(res)
    assert data["matches_count"] >= 1
    assert data["matches"][0]["file"] == "hello.py"

    # Git tools
    diff_res = json.loads(git_diff_tool.invoke({"staged": False}))
    assert "has_changes" in diff_res
    status_res = json.loads(git_status_tool.invoke({}))
    assert "status" in status_res


@pytest.mark.asyncio
async def test_dev_agent_routing():
    orchestrator = MultiAgentSecOpsOrchestrator()
    # Test dev agent routing
    state = await orchestrator.run("git diff and check modified files", mode="plan")
    assert state["active_agent"] == "dev"
    assert len(state["messages"]) > 0
    assert "DevAgent" in str(state["messages"][-1].content)


@pytest.mark.asyncio
async def test_claude_code_tui_slash_commands():
    from scopeforge_engine.tui.app import ScopeForgeTUIApp
    app = ScopeForgeTUIApp()
    async with app.run_test() as pilot:
        # Test /doctor
        pilot.app.handle_user_input("/doctor")
        await pilot.pause()
        # Test /diff
        pilot.app.handle_user_input("/diff")
        await pilot.pause()
        # Test /compact
        pilot.app.handle_user_input("/compact")
        await pilot.pause()
        # Test /pr
        pilot.app.handle_user_input("/pr")
        await pilot.pause()
        # Test /model openrouter dynamic configuration
        pilot.app.handle_user_input("/model openrouter openrouter/free")
        await pilot.pause()
        assert "openrouter" in pilot.app.provider_mgr.active_provider_name


@pytest.mark.asyncio
async def test_general_llm_qa():
    orchestrator = MultiAgentSecOpsOrchestrator()
    # General question should route to supervisor, not specialized security or dev agents
    state = await orchestrator.run("What is the difference between TCP and UDP in computer networks?")
    assert state["active_agent"] == "supervisor"
    assert len(state["messages"]) > 0
    content = str(state["messages"][-1].content)
    assert len(content) > 0


@pytest.mark.asyncio
async def test_interactive_model_config_modal():
    from scopeforge_engine.tui.app import ScopeForgeTUIApp
    from scopeforge_engine.tui.screens.model_screen import ModelPickerModal
    from textual.widgets import Button, Input, OptionList
    app = ScopeForgeTUIApp()
    async with app.run_test() as pilot:
        # 1. Open model screen via /config model
        pilot.app.handle_user_input("/config model")
        await pilot.pause()
        assert isinstance(pilot.app.screen, ModelPickerModal)
        modal = pilot.app.screen

        # Verify OptionList contains model presets
        opt_list = modal.query_one("#model-option-list", OptionList)
        assert opt_list.option_count >= 8

        # Test selecting preset via keyboard shortcut '2' (Nemotron 550B Free)
        await pilot.press("2")
        await pilot.pause()
        assert "nemotron" in pilot.app.provider_mgr.active_provider_name

        # 2. Re-open and switch to Custom view
        pilot.app.handle_user_input("/model")
        await pilot.pause()
        assert isinstance(pilot.app.screen, ModelPickerModal)
        modal = pilot.app.screen

        btn_custom = modal.query_one("#btn-go-custom", Button)
        btn_custom.press()
        await pilot.pause()

        # Fill custom model using Name, Model ID, API Key, Base URL
        name_input = modal.query_one("#inp-cfg-name", Input)
        name_input.value = "my-custom-endpoint"

        model_input = modal.query_one("#inp-cfg-model", Input)
        model_input.value = "deepseek/deepseek-chat"

        key_input = modal.query_one("#inp-cfg-key", Input)
        key_input.value = "sk-custom-secret-123"

        base_input = modal.query_one("#inp-cfg-base", Input)
        base_input.value = "https://api.deepseek.com/v1"

        # Apply and activate ("Save & Select")
        btn_save = modal.query_one("#btn-save-activate", Button)
        btn_save.press()
        await pilot.pause()

        active_cfg = pilot.app.provider_mgr.get_active_config()
        assert active_cfg.name == "my-custom-endpoint"
        assert active_cfg.model == "deepseek/deepseek-chat"
        assert active_cfg.api_key == "sk-custom-secret-123"
        assert active_cfg.api_base == "https://api.deepseek.com/v1"

        # 3. Verify it is now in the OptionList and can be selected
        pilot.app.handle_user_input("/model")
        await pilot.pause()
        assert isinstance(pilot.app.screen, ModelPickerModal)
        modal = pilot.app.screen
        opt_list = modal.query_one("#model-option-list", OptionList)
        assert any("my-custom-endpoint" in opt.prompt for opt in opt_list._options)
        modal.dismiss(None)
        await pilot.pause()

        # 4. Test /model add command
        pilot.app.handle_user_input("/model add fast-qwen qwen/qwen-2.5 sk-test https://api.together.xyz/v1")
        await pilot.pause()
        assert pilot.app.provider_mgr.active_provider_name == "fast-qwen"
        assert pilot.app.provider_mgr.get_active_config().api_base == "https://api.together.xyz/v1"

        # 5. Test inline /config set commands
        pilot.app.handle_user_input("/config set model deepseek/deepseek-r1:free")
        await pilot.pause()
        assert pilot.app.provider_mgr.get_active_config().model == "deepseek/deepseek-r1:free"

        pilot.app.handle_user_input("/config set temp 0.5")
        await pilot.pause()
        assert pilot.app.provider_mgr.get_active_config().temperature == 0.5

        # 6. Test /config free instant switch
        pilot.app.handle_user_input("/config free")
        await pilot.pause()
        assert pilot.app.provider_mgr.active_provider_name == "openrouter-free"


def test_bash_cli_and_google_web_search_tools():
    """Verify bash_cli sandbox execution, dangerous pattern guards, and google_web_search."""
    from scopeforge_engine.sec_tools import bash_cli, google_web_search

    # 1. Test bash_cli success
    res_bash = json.loads(bash_cli.invoke({"command": "echo 'Hello ScopeForge'"}))
    assert res_bash.get("success") is True
    assert res_bash.get("return_code") == 0
    assert "Hello ScopeForge" in res_bash.get("stdout")

    # 2. Test dangerous pattern block
    res_blocked = json.loads(bash_cli.invoke({"command": "rm -rf /"}))
    assert res_blocked.get("success") is False
    assert "dangerous" in res_blocked.get("error").lower()

    # 3. Test google_web_search live returns results
    res_search = json.loads(google_web_search.invoke({"query": "python", "max_results": 3}))
    assert res_search.get("results_count") > 0
    assert len(res_search.get("results")) > 0
    first_hit = res_search.get("results")[0]
    assert "title" in first_hit
    assert "url" in first_hit


@pytest.mark.asyncio
async def test_commercial_tui_slash_commands_and_routing():
    """Verify $ <cmd> prefix, /search, /bash, /mcp tools, and DevAgent routing."""
    from textual.widgets import RichLog
    from scopeforge_engine.agents.graph import MultiAgentSecOpsOrchestrator
    from scopeforge_engine.tui.app import ScopeForgeTUIApp
    from scopeforge_engine.tui.widgets.chat_log import ChatStream

    # 1. Test Orchestrator DevAgent routing for web search and bash
    orchestrator = MultiAgentSecOpsOrchestrator()
    search_state = await orchestrator.run("search the web for fast python frameworks")
    assert search_state["active_agent"] == "dev"
    assert "Web Search Results" in search_state["messages"][-1].content or "Search" in search_state["messages"][-1].content

    cmd_state = await orchestrator.run("$ echo 'DevAgent Terminal Command'")
    assert cmd_state["active_agent"] == "dev"
    assert "DevAgent Terminal Command" in cmd_state["messages"][-1].content

    # 2. Test TUI slash commands execution
    app = ScopeForgeTUIApp()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatStream)

        # Test /search
        app.handle_user_input("/search python asyncio")
        await pilot.pause()
        assert any("Web Search Results" in msg.get("content", "") for msg in chat.transcript)

        # Test /bash
        app.handle_user_input("/bash echo 'Running via /bash'")
        await pilot.pause()
        assert any("Running via /bash" in msg.get("content", "") for msg in chat.transcript)

        # Test $ prefix
        app.handle_user_input("$ echo 'Running via dollar prefix'")
        await pilot.pause()
        assert any("Running via dollar prefix" in msg.get("content", "") for msg in chat.transcript)

        # Test /mcp add and enable
        app.handle_user_input("/mcp add custom-scanner npx custom-sec-scanner")
        await pilot.pause()
        assert any("Added and enabled MCP server" in msg.get("content", "") for msg in chat.transcript)

        # Test /mcp tools
        app.handle_user_input("/mcp tools")
        await pilot.pause()
        assert any("Active MCP Registered Tools" in msg.get("content", "") for msg in chat.transcript)


@pytest.mark.asyncio
async def test_model_picker_contentswitcher_and_ctrl_shortcuts():
    from textual.widgets import ContentSwitcher
    from scopeforge_engine.tui.app import ScopeForgeTUIApp
    from scopeforge_engine.tui.screens.model_screen import ModelPickerModal

    app = ScopeForgeTUIApp()
    
    # 1. Verify Ctrl+[key] bindings
    binding_keys = [b.key for b in app.BINDINGS]
    assert "ctrl+h" in binding_keys
    assert "ctrl+m" in binding_keys
    assert "ctrl+b" in binding_keys
    assert "ctrl+w" in binding_keys
    assert "ctrl+l" in binding_keys

    # 2. Verify ModelPickerModal with ContentSwitcher (no duplicate views/pages)
    async with app.run_test() as pilot:
        modal = ModelPickerModal(current_provider="openrouter-free")
        await app.push_screen(modal)
        await pilot.pause()

        # Ensure ContentSwitcher exists and defaults to view-list
        switcher = modal.query_one("#model-view-switcher", ContentSwitcher)
        assert switcher is not None
        assert switcher.current == "view-list"

        # Trigger switch to custom
        modal._switch_to_custom()
        await pilot.pause()
        assert switcher.current == "view-custom"

        # Trigger switch back to list
        modal._switch_to_list()
        await pilot.pause()
        assert switcher.current == "view-list"


@pytest.mark.asyncio
async def test_dynamic_recon_and_audit_node_extraction():
    orchestrator = MultiAgentSecOpsOrchestrator()

    # Dynamic target & port extraction in ReconAgent
    recon_res = await orchestrator.run("run recon port scan on scanme.org with ports 22,80,443", mode="live")
    assert recon_res["active_agent"] == "recon"
    recon_text = recon_res["messages"][-1].content
    assert "scanme.org" in recon_text
    assert "22,80,443" in recon_text

    # Dynamic CVE and code block extraction in AuditAgent
    audit_res = await orchestrator.run(
        "run audit on CVE-2023-44487 with code:\n```python\nimport os\nos.system(user_cmd)\n```",
        mode="live",
    )
    assert audit_res["active_agent"] == "audit"
    audit_text = audit_res["messages"][-1].content
    assert "CVE-2023-44487" in audit_text


def test_clipboard_duplicate_paste_prevention():
    from scopeforge_engine.tui.clipboard import clean_pasted_text, deduplicate_doubled_text
    from scopeforge_engine.tui.widgets.clipboard_input import ClipboardInput

    # 1. Test string deduplication
    assert deduplicate_doubled_text("apx_live_test123apx_live_test123") == "apx_live_test123"
    assert deduplicate_doubled_text("https://api.deepseek.comhttps://api.deepseek.com") == "https://api.deepseek.com"
    assert clean_pasted_text("model-id-123model-id-123") == "model-id-123"

    # 2. Test widget debounce
    inp = ClipboardInput()
    assert inp._is_duplicate_paste("my-api-key") is False
    # Immediate second paste of same text within 450ms is detected as duplicate
    assert inp._is_duplicate_paste("my-api-key") is True
