# 风格系统使用指南

风格系统为 Lumi 提供了一套可切换的提示词和子 Agent 配置预设。不同风格适用于不同使用场景，用户也可以在此基础上覆盖任意部分。

---

## 内置风格

| 风格 | 说明 |
|------|------|
| `default` | 默认风格。仅包含工具配置模板，不含系统提示词。适合完全自定义的用户 |
| `code` | 面向软件工程。内置完整的系统提示词（SOUL / GUARDRAILS / AGENTS）和 explore / plan 两个子 Agent |

---

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

---

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

加载优先级：用户 `.lumi/` 下的同名文件 > style 内置文件。升级 Lumi 时自动获取改进，用户只需覆盖想要自定义的部分。

---

## 创建自定义风格

在 `lumi/styles/` 下创建新目录：

```bash
mkdir -p lumi/styles/my-style/prompts/tools
mkdir -p lumi/styles/my-style/agents
```

在 `prompts/` 下放置 SOUL.md、GUARDRAILS.md、AGENTS.md，在 `agents/` 下放置子 Agent 配置。
