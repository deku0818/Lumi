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

核心入口是 `lumi/agents/core/graph.py` 中的 `LumiAgent`（自带节点/边装配与 compile）。

**Graph 流程：**
```
START → Summarizer（超阈值当轮就地压缩 / ptl_retry 置位则绕阈值强制压缩）
  → PreprocessMessages（UserPromptSubmit hook 注入上下文；hook Block 则 END）
  → CallModel → is_use_tool() 条件路由:
  ├─ ToolExecutor（已授权 / 只读批次 / 纯内部伪工具 / 本项目记忆目录写入；DENY 预检先于只读短路）→ after_tool_executor → CallModel（循环；Block / 结构化输出失败上限置 tool_cancelled → END）
  ├─ HumanApproval（需用户审批，逐个 decisions）→ 有允许: ToolExecutor（被拒的先补拒绝 ToolMessage，ToolExecutor 只跑未应答的）/ 全拒绝·cancel: END / DENY·无审批通道: CallModel
  ├─ AutoClassify（auto 模式安全分类器）→ approve: ToolExecutor / reject: CallModel / 分类器异常: HumanApproval
  └─ OnAgentStop（无工具调用，分发 Stop hooks）→ END（hook 可拉回 CallModel）
OfflineFlush（刻意无入边，离线写回锚点）→ END
```
路由分支判定在 `permissions/routing.route_decision`，`is_use_tool()` 只是薄壳。

压缩恒在上下文注入之前：hook 永远在压缩后的世界运行，marker 由压缩侧恒剥（`compact._reattach`）后自动全量重建（见 `preprocessing/context_inject.py`）。在线 / PTL / 离线三条压缩路径共用 `compact.build_compacted_update`：删整段 → 摘要 carrier → 重挂 `keep`；**正在被回答的那条真人消息（`find_pending_human`）恒原样保住**，不让摘要转述取代用户原话；carrier 继承被删真人消息的 `ts`，使 dream 判活基线（`latest_human_ts`）不随压缩归零。

**PTL 兜底回路：** CallModel 撞 prompt-too-long 时由**节点级 error_handler**（`on_call_model_error`，挂在 `add_node(..., error_handler=)`）返回 `Command(goto="Summarizer", update={"ptl_retry": True})`，经 Summarizer 的 `_ptl_forced_compact`（绕阈值门、按 API round 保尾）压缩后走正常拓扑重试；成功清 `ptl_retry`，置位期间再撞直接原样抛出（每次 PTL 只换一次压缩机会）。用 error_handler 而非节点内 try/except 的原因：**节点自身返回**的 `Command(goto)` 会与其条件边取并集（曾为此在 `is_use_tool` 挂 ptl_retry 守卫防 Stop hooks 误分发），而 error_handler 的路由不触发条件边求值，守卫随之删除——不变量改由 `test_full_graph_ptl_roundtrip` 的 Stop hook 计数断言把住。

**关键状态 `LumiAgentState`：** messages（`DeltaChannel` 增量通道）、todos、output_schema / structured_output、tool_cancelled、ptl_retry、depth。

**messages 通道是 `DeltaChannel`**（不是 `add_messages`）：checkpoint 只存增量写入 + 周期快照，长会话不再每个 super-step 重写整段历史。两条随之而来的约束：① 压缩写回用 `Overwrite` 整体替换而非逐条 `RemoveMessage`（`build_compacted_update`）；② **`aupdate_state` 不自动补消息 id**（LangGraph 只在 `put_writes` 补节点写入的），补 id 只能在写入构造时做（放进 reducer 会让每次回放生成新 id）。因此**所有离线写回只走 `AgentBridge.flush_offline` 这一个出口**，由它统一补 id + 挂 `OfflineFlush` 锚点；③ `snapshot_frequency` 必须显式给（取 100），官方默认 1000 等于几乎不快照，会让读退化成 O(链长)。语义差异锁在 `tests/test_delta_channel.py`，出口契约锁在 `tests/gateway/test_offline_flush_stamps.py`。

**协作式停机：** `lumi/agents/core/run_control.py` 的 `drain_all()` 让在跑的图停在 super-step 边界（checkpoint 完整、`next` 指向待执行节点；不续跑，下一轮开跑前由 `_recover_stale_state` 按中断轮收尾），由 `bootstrap.drain_runs()` 在拆任何子系统之前调用（`lumi serve` 的 lifespan 在渠道会话池关闭前先调）。与用户按停的硬 cancel 分工不同：cancel 要立刻、可能落在节点半途（靠 `persist_partial_reply` 事后修），drain 要 checkpoint 完整、切不断正在流的模型调用；前台子代理 / workflow 的子图继承同一 control，会让父级工具步在半途结束。因 `astream_events(version="v2")` 不转发 `control=`，改经 config 注入带 control 的 parent runtime（用到私有常量，由 `tests/test_drain.py` 锁住）。

**checkpoint 加密（可选）：** 设 `LUMI_CHECKPOINT_AES_KEY`（16/24/32 字节）后 checkpoint 加密落盘，需可选依赖 `pip install 'lumi-harness[encryption]'`；存量明文仍可读，开关随时可开。

**运行时上下文 `LumiAgentContext`：** 通过 LangGraph 的 `Runtime` 参数传递，包含 tools、system_prompt、model_name / provider / effort、permission_engine、tool_mode（default/accept_edits/privileged/auto，放 context 以便运行中切换；前台子代理经 `mode_parent` 读写根 context）、approval_broker 等。在节点函数中通过 `Runtime[LumiAgentContext]` 访问。所有节点共享同一实例。

### 工具系统

- **结构化输出**：伪工具 `__structured_output__` 机制，模型直接通过 tool args 输出结构化数据，无需额外 LLM 调用
- **只读工具免审批**：`capability._ALWAYS_READONLY`（read/vision/glob/grep/skill/agent/ask/todos）与只读 bash 直接执行，DENY 规则仍先生效

### 权限系统

`agents/permissions/` 下的 `PermissionEngine`：
- 从 `~/.lumi/permissions.json`（用户级）、`.lumi/permissions.json`（项目共享）和 `.lumi/permissions.local.json`（项目本地）加载规则
- 评估取最严格匹配：Deny > Ask > Allow > Unmatched（bash 复合命令逐段评估取最严）
- 工作区边界只约束写操作（只读工具可跨目录读）
- 构造时加载，路由入口 `reload()` 按文件 mtime 热重载
- 路由逻辑在 `permissions/routing.route_decision`（调 `engine.evaluate()` + `engine.check_workspace_boundary()`），`is_use_tool()` 条件边只是薄壳

### 子 Agent

- 工具实现在 `agents/tools/providers/agent.py`
- 创建新 `LumiAgent` 实例，**无 checkpointer**（节省开销），复用父级 `PermissionEngine`
- 前台子代理继承父 tool_mode；后台子代理（与 cron / dream / `lumi -p` 一样无人应答）固定 `auto`，无审批通道，需人工审批的自动拒绝
- 子代理一律不带 `ask` 工具（`_child_tools`）
- 前端通过 `parent_run_id` 识别子 Agent 事件（非空=属于某子 Agent），做轻量统计展示

### Desktop / WS 服务

桌面应用（Electron + TS 前端）经 WebSocket 连 `GatewaySession`，由它驱动 `AgentBridge` 复用 Agent 运行时。详见 `docs/architecture/desktop.md`。

- **`lumi/gateway/channels/ws.py`**：`lumi serve` 拉起的 FastAPI WS 端点，只做鉴权与帧收发。一条 WS = 一个 `GatewaySession`（`gateway/session.py`：传输无关的会话编排——RPC 分发表 `_RPC_HANDLERS` / `_DOMAIN_HANDLERS`、run.lock、通知轮询、detach/reattach），各持一个 `AgentBridge`（可切换 thread）。JSON-RPC 帧 `{id, method, params}` ↔ `{id, result|error}`，流式事件用 `{method:"event", params}`。
- **`AgentBridge`**（`gateway/bridge/`）：desktop（经 WS → GatewaySession）与 IM 渠道（进程内经 `BridgePool`，每 thread 一个）**复用**的中立桥接层，把 LangGraph 事件封装为 `BridgeEvent` 流。`EventKind` 成员值直接 = 对外 wire 名（`namespace.verb`），`gateway/protocol.py` 只做 payload 重组，无映射层。
- **协议单一事实源**：`protocol/events.json`。TS 端 import derive 类型，Python 端由 `tests/gateway/test_protocol_contract.py` 锁住事件名/方法名一致——改协议只改这一处。
- **会话元数据**：列表由 checkpoint 派生（`sessions/session_store.py`），但 pin/重命名等用户标记存在 `sessions/session_meta.py` 的 JSON sidecar（`~/.lumi/checkpoints/session_meta.json`），`list_sessions` 合并后置顶排序。删除经 `bridge.delete_thread()` 清理 LangGraph checkpoint 并回收该会话的持久 shell。
- **消息显示声明制**：每条 HumanMessage 构造时在 `additional_kwargs["lumi"]["items"]` 声明显示内容（气泡条目：text/sender/ts/files），content 只给模型（`<sender>`/`<attached-file>`/command 标签均为纯模型侧约定）——显示侧（`lumi/agents/core/meta_message.py` 的 `visible_user_text`）零正则。`items: []` = 合成消息不可见（摘要 carrier/后台通知/工具回灌，经 `synthetic_human_message` 构造）；未声明（cron/子 agent 直接构造）fallback 到 content 掉 `injected_prefix` 前缀块。上下文注入块经 `inject_text_into_message` 前置并计数（见 `preprocessing/context_inject.py`）。
- **前端**（`desktop/src/`）：`gateway.ts` 每会话一条 WS 连接（指数退避重连）；`App.tsx` 会话状态机 + 聊天流渲染；`Sidebar.tsx` 会话列表 + `⋮` 右键菜单（置顶/重命名/删除）；`ProjectHomePage.tsx` 项目主页（点项目卡片进入：输入岛新建会话 + 项目会话流 + 提示词/记忆/定时/技能/子 Agent 五卡，项目层资源支持增删改、内置/全局层只读可「复制到项目」）。
- **模型解析与思考管理**（详见 `docs/architecture/thinking.md`）：`provider_store.resolve()` 是「模型 + 连接 + 思考档位」单一事实源；`create_llm(apply_effort=...)` 默认不注入思考参数（仅主对话链 `call_model` 传 True，内部链天然干净）。思考能力（有无/档位枚举/开关）来自 models.dev（`lumi/models/catalog.py`，缓存 `~/.lumi/cache/`，context_length 同源），档位按模型存 profile 的 `effort` dict；`lumi/models/manager.effort_params()` 是档位→协议参数的唯一映射点（原生值直传，不存在档位翻译；auto：OpenAI 系 / 无思考能力模型不传参数，Anthropic effort 型 = adaptive thinking）。入口为 desktop ModelPicker（Claude 式三行 + 二级菜单）。**模型是会话属性**：按 thread 存在 session_meta、首轮固化、每轮开跑前经 `bridge.align_session_model()` 对齐，desktop 与 IM 共用 `sessions/session_model.py` 一条解析链（会话模型 > 新会话默认），渠道配置里没有模型字段——详见 `docs/architecture/model-switching.md`。

### 风格系统（Styles）

`lumi/styles/` 下每个子目录是一种风格，可含 `prompts/`、`agents/`、`skills/` 三类子目录（均可选）。

- **加载优先级（三层，逐层同名覆盖）**：style 内置 < 全局层（进程配置目录；`lumi serve` 恒钉 `~/.lumi`，不随启动目录漂移）< 项目层（`<项目>/.lumi/`，随会话绑定的项目，仅该项目生效）。skills/agents 层序单源在 `loader.config_layers`，prompts 在 `manager.prompt_layers`（另有第四层框架内置 `lumi/prompts/` 兜底；空文件视同没有，继续往下找）。桌面「项目主页」的展示与增删改（`gateway/project_config.py`）消费同一层序，UI 所见即会话所加载
- **用户数据根**：机器级数据（`lumi.json` 密钥 / checkpoints / memory / logs / 工具箱 / cron / cache / uploads）全部挂在 `utils/paths.py` 的 `lumi_home()` 下——`LUMI_CONFIG_DIR` > `~/.lumi`，改一个环境变量整体搬家（服务器部署即靠它，见 `docs/guides/deploy.md`）。取值点多为模块级常量（import 时求值），故环境变量须在进程启动前设好；锁定测试 `tests/test_lumi_home.py` 在子进程里验证全部路径跟随。与配置**发现链**（`ConfigDiscovery`，解决「这次读哪份项目配置」）职责不同，别混用
- **配置方式**：项目 `.lumi/config.json` 的 `style`（仅该项目生效）> 进程 `config.json` 的 `style`；CLI `-s/--style` 仅对 `lumi -p` 生效（优先级最高）
- **内置风格**：`default`（默认，**不带内置 prompts**，提示词全部来自 `.lumi/prompts/`；可内置 skill/agent）、`code`（完整编程提示词 + explore/plan 子 Agent）
- **提示词组装**：`SOUL/AGENTS` 两文件按序**直接拼接**（不做 XML 包裹），缺失即跳过；`default` 无内置 prompts 时全靠 `.lumi/prompts/`，都没有则 `load_system_prompt` 返回空串（以无系统提示词运行）。`SUMMARY`（压缩用）走同一条 `load_prompt` 解析链，但框架内置了兜底（`lumi/prompts/SUMMARY.md`）——未配置也能压缩，故各调用点不再有「未配置 SUMMARY」的错误分支
- **工具描述**：内置工具的 description 直接写在各工具函数的 docstring 里；`registry._collect_tools_from_module` 加载时统一 `inspect.cleandoc` 抹掉源码缩进（外部 MCP 工具走异步 loader，不经此处）。工具描述不再可经 style/`.lumi` 配置覆盖
- **`active_style_for(project)`**（`LumiConfig`）：返回某项目会话生效的风格名，CLI override > 项目 `.lumi/config.json` > 进程 config.json（`active_style` 属性）> "default"


