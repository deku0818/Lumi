# MCP 服务器接入

把外部 MCP 服务器（浏览器控制、数据库、Notion、地图……）接进 Lumi，接好后
新工具自动出现在会话里。面向零基础用户——命令全程你来跑，能代劳的都代劳，
用户只需要提供「想接什么」和必要的密钥。

所有命令用 `"${LUMI_BIN:-lumi}" mcp …`；Windows（cmd 没有 `${VAR:-默认}` 语法）
用 `"%LUMI_BIN%" mcp …`。下文示例简写为 `lumi mcp`。

## 配置分两层

| 层 | 文件 | 生效范围 |
|---|---|---|
| project（**默认**） | `<项目>/.lumi/mcp_server.json` | 只在该项目的会话 |
| global | `~/.lumi/mcp_server.json` | 该机器所有项目 |

- 默认落项目层（与桌面端 MCP 面板一致），项目层同名覆盖全局层；
  用户明确说「所有项目都要用」才加 `--scope global`。
- 项目 = 当前会话绑定的项目目录。命令默认取**当前工作目录**当项目根，
  你的 shell 不在项目根时用 `--project /项目路径` 指明。

## 添加

stdio（本地命令拉起，最常见——npx / uvx 包）：

```bash
lumi mcp add chrome npx -y @browsermcp/mcp
```

**选项要放在 name 之前**——name 之后的 `-y` 之类都算给 server 命令的参数。
需要密钥的用 `--env`：

```bash
lumi mcp add -e NOTION_TOKEN=ntn_xxx notion npx -y @notionhq/notion-mcp-server
```

远程（HTTP / SSE，按 URL 自动识别；SSE 加 `-t sse`，鉴权头用 `--header`）：

```bash
lumi mcp add linear https://mcp.linear.app/mcp
lumi mcp add -H "Authorization: Bearer xxx" some https://example.com/mcp
```

用户甩来一段 README 里的 `mcpServers` JSON 片段时，取里面单个 server 的对象
原样交给 add-json（不用自己翻译成命令行）：

```bash
lumi mcp add-json weather '{"command": "uvx", "args": ["weather-mcp"]}'
```

## 加完必须验证

```bash
lumi mcp test <name>
```

连接成功会列出该 server 的工具清单——把工具名念给用户听，确认是想要的东西。
失败时常见原因：

- **命令找不到**（spawn npx/uvx ENOENT）：node 或 uv 没装——回 `env.md`
  的流程 `env install`，装完重试。
- **连接超时**：npx/uvx 首次要拉包，`lumi mcp test` 再试一次；还超时多半是网络/代理，
  处理思路同 `env.md` 的「出问题时」。远程 URL 超时则先 curl 探一下可达性。
- **401 / 403**：密钥错了或没给——找用户要对的，重新 add 覆盖即可（同名直接覆盖）。

密钥类信息让用户直接粘贴给你即可，配置文件权限为 0600。但项目层
`.lumi/mcp_server.json` 可能被提交进 git：含密钥时优先 `--scope global` 写全局层，
或提醒用户把该文件加入 `.gitignore`。Lumi 不展开配置里的 `${VAR}`，不要这样写。

## 生效与管理

- 配置写入后，运行中的会话下一条消息自动感知变化并在后台连接，不用重启 Lumi、
  也不用重开会话；连上后工具随之出现。
- 但**首次连接失败**（如 npx 冷启动超时）后，同一份配置不会自动重连——
  `lumi mcp test` 通过也不会（它是独立的临时连接），同配置重新 add 也不会。
  此时让用户在桌面端「MCP 面板」对该 server 点一次保存或连接测试（测试通过），
  这会强制重连，再下一条消息就能用。
- server 进程中途崩溃则无需处理：调用失败后下一条消息自动重连。
- `lumi mcp list` 分层查看、`lumi mcp get <name>` 看单个配置、
  `lumi mcp remove <name>` 删除（两层同名时会提示加 `--scope`）。
- 桌面端「MCP 面板」与这套命令读写同一份文件，两边改动互通。

## 不知道怎么配的时候

用户只说「接一下 xx」而你不知道那个 MCP server 怎么启动：先联网搜
「xx MCP server」找官方 README 的 `mcpServers` 配置片段，照抄成 add / add-json；
搜不到就问用户要文档链接。**不要凭记忆猜包名**——猜错的包一样能装上、
test 也可能通，但工具是别人家的。
