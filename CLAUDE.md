# CLAUDE.md

本文件是仓库共享的开发指令。

## 项目概览

Lumi 是楚威开发的智能助手，基于 LangGraph，面向技术与非技术用户。桌面端通过本地 WebSocket 或原生 SSH 连接后端，飞书复用同一运行时；对话 API 使用 Gateway RPC，HTTP 端点负责文件上传与读取。支持 OpenAI Chat / Responses、Anthropic、Gemini 及相应兼容接口、工具调用、权限、定时任务、技能和 MCP。

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

品牌视觉：Lumi = 光明。动效用"光"的语言（`index.css` 的 `.lumi-orb` 光点光晕），品牌金走 `--color-accent` + `color-mix`（亮暗主题自适应），不写死色值；**一静一动**——状态与按钮文字保持静止。已确认的流式正文例外：仅新追加的助手字素做模糊消解，新用户消息容器轻弹入场；切会话、重连、历史加载不重播，代码块保留原有高亮；遵循系统减少动态效果偏好。同组操作按钮统一高度和视觉分量，审批按钮固定 32px。

## 开发与验证

依赖使用 uv 管理。常用命令：

```bash
uv sync --all-groups
uv run pytest
uv run ruff format .
uv run ruff check .
```

前端验证在 `desktop/` 下运行相关 `node --test tests/*.test.cjs` 检查与 `npm run build`。

## 架构入口

- [运行时总览](docs/architecture/runtime.md)：图拓扑、状态、压缩、停机与持久化。
- [权限](docs/architecture/permissions.md)、[在途审批](docs/architecture/approval-inflight.md)：逐个工具决策、Broker 与无人值守模式。
- [子代理](docs/architecture/agents.md)：持久 Lumi / Codex / Claude Code 与 Workflow 临时执行单元的边界。
- [桌面与网关](docs/architecture/desktop.md)、[协议](protocol/README.md)：会话、SSH 和事件契约。
- [模型与会话](docs/architecture/model-switching.md)、[思考管理](docs/architecture/thinking.md)。
- [资源分层](docs/architecture/styles.md)、[配置](docs/guides/config.md)、[文档导航](docs/README.md)。

架构细节在上述专题维护，入口文件不复制实现说明。`docs/archive/` 保存历史方案，不作为当前行为依据。本仓库根 `AGENTS.md` 是被 gitignore 的本地说明；共享开发指令保存在 `CLAUDE.md`，修改公共约定时同步本地入口。
