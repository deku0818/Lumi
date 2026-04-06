# Plan Mode 使用指南

Plan Mode 让 Agent 在执行复杂任务前先进入只读的探索与规划阶段，设计方案并提交审批，获得批准后再开始实施。

---

## 进入方式

### 1. 用户手动开启（Shift+Tab）

在 TUI 中按 `Shift+Tab` 切换 Plan Mode 开关。状态栏指示器从 `▶ auto` 变为 `⏸ plan`。

> `privileged` 模式下 `Shift+Tab` 不可用。

### 2. Agent 主动调用 EnterPlanMode

Agent 判断任务复杂度后主动调用 `EnterPlanMode` 工具进入规划模式。

---

## 工作流程

```
用户提出任务 → Agent 调用 EnterPlanMode（或用户 Shift+Tab 开启）
→ 只读探索代码库 → 设计方案
→ 调用 ExitPlanMode 提交计划 → 用户审批（批准/拒绝）
→ 批准：退出计划模式，恢复为 auto，开始实施
→ 拒绝：继续修改计划
```

---

## TUI 状态指示器

| 指示器 | 含义 |
|--------|------|
| `▶ auto` | 默认模式 |
| `⏸ plan` | 计划模式，Agent 处于只读规划阶段 |
| `▶▶ privileged ⚠` | 特权模式 |

---

## 计划模式中的行为约束

- **只读**：不可编辑项目文件、运行非只读命令、修改配置或创建提交
- **唯一例外**：可以创建和编辑 `.lumi/plans/*.md` 计划文件
- 可以使用 `Glob`、`Grep`、`Read` 探索代码库
- 可以使用 `ask` 向用户提问
- 可以使用 `agent` 启动子 Agent 进行探索

---

## 审批界面

Agent 调用 `ExitPlanMode` 时，TUI 展示计划审批组件：

- 显示计划文件路径和内容预览
- `↑↓` 选择、`Enter` 确认、`Esc` 拒绝
- 批准后自动恢复为 `auto` 模式

---

## 自定义提示词

Plan Mode 工具的 description 和 response 内容从 style 文件加载，用户可覆盖。

查找顺序（优先级从高到低）：

1. `.lumi/prompts/tools/EnterPlanMode.md`（用户覆盖）
2. `lumi/styles/{style}/prompts/tools/EnterPlanMode.md`（style 内置）

`ExitPlanMode.md` 同理。
