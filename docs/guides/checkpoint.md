# Checkpoint 回退使用指南

Lumi 在每轮用户消息发送前自动快照文件和会话状态。当 Agent 执行结果不理想时，可以一键回退到任意历史节点。

---

## 快速上手

在 TUI 中，空闲状态下双击 `Esc` 或输入 `/rewind` 打开 Rewind 界面：

```
› 你好
  just now · 3f989e80

› 帮我重构 utils.py
  2 files changed +24 -40
  3m ago · a1b2c3d4
```

`↑↓` 选择目标 checkpoint，`Enter` 确认回退。回退后：

1. 被 Agent 修改的文件恢复到该消息发送前的状态
2. 聊天窗口重新渲染该消息之前的历史对话
3. 该消息内容自动填入输入框，可直接重新发送或修改

---

## Rewind 界面信息

每个 checkpoint 条目显示：

| 信息 | 说明 |
|------|------|
| 标签 | 用户消息摘要（前 70 字符） |
| diff 统计 | 该轮产生的文件变更：`N files changed +X -Y` |
| 时间 | 相对时间（just now / 3m ago / 2h ago） |
| ID | checkpoint hash 前 8 位 |

---

## 注意事项

1. **仅追踪工具修改的文件**：只有通过 edit/write 工具修改的文件会被追踪，手动修改不在回退范围内
2. **仅限当前会话**：checkpoint 与 thread_id 绑定
3. **回退不可逆**：被回退的历史分支无法恢复
4. **磁盘占用**：仅保存被修改文件的原始内容副本，占用远小于完整 git 仓库
