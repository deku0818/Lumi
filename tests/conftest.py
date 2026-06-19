"""共享 Fixtures"""

import pytest

import lumi.agents.core.hooks.config_loader as hooks_config_loader
import lumi.agents.permissions.workspace as workspace
import lumi.agents.runtime.bg_tasks as task_registry
import lumi.agents.tools.registry as registry
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
def reset_hooks_state():
    """隔离 hooks 全局状态：清掉配置 hook + 把 _LOADED 置位，阻止测试期
    create_agent 触发的 load_hooks 读取开发者真实 ~/.lumi/hooks.json。
    （test_hooks_shell.py 自己的 fixture 会先 reset_hooks 还原后再显式 load。）
    """
    hooks_config_loader.reset_hooks()
    hooks_config_loader._LOADED = True
    yield
    hooks_config_loader.reset_hooks()
