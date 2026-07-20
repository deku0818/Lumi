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
    │   └── AGENTS.md
    └── agents/
        ├── explore.md
        └── plan.md
```

---

## 加载机制

### 提示词解析链（load_prompt）

所有提示词按名字逐层查找，命中即返回：

1. `.lumi/prompts/{name}.md` —— 用户自定义，优先级最高
2. `lumi/styles/{style}/prompts/{name}.md` —— 风格内置（风格无 `prompts/` 目录即跳过）
3. `lumi/prompts/{name}.md` —— 框架内置兜底

**空文件（或只剩 frontmatter）视同不存在**，继续往下找——否则一个被误清空的提示词会静默生效。三层都没有有效内容才返回 `None`。

**系统提示词（SOUL.md / AGENTS.md）**：两文件各走一次上述解析链，按 `SOUL → AGENTS` 顺序以 `\n\n` **直接拼接**（不做 XML 包裹），任一缺失则跳过该段。`default` 风格不带内置 `prompts/`，提示词全部来自 `.lumi/prompts/`；都没有时 `load_system_prompt` 返回空串，agent 以无系统提示词运行（不 fail-loud）。

**SUMMARY.md（压缩用）**：框架内置了兜底（`lumi/prompts/SUMMARY.md`），故未配置也能正常压缩，各调用点不再有「未配置 SUMMARY」的错误分支。第三层目前只放这一份——它是运行时基础设施而非风格表达，放进某个 style 会让其它 style 拿不到（style 互斥选一，不叠加）。

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

- **`active_style` 属性**（`LumiConfig`）：返回当前生效的风格名，CLI override > config.json > "default"
- **`list_styles()`**（`lumi/styles/__init__.py`）：扫描 `lumi/styles/` 子目录，列出所有可用风格
- **缓存友好**：工具定义在启动时一次性加载，运行时不变，保持 Prompt Caching 前缀稳定
