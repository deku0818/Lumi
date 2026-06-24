# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

Lumi 是一个基于 LangGraph 的 AI Agent 框架，提供桌面应用（Electron 前端，经 WebSocket 连后端）和 HTTP API（FastAPI）两种交互方式。支持多模型（OpenAI、Anthropic、Bedrock）、工具调用、权限控制、定时任务、技能扩展和 MCP 协议集成。
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

## UI 方案协作方式

涉及 UI 样式 / 动效 / 交互的改动，**先做可视化示例让用户确认，再落地代码**：

1. 写独立 HTML demo 到项目根的 `.demos/` 目录（已 gitignore，不进仓库；取 `desktop/src/index.css` 的主题色值，观感与应用一致），用 `open` 在浏览器打开给用户看。
2. 动效与多状态流转做成循环播放的动态演示；多个候选方案并列展示供挑选。
3. 用户确认或微调后再改真实代码。

品牌视觉：Lumi = 光明。动效用"光"的语言（`index.css` 的 `.lumi-orb` 光点光晕），品牌金走 `--color-accent` + `color-mix`（亮暗主题自适应），不写死色值；**一静一动**——图标动、文字静，不给文字加动效。

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
- 从 `~/.lumi/permissions.json`（用户级）、`.lumi/permissions.json`（项目共享）和 `.lumi/permissions.local.json`（项目本地）加载规则
- 评估顺序：Deny → Allow → Unmatched
- 工作区边界检查：限制工具只能访问授权目录
- 延迟加载，检查文件 mtime 实现热重载
- 在 `is_use_tool()` 节点中调用 `engine.evaluate()` + `engine.check_workspace_boundary()`

### 子 Agent

- 工具实现在 `agents/tools/providers/agent.py`
- 创建新 `LumiAgent` 实例，**无 checkpointer**（节省开销），复用父级 `PermissionEngine`
- tool_mode 从父状态继承
- 前端通过 `parent_run_id` 识别子 Agent 事件（非空=属于某子 Agent），做轻量统计展示

### Desktop / WS 服务

桌面应用（Electron + TS 前端）经 WebSocket 调用后端 `AgentBridge` 复用 Agent 运行时。详见 `docs/architecture/desktop.md`。

- **`lumi/server/ws.py`**：`lumi serve` 拉起的 FastAPI WS 端点。一条 WS = 一个 `AgentBridge`（可切换 thread），JSON-RPC 帧 `{id, method, params}` ↔ `{id, result|error}`，流式事件用 `{method:"event", params}`。
- **`AgentBridge`**（`agents/bridge.py`）：前端（desktop / 未来 TS TUI）经 WS **复用**的中立桥接层，把 LangGraph 事件封装为 `BridgeEvent` 流。`EventKind` 成员值直接 = 对外 wire 名（`namespace.verb`），`server/protocol.py` 只做 payload 重组，无映射层。
- **协议单一事实源**：`protocol/events.json`。TS 端 import derive 类型，Python 端由 `tests/server/test_protocol_contract.py` 锁住事件名/方法名一致——改协议只改这一处。
- **会话元数据**：列表由 checkpoint 派生（`sessions/session_store.py`），但 pin/重命名等用户标记存在 `sessions/session_meta.py` 的 JSON sidecar（`~/.lumi/checkpoints/session_meta.json`），`list_sessions` 合并后置顶排序。消息文本提取/清理/可见性判定在 `lumi/sessions/`（无 textual 依赖，服务端与前端共用同一解析规则）。删除经 `bridge.delete_thread()` 一并清理 LangGraph + 文件级 checkpoint。
- **前端**（`desktop/src/`）：`gateway.ts` 每会话一条 WS 连接（指数退避重连）；`App.tsx` 会话状态机 + 聊天流渲染；`Sidebar.tsx` 会话列表 + `⋮` 右键菜单（置顶/重命名/删除）。
- **模型解析与思考管理**（详见 `docs/architecture/thinking.md`）：`provider_store.resolve()` 是「模型 + 连接 + 思考档位」单一事实源；`create_llm(apply_effort=...)` 默认不注入思考参数（仅主对话链 `call_model` 传 True，内部链天然干净）。思考能力（有无/档位枚举/开关）来自 models.dev（`utils/model_catalog.py`，缓存 `~/.lumi/cache/`，context_length 同源），档位按模型存 profile 的 `effort` dict；`model_manager.effort_params()` 是档位→协议参数的唯一映射点（原生值直传，不存在档位翻译；auto = 不传任何参数）。入口为 desktop ModelPicker（Claude 式三行 + 二级菜单）。

### 风格系统（Styles）

`lumi/styles/` 下每个子目录是一种风格，包含 `prompts/` 和 `agents/` 子目录。

- **加载优先级**：用户 `.lumi/` 下的同名文件 > style 内置文件
- **配置方式**：`config.yaml` 的 `style` 字段，或 CLI `-s/--style` 参数（优先级更高）
- **内置风格**：`code`（完整编程提示词 + 子 Agent）
- **工具描述**：内置工具的 description 直接写在各工具函数的 docstring 里；`registry._collect_tools_from_module` 加载时统一 `inspect.cleandoc` 抹掉源码缩进（外部 MCP 工具走异步 loader，不经此处）。工具描述不再可经 style/`.lumi` 配置覆盖
- **`active_style` 属性**（`LumiConfig`）：返回当前生效的风格名，CLI override > config.yaml > "default"

## 测试

- pytest + pytest-asyncio，`asyncio_mode = "auto"`（异步测试直接用 `async def test_*()`）
- `tests/conftest.py` 中有单例重置 fixtures（`reset_registry`、`reset_filesystem_backend`、`reset_session_manager`）和隔离工作区 fixture（`authorized_tmp_dir`）


