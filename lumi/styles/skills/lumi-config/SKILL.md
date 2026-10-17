---
name: lumi-config
description: Lumi 的配置总入口：本地执行依赖（uv、Node.js、ripgrep）的体检与安装、飞书机器人全程接入（凭证、配置、权限体检、妙记）、MCP 服务器接入管理。用户说「初始化 Lumi」「配置环境」「装依赖」「第一次用要准备什么」「接飞书」「配置飞书机器人」「开妙记纪要」「接/装/配置 MCP」「连一下 xx 的 MCP」，或任务因 uv/node/npm/python 命令找不到而失败、装不上或找不到 lark-cli、飞书机器人不回消息、MCP 工具连不上时使用。
---

# Lumi 配置

Lumi 的配置活都从这里进。面向零基础用户——命令全程你来跑，能代劳的都代劳；
出了问题也是你来修，别把人支到别处去点。

按要做的事读对应篇（都在本技能目录的 `references/` 下），**先读再动手，别凭记忆走**：

| 要做什么 | 读哪篇 |
|---|---|
| 执行依赖：uv / node / rg 的体检与安装，lark-cli 装不上 | `references/env.md` |
| 接飞书机器人 / 妙记：凭证 → 配置 → 权限体检 → 启用 | `references/feishu.md` |
| 接 MCP 服务器：add → test → 生效，连不上排查 | `references/mcp.md` |

通用约定（三篇共用）：

- 命令一律写 `"${LUMI_BIN:-lumi}" …`；Windows 的 shell 是 cmd，没有 `${VAR:-默认}`
  语法，用 `"%LUMI_BIN%" …`。
- 动手前先体检；装东西前先用一句人话说清要装什么、征得同意；装完复检验证再交差。
- 飞书接入（lark-cli 是 npm 包）和 stdio 类 MCP（多经 npx/uvx 拉起）都依赖
  执行依赖——node / uv 缺失就先回 `env.md` 装齐。
