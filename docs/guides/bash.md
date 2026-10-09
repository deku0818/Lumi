# bash 工具使用指南

`bash` 工具在持久化 shell 会话中执行命令，保留环境变量、工作目录等状态。支持超时控制、后台执行和输出大小限制。

---

## 参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `command` | `str` | （必填） | 要执行的 shell 命令 |
| `description` | `str` | （必填） | 命令用途描述（用于审批 UI 与日志） |
| `timeout` | `float \| None` | `None` | 超时秒数，范围 `[0, 600]`；省略时**前台**回落 `120`、**后台**不限时；`0`=不限时（仅后台，前台传 `0` 报错） |
| `run_in_background` | `bool` | `False` | 是否后台执行 |

---

## 文件操作与搜索

文件查找和内容搜索通过 bash 执行，优先使用 `rg`；仅未安装 `rg` 时分别用
`find`、`grep`。`rg` / `grep` 退出码 1 表示无匹配，不应因此切换工具。
命令示例和过滤规则见 [文件与内容搜索](grep-glob.md)。

读、改、写文件分别优先用 `read`、`edit`、`write`；专用工具无法完成时再用 shell。

---

## 持久化会话

每个 thread 复用同一个 shell 进程，意味着：

- `export FOO=bar` 之后 `echo $FOO` 仍然有 `bar`
- `cd somedir` 之后后续命令在新目录下执行
- 同一会话的 bash 调用串行执行（前一条跑完才轮到下一条）

shell 是非交互的 `bash --norc --noprofile`：不读 `~/.bashrc` / profile，别名与 profile 里的设置都不生效，只继承服务进程的环境（`PATH` 末尾追加了工具箱 `bin`）。命令的 stdin 接 `/dev/null`，`read`、无参 `cat` 这类等输入的命令会立即拿到 EOF，不会挂住会话。

会话在被删除或服务退出（`ShellSessionManager.close_all()`）时关闭。Windows 下使用 `cmd.exe`，bash-only 语法不可用。

---

## 输出截断

前台执行单次 stdout 累积上限为 **30 KB**（`BASH_MAX_OUTPUT_BYTES`），超限后续整行丢弃，末尾追加 trailer：

```
... [output truncated - N KB dropped]
```

截断仅影响**返回给模型的字符串**，不会终止命令本身——shell 进程会继续读取 pipe 直到命令自然结束，避免因 pipe 阻塞挂起。

如需查看完整输出，将命令输出重定向到文件后用 `read` 工具分段读取：

```bash
yes | head -n 100000 > /tmp/big.log
```

然后 `read(file_path="/tmp/big.log", offset=0, limit=200)`。

---

## 后台执行

`run_in_background=True` 时命令交给 `BackgroundTaskManager`，在当前 shell 的工作目录下另起进程，立即返回 task ID 与输出文件路径：

```
后台任务已启动
Task ID: bg_xxx
Output File: /path/to/output.log
```

后台任务完成后会通过通知队列推送结果。后台任务的 stdout 写入文件，不受 30 KB 限制。

后台默认**不限时**（起常驻服务/长跑不会被墙钟砍掉）；需要上限再显式传 `timeout`。

运行中的后台任务可在桌面端的后台任务抽屉里查看与停止，模型侧用 `background_task` 工具查询。

---

## 超时

- **前台**：省略时默认 120 秒，最大 600 秒；传 `0`（不限时）会报错——前台无界阻塞会永久挂死当前回合且无 task_id 可取消
- **后台**：省略或传 `0` 即不限时；传正数则按上限
- 超时按整条命令的墙钟计时（持续有输出也会超时），到点对整个进程组发 `SIGTERM`，shell 退出（至多等 5 秒）后对整组补 `SIGKILL`，不留忽略 `SIGTERM` 的孤儿；前台超时会重建 shell，`cd` / `export` 状态随之丢失
- 超时返回 `Error: Timeout`（不带 stdout）

需要长时间运行的任务请用 `run_in_background=True`（默认不限时）。

---

## 权限与工作区

- bash 命令受权限规则约束，参考 [permissions.md](permissions.md)
- 复合命令（`cmd1 && cmd2`、换行分隔、`$(…)` 里的命令）会拆分子命令逐个评估，取最严格结果
- 只读命令（`ls`、`cat`、`git status`、`grep` 等）默认绕过审批
- 写操作命令涉及工作区边界外的路径时需要审批，审批卡会提示越界（见 [permissions.md](permissions.md)）
