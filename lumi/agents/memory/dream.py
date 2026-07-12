"""后台 Dream：会话结束时离线把近期会话的零散记忆综合成连贯记忆。

触发=Stop hook（:func:`auto_dream_stop_hook`）按廉价门控阶梯判断，全过则 fire-and-forget
启动后台 dream agent。**综合归 dream、裁决归召回**（设计见 docs/architecture/memory.md）：
dream 只做 synthesis（合并近重复 / 相对日期转绝对 / 规范化索引），不做冲突的自由判决——
那交给召回时手握当前 query 的活模型。

- **防自递归**：dream agent inputs 带 ``depth=1``，其 stop 经 depth 门直接放行；派生 task 内
  ``set_run_config_hooks(None)`` 清项目 config hooks。``enable_memory`` 不再背防递归的锅。
- **per-project 隔离**：锁 / lastAt / 会话门 / 导出 / 写入全按当前 project，与记忆目录同构。
- **当前会话**靠完整 ``messages`` 进 dream（质量天花板高于 grep）；**其他近期会话**导出为扁平
  text 供 grep。
"""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage

from lumi.agents.memory import dream_lock
from lumi.agents.memory.normalize import normalize_memory_index
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config
from lumi.utils.thread_id import is_channel_thread

if TYPE_CHECKING:
    # 仅类型注解（本模块有 `from __future__ import annotations`，注解为字符串、运行时不求值）。
    # 运行时 import hooks.schema 会触发 hooks/__init__ → builtin → 回头 import 本模块，形成
    # 循环（当 dream 是首个被 import 的模块时直接报错）。移进 TYPE_CHECKING 从根上断环。
    from lumi.agents.core.hooks.schema import HookContext, HookResult

# dream agent 工具白名单：只读 + 写记忆目录。不给 bash/agent/cron/skill/workflow（防递归 + 防危险）。
_DREAM_TOOL_NAMES = {"read", "grep", "glob", "write", "edit"}
# 时间门过后两次会话扫描的最小间隔（秒）——会话门长期不够时避免每次 stop 都建只读图查 DB。
_SCAN_THROTTLE_SECONDS = 600
# 持后台 dream task 的强引用，防 asyncio 只持弱引用、await LLM 时被 GC 取消。
_DREAM_TASKS: set[asyncio.Task] = set()


async def auto_dream_stop_hook(ctx: HookContext) -> HookResult:
    """Stop 时按门控阶梯触发后台 dream；廉价先判，任一不过 ``return None``（放行 END）。"""
    state = ctx.state
    # 1. depth 门（防自递归，首要）：dream agent 自身 stop 在此直接放行
    if state.get("depth", 0) > 0:
        return None
    # 长会话（IM 渠道）不走 Stop 触发的增量 dream——一群/一人是单个永久会话，凑不出
    # 「N 个新会话」的增量门语义；改由渠道每日定时统一 dream + summary（见 feishu channel）。
    thread_id = (ctx.config.get("configurable") or {}).get("thread_id", "")
    if is_channel_thread(thread_id):
        return None
    runtime = ctx.runtime
    if runtime is None:
        return None
    context = runtime.context
    # 2. 记忆开关（子 agent / cron / 后台天然 False）+ 跳过结构化输出轮 + config 开关
    if not getattr(context, "memory_enabled", False):
        return None
    if state.get("output_schema"):
        return None
    cfg = get_config().config.auto_dream
    if not cfg.enabled:
        return None
    engine = getattr(context, "permission_engine", None)
    if engine is None:
        return None
    project_dir = engine.project_dir
    # 3. 并发锁
    if dream_lock.is_in_flight(project_dir):
        return None
    # 4. 时间门
    if time.time() - dream_lock.read_last_at(project_dir) < cfg.min_hours * 3600:
        return None
    # 5. 扫描节流（时间门长期满足时避免每次 stop 都查 DB）
    if dream_lock.throttle_scan(project_dir, _SCAN_THROTTLE_SECONDS):
        return None

    # 全过 → fire-and-forget。会话门 + 导出 + 综合都在 task 内，不阻塞 stop 返回 END。
    workspace = (ctx.config.get("metadata") or {}).get("workspace_dir", "")
    current_thread = (ctx.config.get("configurable") or {}).get("thread_id", "")
    _spawn_dream(
        context, list(state.get("messages", [])), workspace, current_thread, force=False
    )
    return None


def _spawn_dream(
    context, current_messages, workspace: str, current_thread: str, *, force: bool
) -> None:
    """落 in_flight + fire-and-forget 启动后台 dream task（auto hook 与 /dream 共用）。"""
    project_dir = context.permission_engine.project_dir
    dream_lock.mark_in_flight(project_dir)
    task = asyncio.create_task(
        _run_dream(context, current_messages, workspace, current_thread, force=force)
    )
    _DREAM_TASKS.add(task)
    task.add_done_callback(_DREAM_TASKS.discard)
    task.add_done_callback(lambda _t: dream_lock.clear_in_flight(project_dir))


async def start_dream(
    context, current_messages, workspace: str, current_thread: str
) -> str:
    """主动触发 dream（/dream 命令）：绕过时间 / 会话 / 节流门，仅 in_flight 防重复。

    返回给用户看的提示文本。force 跑：即便近期没有其他会话，也综合当前会话进记忆。
    """
    if not workspace or context.permission_engine is None:
        return "当前会话未绑定项目，无法整理记忆。"
    if dream_lock.is_in_flight(context.permission_engine.project_dir):
        return "🌙 已有一次记忆整理在进行中，请稍候。"
    _spawn_dream(context, current_messages, workspace, current_thread, force=True)
    return "🌙 已在后台开始整理记忆（综合近期会话）——完成后会通知你。"


async def start_dream_session(
    context, current_messages, workspace: str, current_thread: str
) -> str:
    """主动触发**长会话 dream**（/dream-session 命令）：只综合当前会话，后台 fire-and-forget。

    与 /dream 的差别：不导出/综合其他会话，只吃当前这段永久会话的历史——供 IM（一群/一人
    一个常驻 thread）手动整理用。返回给用户看的提示文本。
    """
    if not workspace or context.permission_engine is None:
        return "当前会话未绑定项目，无法整理记忆。"
    project_dir = context.permission_engine.project_dir
    if dream_lock.is_in_flight(project_dir):
        return "🌙 已有一次记忆整理在进行中，请稍候。"

    dream_lock.mark_in_flight(project_dir)
    task = asyncio.create_task(
        consolidate_session_dream(
            project_dir, current_messages, current_thread, time.time(), notify=True
        )
    )
    _DREAM_TASKS.add(task)
    task.add_done_callback(_DREAM_TASKS.discard)
    task.add_done_callback(lambda _t: dream_lock.clear_in_flight(project_dir))
    return "🌙 已在后台开始整理本会话记忆——完成后会通知你。"


async def _run_dream(
    context,
    current_messages,
    workspace: str,
    current_thread: str,
    *,
    force: bool = False,
):
    """后台主体：会话门 → 导出其他会话 → 建 dream agent → 综合（bg-task 收尾）。"""
    # 延迟 import，避免 hook 模块顶层与 core.graph / tools 循环依赖
    from lumi.agents.core.graph import create_agent
    from lumi.sessions.session_store import list_sessions

    engine = context.permission_engine
    project_dir = engine.project_dir
    if not workspace:
        # 防御：workspace 为空会让 list_sessions 不按 project 过滤 → 跨 project 综合。
        # 正常 bridge 流程恒有 metadata.workspace_dir，此处仅作保险。
        logger.debug("[dream] workspace 为空，跳过以避免跨 project 综合")
        return
    cfg = get_config().config.auto_dream
    last_at = dream_lock.read_last_at(project_dir)
    snapshot_ts = time.time()  # 本次综合的内容边界，成功后写回作下次门控基准

    # 只读 graph 读其他会话 checkpoint。checkpoint 模式须与 bridge **同源**（agents.checkpoint）：
    # 硬编码 sqlite 会让 postgres 用户的会话（存于 postgres）读不到 → dream 静默永不触发。
    # 用完即 aclose，避开与 bridge 的写锁长争用。
    checkpoint_mode = get_config().config.agents.checkpoint
    reader, _ = await create_agent(
        checkpoint=checkpoint_mode, tools=[], project_dir=project_dir
    )
    try:
        sessions = await list_sessions(reader.graph, workspace=workspace, limit=50)
        # 会话门：自上次 dream 以来活跃（最新 checkpoint 晚于 last_at）的其他会话数。
        # created_at 取自最新 checkpoint = 最后活动时间，老会话有新活动同样计入。
        recent = [
            s
            for s in sessions
            if s.thread_id != current_thread and s.created_at.timestamp() > last_at
        ]
        if not force and len(recent) < cfg.min_sessions:
            logger.debug(
                "[dream] 会话门未过：活跃 %d < %d，跳过", len(recent), cfg.min_sessions
            )
            return
        transcript_dir = await _export_sessions(reader, recent, project_dir)
    finally:
        await reader.aclose()  # 复用 LumiAgent.aclose（= close_checkpointer）

    try:
        # 手动 /dream（force）完成后往会话汇报；Stop 钩子自动触发静默（无人发起不打扰）
        await _consolidate(
            project_dir, current_messages, transcript_dir, snapshot_ts, notify=force
        )
    finally:
        shutil.rmtree(transcript_dir, ignore_errors=True)


async def _export_sessions(reader, sessions, project_dir: Path) -> Path:
    """把其他近期会话各导出为扁平 text（一行一消息）到 per-project 临时目录。"""
    from lumi.sessions.message_text import extract_messages_as_text
    from lumi.utils.paths import lumi_tmp_dir, project_slug

    out_dir = lumi_tmp_dir("dream_transcripts", project_slug(project_dir))
    for stale in out_dir.glob("*.txt"):
        stale.unlink(missing_ok=True)

    async def _export_one(s) -> None:
        snap = await reader.graph.aget_state(
            {"configurable": {"thread_id": s.thread_id}}
        )
        msgs = (snap.values or {}).get("messages", []) if snap else []
        text = extract_messages_as_text(msgs)
        if text:
            (out_dir / f"{s.thread_id}.txt").write_text(text, encoding="utf-8")

    # 各会话独立读，并发取 checkpoint（会话多时显著快于串行）
    await asyncio.gather(*(_export_one(s) for s in sessions))
    return out_dir


async def _consolidate(
    project_dir: Path,
    current_messages,
    transcript_dir: Path,
    snapshot_ts: float,
    *,
    notify: bool,
):
    """短会话 dream：当前会话完整 message + 其他近期会话 grep，综合进记忆。"""
    await _run_dream_fork(
        project_dir,
        current_messages,
        _consolidation_prompt(transcript_dir),
        label=f"dream:{project_dir.name}",
        notify=notify,
        record=lambda: dream_lock.record_dream(project_dir, snapshot_ts),
    )


async def consolidate_session_dream(
    project_dir: Path,
    current_messages,
    thread_id: str,
    snapshot_ts: float,
    *,
    notify: bool,
):
    """长会话 dream：**只**综合当前会话（无其他会话导出），供 IM 渠道每日定时与
    ``/dream-session`` 手动命令共用。可 await（渠道每日循环据此串行 + 屏障）。"""
    await _run_dream_fork(
        project_dir,
        current_messages,
        _consolidation_prompt_session(),
        label=f"dream-session:{project_dir.name}",
        notify=notify,
        record=lambda: dream_lock.record_thread_dream(
            project_dir, thread_id, snapshot_ts
        ),
    )


async def _run_dream_fork(
    project_dir: Path,
    current_messages,
    prompt_text: str,
    *,
    label: str,
    notify: bool,
    record: Callable[[], None],
):
    """共享底座：fork 主 agent（含记忆指令、只读+写记忆工具），经 bg-task 收尾。

    两种 dream（短/长会话）唯一差别是 ``prompt_text`` 与是否导出其他会话——导出与门控
    在调用方，本函数只管「fork + 注入提示 + 综合 + 收尾记账」。全程持 per-project
    ``dream_lock.project_lock``：MEMORY.md 恒只有一个写者，任何入口都绕不开（迟到者
    原地排队，写完再上）。``record``：综合成功后的记账（写回快照时刻，短/长会话各记
    各的表）。``notify``：完成后是否往会话汇报——手动命令给回音，自动触发（Stop 钩子 /
    每日定时）静默。
    """
    from lumi.agents.core.graph import create_agent
    from lumi.agents.core.hooks.dispatch import set_run_config_hooks
    from lumi.agents.core.response import extract_ainvoke_content
    from lumi.agents.permissions.workspace import set_run_authorized_source_for
    from lumi.agents.runtime.bg_tasks import (
        BackgroundTaskEntry,
        TaskKind,
        TaskStatus,
        bg_tasks_dir,
        get_task_registry,
        run_background_task,
    )
    from lumi.agents.tools import get_tools

    async with dream_lock.project_lock(project_dir):
        # dream 白名单全是内建工具：get_tools 的覆盖检查自动免等 MCP 冷池
        dream_tools = await get_tools(tools=list(_DREAM_TOOL_NAMES))
        # enable_memory=True：create_agent 自行组装含记忆指令的 system_prompt（与主 agent
        # 同构，不重复追加）。独立 PermissionEngine（None 时按 project_dir 新建）。
        agent, ctx = await create_agent(
            tools=dream_tools,
            enable_memory=True,
            checkpoint=None,
            project_dir=project_dir,
        )
        # dream agent 无交互审批通道，固定 privileged（tool_mode 是 context 属性）
        ctx.tool_mode = "privileged"
        inputs = {
            "messages": [*current_messages, HumanMessage(content=prompt_text)],
            "depth": 1,  # 防自递归：dream agent 的 stop 经 depth 门直接放行
        }

        task_id = f"dream_{uuid.uuid4().hex[:12]}"
        output_file = bg_tasks_dir() / f"{task_id}.txt"
        entry = BackgroundTaskEntry(
            task_id=task_id,
            kind=TaskKind.AGENT,
            status=TaskStatus.RUNNING,
            label=label,
            started_at=time.time(),
            output_file=output_file,
        )
        get_task_registry().register(entry)
        entry.async_task = asyncio.current_task()  # 使面板取消生效

        async def _produce() -> str:
            # 授权指向 dream 自己 engine（非 bridge 活引用，切项目不失配）+ 清 config hooks
            set_run_authorized_source_for(ctx.permission_engine)
            set_run_config_hooks(None)
            result = await agent.graph.ainvoke(inputs, context=ctx)
            normalize_memory_index(project_dir)  # 兜底规范化索引行的 [type · 日期]
            # 综合成功才推进快照时刻（失败则不动，下次仍按旧边界判活）
            record()
            msgs = result.get("messages") or []
            return extract_ainvoke_content(msgs[-1].content) if msgs else "dream 完成"

        await run_background_task(
            task_id,
            output_file,
            _produce,
            cancel_text="dream 综合已取消",
            notify=notify,
        )


def _consolidation_prompt(transcript_dir: Path) -> str:
    """dream 的四阶段指令（作一条 HumanMessage 注入，相当于用户敲 /dream）。"""
    return f"""# Dream：记忆综合

现在做一次 dream——离线回看最近的对话，把零散记忆综合成连贯、好用的持久记忆，让未来的
会话能快速进入状态。记忆的格式、类型、该存什么/不该存什么，**以你系统提示里的「持久记忆」
段为准**（那是唯一事实源）。

当前这段对话的完整历史已在上文；其他近期会话已导出为扁平 text（一行一消息）放在
`{transcript_dir}`，需要具体上下文时用 grep 窄关键词去查，**不要整篇读**。

## 阶段 1 — 定位
- 列出记忆目录、读 MEMORY.md 索引，浏览已有 topic 文件，以便**改进而非新建重复**。

## 阶段 2 — 收集信号
从上文当前会话 + 必要时 grep `{transcript_dir}` 里其他会话，找出值得长期记住的新信号
（用户偏好、工作方式、项目背景等），只看你已经怀疑重要的东西。

## 阶段 3 — 综合（synthesis）
把新信号写入或并入记忆文件，重点：
- **合并近重复**：并入已有 topic 文件，别造几乎一样的新文件。
- 相对日期（「昨天」「上周」）转成绝对日期。
- **不要在这里做冲突的自由裁决**——「哪条偏好现在作数」交给召回时的活模型；你只负责把
  碎片综合成连贯记忆。若发现明显被现状推翻的过时事实，就地更正。

## 阶段 4 — 收尾索引
更新 MEMORY.md：每条指针一行 `- [标题](文件.md) [type · 写入日期] — 钩子`，保持精简
（删除已失效的指针、为新记忆补指针）。

最后用一两句话总结你综合 / 更新 / 删除了什么；若记忆已经很紧凑、无事可做，直说即可。"""


def _consolidation_prompt_session() -> str:
    """长会话 dream 指令：只面向**当前这一段永久会话**，无其他会话、无 transcript。

    与 ``_consolidation_prompt`` 是两种任务框架——这里是"回顾与某个群/某个人的同一段
    对话，把耐久信号沉淀进记忆"，且这段会话**紧接会被 summary 压掉细节**，所以先把值得
    留的写进记忆再压缩。"""
    return """# Dream：本会话记忆沉淀

现在做一次 dream——回顾上文这段对话，把其中值得长期记住的东西沉淀进持久记忆。记忆的
格式、类型、该存什么 / 不该存什么，**以你系统提示里的「持久记忆」段为准**（唯一事实源）。

⚠️ 这段会话**紧接就会被摘要压缩、丢弃细节**——所以现在就要把值得留的先写进记忆，过后无法
再从原始对话里捞。

## 阶段 1 — 定位
- 列出记忆目录、读 MEMORY.md 索引，浏览已有 topic 文件，以便**改进而非新建重复**。

## 阶段 2 — 收集信号
从上文这段对话里找出值得长期记住的新信号（用户 / 群的偏好、工作方式、项目背景、达成的
决定、在办事项等），只看你已经怀疑重要的东西。

## 阶段 3 — 综合（synthesis）
把新信号写入或并入记忆文件，重点：
- **合并近重复**：并入已有 topic 文件，别造几乎一样的新文件。
- 相对日期（「昨天」「上周」）转成绝对日期。
- **不要在这里做冲突的自由裁决**——「哪条现在作数」交给召回时手握当前 query 的活模型；
  你只负责把碎片综合成连贯记忆。若发现明显被现状推翻的过时事实，就地更正。

## 阶段 4 — 收尾索引
更新 MEMORY.md：每条指针一行 `- [标题](文件.md) [type · 写入日期] — 钩子`，保持精简
（删除已失效的指针、为新记忆补指针）。

最后用一两句话总结你综合 / 更新 / 删除了什么；若无事可做，直说即可。"""
