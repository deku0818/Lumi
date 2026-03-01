"""Lumi TUI 主题 - 暗色主题，橙色强调

Widget 自身的 DEFAULT_CSS 负责各自的样式。
APP_CSS 只定义全局样式和无法归属到特定 widget 的布局规则。
"""

APP_CSS = """
Screen {
    background: transparent;
    color: #e0e0e0;
}

/* ── TitleBlock 外框 ── */
TitleBlock {
    height: auto;
    margin: 1 2;
    border: round #ffcc00;
    border-title-color: #ffcc00;
    border-title-style: bold;
}

#title-row {
    height: auto;
}

#left-panel {
    width: auto;
    min-width: 28;
    height: auto;
    padding: 1 2;
    text-align: center;
    content-align: center middle;
    border-right: solid #444444;
}

#right-col {
    width: 1fr;
    height: auto;
}

#right-top {
    height: auto;
    padding: 1 2;
    border-bottom: solid #444444;
}

#right-bottom {
    height: auto;
    padding: 1 2;
}

#chat-log {
    background: transparent;
    padding: 0 1;
    scrollbar-color: #333340;
    scrollbar-color-hover: #ffcc00;
    scrollbar-color-active: #ffcc00;
}

/* ── InputBar 外层容器 ── */
#input-area {
    dock: bottom;
    height: auto;
    max-height: 10;
    background: transparent;
    padding: 0 2 1 2;
}

/* ── 输入框（带边框） ── */
InputBox {
    height: auto;
    border: round #ffcc00;
    border-title-color: #ffcc00;
    border-title-style: bold;
    padding: 0 1;
}

#input-row {
    height: auto;
}

InputBox #prompt-label {
    color: #ffcc00;
    text-style: bold;
    width: 3;
    height: 1;
    padding: 0;
}

InputBox Input {
    background: transparent;
    color: #e0e0e0;
    border: none !important;
    width: 1fr;
    height: 1;
    padding: 0;
    margin: 0;
}

InputBox Input:focus {
    border: none !important;
}

#mode-indicator {
    height: 1;
    padding: 0 0 0 1;
    color: #888888;
}
"""
