"""Workflow 编排引擎：在受限命名空间里执行一段 Python 脚本，脚本通过注入的钩子
扇出 LLM 子代理（``agent``），``parallel`` / ``pipeline`` 并发编排，``phase`` / ``log`` /
``args`` 辅助。脚本以 ``async def`` 包裹后执行，可用顶层 ``await`` 与 ``return``。

脚本是确定性的编排骨架（循环 / 条件 / 扇出由代码钉死）；``agent()`` 是模型驱动的推理
单元（贵、不可复算）。确定性的活让子代理用 bash / filesystem 等工具去干。

安全靠隔离不靠禁止：受限命名空间去掉 ``import`` / ``open`` 等无关内建，**只为避免误触、
不是安全边界**——编排脚本由受信的主 agent（LLM）生成（与 bash 工具同级），蓄意脚本仍可经
语言级 dunder 遍历逃逸回主进程，故**绝不可喂不可信脚本**。子代理复用父 ``PermissionEngine``，
工具调用仍受工作区边界 + 权限规则约束。

> 移植自 Claude Code 内置 Workflow 工具的设计；本版不含 ``run`` / ``sh`` 确定性执行层
> （Lumi 无沙箱）——确定性重活由 ``agent()`` 子代理经其工具完成。
"""

from __future__ import annotations

import asyncio
import builtins
import inspect
import os
import textwrap
from dataclasses import dataclass, field
from typing import Any

from lumi.utils.logger import logger

_MAX_AGENTS = 1000
"""单个 workflow 终身 agent 调用上限，防失控的兜底。"""

_HARD_CONCURRENCY_CAP = 16
"""并发上限硬顶；实际取 ``min(此值, cpu-2)``。"""

# Workflow 子代理禁用的工具：agent / workflow 防递归扇出；ask 在后台 graph 无法 interrupt
# 会挂；cron / background_task 是编排层专属，子代理不该碰。
_SUBAGENT_DISABLED = ["agent", "workflow", "ask", "cron", "background_task"]

# 注入脚本的安全内建：去掉 import / open / eval / exec 等，保留编排常用的纯函数。
_SAFE_BUILTIN_NAMES = (
    "abs",
    "all",
    "any",
    "bool",
    "callable",
    "dict",
    "enumerate",
    "filter",
    "float",
    "format",
    "frozenset",
    "getattr",
    "hasattr",
    "int",
    "isinstance",
    "iter",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "setattr",
    "sorted",
    "str",
    "sum",
    "tuple",
    "type",
    "zip",
    # class 定义依赖 __build_class__，否则脚本里 `class X:` 直接 NameError
    "__build_class__",
    # 异常类型，让脚本能写 try/except
    "Exception",
    "ValueError",
    "KeyError",
    "TypeError",
    "RuntimeError",
)
_SAFE_BUILTINS = {n: getattr(builtins, n) for n in _SAFE_BUILTIN_NAMES}


class WorkflowScriptError(Exception):
    """脚本编译期错误（语法 / 空脚本）。"""


class WorkflowRuntimeError(Exception):
    """脚本运行期错误（如超过 agent 调用上限）。"""


@dataclass
class WorkflowOutcome:
    """workflow 跑完的产物。``result`` 是脚本 ``return`` 的值，形状由脚本决定。"""

    result: Any
    agent_count: int
    logs: list[str] = field(default_factory=list)


def _max_concurrency() -> int:
    return max(1, min(_HARD_CONCURRENCY_CAP, (os.cpu_count() or 4) - 2))


def _positional_arity(fn: Any) -> int:
    """统计可按位置传入的形参个数（封顶 3）；``*args`` 视为 3。无法内省时按 1——
    pipeline stage 绝大多数是单参 ``lambda d: ...``，按 1 调用比按 3 误传安全。"""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return 1
    count = 0
    for p in params:
        if p.kind is p.VAR_POSITIONAL:
            return 3
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
            count += 1
    return min(count, 3)


async def _ensure_awaitable(value: Any) -> Any:
    """stage / thunk 返回 coroutine 就 await，否则原样返回（允许同步 stage）。"""
    if inspect.isawaitable(value):
        return await value
    return value


class WorkflowEngine:
    """执行一段 workflow 脚本。一次构造，``compile()`` 后 ``run()`` 一次。"""

    def __init__(
        self,
        script: str,
        *,
        permission_engine: Any = None,
        tool_mode: str = "default",
        args: Any = None,
        name: str = "workflow",
    ) -> None:
        self._script = script
        # 子代理复用父 PermissionEngine（共享工作区边界 + 权限规则），故读得到父正在
        # 处理的工作文件——review/audit 类编排能跑的前提。
        self._permission_engine = permission_engine
        self._tool_mode = tool_mode
        self._args = args
        self._name = name

        self._code: Any = None
        self._semaphore = asyncio.Semaphore(_max_concurrency())
        self._agent_count = 0
        # 实时进度计数：dispatched=已派发（含排队）、done=已完成；running=agent_count-done。
        # 用于 drawer 卡片的聚合进度（total=dispatched，bar=done/dispatched）。
        self._dispatched = 0
        self._done = 0
        # 进度回调（由 workflow 工具绑定 task_id → TaskRegistry.notify_progress）；None=不发。
        self._progress_sink: Any = None
        self._current_phase: str | None = None
        self._logs: list[str] = []
        # 子代理实例按 agent_name 缓存复用——同一具名/默认子代理在扇出里只建一次
        # （工具表 + graph 编译 + MCP 发现都不重复）；lock 保证并发首次每键只建一次。
        self._agent_cache: dict[str | None, tuple[Any, Any]] = {}
        self._agent_cache_lock = asyncio.Lock()

    def compile(self) -> None:
        """把脚本包进 ``async def`` 并编译。语法错误抛 ``WorkflowScriptError``。"""
        if not self._script.strip():
            raise WorkflowScriptError("脚本为空")
        wrapped = "async def __workflow_main__():\n" + textwrap.indent(
            self._script, "    "
        )
        try:
            self._code = compile(wrapped, "<workflow>", "exec")
        except SyntaxError as e:
            raise WorkflowScriptError(f"语法错误: {e}") from e

    async def run(self) -> WorkflowOutcome:
        """执行脚本，返回其 ``return`` 值连同统计。``compile()`` 必须先调用。"""
        if self._code is None:
            self.compile()
        # __name__ 供脚本内 class 定义解析 __module__；__builtins__ 是受限白名单。
        namespace: dict[str, Any] = {
            "__name__": "__workflow__",
            "__builtins__": _SAFE_BUILTINS,
            **self._hooks(),
        }
        exec(self._code, namespace)  # noqa: S102 — 受限命名空间，见模块 docstring
        main = namespace["__workflow_main__"]
        result = await main()
        return WorkflowOutcome(
            result=result,
            agent_count=self._agent_count,
            logs=self._logs,
        )

    def set_progress_sink(self, sink: Any) -> None:
        """绑定进度回调 ``sink(progress: dict)``（workflow 工具绑定 task_id 后调用）。"""
        self._progress_sink = sink

    def _emit_progress(self) -> None:
        """把当前聚合进度快照推给 sink（phase 变更 / agent 起止时调用）。"""
        if self._progress_sink is None:
            return
        try:
            self._progress_sink(
                {
                    "phase": self._current_phase,
                    "done": self._done,
                    "total": self._dispatched,
                    "running": max(0, self._agent_count - self._done),
                    "agent_count": self._agent_count,
                }
            )
        except Exception:
            logger.error("[workflow:%s] progress sink 异常", self._name, exc_info=True)

    # ---- 注入脚本的钩子 ------------------------------------------------------

    def _hooks(self) -> dict[str, Any]:
        return {
            "agent": self._agent,
            "parallel": self._parallel,
            "pipeline": self._pipeline,
            "phase": self._phase,
            "log": self._log,
            # print 别名到 log：脚本里 print() 不会真打 stdout，统一进进度日志
            "print": self._log,
            "args": self._args,
        }

    async def _agent(
        self,
        prompt: str,
        *,
        schema: dict | None = None,
        label: str | None = None,
        phase: str | None = None,
        agent_name: str | None = None,
    ) -> Any:
        """派一个子代理。``schema`` 非空时强制结构化输出并返回校验过的 dict；
        否则返回子代理最后一条消息的文本。并发受 semaphore 排队。"""
        from langchain_core.messages import HumanMessage

        # 子代理构建（含首键 MCP 发现等冷启动开销）放 semaphore 外：它自带缓存 + lock，
        # 扇出里每键只建一次，不该占用并发槽拖住其他单元——槽只在真正 invoke 时占。
        sub_agent, context = await self._build_agent(agent_name)
        # 派发计数（含排队中）→ drawer 进度的 total；放在 build 成功之后：确保每个计入
        # total 的 agent 都会走到下方 finally 的 done++（build 失败的不计），否则 total 永久虚高、
        # 进度条到不了 100%。不发事件（避免突发扇出 N 次广播）。
        self._dispatched += 1

        async with self._semaphore:
            # 检查 + 自增紧贴、无 await → 硬上限。检查放 semaphore 外会让突发扇出在任何
            # 一次自增前全部通过、越过 _MAX_AGENTS。
            if self._agent_count >= _MAX_AGENTS:
                raise WorkflowRuntimeError(f"agent 调用超过上限 {_MAX_AGENTS}")
            self._agent_count += 1
            lbl = label or agent_name or f"agent#{self._agent_count}"
            logger.info(
                "[workflow:%s] ▶ %s (phase=%s)",
                self._name,
                lbl,
                phase or self._current_phase,
            )
            self._emit_progress()  # 进入运行（running++）

            try:
                inputs: dict[str, Any] = {
                    "messages": [HumanMessage(content=prompt)],
                    "tool_mode": self._tool_mode,
                }
                if schema:
                    inputs["output_schema"] = schema
                result = await sub_agent.graph.ainvoke(inputs, context=context)

                if schema:
                    out = result.get("structured_output")
                    if out is None:
                        # 结构化输出连续校验失败会被强制结束、structured_output 从不写入。
                        # 返回 None（脚本需用 filter 兜底），但留痕便于排查。
                        logger.warning(
                            "[workflow:%s] %s 结构化输出为空（校验失败或被中止）",
                            self._name,
                            lbl,
                        )
                    return out

                from lumi.agents.core.response import extract_ainvoke_content

                messages = result.get("messages") or []
                content = messages[-1].content if messages else ""
                return extract_ainvoke_content(content)
            finally:
                self._done += 1
                self._emit_progress()  # 完成（done++）

    async def _build_agent(self, agent_name: str | None) -> tuple[Any, Any]:
        """按 ``agent_name`` 缓存复用子代理实例——同一具名/默认子代理在扇出里只建一次。
        具名子代理用其自身配置；缺省用通用子代理（默认模型 + 全工具去递归类）。"""
        cached = self._agent_cache.get(agent_name)
        if cached is not None:  # 快路径：warm 后无锁
            return cached
        async with self._agent_cache_lock:
            cached = self._agent_cache.get(
                agent_name
            )  # 锁内二次判，并发首次每键只建一次
            if cached is None:
                cached = await self._create_agent(agent_name)
                self._agent_cache[agent_name] = cached
        return cached

    async def _create_agent(self, agent_name: str | None) -> tuple[Any, Any]:
        from lumi.agents.core.graph import create_agent
        from lumi.agents.tools import get_tools, load_agents

        if agent_name:
            configs = load_agents(name=agent_name)
            if not configs:
                raise WorkflowRuntimeError(f"子代理 '{agent_name}' 未找到")
            cfg = configs[0]
            tools = await get_tools(
                tools=cfg.tools or None, disabled_tools=_SUBAGENT_DISABLED
            )
            return await create_agent(
                tools=tools,
                system_prompt=cfg.system_prompt,
                model_name=cfg.model or None,
                permission_engine=self._permission_engine,
            )

        tools = await get_tools(disabled_tools=_SUBAGENT_DISABLED)
        return await create_agent(
            tools=tools,
            permission_engine=self._permission_engine,
        )

    # ---- 并发编排 -----------------------------------------------------------

    async def _parallel(self, thunks: list) -> list:
        """并行执行一组无参 thunk，屏障——全完成才返回。失败的 thunk 落为 None。"""

        async def _one(thunk: Any) -> Any:
            try:
                return await _ensure_awaitable(thunk())
            except Exception:
                logger.exception("[workflow:%s] parallel thunk 失败", self._name)
                return None

        return list(await asyncio.gather(*[_one(t) for t in thunks]))

    async def _pipeline(self, items: Any, *stages: Any) -> list:
        """每个 item 独立穿过全部 stage，无屏障。stage 收 ``(prev, item, idx)``
        （按其形参个数截取）；某 stage 抛错则该 item 掉为 None 并跳过其余 stage。"""
        item_list = list(items)
        # 每个 stage 的形参个数只内省一次，避免 N 个 item 重复 inspect 同一函数。
        arities = [_positional_arity(s) for s in stages]

        async def _one(item: Any, idx: int) -> Any:
            prev = item
            for stage, n in zip(stages, arities):
                try:
                    called = stage(*(prev, item, idx)[:n])
                    prev = await _ensure_awaitable(called)
                except Exception:
                    logger.exception(
                        "[workflow:%s] pipeline stage 失败 (item=%d)", self._name, idx
                    )
                    return None
            return prev

        return list(
            await asyncio.gather(*[_one(it, i) for i, it in enumerate(item_list)])
        )

    def _phase(self, title: str) -> None:
        self._current_phase = title
        logger.info("[workflow:%s] phase → %s", self._name, title)
        self._emit_progress()

    def _log(self, *messages: Any) -> None:
        msg = " ".join(str(m) for m in messages)
        self._logs.append(msg)
        logger.info("[workflow:%s] %s", self._name, msg)
