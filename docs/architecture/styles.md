# 风格系统架构

风格系统的内部加载机制。用户使用指南见 [`docs/guides/styles.md`](../guides/styles.md)。

---

## 目录结构

每种风格可含 `prompts/`、`agents/`、`skills/` 三类子目录，三者均为可选：

```
lumi/styles/
├── default/              # 默认风格：不带 prompts/，提示词全部来自 .lumi/prompts/
│   ├── agents/           # （当前为空）
│   └── skills/           # （当前为空）
└── code/
    ├── prompts/
    │   ├── SOUL.md
    │   ├── GUARDRAILS.md
    │   └── AGENTS.md
    └── agents/
        ├── explore.md
        └── plan.md
```

---

## 加载机制

### 系统提示词（SOUL.md / GUARDRAILS.md / AGENTS.md）

1. 从 `lumi/styles/{style}/prompts/` 读取基础文件（风格无 `prompts/` 目录时跳过）
2. 用 `.lumi/prompts/` 下的同名文件覆盖

三个文件按 `SOUL → GUARDRAILS → AGENTS` 顺序、以 `\n\n` **直接拼接**（不做 XML 包裹），任一缺失则跳过该段。`default` 风格不带内置 `prompts/`，提示词全部来自 `.lumi/prompts/`；两处都没有时 `load_system_prompt` 返回空串，agent 以无系统提示词运行（不再 fail-loud）。

### 工具描述

内置工具的 description 直接写在各工具函数的 docstring 里，由 `registry._collect_tools_from_module` 在加载时统一 `inspect.cleandoc` 抹掉缩进。工具描述不再经 style / `.lumi/` 配置覆盖。

### 子 Agent 配置（agents/）

1. 从 `lumi/styles/{style}/agents/` 读取内置 Agent
2. 用 `.lumi/agents/` 下的同名文件覆盖（覆盖时输出 warning 日志）

### 技能配置（skills/）

与 agents 同构：

1. 从 `lumi/styles/{style}/skills/<name>/SKILL.md` 读取内置 Skill（风格无 `skills/` 目录时静默跳过）
2. 用 `.lumi/skills/<name>/SKILL.md` 下的同名文件覆盖

---

## 关键实现

- **`active_style` 属性**（`LumiConfig`）：返回当前生效的风格名，CLI override > config.yaml > "default"
- **`list_styles()`**（`lumi/styles/__init__.py`）：扫描 `lumi/styles/` 子目录，列出所有可用风格
- **缓存友好**：工具定义在启动时一次性加载，运行时不变，保持 Prompt Caching 前缀稳定
