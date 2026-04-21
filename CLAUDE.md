# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

Lumi 是一个基于 LangGraph 的 AI Agent 框架，提供终端 TUI 界面（Textual）和 HTTP API（FastAPI）两种交互方式。支持多模型（OpenAI、Anthropic、Bedrock）、工具调用、权限控制、定时任务、技能扩展和 MCP 协议集成。
Lumi 并非仅仅面向Coder，也面向所有非技术人员。

## 代码风格

- 架构采用分层设计：底层提供基本操作和数据结构，组合后具备充分的灵活性；高层提供开箱即用的 API，足以满足大多数使用场景。
- 偏好简洁明确的函数，每个函数专注于单一任务，输入和输出类型应明确指定。
- 用最少的代码解决问题，不要任何预设之外的东西，如果你写了 200 行，而 50 行足矣，推倒重写。
- 不为不可能发生的场景写错误处理。
- 自问一句："一位资深工程师看到这段代码，会觉得过度设计吗？"答案若是肯定，就精简。
- 偏好不可变对象（初始化后不再变化）。复用性优先。

项目使用 uv 管理；项目依赖始终使用 uv 进行管理，而非直接改 `pyproject.toml`。

## 重要原则

- 对于不确定的东西不要"猜"而是"验证"，禁止"可能是这样"的行为

## 架构概要

### Agent Graph（LangGraph）

核心入口是 `lumi/agents/core/graph.py` 中的 `LumiAgent`，继承自 `BaseGraph`（模板模式）。

**Graph 流程：**
```
START → PreprocessMessages → CallModel → is_use_tool() 条件路由:
  ├─ ToolExecutor（工具已授权或 BYPASS_TOOLS）→ after_tool_executor → CallModel（循环）
  ├─ HumanApproval（需要用户审批）→ ToolExecutor
  ├─ ExtractStructuredOutput（结构化输出）→ END
  └─ END（无工具调用）
并行分支: Summarizer（对话摘要）→ END
```

**关键状态 `LumiAgentState`：** messages（LangGraph add_messages reducer）、tool_mode（auto/privileged）、summary、todos、output_schema 等。

**运行时上下文 `LumiAgentContext`：** 通过 LangGraph 的 `Runtime` 参数传递，包含 tools、system_prompt、model_name、permission_engine。在节点函数中通过 `Runtime[LumiAgentContext]` 访问。所有节点共享同一实例。

### 工具系统

- **ToolRegistry**（`agents/tools/registry.py`）：单例，支持两种 provider 注册方式——异步函数或模块（自动收集模块内所有 `StructuredTool`）
- **Provider 目录**（`agents/tools/providers/`）：每个文件对应一类工具（bash、filesystem、ask、todo、cron、skill、agent、plan）
- **工具加载**：`get_tools()` 通过 `asyncio.gather()` 并发加载所有 provider
- **结构化输出**：伪工具 `__structured_output__` 机制，模型直接通过 tool args 输出结构化数据，无需额外 LLM 调用
- **BYPASS_TOOLS**：`ask` 等工具跳过审批逻辑直接执行

### 权限系统

`agents/permissions/` 下的 `PermissionEngine`：
- 从 `~/.lumi/settings.json`（用户级）和 `.lumi/settings.json`（项目级）加载规则
- 评估顺序：Deny → Allow → Unmatched
- 工作区边界检查：限制工具只能访问授权目录
- 延迟加载，检查文件 mtime 实现热重载
- 在 `is_use_tool()` 节点中调用 `engine.evaluate()` + `engine.check_workspace_boundary()`

### TUI 架构

- **LumiApp**（`tui/app.py`）：Textual App 主体，支持 `privileged` 参数（CLI `--privileged-danger`）
- **AgentBridge**（`tui/agent_bridge.py`）：直接调用 LangGraph（非 HTTP），流式产生 `BridgeEvent`
- **EventRouter**（`tui/event_router.py`）：状态机 + 事件分发，管理 `RunPhase` 转换
- **WidgetAssembler**（`tui/widget_assembler.py`）：将 `RenderItem` 转换为 Textual Widget
- **SubagentTracker**（`tui/subagent_tracker.py`）：跟踪并发子 Agent 执行状态
- **InputBar**（`tui/widgets/input_bar.py`）：输入栏，管理 plan mode 切换（Shift+Tab）和模式指示器（auto/plan/privileged）

**Tool 渲染器**：Protocol 模式（`ToolRenderer`），通过 `@register_renderer("bash")` 注册自定义渲染，每个渲染器被 `_SafeRenderer` 包装以容错。

**GroupingEngine 同步契约**：`grouping.decide_tool()` 之后必须调用 `grouping.on_tool_started()`；widget flush 后必须调用 `grouping.flush_tools()` / `grouping.flush_agents()`。

### 子 Agent

- 工具实现在 `agents/tools/providers/agent.py`
- 创建新 `LumiAgent` 实例，**无 checkpointer**（节省开销），复用父级 `PermissionEngine`
- tool_mode 从父状态继承
- 父 TUI 通过 `parent_run_id` 识别子 Agent 事件，路由到 `AgentGroup` 做轻量统计展示

### 风格系统（Styles）

`lumi/styles/` 下每个子目录是一种风格，包含 `prompts/` 和 `agents/` 子目录。

- **加载优先级**：用户 `.lumi/` 下的同名文件 > style 内置文件
- **配置方式**：`config.yaml` 的 `style` 字段，或 CLI `-s/--style` 参数（优先级更高）
- **内置风格**：`default`（仅工具模板）、`code`（完整编程提示词 + 子 Agent）
- **工具提示词**：`prompts/tools/` 下的 MD 文件定义工具 description 和 response，启动时加载，缺失则 RuntimeError
- **`active_style` 属性**（`LumiConfig`）：返回当前生效的风格名，CLI override > config.yaml > "default"

## 测试

- pytest + pytest-asyncio，`asyncio_mode = "auto"`（异步测试直接用 `async def test_*()`）
- `tests/conftest.py` 中有单例重置 fixtures（`reset_registry`、`reset_filesystem_backend`、`reset_session_manager`）和隔离工作区 fixture（`authorized_tmp_dir`）
- TUI 测试在 `tests/tui/`
- TUI 界面开发时应使用 Textual 的 SVG 能力主动验证样式，参考 `test/TUI_VISUAL_TESTING.md`


