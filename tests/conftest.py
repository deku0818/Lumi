"""共享 Fixtures"""

import pytest

import lumi.agents.permissions.workspace as workspace
import lumi.agents.runtime.bg_tasks as task_registry
import lumi.agents.tools.registry as registry
from lumi.agents.core.hooks import set_run_config_hooks
from lumi.agents.core.preprocessing.agent_detector import AgentChangeDetector
from lumi.agents.runtime import shell_session
from lumi.agents.tools.providers import filesystem


@pytest.fixture
def authorized_tmp_dir(tmp_path):
    """设置 authorized_directory 为 tmp_path，teardown 恢复"""
    old = workspace._authorized_directories[:]
    workspace._authorized_directories = [tmp_path]
    yield tmp_path
    workspace._authorized_directories = old


@pytest.fixture(autouse=True)
def reset_run_authorized():
    """每次测试清空 per-run 授权目录来源 contextvar + 进程全局兜底，避免跨测试泄漏。

    （bridge stream / cron 会设置 contextvar；测试可能调 set_authorized_directory 改全局。）
    """
    workspace._run_authorized_source.set(None)
    old = workspace._authorized_directories[:]
    yield
    workspace._run_authorized_source.set(None)
    workspace._authorized_directories = old


@pytest.fixture(autouse=True)
def reset_filesystem_backend():
    """每次测试重置 filesystem backend 单例"""
    filesystem.backend._backend = None
    yield
    filesystem.backend._backend = None


@pytest.fixture(autouse=True)
def reset_session_manager():
    """每次测试重置 session manager 单例"""
    shell_session._session_manager = None
    yield
    shell_session._session_manager = None


@pytest.fixture(autouse=True)
def reset_registry():
    """每次测试重置 ToolRegistry 单例"""
    old_instance = registry._registry
    registry._registry = None
    yield
    registry._registry = old_instance


@pytest.fixture(autouse=True)
def reset_task_registry():
    """每次测试重置 TaskRegistry 单例"""
    task_registry._registry = None
    yield
    task_registry._registry = None


@pytest.fixture(autouse=True)
def reset_agent_detector():
    """每次测试重置 AgentChangeDetector 单例，避免缓存 digest 跨测试泄漏。"""
    AgentChangeDetector.reset()
    yield
    AgentChangeDetector.reset()


@pytest.fixture(autouse=True)
def reset_hooks_state():
    """隔离 hooks：清空 per-run config hooks contextvar，避免跨测试泄漏。

    config hooks 已改为按会话经 contextvar 注入（不再写进程全局），测试默认无 config
    hook；builtin hook 仍在进程全局 _HOOKS，由 test_hooks_framework 自己的 fixture 隔离。
    """
    set_run_config_hooks(None)
    yield
    set_run_config_hooks(None)
