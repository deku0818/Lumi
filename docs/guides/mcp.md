# MCP 接入与管理

MCP 为 Lumi 提供外部工具。桌面可在「设置 → MCP」管理；CLI 与面板读写同一份配置，在运行后端的机器上启动服务器或连接远程端点。

## 配置范围

| 范围 | 文件 | 生效范围 |
|---|---|---|
| 项目（默认） | `<项目>/.lumi/mcp_server.json` | 该项目 |
| 全局 | `<数据根>/mcp_server.json` | 该机器所有项目 |

项目同名配置覆盖全局。CLI 默认取当前工作目录为项目根，其他目录用 `--project` 指定；全局配置加 `--scope global`。数据根与其它配置见 [配置指南](config.md)。

## 添加与验证

```bash
# 本地 stdio：替换为服务提供方给出的可执行命令
lumi mcp add demo /absolute/path/to/mcp-server

# 远程 HTTP：替换为服务提供方给出的 URL
lumi mcp add remote https://example.com/mcp

lumi mcp test demo
lumi mcp list
lumi mcp get demo
lumi mcp remove demo
```

`add` 的选项放在名称之前，名称后所有参数传给服务器命令。鉴权使用 `--env` 或 `--header`；已有单个 server 的 JSON 对象可用 `add-json` 原样添加。细节见 `lumi mcp add --help` 与 [内置接入参考](../../lumi/styles/skills/lumi-config/references/mcp.md)。具体 MCP 服务的包名、参数和认证方式以该服务官方配置为准。

配置中的 `${VAR}` 不会由 Lumi 展开。需要密钥时可写全局配置，或将含密钥的项目配置加入 `.gitignore`。

## 生效与故障处理

- 配置变更后，运行中的会话在下一条消息感知并后台连接；连上后工具可用，无需重启。
- 首次连接失败后，相同配置不自动重试；CLI `test` 使用独立临时连接，成功不会重置会话连接。修复后在桌面 MCP 面板保存或成功测试，强制重连，再发下一条消息。
- 已运行的 server 崩溃时，工具调用失败后下一条消息会尝试恢复。
- npx / uvx 找不到：在该后端机器检查 Node 或 uv；首次拉包超时先验证网络和包源。401 / 403 按服务方要求修复凭据。

MCP 配置支持 stdio、HTTP / SSE；SSE 的 CLI transport 使用 `-t sse`。工具仍遵循 Lumi 的统一权限规则。
