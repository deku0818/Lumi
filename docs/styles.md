# 风格系统（Styles）

风格系统为 Lumi 提供了一套可切换的提示词和子 Agent 配置预设。不同风格适用于不同使用场景，用户也可以在此基础上覆盖任意部分。

## 内置风格

| 风格 | 说明 |
|------|------|
| `default` | 默认风格。`prompts/` 下仅包含工具配置模板（EnterPlanMode.md、ExitPlanMode.md），不含系统提示词。适合完全自定义提示词的用户 |
| `code` | 面向软件工程。内置完整的 SOUL.md（专业客观的技术人格）、GUARDRAILS.md（安全护栏）、AGENTS.md（编码原则和输出风格），以及 explore / plan 两个子 Agent |

## 配置方式

### config.yaml

```yaml
style: code
```

### CLI 参数（优先级更高）

```bash
lumi -s code
lumi -s code -p "重构这个模块"
```

优先级：CLI `--style` > config.yaml `style` > 默认值 `"default"`

## 目录结构

每个风格是 `lumi/styles/` 下的一个子目录：

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

## 加载机制

### 系统提示词（SOUL.md / GUARDRAILS.md / AGENTS.md）

加载顺序：

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

目前支持的工具配置文件：`EnterPlanMode.md`、`ExitPlanMode.md`。格式详见 [docs/plan.md](plan.md#自定义提示词)。

### 子 Agent 配置（agents/）

加载顺序：

1. 从 `lumi/styles/{style}/agents/` 读取内置 Agent
2. 用 `.lumi/agents/` 下的同名文件覆盖（覆盖时输出 warning 日志）

## 用户覆盖

用户无需修改 style 源文件，只需在 `.lumi/` 下放置同名文件即可覆盖：

```
.lumi/
├── prompts/
│   ├── SOUL.md                    # 覆盖 style 的 SOUL.md
│   ├── AGENTS.md                  # 覆盖 style 的 AGENTS.md
│   └── tools/
│       └── EnterPlanMode.md       # 覆盖 style 的 EnterPlanMode 工具配置
└── agents/
    └── explore.md                 # 覆盖 style 的 explore 子 Agent
```

这种分层设计的好处：

- style 提供开箱即用的默认值，升级 Lumi 时自动获取改进
- 用户只需覆盖想要自定义的部分，其余继承 style 默认值
- `default` 风格不含系统提示词，适合完全自定义的场景

## 创建自定义风格

在 `lumi/styles/` 下创建新目录，包含 `prompts/` 和/或 `agents/` 子目录：

```bash
mkdir -p lumi/styles/my-style/prompts/tools
mkdir -p lumi/styles/my-style/agents
```

然后在 `prompts/` 下放置 SOUL.md、GUARDRAILS.md、AGENTS.md，在 `agents/` 下放置子 Agent 配置。

使用 `lumi/styles/__init__.py` 中的 `list_styles()` 可列出所有可用风格。
