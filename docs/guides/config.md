# 配置文件与生效规则

桌面端优先通过设置面板管理模型、渠道、MCP 和环境。手工配置时先区分以下文件；`config.json` 不是所有配置的合集。

| 文件 | 内容 | 生效范围 |
|---|---|---|
| `<数据根>/config.json` | 图执行、token、视觉辅助模型、环境变量等启动配置 | 当前后端进程 |
| `<数据根>/lumi.json` | 模型供应商 `providers`、运行设置 `settings`、渠道 `channels`、项目 `projects` | 当前机器 |
| `<项目>/.lumi/` | 技能、子代理、提示词、权限、hooks、MCP；`agents.json` 控制原生子代理入口 | 对应项目 |
| `<项目>/AGENTS.md` | 项目说明，模型按需读取 | 对应目录及子目录 |

数据根默认 `~/.lumi`，可在进程启动前通过 `LUMI_CONFIG_DIR` 整体改道。本机和 SSH 远端各有自己的数据根与配置，远端设置不会写到本机。

## config.json 的选择

所有字段均可选，未配置时使用默认值；未知字段会被静默忽略。某一顶层段校验失败（如类型写错）只让该段回默认并记 warning，其余段照常生效；JSON 语法错误则整份回退默认。读哪份 config.json 取决于入口：

- **`lumi serve`（桌面端 / 服务器）**：只读用户级 `~/.lumi/config.json`（设了 `LUMI_CONFIG_DIR` 则读那里）；项目资源通过 `<项目>/.lumi/prompts/`、`agents/`、`skills/` 单独覆盖。
- **命令行 `lumi -p`**：优先使用 `LUMI_CONFIG_DIR`；未设置时选择当前目录中已存在的 `.lumi/`，其次是已存在的 `~/.lumi/`，都不存在则使用当前目录的 `.lumi/` 路径。**不向父目录查找**，也不合并多份 `config.json`。

发现依据是目录是否存在：当前 `.lumi/` 即使只有技能、没有 `config.json`，也会被选为进程配置目录，此时启动参数使用默认值。需要固定用户级启动配置时，显式设置 `LUMI_CONFIG_DIR`。资源覆盖链与启动配置发现是两件事，详见 [资源分层](styles.md)。

## 修改何时生效

| 配置 | 生效时机 |
|---|---|
| `config.json`、`LUMI_CONFIG_DIR`、checkpoint 密钥 | 重启后端；`lumi -p` 每次启动重新读取 |
| 供应商连接、默认模型、默认档位 | 设置中保存后，下次模型调用/轮次使用；已有会话的模型固化规则见 [模型架构](../architecture/model-switching.md) |
| `settings.unattended_tool_mode` | 后续工具权限判断使用；显式 auto / privileged 不被覆盖 |
| 渠道配置 | 设置中保存即重连；CLI 写入由运行中的后端定期检测并应用 |
| 权限文件 | 每批工具调用开始时按文件变更重载 |
| 技能、角色 Markdown | 后续上下文注入检测文件变化；已有子代理保留创建时的角色快照 |
| SOUL / hooks | 创建会话或重新绑定项目时加载，修改后宜新建会话；hooks 不逐轮读取文件 |
| MCP | 连接与重试规则见 [MCP 指南](mcp.md) |

## lumi.json 常用分区

模型配置保存在 `providers`，而非 `config.json`：一个 profile 包含连接信息和模型列表，`active` 是新会话默认，`classifier` / `titler` 是审批与标题用途模型。支持 `openai`（Chat）、`responses`、`anthropic`、`gemini` 四种显式接口；旧 profile 未指定接口时保留原自动判定。

```json
{
  "providers": {
    "profiles": [{
      "id": "example",
      "name": "OpenAI",
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-xxx",
      "protocol": "openai",
      "models": ["gpt-4o"]
    }],
    "active": {"provider": "example", "model": "gpt-4o"}
  },
  "settings": {
    "unattended_tool_mode": "auto",
    "checkpoint_dir": ""
  }
}
```

profile 还可按模型保存 `effort`、`context`、`max_tokens`、`catalog` 覆盖；建议在设置中编辑以获得能力校验。`checkpoint_dir` 留空使用数据根下的 `checkpoints/`，手工修改后需重启后端。渠道配置见 [飞书指南](feishu.md)，数据迁移与加密见 [备份恢复](backup.md)。

下面各节仅描述 **config.json** 的顶层字段。

---

## env — 环境变量注入

启动时将键值对注入 `os.environ`，优先级高于系统环境变量。适合统一管理 API Key、模型名称等配置。

```json
{
  "env": {
    "LLM_MODEL_NAME": "qwen3-max",
    "OPENAI_API_KEY": "sk-xxx",
    "OPENAI_API_BASE": "https://api.example.com/v1",
    "ANTHROPIC_API_KEY": "sk-xxx",
    "ANTHROPIC_API_URL": "https://api.example.com"
  }
}
```

---

## agents — Agent 配置

```json
{
  "agents": {
    "max_tokens": 8192,
    "recursion_limit": 5000,
    "max_delegation_depth": 3,
    "checkpoint": "sqlite",
    "postgres_uri": ""
  }
}
```

字段说明：`max_tokens` 为模型单次输出 token 数的**兜底**值（优先用设置→模型里按模型配的覆盖值，其次 models.dev 探测到的该模型输出上限，两者都没有才用它）；`recursion_limit` 为 Agent 最大执行轮次；`max_delegation_depth` 为子 Agent 委派的最大嵌套层数（主 Agent 为第 0 层，到上限的子 Agent 不再能继续委派，0 = 禁止委派）；`checkpoint` 为检查点存储模式（`sqlite` | `postgres`）；`postgres_uri` 为 PostgreSQL 连接 URI（仅 `checkpoint=postgres` 时需要）。

### agents.checkpoint — 检查点持久化

| 值 | 说明 | 适用场景 |
|---|---|---|
| `sqlite` | SQLite 文件持久化（默认），跨重启保留 | 单机部署、需要会话恢复 |
| `postgres` | PostgreSQL 持久化 | 需要独立数据库的部署；不会同步 lumi.json、子代理索引等本地文件，也不提供跨进程会话锁 |

## vision — 视觉辅助模型

主模型不具备视觉能力时，配一个视觉辅助模型；配置后模型多出一个 `vision(file_path, question)`
工具，可带具体问题识别图片 / PDF（支持本地路径与 http(s) URL）。顶层配置，重启生效。

```json
{
  "vision": {
    "model": "qwen-vl-max",
    "base_url": "",
    "api_key": ""
  }
}
```

字段说明：`model` 为视觉辅助模型名（空 = 不启用 vision 工具）；`base_url` / `api_key` 留空则复用 `providers` 分区里含该模型的 profile 连接。

---

## token — Token 处理配置

```json
{
  "token": {
    "once_tool_ratio": 0.1,
    "round_tool_ratio": 0.3,
    "context_length": 200000,
    "compact_threshold": 0.7,
    "compact_ptl_retry_max": 3,
    "compact_ptl_retry_drop_ratio": 0.3,
    "compact_failure_circuit_threshold": 3,
    "compact_circuit_reset_seconds": 600
  }
}
```

字段说明：
- `once_tool_ratio` / `round_tool_ratio`：单次工具结果 / 单轮全部工具结果的最大占比（相对 `context_length`，按字节衡量）；超出的结果被截断或卸载到文件。
- `context_length`：上下文窗口的兜底值。工具结果上限恒以它为基准；压缩窗口取用户按模型设置的覆盖值，其次 models.dev 探测值，皆无才使用此值。
- `compact_threshold`：上下文用量达到模型窗口的这个比例时自动压缩历史。
- `compact_ptl_retry_max` / `compact_ptl_retry_drop_ratio`：压缩请求本身超长时，最多截头重试几次、每次丢掉多少比例的最早轮次。
- `compact_failure_circuit_threshold` / `compact_circuit_reset_seconds`：同一会话压缩连续失败这么多次后暂停压缩，过多少秒再试。

---

## tool_args — 工具参数映射

```json
{
  "tool_args": {
    "extra_match": ["knowledge_retrieval", "qs_retrieval"]
  }
}
```

---

## llm_params — LLM 参数配置

```json
{
  "llm_params": {
    "openai": {
      "temperature": 0.7
    },
    "anthropic": {
      "temperature": 0.7
    },
    "gemini": {}
  }
}
```

Responses 使用 openai 分区；Gemini 原生接口使用 gemini 分区。最终参数优先级为 config.json 默认值 < 思考档位参数 < 调用方显式参数。

---

## auto_dream — 后台记忆整理

```json
{
  "auto_dream": {
    "enabled": false,
    "min_hours": 24,
    "min_sessions": 3
  }
}
```

会话结束时按门控在后台把近期会话的零散记忆综合成连贯记忆（仅桌面端，按项目隔离，默认关闭）：距上次整理至少 `min_hours` 小时、且期间至少有 `min_sessions` 个其它会话活跃过才触发。

---

## config.json 示例

```json
{
  "env": {
    "LLM_MODEL_NAME": "qwen3-max",
    "OPENAI_API_KEY": "sk-xxx",
    "OPENAI_API_BASE": "https://api.example.com/v1"
  },
  "agents": {
    "checkpoint": "sqlite",
    "max_tokens": 8192,
    "recursion_limit": 5000
  },
  "token": {
    "context_length": 200000,
    "compact_threshold": 0.7
  },
  "llm_params": {
    "anthropic": {
      "temperature": 0.7
    }
  }
}
```
