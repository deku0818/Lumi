# Agent 工具架构

子 Agent 系统的内部实现。用户使用指南见 [`docs/guides/agents.md`](../guides/agents.md)。

---

## 工作原理

1. Lumi 启动时扫描 `.lumi/agents/*.md` 和 `lumi/styles/{style}/agents/*.md`，解析所有 Agent 配置
2. 存在有效配置时，注册 `agent` 工具到 `ToolRegistry`
3. 对话中主 Agent 根据 `description` 判断是否委托任务
4. 调用时创建独立的 `LumiAgent` 子实例（无 checkpointer），执行完毕后返回结果

### 关键设计

- **无 checkpointer**：子 Agent 不持久化状态，节省开销
- **工具自排除**：子 Agent 的工具列表中自动排除 `agent` 工具，避免递归调用
- **权限继承**：复用父级 `PermissionEngine`，`tool_mode` 从父状态继承
- **模式策略**：plan/readonly 模式下，子 Agent 创建时通过 `filter_tools_for_mode()` 静态过滤工具列表

## 模块

| 模块 | 路径 | 职责 |
|---|---|---|
| 工具实现 | `lumi/agents/tools/providers/agent.py` | Agent 工具的核心逻辑，创建子 LumiAgent 实例 |
| Agent 加载 | `lumi/styles/loader.py` | 扫描和解析 Agent 配置文件 |
| TUI 渲染 | `lumi/tui/widgets/agent_group.py` | AgentGroup 组件，管理子 Agent 的 TUI 展示 |
| 状态追踪 | `lumi/tui/subagent_tracker.py` | 跟踪并发子 Agent 的执行状态和 run_id 映射 |

## TUI 集成

父 TUI 通过 `parent_run_id` 识别子 Agent 事件，路由到 `AgentGroup` 做轻量统计展示。详见 [`architecture/subagent-rendering.md`](subagent-rendering.md)。
