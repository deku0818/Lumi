# Checkpoint 回退机制

Lumi 内置 Checkpoint 系统，在每轮用户消息发送前自动快照被修改文件的原始状态和 LangGraph 会话状态。当 Agent 执行结果不理想时，可以一键回退到任意历史节点，恢复文件和对话，就像那条消息从未发送过一样。

---

## 快速上手

在 TUI 中，空闲状态下双击 `Esc` 键或输入 `/rewind` 命令打开 Rewind 界面：

```
› 你好
  just now · 3f989e80

› 帮我重构 utils.py
  2 files changed +24 -40
  3m ago · a1b2c3d4
```

用 ↑↓ 选择目标 checkpoint，按 Enter 确认回退。回退后：

1. 被 Agent 修改的文件恢复到该消息发送前的状态
2. 聊天窗口清空并重新渲染该消息之前的历史对话
3. 该消息内容自动填入输入框，可直接重新发送或修改

---

## 工作原理

Checkpoint 由两个独立的快照机制协同工作：

### 文件快照 — FileChangeTracker + FileCheckpointManager

不依赖 git，只追踪 edit/write 工具实际修改的文件。`FileChangeTracker` 在每轮 Agent 执行期间拦截文件操作，记录修改前的原始内容；`FileCheckpointManager` 在每轮开始前将上一轮的变更持久化到磁盘。

```
~/.lumi/checkpoints/filediff/{thread_id}/
  meta.json                              # checkpoint 列表
  changes/{checkpoint_id}/
      manifest.json                      # 变更文件清单
      files/{safe_filename}              # 原始文件内容副本
```

每轮用户消息发送前，`FileCheckpointManager` 执行：

1. `end_turn()` — 收集上一轮 tracker 中的文件变更
2. `_save_changes()` — 将变更（原始内容）持久化到对应 checkpoint 的 changes 目录
3. 创建新 checkpoint 条目 — 记录 hash、时间戳、用户消息摘要、LangGraph checkpoint_id
4. `start_turn()` — 开始追踪新一轮的文件变更

`FileChangeTracker` 注册在 filesystem backend 中，edit/write 工具在执行前自动调用 `record_pre_edit()`/`record_pre_write()`，记录文件修改前的原始内容或标记新建文件。

### 会话快照 — LangGraph Checkpoint

LangGraph 自身的 checkpointer（SQLite / Postgres / Memory）会在每个节点执行后自动保存会话状态。Lumi 在创建文件快照时，同时记录当前 LangGraph 的 `checkpoint_id`，建立文件快照与会话状态的关联。

---

## 回退流程

当用户选择回退到某个 checkpoint 时，系统按以下顺序执行：

```
用户选择 checkpoint
       │
       ▼
┌─────────────────────────┐
│ 1. 恢复文件              │  收集目标之后所有轮次的变更
│    created → 删除        │  新建的文件直接删除
│    modified → 写回原始   │  修改的文件恢复到原始内容
│    截断 meta.json        │  移除目标之后的所有记录
│    清理 changes 目录     │  删除孤立的变更数据
└─────────────────────────┘
       │
       ▼
┌─────────────────────────┐
│ 2. 回退 LangGraph        │  将 config 指向目标 checkpoint
│    更新 checkpoint_id    │  后续对话从该状态分支继续
└─────────────────────────┘
       │
       ▼
┌─────────────────────────┐
│ 3. 重建 TUI              │  清空 ChatLog
│    渲染历史消息           │  用目标 checkpoint_id 读取会话状态
│    填充输入框            │  将回退消息的内容填入输入框
└─────────────────────────┘
```

---

## Rewind 界面

每个 checkpoint 条目显示：

| 信息 | 说明 |
|------|------|
| 标签 | 用户消息摘要（截取前 70 字符） |
| diff 统计 | 该轮 Agent 执行后产生的文件变更：`N files changed +X -Y` |
| 时间 | 相对时间（just now / 3m ago / 2h ago） |
| ID | checkpoint hash 前 8 位（`CheckpointInfo.checkpoint_id` 派生属性） |

列表按时间正序排列（从旧到新），打开时自动选中并滚动到最后一项（最新 checkpoint）。

diff 归属逻辑：checkpoint 快照的是消息发送前的文件状态，某轮 Agent 造成的文件变更会保存在该 checkpoint 的 changes 目录中。

- 非最后一个 checkpoint：从已保存的 changes 目录计算 diff 统计
- 最后一个 checkpoint：从 tracker 内存中的实时变更计算 diff 统计

---

## 数据存储

### Checkpoint 目录

| 路径 | 说明 |
|------|------|
| `~/.lumi/checkpoints/filediff/{thread_id}/meta.json` | checkpoint 元数据列表 |
| `~/.lumi/checkpoints/filediff/{thread_id}/changes/{id}/manifest.json` | 单轮变更的文件清单 |
| `~/.lumi/checkpoints/filediff/{thread_id}/changes/{id}/files/` | 原始文件内容副本 |

`meta.json` 格式：

```json
[
  {
    "checkpoint_id": "a1b2c3d4",
    "commit_hash": "a1b2c3d4e5f6...",
    "timestamp": 1710000000.0,
    "label": "帮我重构 utils.py",
    "langgraph_checkpoint_id": "lg-xxxx-...",
    "langgraph_parent_checkpoint_id": "lg-yyyy-..."
  }
]
```

`manifest.json` 格式：

```json
[
  {
    "path": "/absolute/path/to/file.py",
    "change_type": "modified",
    "safe_name": "%2Fabsolute%2Fpath%2Fto%2Ffile.py"
  }
]
```

每个 thread 最多保留 20 个 checkpoint（`_MAX_CHECKPOINTS = 20`），超出时自动淘汰最旧的记录及其 changes 目录。

`meta.json` 采用原子写入（先写临时文件再 rename），防止进程中断导致文件损坏。若检测到 meta.json 损坏，会自动备份为 `meta.json.bak` 后重置。

---

## 注意事项

1. **仅追踪工具修改的文件**：只有通过 edit/write 工具修改的文件会被追踪，手动修改或其他途径的变更不在回退范围内
2. **仅限当前会话**：checkpoint 与 thread_id 绑定，切换会话后无法访问其他会话的 checkpoint
3. **回退不可逆**：回退时会截断目标 checkpoint 之后的所有记录，被回退的历史分支无法恢复
4. **磁盘占用**：仅保存被修改文件的原始内容副本，占用远小于完整 git 仓库
5. **首次 checkpoint**：第一个 checkpoint 是初始快照。若其关联的 LangGraph checkpoint_id 为空，回退后聊天窗口将为空白，工作区恢复到会话开始时的状态
6. **LangGraph 会话回退**：通过直接指向目标 checkpoint_id 实现分支，不会删除 LangGraph 中的历史 checkpoint 数据
7. **残留状态恢复**：`AgentBridge` 在每次流式调用前检测残留的图状态（待执行节点但无中断），自动回退到最近的干净 checkpoint

---

## 架构

```
┌──────────────────────────────────────────────────────┐
│                     Lumi TUI                         │
│                                                      │
│  用户输入 ──▶ AgentBridge.stream_response            │
│                    │                                 │
│          _create_checkpoint_before_turn               │
│                    │                                 │
│         ┌─────────┴──────────┐                       │
│         ▼                    ▼                       │
│   FileCheckpointManager LangGraph aget_state         │
│   (end_turn + save)     (获取 checkpoint_id)         │
│         │                    │                       │
│         └─────────┬──────────┘                       │
│                   ▼                                  │
│             meta.json 记录关联                        │
│                                                      │
│  双击 Esc ──▶ RewindScreen                           │
│                    │                                 │
│          list_checkpoints (含 diff stat)              │
│                    │                                 │
│          用户选择 ──▶ rewind_to_checkpoint            │
│                    │                                 │
│         ┌─────────┴──────────┐                       │
│         ▼                    ▼                       │
│   restore_checkpoint   设置 checkpoint_id            │
│   (恢复文件内容)       (回退会话状态)                 │
│         │                    │                       │
│         └─────────┬──────────┘                       │
│                   ▼                                  │
│         _restore_messages + 重建 ChatLog              │
└──────────────────────────────────────────────────────┘
```

### 核心模块

| 模块 | 路径 | 职责 |
|------|------|------|
| 文件变更追踪 | `lumi/agents/tools/file_tracker.py` | 拦截 edit/write 操作，记录修改前的原始内容 |
| Checkpoint 管理 | `lumi/agents/tools/checkpoint.py` | 文件快照的创建、列表、恢复、diff 统计 |
| Bridge 集成 | `lumi/tui/agent_bridge.py` | 协调文件快照与 LangGraph 会话回退 |
| Rewind 界面 | `lumi/tui/screens/rewind_screen.py` | checkpoint 选择 UI |
| TUI 入口 | `lumi/tui/app.py` | rewind 命令注册、回退后 ChatLog 重建 |
