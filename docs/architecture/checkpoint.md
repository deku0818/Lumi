# Checkpoint 回退架构

Checkpoint 系统的内部实现。用户使用指南见 [`docs/guides/checkpoint.md`](../guides/checkpoint.md)。

---

## 核心机制

Checkpoint 由两个独立的快照机制协同工作：

### 文件快照 — FileChangeTracker + FileCheckpointManager

不依赖 git，只追踪 edit/write 工具实际修改的文件。`FileChangeTracker` 在每轮 Agent 执行期间拦截文件操作，记录修改前的原始内容；`FileCheckpointManager` 在每轮开始前将上一轮的变更持久化到磁盘。

每轮用户消息发送前，`FileCheckpointManager` 执行：

1. `end_turn()` — 收集上一轮 tracker 中的文件变更
2. `_save_changes()` — 将变更持久化到对应 checkpoint 的 changes 目录
3. 创建新 checkpoint 条目 — 记录 hash、时间戳、用户消息摘要、LangGraph checkpoint_id
4. `start_turn()` — 开始追踪新一轮的文件变更

`FileChangeTracker` 注册在 filesystem backend 中，edit/write 工具在执行前自动调用 `record_pre_edit()`/`record_pre_write()`。

### 会话快照 — LangGraph Checkpoint

LangGraph 自身的 checkpointer 在每个节点执行后自动保存会话状态。Lumi 在创建文件快照时同时记录当前 LangGraph 的 `checkpoint_id`，建立关联。

---

## 回退流程

```
用户选择 checkpoint
       │
       ▼
┌─────────────────────────┐
│ 1. 恢复文件              │  收集目标及之后所有轮次的变更
│    created → 删除        │  新建的文件直接删除
│    modified → 写回原始   │  修改的文件恢复到原始内容
│    截断 meta.json        │  移除目标及之后的所有记录
└─────────────────────────┘
       │
       ▼
┌─────────────────────────┐
│ 2. 回退 LangGraph        │  将 config 指向目标 checkpoint
│    清理旧分支            │  删除目标之后的所有 checkpoint
└─────────────────────────┘
       │
       ▼
┌─────────────────────────┐
│ 3. 前端重渲染            │  回退成功后前端重新拉取历史
│                         │  （load_history），重建聊天流
└─────────────────────────┘
```

`rewind_to_checkpoint()` 本身只做「恢复文件 + 回退 LangGraph 会话 + 清理旧分支」，不涉及任何前端渲染；消息重渲染由前端在回退后重新加载历史完成。

---

## 数据存储

```
~/.lumi/checkpoints/filediff/{thread_id}/
├── meta.json                              # checkpoint 列表
└── changes/{checkpoint_id}/
    ├── manifest.json                      # 变更文件清单
    └── files/{safe_filename}              # 原始文件内容副本
```

每个 thread 最多保留 `max_checkpoints`（默认 20，配置项见 `GlobalConfig`）个 checkpoint，超出时淘汰最旧记录。`meta.json` 采用原子写入，损坏时自动备份为 `meta.json.bak` 后重置。

---

## 架构图

Checkpoint 完全是后端 `AgentBridge` 层的能力。每轮发送消息时自动建点，回退则通过 bridge 方法触发。

```
┌──────────────────────────────────────────────────────┐
│                   AgentBridge (后端)                  │
│                                                      │
│  send_message ──▶ stream_response                    │
│                    │                                 │
│          _create_checkpoint_before_turn               │
│         ┌─────────┴──────────┐                       │
│         ▼                    ▼                       │
│   FileCheckpointManager LangGraph aget_state         │
│   (end_turn + save)     (取 clean checkpoint_id)     │
│         └─────────┬──────────┘                       │
│                   ▼                                  │
│             meta.json 记录关联                        │
│                                                      │
│  list_checkpoints() ──▶ 列出当前 thread 的快照        │
│  rewind_to_checkpoint(cp)                            │
│         ┌─────────┴──────────┐                       │
│         ▼                    ▼                       │
│   restore_checkpoint   设置 checkpoint_id +          │
│   (恢复文件内容)       aprune_checkpoints_after      │
│                        (回退会话状态 + 清旧分支)       │
└──────────────────────────────────────────────────────┘
```

> 注：`AgentBridge` 已提供 `list_checkpoints()` / `rewind_to_checkpoint()`，但目前尚未通过 `lumi/gateway/channels/ws.py` 暴露为 WS RPC（旧 TUI 的 RewindScreen 已随 TUI 一并移除）。desktop 端的回退入口待后续接线，届时前端在回退成功后通过 `load_history` 重新拉取消息重建聊天流。

## 核心模块

| 模块 | 路径 | 职责 |
|------|------|------|
| 文件变更追踪 | `lumi/agents/runtime/file_tracker.py` | 拦截 edit/write 操作，记录修改前的原始内容 |
| Checkpoint 管理 | `lumi/agents/runtime/checkpoint.py` | 文件快照的创建、列表、恢复、diff 统计 |
| Bridge 集成 | `lumi/gateway/bridge.py` | 协调文件快照与 LangGraph 会话回退（`_create_checkpoint_before_turn` / `list_checkpoints` / `rewind_to_checkpoint`） |

## 残留状态恢复

`AgentBridge` 在每次流式调用前检测残留的图状态——若 `state.next` 非空且无 interrupt，沿 parent 链回退到最近的 clean checkpoint，确保不包含中断轮次的消息。
