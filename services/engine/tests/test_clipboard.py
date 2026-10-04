"""Tests for ScopeForge TUI clipboard copy and paste functionality."""
from pathlib import Path
import pytest
from textual import events
from scopeforge_engine.tui.clipboard import (
    clean_pasted_text,
    copy_to_system_clipboard,
    paste_from_system_clipboard,
)
from scopeforge_engine.tui.widgets.clipboard_input import ClipboardInput
from scopeforge_engine.tui.widgets.prompt_bar import PromptBar, PromptInput
from scopeforge_engine.tui.widgets.chat_log import ChatStream
from scopeforge_engine.tui.app import ScopeForgeTUIApp


def test_clean_pasted_text():
    # Multi-line text into single line
    raw = "line 1\nline 2\r\nline 3"
    cleaned = clean_pasted_text(raw, single_line=True)
    assert cleaned == "line 1 line 2 line 3"

    # API key or URL with trailing newline
    api_key = "  sk-proj-12345abcdef  \n"
    assert clean_pasted_text(api_key, single_line=True) == "sk-proj-12345abcdef"

    # Preserving multi-line when single_line is False
    assert clean_pasted_text("a\nb\n", single_line=False) == "a\nb"

    # Empty string
    assert clean_pasted_text("", single_line=True) == ""


def test_system_clipboard_copy_and_paste():
    test_str = "ScopeForge_SecOps_Token_Test_999"
    ok = copy_to_system_clipboard(test_str)
    # On systems with pbcopy / xclip / powershell, ok should be True
    if ok:
        pasted = paste_from_system_clipboard()
        assert test_str in pasted


def test_chat_stream_last_response_and_transcript():
    chat = ChatStream()
    # Add a message
    chat.add_user_message("What is SQL injection?")
    chat.add_agent_message("supervisor", "SQL injection is a code injection technique.\n```python\nquery = 'SELECT * FROM users'\n```")

    assert "SQL injection" in chat.get_last_agent_response()
    transcript = chat.get_full_transcript()
    assert "### User" in transcript
    assert "### SupervisorAgent" in transcript

    code = chat.get_last_code_block()
    assert "query = 'SELECT * FROM users'" in code


@pytest.mark.asyncio
async def test_tui_slash_copy_and_paste_commands(tmp_path: Path):
    app = ScopeForgeTUIApp()
    async with app.run_test() as pilot:
        chat = pilot.app.query_one(ChatStream)
        prompt_bar = pilot.app.query_one(PromptBar)
        prompt_input = pilot.app.query_one("#prompt-input", PromptInput)

        # 1. Simulate agent response
        chat.add_agent_message("Supervisor", "Hello! Assessment complete.\n```bash\nnmap -sV target.local\n```")

        # 2. Test /copy (last response)
        pilot.app.handle_user_input("/copy")
        await pilot.pause()
        assert pilot.app.clipboard.strip() == "Hello! Assessment complete.\n```bash\nnmap -sV target.local\n```"

        # 3. Test /copy code
        pilot.app.handle_user_input("/copy code")
        await pilot.pause()
        assert pilot.app.clipboard.strip() == "nmap -sV target.local"

        # 4. Test /copy all
        pilot.app.handle_user_input("/copy all")
        await pilot.pause()
        assert "# ScopeForge Session Transcript" in pilot.app.clipboard

        # 5. Test /copy findings
        pilot.app.handle_user_input("/copy findings")
        await pilot.pause()
        assert "FIND-001" in pilot.app.clipboard

        # 6. Test F6 action (copy last response)
        chat.add_agent_message("Supervisor", "New findings: CVE-2024-12345 verified.")
        pilot.app.action_copy_last_response()
        assert "CVE-2024-12345" in pilot.app.clipboard

        # 7. Test /paste into prompt input
        copy_to_system_clipboard("echo 'hello scopeforge'")
        pilot.app.handle_user_input("/paste")
        await pilot.pause()

        # 8. Test /mouse toggle (F7)
        pilot.app.handle_user_input("/mouse")
        await pilot.pause()
        assert pilot.app._native_mouse_mode is True

        pilot.app.action_toggle_mouse_capture()
        await pilot.pause()
        assert pilot.app._native_mouse_mode is False

        # 9. Test /export to file
        export_file = tmp_path / "test_export.md"
        pilot.app.handle_user_input(f"/export {export_file}")
        await pilot.pause()
        assert export_file.exists()
        content = export_file.read_text(encoding="utf-8")
        assert "ScopeForge Session Transcript" in content


@pytest.mark.asyncio
async def test_mouse_selection_and_keyboard_copy():
    app = ScopeForgeTUIApp()
    async with app.run_test() as pilot:
        # Mock screen selection on active screen
        pilot.app.screen.get_selected_text = lambda: "Selected text from terminal screen"
        pilot.app.on_text_selected(events.TextSelected())
        assert "Selected text from terminal screen" in pilot.app.clipboard

        # Test Ctrl+C / Cmd+C copy selection
        pilot.app.action_handle_ctrl_c()
        assert "Selected text from terminal screen" in pilot.app.clipboard
