# Plan Mode（计划模式）

Plan Mode 让 Agent 在执行非平凡任务前先进入只读的探索与规划阶段，设计实施方案并提交给用户审批，获得批准后再开始实施。

## 进入方式

有两种方式进入 Plan Mode：

### 1. 用户手动开启（Shift+Tab）

在 TUI 中按 `Shift+Tab` 切换 Plan Mode 开关。状态栏指示器会从 `▶ auto` 变为 `⏸ plan`。

开启后发送的消息会自动注入 plan mode reminder（仅首条消息注入一次），引导 Agent 进入只读规划流程。后续消息不会重复注入。

> 注意：`privileged` 模式下 `Shift+Tab` 不可用。

### 2. Agent 主动调用 EnterPlanMode

Agent 判断任务复杂度后主动调用 `EnterPlanMode` 工具。此时 TUI 状态栏会自动同步为 `⏸ plan` 指示器，但不会额外注入 reminder（因为工具响应本身已包含完整的 plan mode 指令）。

## 工作流程

```
用户提出任务 → Agent 调用 EnterPlanMode（或用户 Shift+Tab 开启）
→ 只读探索代码库 → 设计方案
→ 调用 ExitPlanMode 提交计划 → 用户审批（批准/拒绝）
→ 批准：退出计划模式，指示器恢复为 auto，开始实施
→ 拒绝：继续修改计划
```

## TUI 状态指示器

状态栏左下角显示当前模式：

| 指示器 | 含义 |
|--------|------|
| `▶ auto` | 默认模式，工具按权限规则自动执行或审批 |
| `⏸ plan` | 计划模式，Agent 处于只读规划阶段 |
| `▶▶ privileged ⚠` | 特权模式（`--privileged-danger`），跳过所有审批 |

`auto` 和 `plan` 之间可通过 `Shift+Tab` 切换。`privileged` 模式由 CLI 启动参数决定，不可在运行时切换。

## 工具

### EnterPlanMode

进入计划模式。Agent 在以下场景应主动使用：

- 新功能实现（需要架构决策）
- 存在多种实现方案的任务
- 影响多个文件的修改
- 需求不明确，需要先探索再规划

不需要使用的场景：单行修复、拼写错误、用户给出了精确指令的简单任务。

### ExitPlanMode

提交计划供用户审批。参数：

| 参数 | 说明 |
|------|------|
| `plan_file_path` | 计划文件路径（规划阶段写入的 `.md` 文件） |

## 计划模式中的行为约束

- **只读**：不可编辑项目文件、运行非只读工具、修改配置或创建提交
- **唯一例外**：可以创建和编辑计划文件
- 可以使用 `Glob`、`Grep`、`Read` 探索代码库
- 可以使用 `ask` 向用户提问
- 可以使用 `agent` 启动子 Agent 进行探索

## 规划阶段

1. **初步理解**：使用 Explore 子 Agent 并行探索代码库
2. **方案设计**：使用 Plan 子 Agent 设计实施方案
3. **审查**：阅读关键文件，确保方案与用户意图一致
4. **最终计划**：将方案写入计划文件
5. **提交审批**：调用 `ExitPlanMode`

## TUI 审批界面

当 Agent 调用 `ExitPlanMode` 时，TUI 会展示计划审批组件：

- 显示计划文件路径和内容预览
- 用户可通过 `↑↓` 选择、`Enter` 确认、`Esc` 拒绝
- 批准后 TUI 自动关闭 plan mode 指示器，恢复为 `auto`

## 自定义提示词

Plan Mode 工具的 description 和 response 内容从 style 文件加载，用户可覆盖。

查找顺序（优先级从高到低）：

1. `.lumi/prompts/tools/EnterPlanMode.md`（用户覆盖）
2. `lumi/styles/{style}/prompts/tools/EnterPlanMode.md`（style 内置）

`ExitPlanMode.md` 同理。

### EnterPlanMode.md 格式

```markdown
---
description: |
  工具描述（LLM 看到的 tool description）...
---

进入计划模式后的响应内容（plan mode reminder）...
```

- `description`：YAML front-matter 中的工具描述
- body：进入 plan mode 后返回给 LLM 的响应内容

### ExitPlanMode.md 格式

```markdown
---
description: |
  工具描述...
approved: |
  计划被批准后的响应内容...
rejected: |
  计划被拒绝后的响应内容...
---
```

- `description`：工具描述
- `approved`：用户批准计划后返回的响应
- `rejected`：用户拒绝计划后返回的响应

> 如果配置文件缺失或关键字段为空，启动时会抛出 `RuntimeError`。
