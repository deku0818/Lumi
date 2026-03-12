# 斜杠命令

Lumi TUI 支持通过 `/` 前缀触发斜杠命令。输入 `/` 后会弹出补全菜单，展示所有匹配的命令。

## 命令类型

| 类型 | 说明 |
|------|------|
| `builtin` | 内置系统命令，由 TUI 直接处理，不发送给 Agent |
| `skill` | 技能命令，加载 `.lumi/skills/<name>/SKILL.md` 中的 prompt 发送给 Agent |

## 内置命令

| 命令 | 说明 |
|------|------|
| `/skills` | 查看所有可用技能列表（独立 Screen） |
| `/resume` | 恢复历史会话（需要 checkpoint 持久化，见下文） |
| `/cron` | 查看和管理定时任务（独立 Screen，支持删除） |
| `/cron-notify` | 查看定时任务通知记录 |
| `/agents` | 查看所有可用 Agent 列表（独立 Screen，Enter 查看详情） |
| `/mcp` | 查看 MCP 服务器状态和工具列表 |
| `/clear` | 清空对话历史，开始新会话 |

## 技能命令

技能命令从 `.lumi/skills/` 目录自动加载。每个技能目录下需包含 `SKILL.md`，定义技能的名称、描述和 prompt。

使用方式：

```
/skill-name [额外文本]
```

额外文本会追加到技能 prompt 后一并发送给 Agent。

技能命令的消息格式为结构化 XML：

```
Block 0: <command-name>/xxx</command-name><command-type>skill</command-type>
Block 1: <skill-content>{prompt}</skill-content>
Block 2: <user-input>{extra_text}</user-input>  （仅当有额外文本时）
```

## 会话恢复（/resume）

`/resume` 命令用于恢复之前的对话会话。

### 前置条件

需要在 `.lumi/config.yaml` 中将 checkpoint 模式设置为持久化存储：

```yaml
agents:
  checkpoint: sqlite   # 或 postgres
```

`memory` 模式下会话不会持久化，`/resume` 会提示无法使用。

### 使用流程

1. 输入 `/resume` 打开会话选择界面
2. 使用 `↑↓` 键选择目标会话，支持搜索过滤
3. `Enter` 确认恢复，`Esc` 取消
4. 恢复后 ChatLog 会重新渲染历史消息（包括用户消息、AI 回复和工具调用）

### 实现细节

- 会话列表通过 LangGraph checkpointer 的 `alist` 接口获取，按最近活跃时间降序排列
- 当前会话会从列表中排除
- 恢复时通过 `graph.aget_state()` 读取 `StateSnapshot`，还原完整消息历史
- 技能命令消息会从 `<command-name>` 和 `<user-input>` 标签还原为 `/skill-name 用户输入` 的显示格式

## 补全菜单

输入 `/` 后自动弹出补全菜单，按前缀模糊匹配已注册命令。菜单展示命令名称和描述，CJK 字符按双倍宽度计算截断。

快捷键：

| 按键 | 操作 |
|------|------|
| `↑↓` | 切换选中项 |
| `Tab` / `Enter` | 确认补全 |
| `Esc` | 关闭菜单 |

## 架构

```
lumi/tui/slash_commands/
  models.py     # SlashCommand 数据模型、CommandType 枚举
  parser.py     # 命令解析（前缀提取、输入拆分）
  registry.py   # CommandRegistry 注册表（注册、匹配、技能同步）
  handlers.py   # 技能命令处理器工厂
```

`CommandRegistry` 管理所有命令的注册和查找。内置命令在 `LumiApp._register_commands()` 中注册，技能命令通过 `sync_skills()` 从 `.lumi/skills/` 自动同步。内置命令优先级高于同名技能命令。
