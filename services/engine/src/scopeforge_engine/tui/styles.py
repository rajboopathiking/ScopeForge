"""Textual CSS styles for Claude Code and Open Code dark aesthetic."""

TCSS_STYLES = """
Screen {
    background: #0f1117;
    color: #e6edf3;
    layout: vertical;
}

#brand-logo {
    color: #58a6ff;
    text-style: bold;
    width: auto;
    margin-right: 2;
}

.header-badge {
    height: 1;
    margin-right: 1;
    padding: 0 1;
    background: #21262d;
    color: #8b949e;
    border: none;
    text-style: bold;
}

#badge-mode-plan {
    background: #238636;
    color: #ffffff;
}

#badge-mode-artifacts {
    background: #d29922;
    color: #000000;
}

#badge-mode-live {
    background: #da3633;
    color: #ffffff;
}

#badge-model {
    background: #1f6feb;
    color: #ffffff;
}

#badge-agent {
    background: #8957e5;
    color: #ffffff;
}

#badge-scope {
    background: #30363d;
    color: #58a6ff;
}

#badge-cost {
    background: #21262d;
    color: #8b949e;
}

#workspace-container {
    height: 1fr;
    width: 100%;
    layout: horizontal;
}

/* Modals */
ModalScreen {
    align: center middle;
    background: rgba(0, 0, 0, 0.7);
}

#modal-dialog {
    width: 75%;
    height: 75%;
    background: #161b22;
    border: thick #58a6ff;
    padding: 1 2;
    layout: vertical;
}

#modal-title {
    text-style: bold;
    color: #58a6ff;
    border-bottom: solid #30363d;
    padding-bottom: 1;
    margin-bottom: 1;
}

#modal-content {
    height: 1fr;
    overflow-y: scroll;
}

#modal-buttons {
    height: 3;
    dock: bottom;
    layout: horizontal;
    align: right middle;
}

.modal-btn {
    margin-left: 2;
}
"""
