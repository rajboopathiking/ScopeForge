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

#model-picker-dialog {
    width: 84;
    height: auto;
    max-height: 42;
    background: #161b22;
    border: thick #58a6ff;
    padding: 1 2;
    layout: vertical;
    overflow-y: auto;
}

#model-view-switcher {
    height: auto;
    max-height: 38;
}

#view-list {
    height: auto;
    max-height: 36;
}

#view-custom {
    height: auto;
    max-height: 36;
    overflow-y: auto;
    padding-right: 1;
}

/* View toggle for ModelPickerModal: only one of list/custom is visible. */
#view-list.-hidden, #view-custom.-hidden {
    display: none;
}

.-hidden {
    display: none;
}

#model-option-list {
    height: auto;
    max-height: 16;
    background: #0d1117;
    border: solid #30363d;
    margin-top: 1;
    margin-bottom: 1;
}

.modal-hint {
    color: #8b949e;
    margin-bottom: 1;
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

#modal-buttons, #modal-buttons-select, #modal-buttons-config {
    height: 3;
    layout: horizontal;
    align: right middle;
    margin-top: 1;
}

Button {
    height: 3;
    min-width: 13;
    padding: 0 1;
    border: tall #30363d;
    text-align: center;
    content-align: center middle;
    color: #f0f6fc;
}

Button.-primary {
    background: #1f6feb;
    color: #ffffff;
    text-style: bold;
    border: tall #388bfd;
}

Button.-success {
    background: #238636;
    color: #ffffff;
    text-style: bold;
    border: tall #2ea043;
}

Button.-default {
    background: #21262d;
    color: #e6edf3;
    border: tall #30363d;
}

Button.-error {
    background: #da3633;
    color: #ffffff;
    text-style: bold;
    border: tall #f85149;
}

Button:focus {
    border: tall #58a6ff;
    text-style: bold;
}

Button:hover {
    background: #30363d;
}

.modal-btn {
    margin-left: 1;
    min-width: 12;
    height: 3;
}

.form-label {
    color: #58a6ff;
    text-style: bold;
    margin-top: 1;
    margin-bottom: 0;
}

.form-section-title {
    color: #e6edf3;
    text-style: bold;
    margin-bottom: 1;
}

.tab-help {
    color: #8b949e;
    margin-bottom: 1;
}

#preset-container {
    layout: horizontal;
    height: auto;
    margin-bottom: 1;
}

.preset-btn {
    margin-right: 1;
    min-width: 14;
    height: 1;
}

#config-form-scroll {
    height: 1fr;
    overflow-y: auto;
    padding-right: 1;
}

.form-row {
    layout: horizontal;
    height: auto;
}

.half-col {
    width: 1fr;
    margin-right: 1;
}

#cfg-status-msg {
    color: #3fb950;
    text-style: bold;
    margin-top: 1;
}

Select {
    margin-bottom: 1;
}

Input {
    margin-bottom: 1;
}
"""
