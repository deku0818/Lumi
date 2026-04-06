# TUI 改进计划

> 基于 2026-04-06 对 `lumi/tui/` 全模块（~20K 行）的代码审查。
> 按优先级排列，每项包含现状分析、改进方案和参考代码。

---

## 目录

1. [清理重复的主题检测代码](#1-清理重复的主题检测代码)
2. [AgentGroup 私有属性访问封装](#2-agentgroup-私有属性访问封装)
3. [GroupingEngine 同步契约加固](#3-groupingengine-同步契约加固)
4. [SubagentTracker 消除 magic prefix 键](#4-subagenttracker-消除-magic-prefix-键)
5. [审批 UI 改用 Textual ModalScreen](#5-审批-ui-改用-textual-modalscreen)
6. [LumiApp 上帝类拆分为 Controller 层](#6-lumiapp-上帝类拆分为-controller-层)
7. [引入 Protocol 接口层消除循环依赖](#7-引入-protocol-接口层消除循环依赖)

---

## 1. 清理重复的主题检测代码

**难度**：5min | **收益**：消除混淆 | **时机**：立即

### 现状

`app.py:179-234` 和 `_app_lifecycle.py:31-78` 存在完全重复的 `_detect_macos_theme` / `_detect_windows_theme` / `_apply_theme_mode` 实现。属于重构进行到一半的遗留。

### 方案

删除 `app.py` 中的重复方法，统一使用 `_app_lifecycle.py` 中的版本：

```python
# app.py — 删除以下方法：
#   _detect_system_theme()
#   _detect_macos_theme()
#   _detect_windows_theme()
#   _apply_theme_mode()

# 在 _finish_mount 或其他需要主题检测的地方，改为调用：
from lumi.tui._app_lifecycle import apply_theme_mode

await apply_theme_mode(self, self._global_config.theme_mode)
```

---

## 2. AgentGroup 私有属性访问封装

**难度**：5min | **收益**：封装性 | **时机**：立即

### 现状

`event_router.py:126` 直接访问 `AgentGroup` 的私有属性：

```python
entry = group._entries.get(run_id)
```

### 方案

在 `AgentGroup` 上暴露查询方法：

```python
# widgets/agent_group.py
def has_entry(self, run_id: str) -> bool:
    """检查指定 run_id 是否在当前 AgentGroup 中。"""
    return run_id in self._entries
```

`event_router.py` 改为：

```python
if not group.has_entry(run_id):
    logger.debug(
        "_dispatch_subagent: parent_run_id=%s not in entries (kind=%s)",
        run_id, evt.kind,
    )
```

---

## 3. GroupingEngine 同步契约加固

**难度**：30min | **收益**：防御性编程，开发阶段快速暴露调用顺序错误 | **时机**：本周

### 现状

`grouping.py` 中 `decide_tool()` 后必须调用 `on_tool_started()` 的契约仅写在 docstring 中，违反时无任何运行时反馈。

### 方案

添加 `_pending_decision` 守卫字段：

```python
class GroupingEngine:
    __slots__ = (
        "_has_pending", "_has_active_group", "_has_agent_group",
        "_pending_decision",
    )

    def __init__(self) -> None:
        self._has_pending: bool = False
        self._has_active_group: bool = False
        self._has_agent_group: bool = False
        self._pending_decision: GroupDecision | None = None

    def decide_tool(self, name: str, approval_mode: bool) -> GroupDecision:
        if self._pending_decision is not None:
            raise RuntimeError(
                f"decide_tool() called twice without on_tool_started(). "
                f"Pending decision: {self._pending_decision}"
            )
        decision = self._compute_decision(name, approval_mode)
        self._pending_decision = decision
        return decision

    def on_tool_started(self, decision: GroupDecision) -> None:
        if self._pending_decision is None:
            raise RuntimeError(
                "on_tool_started() called without prior decide_tool()"
            )
        if self._pending_decision != decision:
            raise RuntimeError(
                f"on_tool_started({decision}) doesn't match "
                f"pending decision {self._pending_decision}"
            )
        self._pending_decision = None
        # ... 原有状态更新逻辑不变

    def _compute_decision(self, name: str, approval_mode: bool) -> GroupDecision:
        """原 decide_tool 的纯逻辑部分。"""
        if name == "agent":
            return GroupDecision.AGENT
        if should_exclude_from_group(name, approval_mode):
            return GroupDecision.STANDALONE
        if self._has_pending or self._has_active_group:
            return GroupDecision.GROUP_APPEND
        return GroupDecision.GROUP_FIRST

    def flush_tools(self) -> None:
        self._has_pending = False
        self._has_active_group = False
        self._pending_decision = None  # flush 时也要清除

    def reset(self) -> None:
        self._has_pending = False
        self._has_active_group = False
        self._has_agent_group = False
        self._pending_decision = None
```

---

## 4. SubagentTracker 消除 magic prefix 键

**难度**：1h | **收益**：可读性和可维护性 | **时机**：本周

### 现状

`subagent_tracker.py` 中使用 `f"__unmapped_{id(state.agent_block)}"` 作为字典键，将真实 run_id 和 unmapped 占位符混在同一个 `_by_run_id` 字典中。

问题：
- `id()` 在对象回收后可能被复用（虽然实践中概率极低）
- 字典键语义不统一，遍历时需要判断前缀
- 可调试性差

### 方案

分离为两个数据结构：

```python
class SubagentTracker:
    def __init__(self) -> None:
        self._by_run_id: dict[str, SubagentState] = {}   # 真实 run_id 映射
        self._unmapped: list[SubagentState] = []          # 等待 remap 的状态
        self._approval_run_id: str | None = None

    def mark_unmapped(self, run_id: str) -> None:
        """将 agent 运行标记为 unmapped（cancel/reject 后等待 replay 复用）。"""
        state = self._by_run_id.pop(run_id, None)
        if state is None:
            return
        state.finalize_assistant_msg()
        state.tool_blocks.clear()
        self._unmapped.append(state)

    def find_unmapped_running(self, args: dict | None = None) -> ToolBlock | None:
        """查找尚未被真实 run_id 映射的 RUNNING agent ToolBlock。"""
        from lumi.tui.widgets.tool_block import ToolStatus

        fallback: ToolBlock | None = None
        for state in self._unmapped:
            if state.agent_block.status == ToolStatus.RUNNING:
                if args and state.agent_block._args == args:
                    return state.agent_block
                if fallback is None:
                    fallback = state.agent_block
        return fallback

    def remap(self, new_run_id: str, agent_block: ToolBlock) -> SubagentState | None:
        """将已有 block 关联到新的 run_id（replay 场景）。"""
        # 从 _unmapped 中取出
        target: SubagentState | None = None
        for i, state in enumerate(self._unmapped):
            if state.agent_block is agent_block:
                target = self._unmapped.pop(i)
                break

        # 也从 _by_run_id 中清除旧映射（如有）
        old_keys = [k for k, v in self._by_run_id.items()
                    if v.agent_block is agent_block]
        for k in old_keys:
            del self._by_run_id[k]

        if target is None:
            return None

        target.run_id = new_run_id
        target.finalize_assistant_msg()
        target.tool_blocks.clear()
        target.pending_dom_clear = True
        self._by_run_id[new_run_id] = target
        return target

    def prepare_for_resume(self) -> None:
        """resume 前调用：将所有活跃状态移入 _unmapped。"""
        seen: set[int] = set()
        for state in list(self._by_run_id.values()):
            sid = id(state)
            if sid in seen:
                continue
            seen.add(sid)
            state.finalize_assistant_msg()
            state.tool_blocks.clear()
            self._unmapped.append(state)
        self._by_run_id.clear()
        self._approval_run_id = None

    @property
    def active_run_ids(self) -> frozenset[str]:
        return frozenset(self._by_run_id)

    def reset(self) -> None:
        self._by_run_id.clear()
        self._unmapped.clear()
        self._approval_run_id = None
```

**注意事项**：`prepare_for_resume` 中不再需要保留旧 run_id 键，因为 replay 期间携带旧 `parent_run_id` 的子代理事件可以在 `get()` 未命中时，回退查 `_unmapped` 列表。需要在 `get()` 中加 fallback 逻辑，或者在 `prepare_for_resume` 中同时保留旧键映射（作为过渡别名）。

---

## 5. 审批 UI 改用 Textual ModalScreen

**难度**：2-3h | **收益**：消除所有 `_hidden_for_approval` 状态补丁 | **时机**：下次修改审批流程时

### 现状

审批 widget 通过 `app.mount(approval, before=input_bar)` 插入 DOM，然后手动：

```python
hide_input_for_approval(app)        # 隐藏输入栏
app._hide_todos_bar_for_approval()  # 隐藏待办栏
# ... 审批结束后
restore_input_after_approval(app)
app._restore_todos_bar_after_approval()
```

每新增一个需要在审批期间隐藏的元素，就要新增一对 hide/restore 方法和一个 bool flag。

### 方案

使用 Textual 原生的 `ModalScreen`：

```python
# tui/screens/approval_screen.py
from textual.screen import ModalScreen

class ApprovalScreen(ModalScreen[str]):
    """审批浮层 — Textual 原生模态，自动遮盖底层 UI。"""

    DEFAULT_CSS = """
    ApprovalScreen {
        align: center bottom;
    }
    """

    def __init__(self, approval_widget: Widget) -> None:
        super().__init__()
        self._widget = approval_widget

    def compose(self) -> ComposeResult:
        yield self._widget
```

调用方：

```python
# _app_approval.py — 统一简化
async def handle_tool_approval(app: LumiApp, evt: BridgeEvent, chat_log: ChatLog) -> None:
    app._assembler.finalize_assistant_msg()
    app._run.last_approval_data = dict(evt.data or {})
    app._run.last_approval_tool_calls = (evt.data or {}).get("tool_calls", [])

    approval = ToolApproval(evt.data)
    # push_screen_wait 阻塞直到用户做出决定，底层 UI 自动被遮盖
    decision = await app.push_screen_wait(ApprovalScreen(approval))
    await on_tool_approval_decided(app, decision)
```

**收益**：
- 删除 `hide_input_for_approval` / `restore_input_after_approval`
- 删除 `_hide_todos_bar_for_approval` / `_restore_todos_bar_after_approval`
- 删除 `_todos_hidden_for_approval` flag
- 未来新增底层 UI 元素时无需修改审批代码

**注意事项**：
- `ToolApproval` / `AskDialog` / `PlanApproval` 的 `Decided` / `Answered` 消息机制需要改为调用 `screen.dismiss(result)` 返回结果
- 子代理审批的 `set_approval_context` 仍然需要，但 UI 可见性管理完全由 ModalScreen 接管

---

## 6. LumiApp 上帝类拆分为 Controller 层

**难度**：1-2d | **收益**：架构质量，降低模块间耦合 | **时机**：功能冻结后的重构迭代

### 现状

`LumiApp.__init__` 有 15+ 个实例变量。4 个 `_app_*.py` 文件的函数全部以 `app: LumiApp` 作为第一个参数，大量直接访问 `app._assembler`、`app._run`、`app._bridge` 等私有属性。本质上是把方法搬到了外面，并没有降低耦合。

### 方案

提取三个独立的 Controller，各自持有最小依赖集：

```
LumiApp (Composition Root)
├── ApprovalController   ← 审批流程（tool approval / ask / plan）
├── InputController      ← 输入处理（提交 / Ctrl+C / Esc / 滚动）
└── CronController       ← 定时任务（初始化 / 轮询通知 / 状态回调）
```

#### Controller 设计原则

1. **通过构造函数注入依赖**，不持有 `app` 引用
2. **暴露纯净的异步方法**，`LumiApp` 只做事件转发
3. **各自管理自己的状态**，不共享 mutable state

```python
# tui/controllers/approval.py
class ApprovalController:
    def __init__(
        self,
        assembler: WidgetAssembler,
        tracker: SubagentTracker,
        run: RunContext,
        push_modal: Callable[[ModalScreen], Awaitable[str]],
    ) -> None:
        self._asm = assembler
        self._tracker = tracker
        self._run = run
        self._push_modal = push_modal

    async def handle_tool_approval(self, evt: BridgeEvent) -> None:
        """处理工具审批事件，返回后 agent 自动 resume。"""
        self._asm.finalize_assistant_msg()
        self._run.last_approval_data = dict(evt.data or {})
        tool_calls = (evt.data or {}).get("tool_calls", [])
        self._run.last_approval_tool_calls = tool_calls
        for tc in tool_calls:
            key = tc.get("id") or tc.get("name", "unknown")
            self._asm.pop_tool_block(key)

        if evt.parent_run_id:
            self._tracker.set_approval_context(evt.parent_run_id)

        decision = await self._push_modal(ApprovalScreen(ToolApproval(evt.data)))
        self._tracker.clear_approval_context()
        return decision
```

```python
# tui/app.py — LumiApp 退化为 Composition Root
class LumiApp(App):
    async def _finish_mount(self) -> None:
        # ... 初始化各组件 ...
        self._approval = ApprovalController(
            assembler=self._assembler,
            tracker=self._subagent_tracker,
            run=self._run,
            push_modal=self.push_screen_wait,
        )
        self._input = InputController(...)
        self._cron = CronController(...)
```

#### AppCallbacks Protocol 的演变

当前 `EventRouter` 的 `AppCallbacks` 中的方法可以逐步迁移到对应 Controller：

```python
# 迁移前
class AppCallbacks(Protocol):
    async def _handle_ask(self, evt, chat_log) -> None: ...
    async def _handle_tool_approval(self, evt, chat_log) -> None: ...
    def _finish_run(self) -> None: ...

# 迁移后 — EventRouter 直接持有 Controller 引用
class EventRouter:
    def __init__(
        self,
        run: RunContext,
        assembler: WidgetAssembler,
        tracker: SubagentTracker,
        approval: ApprovalController,
        on_finish: Callable[[], None],
    ) -> None: ...
```

---

## 7. 引入 Protocol 接口层消除循环依赖

**难度**：1-2d | **收益**：消除 function-level import，提高可测试性 | **时机**：与 #6 一起

### 现状

TUI 模块间存在循环依赖链（如 `app → widgets → assembler → app`），通过大量 function-level import 绕过：

- `_app_input.py`：~15 处
- `_app_approval.py`：~8 处
- `event_router.py`：~5 处

### 方案

创建独立的 Protocol 定义模块：

```python
# tui/protocols.py — 所有跨模块接口集中定义（无 Textual 依赖）
from __future__ import annotations
from typing import Any, Protocol

class ChatLogLike(Protocol):
    """ChatLog 的最小接口。"""
    async def mount(self, widget: Any) -> None: ...
    async def auto_scroll_if_needed(self) -> None: ...
    async def append_error(self, title: str, msg: str) -> None: ...
    async def append_hint(self, prefix: str, text: str, **kw: Any) -> None: ...

class AssemblerLike(Protocol):
    """WidgetAssembler 的最小接口。"""
    @property
    def tool_blocks(self) -> dict[str, Any]: ...
    def finalize_assistant_msg(self) -> None: ...
    async def flush_groups(self) -> None: ...
    async def apply_item(self, item: Any) -> None: ...

class InputBarLike(Protocol):
    """InputBar 的最小接口。"""
    def set_disabled(self, disabled: bool) -> None: ...
    def set_plan_mode(self, enabled: bool) -> None: ...
    def flash_message(self, msg: str) -> None: ...
    @property
    def display(self) -> bool: ...
    @display.setter
    def display(self, value: bool) -> None: ...
```

下游模块依赖 Protocol 而非具体类，打断循环链：

```python
# event_router.py — 改为依赖 Protocol
from lumi.tui.protocols import ChatLogLike, AssemblerLike

class EventRouter:
    def __init__(
        self,
        run: RunContext,
        assembler: AssemblerLike,   # 不再直接导入 WidgetAssembler
        tracker: SubagentTracker,
        ...
    ) -> None: ...
```

**好处**：
- function-level import 可以改回 module-level，提高代码可读性
- Protocol 接口天然支持 mock，测试时无需挂载真实 Textual widget
- 新增模块时无需担心循环依赖

---

## 优先级总览

| # | 改进项 | 难度 | 收益 | 建议时机 |
|---|--------|------|------|----------|
| 1 | 清理重复主题检测 | 5min | 消除混淆 | 立即 |
| 2 | AgentGroup 私有属性封装 | 5min | 封装性 | 立即 |
| 3 | GroupingEngine 守卫 | 30min | 防御性 | 本周 |
| 4 | SubagentTracker 分离字典 | 1h | 可读性 | 本周 |
| 5 | 审批改 ModalScreen | 2-3h | 消除状态补丁 | 下次动审批时 |
| 6 | App Controller 拆分 | 1-2d | 架构质量 | 功能冻结后 |
| 7 | Protocol 接口层 | 1-2d | 消除循环依赖 | 与 #6 一起 |

建议 #1-#4 作为日常清理逐步完成，#5 在下次修改审批流程时顺手做，#6 和 #7 作为一次专门的重构迭代。
