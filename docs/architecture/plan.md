# Plan Mode 架构

Plan Mode 的内部实现。用户使用指南见 [`docs/guides/plan.md`](../guides/plan.md)。

---

## 核心机制

Plan Mode 通过执行模式策略（`mode_policy.py`）实现工具限制，而非替换工具集。这保证了工具定义不变，最大化 Prompt Caching 命中率。

### 状态流转

```
execution_mode state 字段:
  "normal" → EnterPlanMode 工具调用 → "plan"
  "plan" → ExitPlanMode 工具调用 + 用户批准 → "normal"
  "plan" → ExitPlanMode 工具调用 + 用户拒绝 → "plan"（继续修改）
```

### 策略守卫

`PLAN_POLICY`（`mode_policy.py`）：

- `allow_write = False`
- `path_filter = _is_under_lumi_plans`：仅允许写入 `.lumi/plans/*.md`

`is_use_tool()` 路由函数中，`execution_mode != "normal"` 时调用 `check_policy()`，写入操作被路由到 `PolicyReject` 节点。

---

## 工具提示词格式

### EnterPlanMode.md

```markdown
---
description: |
  工具描述（LLM 看到的 tool description）...
---

进入计划模式后的响应内容（plan mode reminder）...
```

- `description`：YAML front-matter 中的工具描述
- body：进入 plan mode 后返回给 LLM 的响应内容

### ExitPlanMode.md

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

如果配置文件缺失或关键字段为空，启动时抛出 `RuntimeError`。

---

## TUI 集成

- **Shift+Tab 切换**：`InputBar` 处理按键事件，通过消息注入 plan mode reminder
- **状态栏同步**：`EnterPlanMode` 工具调用时 TUI 自动切换指示器为 `⏸ plan`
- **审批组件**：`PlanApproval` widget 展示计划文件内容，用户通过 `Enter`/`Esc` 决定
