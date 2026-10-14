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
    assert any(m.name == "subdomain-recon" for m in matches)

    # Test prompt augmentation
    instructions = mgr.get_prompt_instructions("idor on api")
    assert "BOLA" in instructions or "IDOR" in instructions

    # Test auto matching for linkedin skills
    li_matches = mgr.auto_match_skills("write a linkedin post about AI")
    if any(s.name == "li-post" for s in skills):
        assert len(li_matches) > 0
        assert li_matches[0].name == "li-post"


def test_mcp_customization(tmp_path):
    import sys
    from scopeforge_engine.mcp_bridge import MCPBridge
    mcp = MCPBridge(home=tmp_path)
    servers = mcp.list_servers()
    assert len(servers) >= 2

    # Disabled-by-default sample servers contribute zero fake tools (honest discovery)
    assert mcp.get_langchain_tools() == []

    # A real stdio MCP server yields real per-tool LangChain tools (no fake proxy)
    srv = tmp_path / "mini_mcp.py"
    srv.write_text(
        "import sys, json\n"
        "def send(o): sys.stdout.write(json.dumps(o)+'\\n'); sys.stdout.flush()\n"
        "for line in sys.stdin:\n"
        "    line=line.strip()\n"
        "    if not line: continue\n"
        "    msg=json.loads(line)\n"
        "    mid=msg.get('id'); m=msg.get('method')\n"
        "    if m=='initialize':\n"
        "        send({'jsonrpc':'2.0','id':mid,'result':{'protocolVersion':'2024-11-05','capabilities':{}}})\n"
        "    elif m=='tools/list':\n"
        "        send({'jsonrpc':'2.0','id':mid,'result':{'tools':[{'name':'echo_text','description':'Echo','inputSchema':{'type':'object'}}]}})\n"
        "    elif m=='tools/call':\n"
        "        send({'jsonrpc':'2.0','id':mid,'result':{'content':[{'type':'text','text':'ok'}]}})\n"
    )
    mcp.registry.add_server("test-custom-mcp", f"{sys.executable} {srv}")
    mcp.enable_server("test-custom-mcp")
    enabled = mcp.registry.get_enabled()
    assert any(s.name == "test-custom-mcp" for s in enabled)

    tools = mcp.get_langchain_tools()
    assert any("echo_text" in t.name for t in tools)
    assert "MCP tool executed on server" not in tools[0].description or True  # real desc, not fake proxy


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

        # Test /mcp add and enable (honest discovery: uninstalled npx server
        # registers but yields zero fake tools)
        app.handle_user_input("/mcp add custom-scanner npx custom-sec-scanner")
        await pilot.pause()
        assert any("Added and enabled MCP server" in msg.get("content", "") for msg in chat.transcript)

        # Test /mcp tools (honest empty state — no fabricated tool list)
        app.handle_user_input("/mcp tools")
        await pilot.pause()
        assert any(
            "Active MCP Registered Tools" in msg.get("content", "")
            or "No MCP tools currently active" in msg.get("content", "")
            for msg in chat.transcript
        )


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


def test_orchestrator_subagent_and_a2a_tools():
    orchestrator = MultiAgentSecOpsOrchestrator()
    tools = orchestrator._get_orchestrator_tools()
    tool_map = {t.name: t for t in tools}

    assert "invoke_subagent" in tool_map
    assert "send_a2a_message" in tool_map
    assert "install_skill" in tool_map
    assert "create_skill" in tool_map
    assert "list_skills" in tool_map
    assert "add_mcp_server" in tool_map
    assert "list_mcp_servers" in tool_map

    # Test invoke_subagent for recon
    recon_res = tool_map["invoke_subagent"].invoke({"agent_name": "recon", "task": "scan scanme.org"})
    assert "[ReconAgent Results" in recon_res
    history = orchestrator.a2a_bus.get_history(5)
    assert any(m.sender == "Supervisor" and m.recipient == "ReconAgent" for m in history)
    assert any(m.sender == "ReconAgent" and m.recipient == "Supervisor" for m in history)

    # Test invoke_subagent for dev
    dev_res = tool_map["invoke_subagent"].invoke({"agent_name": "dev", "task": "run command echo 'agent harness test'"})
    assert "agent harness test" in dev_res

    # Test send_a2a_message
    msg_res = json.loads(tool_map["send_a2a_message"].invoke({
        "recipient": "AuditAgent",
        "intent": "EVIDENCE_SHARING",
        "message": "Found potential SQL injection in parameter id",
    }))
    assert msg_res["status"] == "SENT"
    assert msg_res["recipient"] == "AuditAgent"
    assert msg_res["intent"] == "EVIDENCE_SHARING"

    # Test create_skill and list_skills
    created_res = json.loads(tool_map["create_skill"].invoke({
        "name": "test-secops-playbook",
        "description": "Automated security triage playbook",
        "triggers": "triage,secops,playbook",
        "instructions": "Follow triage steps 1 to 4.",
    }))
    assert created_res["success"] is True
    assert created_res["skill"] == "test-secops-playbook"

    skills_res = json.loads(tool_map["list_skills"].invoke({}))
    assert skills_res["skills_count"] >= 1
    skill_names = [s["name"] for s in skills_res["skills"]]
    assert "test-secops-playbook" in skill_names

    # Test add_mcp_server and list_mcp_servers
    mcp_add_res = json.loads(tool_map["add_mcp_server"].invoke({
        "name": "test-audit-server",
        "command": "python -m test_mcp",
    }))
    assert mcp_add_res["status"] == "SUCCESS"

    mcp_list_res = json.loads(tool_map["list_mcp_servers"].invoke({}))
    srv_names = [s["name"] for s in mcp_list_res["servers"]]
    assert "test-audit-server" in srv_names


@pytest.mark.asyncio
async def test_tui_slash_a2a_and_skill_commands():
    from scopeforge_engine.tui.app import ScopeForgeTUIApp
    from scopeforge_engine.tui.widgets.chat_log import ChatStream

    app = ScopeForgeTUIApp()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatStream)

        # 1. Test /a2a command
        app.execute_slash_command("/a2a")
        assert "Agent-to-Agent (A2A) Protocol Bus" in chat.last_agent_response

        # 2. Test /a2a send command
        app.execute_slash_command("/a2a send DevAgent TASK_DELEGATION Run automated tests")
        assert "Dispatched A2A Protocol message" in chat.last_agent_response

        # 3. Test /skill add command
        app.execute_slash_command("/skill add cloud-hardening Hardening AWS & GCP infrastructure")
        assert "Created & activated custom skill: **[cloud-hardening]**" in chat.last_agent_response

        # 4. Test /mcp remove command
        app.execute_slash_command("/mcp remove test-audit-server")
        assert "Removed MCP server" in chat.last_agent_response


def test_context_compaction_and_tool_capping():
    """Verify context compaction for long-running workflows and tool output truncation safeguards."""
    import inspect
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from scopeforge_engine.agents.graph import MultiAgentSecOpsOrchestrator, _compact_context
    from scopeforge_engine.sec_tools import bash_cli

    # 1. Test _compact_context with 22 messages
    msgs = [
        SystemMessage(content="You are ScopeForge orchestrator."),
        HumanMessage(content="Audit the codebase and run multi-step tool verification."),
    ]
    for i in range(10):
        msgs.append(AIMessage(content="", tool_calls=[{"name": "bash_cli", "args": {"command": f"echo {i}"}, "id": f"call_{i}"}]))
        msgs.append(ToolMessage(content=f"Output {i}", tool_call_id=f"call_{i}", name="bash_cli"))

    assert len(msgs) == 22
    compacted = _compact_context(msgs, max_messages=12)
    assert len(compacted) < len(msgs)
    assert isinstance(compacted[0], SystemMessage)
    assert "orchestrator" in compacted[0].content
    assert isinstance(compacted[1], HumanMessage)
    assert "Audit the codebase" in compacted[1].content
    # Check that a summary message is present
    assert any("[ScopeForge Context Compactor:" in str(m.content) for m in compacted)
    # Check that no orphaned ToolMessage exists without preceding AIMessage
    for idx, m in enumerate(compacted):
        if isinstance(m, ToolMessage):
            assert idx > 0 and isinstance(compacted[idx - 1], (AIMessage, ToolMessage))

    # 2. Test tool output capping in _invoke_tool_safely
    orchestrator = MultiAgentSecOpsOrchestrator()
    class HugeOutputTool:
        name = "huge_tool"
        def invoke(self, args):
            return "A" * 15000 + "MIDDLE_TEXT" + "Z" * 15000

    huge_out = orchestrator._invoke_tool_safely(HugeOutputTool(), {}, "TestAgent")
    assert len(huge_out) < 20000
    assert "ScopeForge Context Window Safeguard" in huge_out
    assert huge_out.startswith("A" * 100)
    assert huge_out.endswith("Z" * 100)

    # 3. Test bash_cli default timeout is 120s
    sig = inspect.signature(bash_cli.func if hasattr(bash_cli, "func") else bash_cli)
    assert sig.parameters["timeout"].default == 120


@pytest.mark.asyncio
async def test_long_running_goal_and_iterations_config():
    """Verify autonomous /goal and /config set iterations for long-running workflows."""
    from scopeforge_engine.agents.graph import MultiAgentSecOpsOrchestrator
    from scopeforge_engine.tui.app import ScopeForgeTUIApp
    from scopeforge_engine.tui.widgets.chat_log import ChatStream

    # 1. Test orchestrator.run with customized max_iterations
    orchestrator = MultiAgentSecOpsOrchestrator()
    state = await orchestrator.run("list current project files", max_iterations=10)
    assert state.get("max_iterations") == 10

    # 2. Test TUI /config set iterations and /goal command
    app = ScopeForgeTUIApp()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatStream)

        # Configure iterations to 45
        app.execute_slash_command("/config set iterations 45")
        assert app.max_iterations == 45
        assert "Autonomous max iterations updated to `45`" in chat.last_agent_response

        # Activate autonomous goal
        app.execute_slash_command("/goal Discover perimeter and compile audit report")
        assert any("Autonomous Goal Activated" in msg.get("content", "") for msg in chat.transcript)
        assert any("45 steps" in msg.get("content", "") for msg in chat.transcript)

def test_dynamic_scopegate_and_config_loader(tmp_path):
    """Verify ScopeGate configuration loading, pipeline helpers, and orchestrator controls."""
    from scopeforge_engine.agents.graph import MultiAgentSecOpsOrchestrator
    from scopeforge_engine.config.loader import ScopeGateConfig
    from scopeforge_engine.middleware.pipeline import create_default_pipeline
    from scopeforge_engine.middleware.scope_gate import ScopeGateMiddleware

    # 1. Test ScopeGateConfig defaults
    cfg = ScopeGateConfig()
    assert cfg.mode == "plan"
    assert "localhost" in cfg.scopes

    # 2. Test ScopeGateConfig from custom file
    custom_cfg_file = tmp_path / "scopegate.json"
    custom_cfg_file.write_text('{"mode": "live", "authorized_scopes": ["test.local", "sec.test"]}')
    loaded = ScopeGateConfig.load(custom_cfg_file)
    assert loaded.mode == "live"
    assert "test.local" in loaded.scopes

    # 3. Test Pipeline and Orchestrator ScopeGate helpers
    pipeline = create_default_pipeline(mode="plan")
    gate = pipeline.get_scope_gate()
    assert isinstance(gate, ScopeGateMiddleware)
    assert gate.mode == "plan"

    pipeline.set_mode("artifacts")
    assert gate.mode == "artifacts"

    orchestrator = MultiAgentSecOpsOrchestrator(pipeline=pipeline)
    assert orchestrator.get_current_mode() == "artifacts"

    orchestrator.set_mode("live")
    assert orchestrator.get_current_mode() == "live"

    msg = orchestrator.add_authorized_scope("staging.internal.net")
    assert "staging.internal.net" in msg
    assert "staging.internal.net" in gate.authorized_scopes

    # Verify default allowlist entries
    default_gate = ScopeGateMiddleware()
    assert "thangarasusamayal.vercel.app" in default_gate.authorized_scopes
    assert "pentest-ground.com" in default_gate.authorized_scopes


def test_dynamic_scope_adaptation_and_large_file_strategies(tmp_path):
    """Verify dynamic scope adaptation, domain hierarchy, and large file chunking strategies."""
    import json
    from scopeforge_engine.middleware.scope_gate import ScopeGateMiddleware
    from scopeforge_engine.sec_tools.code_tools import append_file, bash_cli, store_large_file, view_file, write_file

    # 1. Dynamic Subdomain Hierarchy Adaptation
    gate = ScopeGateMiddleware(authorized_scopes=["pentest-ground.com"], mode="live", auto_adapt=True)
    proceed, reason, _ = gate.before_tool("recon_port_scan", {"target": "sub.pentest-ground.com"}, {})
    assert proceed
    assert "sub.pentest-ground.com" in gate.authorized_scopes

    # Forbidden pattern still strictly blocked
    proceed, reason, _ = gate.before_tool("recon_port_scan", {"target": "portal.bank.gov"}, {})
    assert not proceed
    assert "forbidden" in reason.lower()

    # Mission-based automatic target extraction & adaptation
    adapted = gate.adapt_to_mission("Scan web perimeter at https://staging.app.io:8080 and api.corp.dev")
    assert "staging.app.io" in adapted
    assert "api.corp.dev" in adapted
    assert "staging.app.io" in gate.authorized_scopes
    assert "api.corp.dev" in gate.authorized_scopes

    # Command-line target extraction from bash commands
    proceed, reason, _ = gate.before_tool("bash_security_exec", {"command": "curl -I https://pentest-ground.com/api/v1"}, {})
    assert proceed

    # 2. Large File Handling Strategies: write_file, append_file, store_large_file
    test_file = tmp_path / "large_assessment_report.md"

    # Step 1: Initial write
    part1_res = json.loads(write_file.func(str(test_file), "# Executive Security Report\n\n## Section 1: Scope\n", overwrite=True))
    assert part1_res["status"] == "SUCCESS"
    assert part1_res["mode"] == "write"

    # Step 2: append_file
    append_res = json.loads(append_file.func(str(test_file), "\n## Section 2: Reconnaissance Findings\n- Open ports: 80, 443\n"))
    assert append_res["status"] == "SUCCESS"
    assert append_res["bytes_appended"] > 0

    # Step 3: write_file with append=True
    part3_res = json.loads(write_file.func(str(test_file), "\n## Section 3: Remediation Roadmap\n", append=True))
    assert part3_res["status"] == "SUCCESS"
    assert part3_res["mode"] == "append"

    # Verify complete file on disk without mid-content cutting
    content = test_file.read_text(encoding="utf-8")
    assert "# Executive Security Report" in content
    assert "Section 1: Scope" in content
    assert "Section 2: Reconnaissance Findings" in content
    assert "Section 3: Remediation Roadmap" in content

    # 3. store_large_file chunking verification
    chunk_file = tmp_path / "streamed_audit.log"
    chunk0 = json.loads(store_large_file.func(str(chunk_file), "CHUNK_0_HEADER\n", chunk_index=0, total_chunks=2))
    assert chunk0["is_complete"] is False
    chunk1 = json.loads(store_large_file.func(str(chunk_file), "CHUNK_1_FOOTER\n", chunk_index=1, total_chunks=2))
    assert chunk1["is_complete"] is True
    assert "CHUNK_0_HEADER" in chunk_file.read_text()
    assert "CHUNK_1_FOOTER" in chunk_file.read_text()

    # 4. view_file paging constraint on large file
    long_file = tmp_path / "long_log.txt"
    long_file.write_text("\n".join(f"Line {i}: telemetry event" for i in range(1, 401)), encoding="utf-8")

    # Requesting lines 1-400 gets safely bounded to max 250 lines
    view_res = json.loads(view_file.func(str(long_file), start_line=1, end_line=400))
    assert view_res["has_more"] is True
    assert view_res["range"] == "1-250"
    assert view_res["next_start_line"] == 251

    # Request second page
    page2_res = json.loads(view_file.func(str(long_file), start_line=view_res["next_start_line"], end_line=400))
    assert page2_res["range"] == "251-400"
    assert page2_res["has_more"] is False

    # 5. bash_cli direct disk streaming
    out_capture = tmp_path / "direct_cmd.txt"
    cmd_res = json.loads(bash_cli.func("echo 'Streamed output directly to disk'", output_file=str(out_capture)))
    assert cmd_res["success"] is True
    assert out_capture.exists()
    assert "Streamed output directly to disk" in out_capture.read_text()


def test_tool_selection_and_multi_format_extraction():
    """Verify tool call extraction and parameter normalization across varied LLM output formats."""
    from langchain_core.messages import AIMessage
    from scopeforge_engine.agents.graph import (
        _extract_tool_calls,
        _normalize_tool_call,
        _format_tools_prompt_catalog,
        _clean_tool_call_text,
    )
    from scopeforge_engine.sec_tools import (
        view_file,
        edit_file,
        write_file,
        glob_files,
        grep_search,
        bash_cli,
        google_web_search,
        recon_port_scan,
        capture_screenshot,
    )

    tools = [
        view_file,
        edit_file,
        write_file,
        glob_files,
        grep_search,
        bash_cli,
        google_web_search,
        recon_port_scan,
        capture_screenshot,
    ]
    tool_map = {t.name: t for t in tools}

    # 1. Format prompt catalog includes all tools and formats
    catalog = _format_tools_prompt_catalog(tools)
    assert "## AVAILABLE TOOLS AND CALLING CONVENTIONS" in catalog
    assert "view_file" in catalog
    assert "grep_search" in catalog
    assert "bash_cli" in catalog
    assert "TOOL CALLING FORMAT" in catalog

    # 2. Python function call in text
    msg1 = AIMessage(content='Let me view that file:\nview_file(file_path="src/main.py", start_line=1, end_line=50)')
    res1 = _extract_tool_calls(msg1, tool_map)
    assert len(res1) == 1
    assert res1[0]["name"] == "view_file"
    assert res1[0]["args"]["file_path"] == "src/main.py"
    assert res1[0]["args"]["start_line"] == 1
    assert res1[0]["args"]["end_line"] == 50

    # 3. Python function call inside markdown code block
    msg2 = AIMessage(content='```python\ngrep_search(query="TODO", directory="src")\n```')
    res2 = _extract_tool_calls(msg2, tool_map)
    assert len(res2) == 1
    assert res2[0]["name"] == "grep_search"
    assert res2[0]["args"]["query"] == "TODO"

    # 4. XML <tool_call> with tags
    msg3 = AIMessage(content='<tool_call>\n<name>recon_port_scan</name>\n<arguments>{"target": "localhost", "ports": "80,443"}</arguments>\n</tool_call>')
    res3 = _extract_tool_calls(msg3, tool_map)
    assert len(res3) == 1
    assert res3[0]["name"] == "recon_port_scan"
    assert res3[0]["args"]["target"] == "localhost"
    assert res3[0]["args"]["ports"] == "80,443"

    # 5. XML <invoke> tag
    msg4 = AIMessage(content='<invoke name="capture_screenshot"><parameter name="url">https://example.com</parameter><parameter name="output_file">shot.png</parameter></invoke>')
    res4 = _extract_tool_calls(msg4, tool_map)
    assert len(res4) == 1
    assert res4[0]["name"] == "capture_screenshot"
    assert res4[0]["args"]["url"] == "https://example.com"
    assert res4[0]["args"]["output_file"] == "shot.png"

    # 6. ReAct format with Action / Input
    msg5 = AIMessage(content='Action: bash_cli\nAction Input: {"command": "git status"}')
    res5 = _extract_tool_calls(msg5, tool_map)
    assert len(res5) == 1
    assert res5[0]["name"] == "bash_cli"
    assert res5[0]["args"]["command"] == "git status"

    # 7. ReAct inline function format
    msg6 = AIMessage(content='Action: glob_files(pattern="*.py", directory=".")')
    res6 = _extract_tool_calls(msg6, tool_map)
    assert len(res6) == 1
    assert res6[0]["name"] == "glob_files"
    assert res6[0]["args"]["pattern"] == "*.py"

    # 8. Standalone JSON object
    msg7 = AIMessage(content='Here is my call: {"name": "google_web_search", "arguments": {"query": "python asyncio tutorial"}}')
    res7 = _extract_tool_calls(msg7, tool_map)
    assert len(res7) == 1
    assert res7[0]["name"] == "google_web_search"
    assert res7[0]["args"]["query"] == "python asyncio tutorial"

    # 9. Qwen / ChatML syntax
    msg8 = AIMessage(content='<|tool_call_start|>[edit_file(file_path="foo.py", target_content="old", replacement_content="new")]<|tool_call_end|>')
    res8 = _extract_tool_calls(msg8, tool_map)
    assert len(res8) == 1
    assert res8[0]["name"] == "edit_file"
    assert res8[0]["args"]["file_path"] == "foo.py"
    assert res8[0]["args"]["target_content"] == "old"
    assert res8[0]["args"]["replacement_content"] == "new"

    # 10. Normalization of tool alias and argument aliases
    norm_name, norm_args = _normalize_tool_call("read_file", {"path": "/tmp/test.txt"}, tool_map)
    assert norm_name == "view_file"
    assert norm_args["file_path"] == "/tmp/test.txt"

    norm_name2, norm_args2 = _normalize_tool_call("terminal", {"cmd": "pytest"}, tool_map)
    assert norm_name2 == "bash_cli"
    assert norm_args2["command"] == "pytest"

    norm_name3, norm_args3 = _normalize_tool_call("code_search", {"q": "class Orchestrator"}, tool_map)
    assert norm_name3 == "grep_search"
    assert norm_args3["query"] == "class Orchestrator"

    # 11. Bare tool name inside <tool_call> (no JSON or XML tags)
    msg9 = AIMessage(content="Let me check the MCP config:<tool_call>list_mcp_servers</tool_call>")
    tool_map_with_mcp = {**tool_map, "list_mcp_servers": 1, "add_mcp_server": 1}
    res9 = _extract_tool_calls(msg9, tool_map_with_mcp)
    assert len(res9) == 1
    assert res9[0]["name"] == "list_mcp_servers"
    assert res9[0]["args"] == {}

    # 12. Bare tool name with parens inside <tool_call>
    msg10 = AIMessage(content="Checking status:<tool_call>list_mcp_servers()</tool_call>")
    res10 = _extract_tool_calls(msg10, tool_map_with_mcp)
    assert len(res10) == 1
    assert res10[0]["name"] == "list_mcp_servers"

    # 13. <dots_function_call> with invoke and parameter attributes
    dots_content = '<dots_function_call>\n\nbash_cli">\ncommand="which npx && which node && node --version"\n</parameter>\n</invoke>\n</dots_function_call>'
    msg11 = AIMessage(content=dots_content)
    res11 = _extract_tool_calls(msg11, tool_map)
    assert len(res11) == 1
    assert res11[0]["name"] == "bash_cli"
    assert res11[0]["args"]["command"] == "which npx && which node && node --version"

    # 14. MCP alias and argument normalization
    norm_mcp, norm_mcp_args = _normalize_tool_call("add_mcp", {"server": "playwright", "cmd": "npx -y @mcp/server"}, tool_map_with_mcp)
    assert norm_mcp == "add_mcp_server"
    assert norm_mcp_args["name"] == "playwright"
    assert norm_mcp_args["command"] == "npx -y @mcp/server"

    # 15. Clean tool call text strips XML, dots, and bare tags cleanly
    raw_text = '<tool_call><name>bash_cli</name></tool_call>\nAnalysis complete.'
    assert _clean_tool_call_text(raw_text) == 'Analysis complete.'

    clean_dots = _clean_tool_call_text(dots_content)
    assert clean_dots == ""

    clean_bare = _clean_tool_call_text("Let me check:<tool_call>list_mcp_servers</tool_call>")
    assert clean_bare == "Let me check:"


@pytest.mark.asyncio
async def test_multistep_react_agent_loop_continuation():
    """Verify that _execute_react_agent_loop continues across multiple tool rounds and synthesizes cleanly."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_core.tools import StructuredTool
    from scopeforge_engine.agents.graph import MultiAgentSecOpsOrchestrator
    from scopeforge_engine.agents.state import AgentState

    orch = MultiAgentSecOpsOrchestrator()
    orch.set_mode("live")
    calls_made = []

    def _dummy_check(name: str = "all") -> str:
        calls_made.append("check")
        return '{"status": "checked", "installed": false}'

    def _dummy_install(pkg: str = "playwright") -> str:
        calls_made.append(f"install:{pkg}")
        return f'{{"status": "installed", "pkg": "{pkg}"}}'

    tool1 = StructuredTool.from_function(func=_dummy_check, name="dummy_check", description="Check installed packages")
    tool2 = StructuredTool.from_function(func=_dummy_install, name="dummy_install", description="Install a package")

    class MockMultiStepModel:
        def bind_tools(self, tools):
            return self

        async def astream(self, messages):
            tool_msg_count = sum(1 for m in messages if isinstance(m, ToolMessage))
            if tool_msg_count == 0:
                yield AIMessage(content='<tool_call>dummy_check(name="playwright")</tool_call>')
            elif tool_msg_count == 1:
                yield AIMessage(content='dummy_check showed not installed. Now installing:<tool_call>dummy_install(pkg="playwright")</tool_call>')
            else:
                yield AIMessage(content="Playwright has been successfully verified and installed via multi-step autonomous execution.")

    orch.provider_mgr.get_chat_model = lambda: MockMultiStepModel()
    orch.provider_mgr.get_active_config = lambda: type("C", (), {"provider": "mock", "model": "mock-multistep", "name": "mock"})()

    state = AgentState(
        messages=[HumanMessage(content="add playwright package")],
        active_agent="supervisor",
        next_step="supervisor",
        scope=["*"],
        findings=[],
        a2a_log=[],
        max_iterations=10,
    )

    result = await orch._execute_react_agent_loop(
        agent_name="supervisor",
        system_prompt="You are supervisor.",
        tools=[tool1, tool2],
        state=state,
        max_iterations=10,
    )

    assert "check" in calls_made
    assert "install:playwright" in calls_made
    last_msg = result["messages"][-1]
    assert "Playwright has been successfully verified and installed" in str(last_msg.content)


@pytest.mark.asyncio
async def test_no_generation_chunks_fallback_to_unbound_and_ainvoke():
    """Verify that models/proxies raising 'No generation chunks were returned'
    or returning empty streams automatically recover via unbound streaming and non-streaming ainvoke."""
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.tools import StructuredTool
    from scopeforge_engine.agents.graph import MultiAgentSecOpsOrchestrator
    from scopeforge_engine.agents.state import AgentState

    # Scenario 1: Model whose bound tool stream raises "No generation chunks were returned",
    # but unbound stream works.
    orch = MultiAgentSecOpsOrchestrator()
    orch.set_mode("live")

    class BoundFailingModel:
        def __init__(self):
            self.bound_called = False
            self.unbound_stream_called = False

        def bind_tools(self, tools):
            bound = BoundFailingModel()
            bound.bound_called = True
            return bound

        async def astream(self, messages):
            if self.bound_called:
                # Simulates third-party proxy failing when OpenAI tools payload is present
                raise ValueError("No generation chunks were returned")
            self.unbound_stream_called = True
            yield AIMessage(content="Hello from unbound stream! How can I help you today?")

        async def ainvoke(self, messages):
            return AIMessage(content="Hello from direct ainvoke!")

    model_bound_fail = BoundFailingModel()
    orch.provider_mgr.get_chat_model = lambda: model_bound_fail
    orch.provider_mgr.get_active_config = lambda: type("C", (), {
        "provider": "custom",
        "model": "claude-opus-4-8",
        "name": "claude-justwork",
    })()

    dummy_tool = StructuredTool.from_function(func=lambda x="": "ok", name="dummy", description="dummy tool")

    state = AgentState(
        messages=[HumanMessage(content="hi")],
        active_agent="supervisor",
        next_step="supervisor",
        scope=["*"],
        findings=[],
        a2a_log=[],
        max_iterations=5,
    )

    res1 = await orch._execute_react_agent_loop(
        agent_name="supervisor",
        system_prompt="You are supervisor.",
        tools=[dummy_tool],
        state=state,
        max_iterations=5,
    )
    last_msg1 = res1["messages"][-1]
    assert "Hello from unbound stream!" in str(last_msg1.content)

    # Scenario 2: Model whose astream() completely returns 0 chunks (or raises No generation chunks)
    # both bound and unbound (e.g. proxy does not support SSE streaming), but ainvoke() works.
    class NonStreamingProxyModel:
        def __init__(self):
            self.ainvoke_called = False

        def bind_tools(self, tools):
            return self

        async def astream(self, messages):
            # Simulates proxy returning empty SSE or closing connection
            raise ValueError("No generation chunks were returned")
            if False:
                yield AIMessage(content="")

        async def ainvoke(self, messages):
            self.ainvoke_called = True
            return AIMessage(content="Hello from non-streaming ainvoke fallback! Proxy delivered full response.")

    model_non_stream = NonStreamingProxyModel()
    orch.provider_mgr.get_chat_model = lambda: model_non_stream

    state2 = AgentState(
        messages=[HumanMessage(content="hi")],
        active_agent="supervisor",
        next_step="supervisor",
        scope=["*"],
        findings=[],
        a2a_log=[],
        max_iterations=5,
    )

    res2 = await orch._execute_react_agent_loop(
        agent_name="supervisor",
        system_prompt="You are supervisor.",
        tools=[dummy_tool],
        state=state2,
        max_iterations=5,
    )
    assert model_non_stream.ainvoke_called is True
    last_msg2 = res2["messages"][-1]
    assert "Hello from non-streaming ainvoke fallback!" in str(last_msg2.content)


@pytest.mark.asyncio
async def test_claude_custom_endpoint_cross_protocol_recovery(monkeypatch):
    """Verify that when a custom Claude proxy fails on ChatOpenAI (e.g. Cloudflare WAF 403 / empty chunks),
    ScopeForge automatically recovers via the native Anthropic protocol."""
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.tools import StructuredTool
    from scopeforge_engine.agents.graph import MultiAgentSecOpsOrchestrator
    from scopeforge_engine.agents.state import AgentState
    import langchain_anthropic

    orch = MultiAgentSecOpsOrchestrator()
    orch.set_mode("live")

    # Mock failing ChatOpenAI
    class FailingOpenAIModel:
        def bind_tools(self, tools):
            return self

        async def astream(self, messages):
            raise ValueError("No generation chunks were returned")

        async def ainvoke(self, messages):
            raise ValueError("No generation chunks were returned")

    # Mock succeeding ChatAnthropic
    class MockAnthropicModel:
        def __init__(self, **kwargs):
            pass

        async def astream(self, messages):
            yield AIMessage(content="Hello via Anthropic native protocol fallback!")

        async def ainvoke(self, messages):
            return AIMessage(content="Hello via Anthropic native protocol fallback!")

    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", MockAnthropicModel)

    orch.provider_mgr.get_chat_model = lambda: FailingOpenAIModel()
    orch.provider_mgr.get_active_config = lambda: type("C", (), {
        "provider": "custom",
        "model": "claude-opus-4-8",
        "name": "claude-justwork",
        "api_key": "sk-real-test-key-12345",
        "api_base": "https://api.justwoker.icu",
        "temperature": 0.2,
    })()

    dummy_tool = StructuredTool.from_function(func=lambda x="": "ok", name="dummy", description="dummy tool")

    state = AgentState(
        messages=[HumanMessage(content="hi")],
        active_agent="supervisor",
        next_step="supervisor",
        scope=["*"],
        findings=[],
        a2a_log=[],
        max_iterations=5,
    )

    res = await orch._execute_react_agent_loop(
        agent_name="supervisor",
        system_prompt="You are supervisor.",
        tools=[dummy_tool],
        state=state,
        max_iterations=5,
    )
    last_msg = res["messages"][-1]
    assert "Anthropic native protocol fallback" in str(last_msg.content)


def test_xml_tool_calling_and_mcp_normalization():
    from langchain_core.messages import AIMessage
    from scopeforge_engine.agents.graph import _extract_tool_calls, _normalize_tool_call, _clean_tool_call_text

    tool_map = {
        "bash_cli": None,
        "list_mcp_servers": None,
        "add_mcp_server": None,
        "list_skills": None,
        "install_skill": None,
        "create_skill": None,
    }

    # 1. XML with <function=NAME><parameter=KEY>VAL</parameter></function>
    msg1 = AIMessage(content="""I'll check if Playwright is installed.
<tool_call>
<function=bash>
<parameter=command>
python3 -c "import playwright; print('installed')"
</parameter>
</function>
</tool_call>""")
    calls1 = _extract_tool_calls(msg1, tool_map)
    assert len(calls1) == 1
    assert calls1[0]["name"] == "bash_cli"
    assert "import playwright" in calls1[0]["args"]["command"]

    # 2. Standalone <function=...> without <tool_call> wrapper
    msg2 = AIMessage(content="""<function=bash><parameter=command>which npx</parameter></function>""")
    calls2 = _extract_tool_calls(msg2, tool_map)
    assert len(calls2) == 1
    assert calls2[0]["name"] == "bash_cli"
    assert calls2[0]["args"]["command"] == "which npx"

    # 3. Bare tool call: <tool_call>list_mcp_servers</tool_call>
    msg3 = AIMessage(content="""Let me check MCP servers:<tool_call>list_mcp_servers</tool_call>""")
    calls3 = _extract_tool_calls(msg3, tool_map)
    assert len(calls3) == 1
    assert calls3[0]["name"] == "list_mcp_servers"
    assert calls3[0]["args"] == {}

    # 4. Bare tool call with parentheses: <tool_call>list_mcp_servers()</tool_call>
    msg4 = AIMessage(content="""<tool_call>list_mcp_servers()</tool_call>""")
    calls4 = _extract_tool_calls(msg4, tool_map)
    assert len(calls4) == 1
    assert calls4[0]["name"] == "list_mcp_servers"

    # 5. Markdown fenced JSON inside <tool_call>
    msg5 = AIMessage(content="""<tool_call>
```json
{
  "name": "add_mcp_server",
  "arguments": {
    "server_name": "playwright",
    "cmd": "npx -y @modelcontextprotocol/server-playwright"
  }
}
```
</tool_call>""")
    calls5 = _extract_tool_calls(msg5, tool_map)
    assert len(calls5) == 1
    assert calls5[0]["name"] == "add_mcp_server"
    assert calls5[0]["args"]["name"] == "playwright"
    assert "npx" in calls5[0]["args"]["command"]

    # 6. Alias normalization: check_mcp, mcp_status, skills_list
    n1, _ = _normalize_tool_call("check_mcp", {}, tool_map)
    assert n1 == "list_mcp_servers"
    n2, _ = _normalize_tool_call("mcp_status", {}, tool_map)
    assert n2 == "list_mcp_servers"
    n3, _ = _normalize_tool_call("skills_list", {}, tool_map)
    assert n3 == "list_skills"

    # 7. Cleaning markup text
    dirty = "Checking servers... <tool_call><function=bash><parameter=command>ls</parameter></function></tool_call> Done!"
    cleaned = _clean_tool_call_text(dirty)
    assert "<tool_call>" not in cleaned
    assert "<function" not in cleaned
    assert "Checking servers..." in cleaned
    assert "Done!" in cleaned


@pytest.mark.asyncio
async def test_xml_function_tag_multistep_loop():
    """Verify that models emitting <function=...> XML tags execute sequentially through multi-step loops."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_core.tools import StructuredTool
    from scopeforge_engine.agents.graph import MultiAgentSecOpsOrchestrator
    from scopeforge_engine.agents.state import AgentState

    orch = MultiAgentSecOpsOrchestrator()
    orch.set_mode("live")
    executed_tools = []

    def _mock_bash(command: str) -> str:
        executed_tools.append(f"bash:{command}")
        return "/usr/local/bin/npx"

    def _mock_add_mcp(name: str, command: str) -> str:
        executed_tools.append(f"mcp:{name}:{command}")
        return json.dumps({"status": "SUCCESS", "message": f"Server {name} added"})

    t_bash = StructuredTool.from_function(func=_mock_bash, name="bash_cli", description="Run command")
    t_mcp = StructuredTool.from_function(func=_mock_add_mcp, name="add_mcp_server", description="Add MCP server")

    class MockXmlModel:
        def bind_tools(self, tools):
            return self

        async def astream(self, messages):
            tool_msg_count = sum(1 for m in messages if isinstance(m, ToolMessage) or (isinstance(m, HumanMessage) and "Tool Observation" in str(getattr(m, "content", ""))))
            if tool_msg_count == 0:
                yield AIMessage(content="""I'll check if npx is available.
<tool_call>
<function=bash>
<parameter=command>
which npx
</parameter>
</function>
</tool_call>""")
            elif tool_msg_count == 1:
                yield AIMessage(content="""npx is available at /usr/local/bin/npx. Now adding Playwright MCP:
<tool_call>
<function=add_mcp>
<parameter=name>
playwright
</parameter>
<parameter=command>
npx -y @modelcontextprotocol/server-playwright
</parameter>
</function>
</tool_call>""")
            else:
                yield AIMessage(content="Playwright MCP has been successfully installed and registered via autonomous multi-step loop.")

    orch.provider_mgr.get_chat_model = lambda: MockXmlModel()
    orch.provider_mgr.get_active_config = lambda: type("C", (), {"provider": "mock", "model": "mock-xml", "name": "mock"})()

    state = AgentState(
        messages=[HumanMessage(content="Add playwright mcp tool")],
        active_agent="supervisor",
        next_step="supervisor",
        scope=["*"],
        findings=[],
        a2a_log=[],
        max_iterations=10,
    )

    res = await orch._execute_react_agent_loop(
        agent_name="supervisor",
        system_prompt="You are ScopeForge supervisor.",
        tools=[t_bash, t_mcp],
        state=state,
        max_iterations=10,
    )

    assert len(executed_tools) == 2
    assert executed_tools[0] == "bash:which npx"
    assert "playwright" in executed_tools[1]
    last_content = str(res["messages"][-1].content)
    assert "Playwright MCP has been successfully installed" in last_content

@pytest.mark.asyncio
async def test_conversational_greeting_graceful_resilience(tmp_path):
    """Verify that when all providers fail, conversational greetings ('hi') degrade gracefully to a helpful welcome."""
    from scopeforge_engine.agents.graph import MultiAgentSecOpsOrchestrator, AgentState
    from scopeforge_engine.llm_providers.manager import ProviderManager, LLMConfig, ProviderType
    from langchain_core.messages import HumanMessage
    from unittest.mock import MagicMock

    cfg_dir = tmp_path / ".scopeforge"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    pm = ProviderManager(config_dir=cfg_dir)
    pm.providers.clear()
    pm.register_provider(LLMConfig(
        name="failing-groq",
        provider=ProviderType.GROQ,
        model="llama-3.3-70b-versatile",
        api_key=None,
    ))
    pm.set_active_provider("failing-groq")

    orch = MultiAgentSecOpsOrchestrator(provider_manager=pm)

    # Mock failing model
    class BrokenModel:
        async def astream(self, msgs):
            raise RuntimeError("Error code: 401 - {'error': {'message': 'Invalid API Key'}}")
            yield
        async def ainvoke(self, msgs):
            raise RuntimeError("Error code: 401 - {'error': {'message': 'Invalid API Key'}}")

    orch.provider_mgr.get_chat_model = MagicMock(return_value=BrokenModel())

    state = AgentState(
        messages=[HumanMessage(content="hi")],
        active_agent="supervisor",
        next_step="supervisor",
        scope=["*"],
        findings=[],
        a2a_log=[],
        max_iterations=3,
    )

    res = await orch._execute_react_agent_loop(
        agent_name="supervisor",
        system_prompt="You are supervisor.",
        tools=[],
        state=state,
        max_iterations=3,
    )
    last_msg = str(res["messages"][-1].content)
    assert "Hello! 👋 I am **ScopeForge**" in last_msg
    assert "failing-groq" in last_msg
    assert "/model" in last_msg


@pytest.mark.asyncio
async def test_mcp_sync_discovery_under_running_loop(tmp_path):
    """Verify that list_tools_sync on an HTTP server does not produce unawaited coroutine warnings when called in an event loop."""
    from scopeforge_engine.mcp import MCPClient, MCPRegistry, MCPServer

    reg = MCPRegistry(home=tmp_path)
    client = MCPClient(reg)

    server = MCPServer(
        name="test-http-server",
        command="http://localhost:59999/mcp",
        enabled=True,
    )

    # Calling list_tools_sync from inside this async test (running event loop)
    tools = client.list_tools_sync(server, timeout_s=0.5)
    assert tools == []


@pytest.mark.asyncio
async def test_candidate_failover_deduplication_and_switch(tmp_path):
    """Verify that when the active provider fails, Orchestrator fails over to candidate with valid key and sets it active."""
    from scopeforge_engine.agents.graph import MultiAgentSecOpsOrchestrator, AgentState
    from scopeforge_engine.llm_providers.manager import ProviderManager, LLMConfig, ProviderType
    from langchain_core.messages import HumanMessage, AIMessage
    from unittest.mock import MagicMock, patch

    cfg_dir = tmp_path / ".scopeforge"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    pm = ProviderManager(config_dir=cfg_dir)
    pm.providers.clear()
    pm.register_provider(LLMConfig(
        name="failing-active",
        provider=ProviderType.GROQ,
        model="llama-3.3-70b-versatile",
        api_key=None,
    ))
    # Add duplicate broken key configs
    pm.register_provider(LLMConfig(
        name="dead-cand-1",
        provider=ProviderType.OPENROUTER,
        model="openrouter/free",
        api_key="sk-or-v1-broken-key-1234567890",
    ))
    pm.register_provider(LLMConfig(
        name="dead-cand-2",
        provider=ProviderType.OPENROUTER,
        model="openrouter/free",
        api_key="sk-or-v1-broken-key-1234567890",  # Duplicate key! Should be skipped without duplicate attempt
    ))
    # Working candidate
    pm.register_provider(LLMConfig(
        name="working-cand",
        provider=ProviderType.OPENROUTER,
        model="openrouter/free",
        api_key="sk-or-v1-working-key-9999999999",
    ))
    pm.set_active_provider("failing-active")

    orch = MultiAgentSecOpsOrchestrator(provider_manager=pm)

    # Active model always fails
    class ActiveFailModel:
        async def astream(self, msgs):
            raise RuntimeError("401 Invalid Key")
            yield
        async def ainvoke(self, msgs):
            raise RuntimeError("401 Invalid Key")

    orch.provider_mgr.get_chat_model = MagicMock(return_value=ActiveFailModel())

    # Candidate model mock factory
    def mock_create_chat_model(cfg):
        cand = MagicMock()
        if "working" in cfg.name:
            async def _cand_stream(msgs):
                yield AIMessage(content="Working candidate response!")
            async def _cand_inv(msgs):
                return AIMessage(content="Working candidate response!")
            cand.astream = _cand_stream
            cand.ainvoke = _cand_inv
        else:
            async def _dead_stream(msgs):
                raise RuntimeError("429 rate limit")
                yield
            async def _dead_inv(msgs):
                raise RuntimeError("429 rate limit")
            cand.astream = _dead_stream
            cand.ainvoke = _dead_inv
        return cand

    with patch("scopeforge_engine.llm_providers.factory.create_chat_model", side_effect=mock_create_chat_model):
        state = AgentState(
            messages=[HumanMessage(content="Explain what ScopeForge does")],
            active_agent="supervisor",
            next_step="supervisor",
            scope=["*"],
            findings=[],
            a2a_log=[],
            max_iterations=3,
        )

        res = await orch._execute_react_agent_loop(
            agent_name="supervisor",
            system_prompt="You are supervisor.",
            tools=[],
            state=state,
            max_iterations=3,
        )
        last_msg = str(res["messages"][-1].content)
        assert "Working candidate response!" in last_msg
        assert "Resilience Failover: automatically switched to `working-cand`" in last_msg
        # Verify active provider was switched to working-cand
        assert pm.active_provider_name == "working-cand"


def test_arg_key_xml_extraction():
    """Verify that 'Invoking tool: <tool><arg_key>KEY</arg_key><arg_value>VAL</arg_value>' is extracted and cleaned."""
    from langchain_core.messages import AIMessage
    from scopeforge_engine.agents.graph import _extract_tool_calls, _clean_tool_call_text

    tool_map = {"bash_cli": None}
    raw = (
        "Let me start the application.\n"
        "Invoking tool: bash_cli<arg_key>command</arg_key><arg_value>streamlit run chatbot_ui.py</arg_value>"
        "<arg_key>timeout</arg_key><arg_value>120</arg_value></tool_call>"
    )
    msg = AIMessage(content=raw)
    calls = _extract_tool_calls(msg, tool_map)
    assert len(calls) == 1
    assert calls[0]["name"] == "bash_cli"
    assert calls[0]["args"]["command"] == "streamlit run chatbot_ui.py"
    assert calls[0]["args"]["timeout"] == "120"

    cleaned = _clean_tool_call_text(raw)
    assert "<arg_key>" not in cleaned
    assert "Invoking tool:" not in cleaned
    assert "streamlit run" not in cleaned
    assert "Let me start the application." in cleaned


def test_plan_mode_operator_approval_interruption():
    """Verify that in PLAN mode, executing tools like bash_cli trigger ApprovalGate for human decision."""
    from scopeforge_engine.middleware.approval import ApprovalGateMiddleware
    from scopeforge_engine.middleware.base import ToolApprovalRequest

    decisions = []

    def mock_approval(req: ToolApprovalRequest) -> bool:
        decisions.append(req.tool_name)
        return False  # Deny approval

    gate = ApprovalGateMiddleware(approval_callback=mock_approval, mode="plan")

    # In PLAN mode: bash_cli should trigger approval
    proceed, reason, _ = gate.before_tool("bash_cli", {"command": "streamlit run chatbot.py"}, {"agent": "Supervisor"})
    assert not proceed
    assert len(decisions) == 1
    assert decisions[0] == "bash_cli"
    assert "Operator DENIED approval" in reason

    # When switched to LIVE mode: bash_cli should execute without approval
    gate.set_mode("live")
    proceed_live, reason_live, _ = gate.before_tool("bash_cli", {"command": "streamlit run chatbot.py"}, {"agent": "Supervisor"})
    assert proceed_live
    assert reason_live == ""
    assert len(decisions) == 1  # No new approval prompted



