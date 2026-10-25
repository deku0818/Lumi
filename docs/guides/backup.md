# 备份恢复与 checkpoint 加密

Lumi 的会话、配置、记忆和附件分开存放。完整迁移需要同时备份用户数据和项目资源；后端重启不会自动恢复正在执行的任务或内存中的审批。

## 数据位置

默认数据根为 `~/.lumi`；启动前设置 `LUMI_CONFIG_DIR` 可整体改道。

| 位置 | 内容 |
|---|---|
| `config.json`、`lumi.json` | 启动配置、模型连接、机器设置、渠道和项目登记 |
| `checkpoints/` | SQLite checkpoint、session_meta.json、subagents.db、子代理输出、Dream 状态等 |
| `memory/projects/` | 按项目绝对路径派生的记忆目录 |
| `uploads/` | 持久附件；历史记录可能引用绝对路径 |
| `cron/` | 调度任务与执行记录，按启动工作目录派生存储键 |
| `prompts/`、`agents/`、`skills/`、权限 / hooks / MCP 文件 | 全局资源与规则 |
| 各项目的 `.lumi/`、`AGENTS.md` 和工作文件 | 项目资源、原生子代理开关和实际产出 |

`settings.checkpoint_dir` 可将 checkpoint 目录放到数据根以外，该目录也要单独备份。使用 PostgreSQL 时，图 checkpoint 在数据库中，需一并做数据库备份；session meta、子代理索引等本地文件仍需保留。

`bin/`、`node/`、`npm-global/` 等工具安装与 `cache/` 可重新准备；`logs/` 可按排障需要保留。`remote-runtime/` 是本机用户服务和版本环境，不应把其中的 socket、锁或虚拟环境当作可跨机器直接恢复的程序。

## 备份与恢复步骤

1. 等任务完成并退出本地 Lumi；远端先停止对应数据目录的用户服务。停止所有写同一份数据的进程后，再复制数据目录与自定义 checkpoint 目录，避免漏掉 SQLite WAL 中的写入。
2. 一并保存项目目录；若使用 PostgreSQL、外部 Codex / Claude Code 或 lark-cli，另外保存相应数据库和原生会话/认证环境。Lumi 的子代理索引不能替代外部 CLI 自己的会话存储。
3. 在目标机器安装相同版本，停机后恢复数据和项目，再检查权限、模型连接与凭据；需要时重新授权外部 CLI。
4. 启动前设置正确的 `LUMI_CONFIG_DIR`、自定义 checkpoint 目录及加密密钥。打开旧会话，核对历史、附件、记忆和定时任务。

尽量保留原绝对路径。项目登记、附件引用、子代理 cwd、记忆键和 cron 存储键包含路径信息，移动目录不会自动重写这些关系。换路径时需逐项重新绑定或迁移，不能只移动 checkpoint 数据库。

恢复后，未开始的子代理业务轮次会取消、运行轮次标为中断，不自动重做；需要确认进展后再向原实例派发。待投递结果有持久化回执，但普通内存审批和渠道事件队列不跨进程幸存。启动定时调度可能补跑离线错过的任务，恢复验证时应留意已启用任务。

## 可选 checkpoint 加密

加密依赖通过 uv 安装：源码项目使用 `uv sync --extra encryption`；独立 CLI 安装使用 `uv tool install 'lumi-harness[encryption]'`，已有安装需要替换时加 `--force`。桌面内嵌后端不通过手工修改其环境安装依赖。

在启动后端前设置 `LUMI_CHECKPOINT_AES_KEY`，其 UTF-8 编码必须恰为 16、24 或 32 字节。密钥应由运行环境注入并单独保存，恢复旧数据必须使用同一把密钥。

- 开启后新 checkpoint 加密写入，已有明文仍可读；不会自动重写旧记录。
- 关闭或更换密钥不会解密已有记录，旧加密 checkpoint 将无法正常读取。当前没有自动轮换或解密迁移命令。
- 此功能只保护 checkpoint 序列化内容；不加密 lumi.json、session meta、子代理事件、记忆、附件、日志或项目文件。

实现入口为 `lumi/agents/core/graph.py` 的 `_checkpoint_serde`，测试见 `tests/test_checkpoint_serde.py`。
