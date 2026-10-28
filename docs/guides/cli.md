# CLI 与独立后端

桌面用户通常无需手动启动后端。CLI 适合终端单次任务、服务器运维和外部程序接入；桌面原生远端仍使用 [SSH 用户服务](deploy.md)。

## 安装与单次任务

```bash
uv tool install lumi-harness
lumi --version
lumi -p "概括当前目录中的 README"
```

源码仓库使用 `uv sync`，命令前加 `uv run`。模型配置见 [配置指南](config.md)；单次任务以当前目录为工作目录，config.json 的发现不遍历父目录。

`-p` 没有人工审批通道，遵循全局无人值守设置，默认 AI 审批；`--privileged-danger` 显式选择特权模式，deny 仍生效。`--accept-edits` 在无人值守入口跟随全局无人值守设置。具体规则见 [权限指南](permissions.md#无人应答的执行)。

## 启动 API 服务

```bash
lumi serve --host 127.0.0.1 --port 8765
```

鉴权令牌可在启动前通过 `LUMI_TOKEN` 注入；未配置时不启用令牌校验。外部程序的对话走 WebSocket JSON-RPC，文件走 HTTP，见 [API 指南](api.md)。`serve` 的进程配置固定用户数据根，项目资源按会话的工作目录加载。

容器镜像运行同一 `serve` 接口，示例见 [镜像说明](../dockerhub-description.md)。容器的配置与数据目录要挂持久卷，项目路径需在容器内真实存在；桌面不连接容器的服务地址。

## 运维与接入命令

| 命令 | 用途 |
|---|---|
| `lumi status` | 版本、安装方式、数据目录与服务状态 |
| `lumi logs -f` | 跟踪运行日志 |
| `lumi migrate --check` / `--apply` | [临时旧数据迁移](migrate.md)，不自动备份 |
| `lumi update` | 更新可独立升级的 CLI 安装；源码、容器和桌面内嵌后端按各自方式更新 |
| `lumi env status` | 检查 uv / Node / rg / OfficeCLI |
| `lumi env install [uv\|node\|rg\|officecli]` | 留空装齐缺失项，指定名称只装该工具；已安装的跳过 |
| `lumi mcp ...` | [MCP 配置与测试](mcp.md) |
| `lumi feishu config` | 列出或修改机器人配置 |
| `lumi feishu diagnose` | 检查环境、凭证、权限、事件和版本发布 |
| `lumi feishu sync-skills` | 同步技能到机器人绑定项目 |

飞书多机器人使用 `--bot` 选择、`--new` 新建；完整流程见 [内置接入参考](../../lumi/styles/skills/lumi-config/references/feishu.md)。各命令的参数以 `lumi <命令> --help` 为准。
