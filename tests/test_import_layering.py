"""分层：底层模块能在新进程里单独导入，不经包 __init__ 拖进整棵依赖。"""

import subprocess
import sys


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)


def test_context_inject_imports_standalone():
    # 回归：hooks/__init__ 顶层 import builtin，builtin 回头 import 尚未初始化完的
    # 模块——context_inject 是首个被导入的模块时直接 ImportError
    r = _run("import lumi.agents.core.preprocessing.context_inject")
    assert r.returncode == 0, r.stderr


def test_permissions_workspace_stays_light():
    # 回归：permissions/__init__ 无人消费的 re-export 经 engine 把 PDF / MCP / 调度器
    # 整棵拖进来，只想拿授权目录的模块也要付上千个模块的导入
    r = _run(
        "import sys, lumi.agents.permissions.workspace\n"
        "print(sorted(m for m in ('fitz', 'mcp', 'apscheduler') if m in sys.modules))"
    )
    assert r.stdout.strip() == "[]", r.stdout + r.stderr
