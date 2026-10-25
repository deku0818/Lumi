# Lumi 文档导航

日常使用从安装与设置开始；开发者先读运行时总览，再进入对应专题。`guides/` 说明怎么用，`architecture/` 说明当前实现与边界，`archive/` 保留被替代的方案和实验。

## 使用指南

| 任务 | 文档 |
|---|---|
| 安装桌面端、首次配置与排障 | [安装与首次使用](guides/install.md) |
| 选择配置文件、理解优先级与生效时机 | [配置](guides/config.md) |
| 命令行执行、运维和容器入口 | [CLI](guides/cli.md) |
| 外部程序调用 Lumi | [Gateway 与文件 API](guides/api.md) |
| 添加、测试和管理 MCP | [MCP](guides/mcp.md) |
| SSH 远端 | [部署](guides/deploy.md) |
| 机器人、用户授权和妙记 | [飞书](guides/feishu.md) |
| 工具审批和权限规则 | [权限](guides/permissions.md) |
| 持久子代理与原生 CLI | [子 Agent](guides/agents.md) |
| 定时执行 | [定时任务](guides/cron.md) |
| 项目提示词、技能与资源覆盖 | [资源分层](guides/styles.md)、[斜杠命令](guides/slash-commands.md) |
| 记忆和项目说明 | [记忆](guides/memory.md) |
| 历史加载、编辑与重新生成 | [聊天历史](guides/checkpoint.md) |
| Shell、搜索与文件读取 | [Bash](guides/bash.md)、[搜索](guides/grep-glob.md)、[多模态读取](guides/read-multimodal.md) |
| 数据迁移、备份和 checkpoint 加密 | [备份恢复](guides/backup.md) |

## 当前架构

| 主题 | 文档 |
|---|---|
| 图、状态、运行入口与停机 | [运行时总览](architecture/runtime.md) |
| 权限与等待用户 | [权限](architecture/permissions.md)、[在途审批](architecture/approval-inflight.md) |
| 桌面、SSH、会话与文件预览 | [桌面与网关](architecture/desktop.md) |
| 独立上下文与编排 | [子代理](architecture/agents.md)、[Workflow](architecture/workflow.md) |
| 模型与思考 | [会话模型](architecture/model-switching.md)、[思考管理](architecture/thinking.md) |
| 历史与上下文 | [压缩](architecture/compact.md)、[记忆](architecture/memory.md) |
| 定时与渠道 | [定时任务](architecture/cron.md)、[飞书](architecture/feishu.md)、[妙记](architecture/feishu-minutes.md) |
| 扩展与安装 | [Hooks](architecture/hooks.md)、[资源加载](architecture/styles.md)、[工具箱](architecture/toolbox.md) |

## 契约与维护

- [Gateway 协议](../protocol/README.md)：事件与方法契约，数据源为 `protocol/events.json`。
- [Prompt 缓存](reference/prompt-caching.md)：Lumi 请求构造与缓存约束。
- [共享开发指令](../CLAUDE.md)：开发约定、UI 协作与验证入口。
- [版本变更](../CHANGELOG.md)：按版本保存历史，不作为当前 API 参考。
- [历史归档](archive/README.md)：重构基线、未采用方案与早期实验。

修改行为时同步对应指南和架构专题；入口文档以链接导航，避免重复复制参数、权限矩阵或模块细节。协议名称以 JSON 契约为准，配置字段以对应 Pydantic / dataclass 定义为准。
