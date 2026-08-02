"""共享 Fixtures"""

import pytest

import lumi.agents.permissions.workspace as workspace
import lumi.agents.runtime.bg_tasks as task_registry
import lumi.agents.tools.registry as registry
from lumi.agents.core.hooks import set_run_config_hooks
from lumi.agents.core.preprocessing.agent_detector import AgentChangeDetector
from lumi.agents.runtime import shell_session
from lumi.agents.tools.providers import filesystem


def resolved(context_window: int = 0, max_tokens: int = 0):
    """构造一个只关心限制的 ResolvedModel，用于 patch 掉 resolve 的读盘 + 查目录。

    压缩阈值与输出上限都从 provider_store.resolve 取（用户覆盖 > catalog 探测），
    单测在这里给定值，避免依赖本机 ~/.lumi 配置与 models.dev 缓存。
    """
    from lumi.models.provider_store import ResolvedModel

    return ResolvedModel("m", "", "", "auto", context_window, max_tokens)


def catalog_entry(context_length: int = 0, max_output: int = 0, model_id: str = "m"):
    """构造 models.dev 目录条目。

    用真实 ``ModelEntry`` 而非 ``SimpleNamespace``：鸭子类型的假条目在目录新增字段
    时会静默通过，直到某个消费方读到不存在的属性才炸（本仓库已经这么炸过一次）。
    """
    from lumi.models.catalog import ModelEntry

    return ModelEntry(
        id=model_id,
        context_length=context_length,
        control="none",
        values=(),
        has_toggle=False,
        toggle_anywhere=False,
        max_output=max_output,
    )


@pytest.fixture
def authorized_tmp_dir(tmp_path):
    """设置 authorized_directory 为 tmp_path，teardown 恢复"""
    old = workspace._authorized_directories[:]
    workspace._authorized_directories = [tmp_path]
    yield tmp_path
    workspace._authorized_directories = old


@pytest.fixture(autouse=True)
def isolate_user_store(tmp_path, monkeypatch):
    """所有测试的用户级配置（~/.lumi/lumi.json）重定向到 tmp，杜绝读写真实 ~/.lumi。

    需要具体路径的测试可再显式 monkeypatch user_store.CONFIG_FILE（同一 tmp_path、同名文件，
    值一致、无冲突）。
    """
    from lumi.utils.config import user_store

    monkeypatch.setattr(user_store, "CONFIG_FILE", tmp_path / "lumi.json")


@pytest.fixture(autouse=True)
def reset_lumi_config():
    """每次测试重置 LumiConfig 单例。

    它是唯一带「记住上次传入的目录」的配置单例：任何一个用例显式取过某个配置目录
    （`lumi env` 会钉住 ~/.lumi、fixture 会钉住 tmp），实例就留在进程里，后续用例的
    config_dir / bin_dir / config_layers 全跟着它走，测试随执行顺序与机器漂移。
    """
    from lumi.utils.config import LumiConfig

    LumiConfig.reset_instance()
    yield
    LumiConfig.reset_instance()


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """隔离配置目录：LumiConfig 单例指向 tmp（含 LUMI_CONFIG_DIR，使 toolbox_dir /
    缓存等机器级路径一并进 tmp）。复位由 reset_lumi_config 自动兜底。"""
    from lumi.utils.config import LumiConfig

    config_dir = tmp_path / "lumi-config"
    config_dir.mkdir()
    monkeypatch.setenv("LUMI_CONFIG_DIR", str(config_dir))
    LumiConfig.get_instance(str(config_dir), reset=True)
    return config_dir


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


@pytest.fixture(autouse=True)
def reset_summary_circuits():
    """每次测试清空 summary / PTL 压缩共享的进程级熔断器状态，避免跨测试串染。"""
    from lumi.agents.core.preprocessing.compact import clear_all_circuits

    clear_all_circuits()
    yield
    clear_all_circuits()


class PTLError(Exception):
    """prompt-too-long 异常桩：满足 is_ptl_error 的 substring + status_code 判定。"""

    status_code = 400


def tool_loop_history() -> list:
    """[System, Human, (AI+Tool)×4]，模拟工具循环中段（末条 ToolMessage）。"""
    from langchain_core.messages import (
        AIMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )

    msgs: list = [
        SystemMessage(content="sys", id="s"),
        HumanMessage(content="q", id="h"),
    ]
    for i in range(4):
        msgs.append(
            AIMessage(
                content=f"a{i}",
                id=f"a{i}",
                tool_calls=[{"name": "read", "args": {}, "id": f"tc{i}"}],
            )
        )
        msgs.append(ToolMessage(content=f"t{i}", tool_call_id=f"tc{i}", id=f"t{i}"))
    return msgs


@pytest.fixture
def run_summarizer():
    """驱动串行 summarizer：mock 掉 LLM 链 / 配置 / token 计数，强制触发压缩。

    返回 ``await run_summarizer(state, runtime, summary_text, thread_id)``，断言压缩后
    返回的 messages（RemoveMessage + 注入了摘要/技能/agent 提示的末条 Human）。
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from langchain_core.messages import AIMessage

    from lumi.utils.config.models import TokenConfig

    async def _run(state, runtime, summary_text, thread_id):
        # summarizer 取 context.system_prompt / model_name 传给（已 mock 的）链，需补齐
        runtime.context.system_prompt = ""
        runtime.context.model_name = ""
        fake_chain = SimpleNamespace(
            ainvoke=AsyncMock(return_value=AIMessage(content=summary_text))
        )
        fake_config = SimpleNamespace(
            config=SimpleNamespace(token=TokenConfig()),
            load_prompt=lambda name: "SUMMARY PROMPT",
        )
        with (
            patch("lumi.agents.core.nodes.context_window_tokens", return_value=10**9),
            patch("lumi.agents.core.nodes.tool_call_chain", return_value=fake_chain),
            patch("lumi.agents.core.nodes.get_config", return_value=fake_config),
        ):
            from lumi.agents.core.nodes import summarizer

            return await summarizer(
                state, runtime, {"configurable": {"thread_id": thread_id}}
            )

    return _run
