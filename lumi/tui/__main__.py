"""Lumi TUI 入口

用法:
    python -m lumi.tui
"""

from lumi.tui.app import LumiApp
from lumi.utils.patches import apply_all


def main() -> None:
    """启动 TUI（供 python -m lumi.tui 和 textual-serve 子进程调用）。"""
    import sys

    apply_all()

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
