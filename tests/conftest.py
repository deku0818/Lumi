"""共享 Fixtures"""

import pytest

import lumi.agents.tools.workspace as workspace
import lumi.agents.tools.session as session
from lumi.agents.tools.providers import filesystem
from lumi.agents.tools.registry import ToolRegistry


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
    filesystem._backend = None
    yield
    filesystem._backend = None


@pytest.fixture(autouse=True)
def reset_session_manager():
    """每次测试重置 session manager 单例"""
    session._session_manager = None
    yield
    session._session_manager = None


@pytest.fixture(autouse=True)
def reset_registry():
    """每次测试重置 ToolRegistry 单例"""
    old_instance = ToolRegistry._instance
    ToolRegistry._instance = None
    yield
    ToolRegistry._instance = old_instance
