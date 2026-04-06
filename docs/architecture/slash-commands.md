# 斜杠命令架构

斜杠命令系统的内部实现。用户使用指南见 [`docs/guides/slash-commands.md`](../guides/slash-commands.md)。

---

## 模块结构

```
lumi/tui/slash_commands/
├── models.py     # SlashCommand 数据模型、CommandType 枚举
├── parser.py     # 命令解析（前缀提取、输入拆分）
├── registry.py   # CommandRegistry 注册表（注册、匹配、技能同步）
└── handlers.py   # 技能命令处理器工厂
```

---

## 命令类型

| 类型 | 说明 |
|------|------|
| `builtin` | 内置系统命令，由 TUI 直接处理，不发送给 Agent |
| `skill` | 技能命令，加载 `.lumi/skills/<name>/SKILL.md` 中的 prompt 发送给 Agent |

---

## CommandRegistry

管理所有命令的注册和查找：

- 内置命令在 `LumiApp._register_commands()` 中注册
- 技能命令通过 `sync_skills()` 从 `.lumi/skills/` 自动同步
- 内置命令优先级高于同名技能命令

---

## 技能命令消息格式

技能命令的消息采用结构化 XML：

```
Block 0: <command-name>/xxx</command-name><command-type>skill</command-type>
Block 1: <skill-content>{prompt}</skill-content>
Block 2: <user-input>{extra_text}</user-input>  （仅当有额外文本时）
```

---

## /resume 实现

- 会话列表通过 LangGraph checkpointer 的 `alist` 接口获取，按最近活跃时间降序
- 当前会话从列表中排除
- 恢复时通过 `graph.aget_state()` 读取 `StateSnapshot`，还原完整消息历史
- 技能命令消息从 `<command-name>` 和 `<user-input>` 标签还原为 `/skill-name 用户输入` 的显示格式
