# 持久记忆 + 项目说明注入设计

> 状态：已实现（2026-06-28，v0.2.10）。移植自 Claude Code 的 `memdir`，**仅做主动写入**——
> 不做后台提取（forked agent 挖对话）、autoDream（夜间整理）、召回旁路（side-LLM 选文件）。

## 两件事

| | 持久记忆 | 项目说明 |
|--|--|--|
| 文件 | `~/.lumi/memory/projects/<项目>/`（`MEMORY.md` 索引 + topic `.md`） | 项目根 `LUMI.md` |
| 谁写 | 模型在对话中自己 `write`/`edit` | 人手维护 |
| 承载 | 跨会话积累「用户是谁 / 怎么协作 / 项目背景」 | 「这个项目要什么」 |
| 注入范围 | 仅主 agent（`enable_memory`） | 主 + 子 agent |

二者与 style 系统提示词（`.lumi/prompts/` 的 SOUL/AGENTS，「Lumi 是谁」）正交。

## 存储结构

每条记忆 = 一个 `.md` 文件，带 frontmatter（`name` / `description` / `type`）。`type` 为封闭四类，
只存**无法从项目当前状态推导**的信息：

- **user** — 用户角色 / 专长 / 偏好
- **feedback** — 工作方式指导（纠正 + 确认都存，正文带 `Why:` / `How to apply:`）
- **project** — 进行中的工作 / 事故 / 决策（相对日期转绝对日期）
- **reference** — 外部系统指针（Linear、监控看板等）

`MEMORY.md` 是**索引不是记忆**：每行一个指针 `- [标题](文件.md) — 钩子`，无 frontmatter；
注入上下文时截断到 200 行。各条记忆正文不随会话注入，只有模型主动 grep/read 时才进上下文。

`~/.lumi/memory/projects/<项目>/` 的项目 key = 项目根绝对路径 sanitize（`/` → `-`，保留可读性，
与 Claude Code 一致），home 级、跨会话持久，与 checkpoints 同级。

## 模块（`lumi/agents/memory/`）

- **`paths.py`** — 记忆目录单一事实源：`memory_dir` / `memory_entrypoint` / `ensure_memory_dir` /
  `is_memory_path`（边界判定，resolve 两侧防 `..` 穿越与 symlink 逃逸）/ `read_text_or_none`（共用安全读）。
- **`prompt.py`** — `build_memory_instructions()`（行为说明：taxonomy / 不该存什么 / 两步存法 /
  何时召回 / 推荐前验证）+ `load_memory_index()`（读 `MEMORY.md`，200 行截断）。
- **`project_doc.py`** — `load_project_doc()`（读 `LUMI.md`，50KB 截断）。

## 三个接入点

1. **行为说明 → 系统提示词**：`create_agent(enable_memory=True)` 时把 `build_memory_instructions`
   追加到系统提示词尾部，并 `ensure_memory_dir`。
2. **`MEMORY.md` 索引 + `LUMI.md` → 首条 user 消息**：`preprocessing/memory.py` 的
   `inject_memory_context_into_message`，挂在 `preprocess_messages` 的 `first_message` 分支
   （与 `system_info` 并列，复用 `format_reminder` 包 `<system-reminder>`）。`MEMORY.md` 受
   `context.memory_enabled` 门控，`LUMI.md` 不受。
3. **写入免审批 carve-out**：`routing.route_decision` 在 bypass-immune 之后短路——写记忆目录的
   `write`/`edit` 所有 tool_mode 直接 `ToolExecutor`（项目根取 `get_authorized_directory()`，
   与注入同源）；同时 `engine._rebuild_boundary` 把记忆目录并入工作区边界，使 `validate_path` 放行。

   **顺序很关键**：DENY 规则、只读短路、执行模式策略守卫（plan/readonly）、bypass-immune 都在 carve-out
   **之前**，故用户的 DENY 规则与 readonly 模式仍能拦住记忆写入；carve-out 只免掉「本该问人」的审批。

## opt-in 语义

`create_agent(enable_memory=...)` **默认 False**。持久记忆有副作用（写盘 / 改 prompt / 注入上下文 /
写入免审批），故只有面向用户的对话入口 `bridge` 显式传 `True`；子 agent（`agent.py`）、workflow、cron
走默认 False 天然干净。这样「需要记忆的少数显式声明」而非「不需要的多数记得排除」，新增调用方默认安全。

## 刻意没做（未来可补）

- **后台提取**：Claude Code 用 stop hook + forked agent 回看对话补写（游标跳过主 agent 已直写的范围）。
- **autoDream**：夜间把零散记忆蒸馏进 topic 文件 + `MEMORY.md`。
- **召回旁路**：用一次廉价 side-LLM 按 query 选最相关的几条记忆当 attachment 注入（取代索引常驻）。

参考实现见 Claude Code 的 `src/memdir/`（`findRelevantMemories` / `extractMemories` / `autoDream`）。
