# 风格系统架构

风格系统的内部加载机制。用户使用指南见 [`docs/guides/styles.md`](../guides/styles.md)。

---

## 目录结构

```
lumi/styles/
├── default/
│   ├── prompts/
│   │   └── tools/
│   │       ├── EnterPlanMode.md
│   │       └── ExitPlanMode.md
│   └── agents/
└── code/
    ├── prompts/
    │   ├── SOUL.md
    │   ├── GUARDRAILS.md
    │   ├── AGENTS.md
    │   └── tools/
    │       ├── EnterPlanMode.md
    │       └── ExitPlanMode.md
    └── agents/
        ├── explore.md
        └── plan.md
```

---

## 加载机制

### 系统提示词（SOUL.md / GUARDRAILS.md / AGENTS.md）

1. 从 `lumi/styles/{style}/prompts/` 读取基础文件
2. 用 `.lumi/prompts/` 下的同名文件覆盖

每个文件用 XML 标签包裹后拼接为最终系统提示词：

```xml
<SOUL>
...SOUL.md 内容...
</SOUL>

<GUARDRAILS>
...GUARDRAILS.md 内容...
</GUARDRAILS>

<AGENTS>
...AGENTS.md 内容...
</AGENTS>
```

### 工具提示词（prompts/tools/）

工具的 description 和 response 从 MD 文件加载，查找顺序：

1. `.lumi/prompts/tools/{ToolName}.md`（用户覆盖）
2. `lumi/styles/{style}/prompts/tools/{ToolName}.md`（style 内置）

### 子 Agent 配置（agents/）

1. 从 `lumi/styles/{style}/agents/` 读取内置 Agent
2. 用 `.lumi/agents/` 下的同名文件覆盖（覆盖时输出 warning 日志）

---

## 关键实现

- **`active_style` 属性**（`LumiConfig`）：返回当前生效的风格名，CLI override > config.yaml > "default"
- **`list_styles()`**（`lumi/styles/__init__.py`）：扫描 `lumi/styles/` 子目录，列出所有可用风格
- **缓存友好**：工具定义在启动时一次性加载，运行时不变，保持 Prompt Caching 前缀稳定
