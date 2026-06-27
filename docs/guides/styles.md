# 风格系统使用指南

风格系统为 Lumi 提供了一套可切换的提示词、子 Agent 和技能配置预设。不同风格适用于不同使用场景，用户也可以在此基础上覆盖任意部分。

---

## 内置风格

| 风格 | 说明 |
|------|------|
| `default` | 默认风格。**不内置提示词**——系统提示词全部来自用户 `.lumi/prompts/`；可内置 skill / agent（当前为空） |
| `code` | 面向软件工程。内置完整的系统提示词（SOUL / AGENTS）和 explore / plan 两个子 Agent |

每种风格可内置三类资源：`prompts/`（系统提示词）、`agents/`（子 Agent）、`skills/`（技能），三者均为可选。`default` 不带 `prompts/`，提示词全部来自用户 `.lumi/prompts/`；两处都没有时以空系统提示词运行（不报错）。

### 提示词组装

系统提示词由 `SOUL.md`、`AGENTS.md` 两个文件按此顺序**直接拼接**（不做 XML 包裹），任一文件缺失则跳过该段。

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
│   └── AGENTS.md                  # 覆盖 style 的 AGENTS.md
├── agents/
│   └── explore.md                 # 覆盖 style 的 explore 子 Agent
└── skills/
    └── my-skill/SKILL.md          # 覆盖 style 的同名 skill
```

加载优先级：用户 `.lumi/` 下的同名文件 > style 内置文件（prompts / agents / skills 三类一致）。升级 Lumi 时自动获取改进，用户只需覆盖想要自定义的部分。

---

## 创建自定义风格

在 `lumi/styles/` 下创建新目录（三个子目录均可选）：

```bash
mkdir -p lumi/styles/my-style/prompts
mkdir -p lumi/styles/my-style/agents
mkdir -p lumi/styles/my-style/skills
```

在 `prompts/` 下放置 SOUL.md、AGENTS.md，在 `agents/` 下放置子 Agent 配置，在 `skills/<name>/SKILL.md` 下放置技能。不提供 `prompts/` 的风格（如 `default`）提示词全部由用户 `.lumi/prompts/` 提供。
