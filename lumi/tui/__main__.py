"""Lumi TUI 入口

用法:
    python -m lumi.tui
    lumi  (通过 pyproject.toml scripts)
"""

from lumi.tui.app import LumiApp


def main() -> None:
    app = LumiApp()
    app.run()


if __name__ == "__main__":
    main()
