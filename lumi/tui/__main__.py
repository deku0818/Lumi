"""Lumi TUI 入口

用法:
    python -m lumi.tui
    lumi  (通过 pyproject.toml scripts)
"""

from lumi.tui.app import LumiApp


def main() -> None:
    import sys

    _original_unraisablehook = sys.unraisablehook

    def _quiet_unraisablehook(args):  # type: ignore[type-arg]
        """静默解释器 shutdown 阶段 threading 模块抛出的 KeyboardInterrupt。"""
        if args.exc_type is KeyboardInterrupt:
            return
        _original_unraisablehook(args)

    sys.unraisablehook = _quiet_unraisablehook

    app = LumiApp()
    app.run()


if __name__ == "__main__":
    main()
