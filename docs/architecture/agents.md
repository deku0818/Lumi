# Agent 工具架构

子 Agent 系统的内部实现。用户使用指南见 [`docs/guides/agents.md`](../guides/agents.md)。

---

## 工作原理

1. `agent` 工具静态注册到 `ToolRegistry`（恒注册，与 skill 一致），不依赖启动期是否存在配置
2. 可用代理列表经 `<system-reminder>` 动态注入对话：`AgentChangeDetector` 检测 `.lumi/agents/*.md` 变更后刷新（与 `lumi/styles/{style}/agents/*.md` 内置代理合并），新增/删除代理无需重启或重建工具 schema
3. 对话中持有 `agent` 工具的 Agent 根据 `description` 判断是否委托任务
4. 调用时创建独立的 `LumiAgent` 子实例（无 checkpointer），执行完毕后返回结果

### 关键设计

- **无 checkpointer**：子 Agent 不持久化状态，节省开销
- **动态加载**：`agent` 工具描述/schema 静态，可用代理列表经 `<system-reminder>` 注入（与 skill 同款机制，见 `preprocessing/change_detector.py`），随 `.lumi/agents` 热更新；注入门控以「工具集是否含 `agent`」为准
- **多层委派**：子 Agent 可继续委派下层子 Agent，深度由 `agents.max_delegation_depth` 限制（默认 3，主 Agent 为第 0 层，每委派 +1）；达上限的子 Agent 工具集中剔除 `agent` 工具、不能再往下委派（`0` = 禁止委派）。`depth` 经 `LumiAgentState` 逐层 +1 传播
- **权限继承**：复用父级 `PermissionEngine`，`tool_mode` 从父状态继承
- **模式策略**：readonly 模式下，子 Agent 创建时通过 `filter_tools_for_mode()` 静态过滤工具列表

## 模块

| 模块 | 路径 | 职责 |
|---|---|---|
| 工具实现 | `lumi/agents/tools/providers/agent.py` | Agent 工具核心逻辑：委派深度网关、创建子 LumiAgent |
| Agent 加载 | `lumi/agents/tools/loader.py` | 扫描解析 `.lumi/agents` 与风格内置 Agent 配置（`load_agents`） |
| 变更检测 / 注入 | `lumi/agents/core/preprocessing/{agent_detector,agents}.py` | 检测 `.lumi/agents` 变更，把可用列表注入 `<system-reminder>` |

## 前端集成

前端通过 `parent_run_id` 识别子 Agent 事件（非空=属于某子 Agent），做轻量分组展示。多层委派下孙及更深活动确定性归并到顶层子代理卡片，详见 [`architecture/desktop.md`](desktop.md) 的「子代理事件归属」与 [`architecture/subagent-rendering.md`](subagent-rendering.md)。
