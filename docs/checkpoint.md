# Checkpoint 回退机制

Lumi 内置 Checkpoint 系统，在每轮用户消息发送前自动快照工作区文件状态和 LangGraph 会话状态。当 Agent 执行结果不理想时，可以一键回退到任意历史节点，恢复文件和对话，就像那条消息从未发送过一样。

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

1. 工作区文件恢复到该消息发送前的状态
2. 聊天窗口清空并重新渲染该消息之前的历史对话
3. 该消息内容自动填入输入框，可直接重新发送或修改

---

## 工作原理

Checkpoint 由两个独立的快照机制协同工作：

### 文件快照 — Shadow Git

在项目目录之外维护一个独立的 git 仓库（shadow repo），专门用于追踪项目文件变化，项目本身的 Git 历史完全不受影响。

```
~/.lumi/checkpoints/shadow/{thread_id}/
  .git/          # shadow repo 的 git 数据
  meta.json      # checkpoint 元数据列表
```

每轮用户消息发送前，`ShadowGitManager` 执行：

1. `git add -A` — 暂存所有变化（新增、修改、删除）
2. `git commit --allow-empty` — 提交快照（无变化时创建空 commit 以记录会话断点）
3. 将 commit hash、时间戳、用户消息摘要、LangGraph checkpoint_id 写入 `meta.json`

shadow repo 的 `GIT_WORK_TREE` 指向项目目录（即 `ShadowGitManager` 的 `project_dir` 参数），`GIT_DIR` 指向 shadow 目录下的 `.git`，两者通过环境变量隔离。

### 会话快照 — LangGraph Checkpoint

LangGraph 自身的 checkpointer（SQLite / Postgres / Memory）会在每个节点执行后自动保存会话状态。Lumi 在创建文件快照时，同时记录当前 LangGraph 的 `checkpoint_id`，建立文件快照与会话状态的关联。

---

## 回退流程

当用户选择回退到某个 checkpoint 时，系统按以下顺序执行：

```
用户选择 checkpoint
       │
       ▼
┌─────────────────────┐
│ 1. 恢复文件          │  git add -A 暂存工作区变更
│    清理新增文件       │  对比暂存区与目标 commit，逐文件删除新增文件
│    截断 meta.json    │  移除目标及之后的所有 checkpoint 记录
│    重置 shadow HEAD  │  git reset --soft {commit}
└─────────────────────┘
       │
       ▼
┌─────────────────────┐
│ 2. 回退 LangGraph    │  aupdate_state 从目标 checkpoint fork
│    更新 config       │  后续对话基于 fork 后的状态继续
└─────────────────────┘
       │
       ▼
┌─────────────────────┐
│ 3. 重建 TUI          │  清空 ChatLog
│    渲染历史消息       │  用目标 checkpoint_id 读取会话状态
│    填充输入框        │  将回退消息的内容填入输入框
└─────────────────────┘
```

---

## Rewind 界面

每个 checkpoint 条目显示：

| 信息 | 说明 |
|------|------|
| 标签 | 用户消息摘要（截取前 70 字符） |
| diff 统计 | 该轮 Agent 执行后产生的文件变更：`N files changed +X -Y` |
| 时间 | 相对时间（just now / 3m ago / 2h ago） |
| ID | shadow git commit hash 前 8 位（`CheckpointInfo.checkpoint_id` 派生属性） |

列表按时间正序排列（从旧到新），打开时自动选中并滚动到最后一项（最新 checkpoint）。

diff 归属逻辑：checkpoint 快照的是消息发送前的文件状态，因此某轮 Agent 造成的文件变更会体现在当前 checkpoint 与下一个 checkpoint 之间的差异中。为了让 diff 正确归属到对应的消息：

- 非最后一个 checkpoint：显示 `diff(当前 commit, 下一个 commit)`
- 最后一个 checkpoint：显示 `diff(当前 commit, 工作区)`，无需等待下一条消息即可看到变更

---

## 数据存储

### Shadow Repo

| 路径 | 说明 |
|------|------|
| `~/.lumi/checkpoints/shadow/{thread_id}/.git` | shadow git 数据 |
| `~/.lumi/checkpoints/shadow/{thread_id}/meta.json` | checkpoint 元数据 |

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

每个 thread 最多保留 20 个 checkpoint（`_MAX_CHECKPOINTS = 20`），超出时自动淘汰最旧的 meta 记录。注意：仅 meta.json 条目被移除，对应的 git commit 仍保留在 shadow repo 历史中。

`meta.json` 采用原子写入（先写临时文件再 rename），防止进程中断导致文件损坏。若检测到 meta.json 损坏，会自动备份为 `meta.json.bak` 后重置。

### .gitignore 复用

shadow repo 初始化时会复制项目的 `.gitignore` 到 `{shadow_repo}/.git/info/exclude`，确保 `node_modules`、`__pycache__` 等目录不被追踪。

---

## 注意事项

1. **仅限当前会话**：checkpoint 与 thread_id 绑定，切换会话后无法访问其他会话的 checkpoint
2. **回退不可逆**：回退时会截断目标 checkpoint 之后的所有记录，被回退的历史分支无法恢复
3. **文件范围**：shadow git 追踪的是整个项目目录（受 `.gitignore` 过滤），包括 Agent 未修改的文件
4. **空 commit**：如果某轮对话没有产生文件变更，仍会创建空 commit 作为会话断点
5. **磁盘占用**：shadow repo 是完整的 git 仓库，大型项目可能占用较多磁盘空间
6. **首次 checkpoint**：第一个 checkpoint 是初始快照。若其关联的 LangGraph checkpoint_id 为空，回退后聊天窗口将为空白，工作区恢复到会话开始时的状态
7. **LangGraph 会话回退**：通过 `aupdate_state` fork 实现，不会删除 LangGraph 中的历史 checkpoint 数据

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
│   ShadowGitManager    LangGraph aget_state           │
│   (git add + commit)  (获取 checkpoint_id)           │
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
│   restore_checkpoint   aupdate_state (fork)          │
│   (git checkout)       (回退会话状态)                 │
│         │                    │                       │
│         └─────────┬──────────┘                       │
│                   ▼                                  │
│         _restore_messages + 重建 ChatLog              │
└──────────────────────────────────────────────────────┘
```

### 核心模块

| 模块 | 路径 | 职责 |
|------|------|------|
| Shadow Git 管理 | `lumi/agents/tools/checkpoint.py` | 文件快照的创建、列表、恢复、diff 统计 |
| Bridge 集成 | `lumi/tui/agent_bridge.py` | 协调文件快照与 LangGraph 会话回退 |
| Rewind 界面 | `lumi/tui/screens/rewind_screen.py` | checkpoint 选择 UI |
| TUI 入口 | `lumi/tui/app.py` | rewind 命令注册、回退后 ChatLog 重建 |
