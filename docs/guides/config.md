# config.json 配置说明

Lumi 的项目级配置文件位于 `.lumi/config.json`，启动时自动加载。所有字段均可选，未配置时使用默认值。

---

## style — 提示词风格

```json
{
  "style": "code"
}
```

默认值为 `"default"`。指定系统提示词和子 Agent 配置的风格，详见 [styles.md](styles.md)。

CLI 参数可覆盖：`lumi -s code`。优先级：CLI > config.json > 默认值。

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
    "tools": [],
    "disabled_tools": [],
    "max_tokens": 8192,
    "recursion_limit": 5000,
    "checkpoint": "sqlite",
    "postgres_uri": ""
  }
}
```

字段说明：`tools` 为启用的工具白名单（空列表 = 全部启用）；`disabled_tools` 为禁用的工具黑名单（优先级高于 `tools`）；`max_tokens` 为模型输出最大 token 数；`recursion_limit` 为 Agent 最大执行轮次；`checkpoint` 为检查点存储模式（`sqlite` | `memory` | `postgres`）；`postgres_uri` 为 PostgreSQL 连接 URI（仅 `checkpoint=postgres` 时需要）。

### vision — 视觉辅助模型

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

### checkpoint 检查点持久化

| 值 | 说明 | 适用场景 |
|---|---|---|
| `sqlite` | SQLite 文件持久化（默认），跨重启保留 | 单机部署、需要会话恢复（[`/resume`](slash-commands.md)） |
| `memory` | 内存存储，进程退出后丢失，且同进程内连接间互相隔离 | 开发调试、临时使用 |
| `postgres` | PostgreSQL 持久化 | 多实例部署、生产环境 |

---

## token — Token 处理配置

```json
{
  "token": {
    "once_tool_ratio": 0.1,
    "trim_messages_ratio": 0.96,
    "context_length": 200000,
    "summary_threshold": 0.7
  }
}
```

字段说明：`once_tool_ratio` 为单次工具调用返回结果最大 token 占比；`trim_messages_ratio` 为消息修剪器最大 token 占比；`context_length` 为模型上下文窗口最大 token 数；`summary_threshold` 为触发总结的阈值比例。

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

## tool_offload — 工具结果卸载

将大量返回结果卸载到文件系统，避免占用过多上下文窗口：

```json
{
  "tool_offload": {
    "enabled": false,
    "token_threshold": 2000,
    "tools": []
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
    }
  }
}
```

---

## skill_execution — 技能命令执行

```json
{
  "skill_execution": {
    "enabled": true,
    "command_timeout": 10.0,
    "max_output_bytes": 10000
  }
}
```

字段说明：`command_timeout` 为超时时间（秒）；`max_output_bytes` 为输出最大字节数。

---

## ptc — Programmatic Tool Calling

将 MCP 工具转换为可直接调用的 Python 函数：

```json
{
  "ptc": {
    "enabled": true,
    "tools": [],
    "disabled_tools": []
  }
}
```

字段说明：`tools` 为启用 PTC 的工具列表（空 = 所有 MCP 工具）；`disabled_tools` 为排除的工具列表。

---

## filesystem — 文件系统工具配置

```json
{
  "filesystem": {
    "grep_max_file_size_mb": 10
  }
}
```

`grep_max_file_size_mb` 为 grep 搜索时跳过的最大文件大小（MB）。

---

## 完整示例

```json
{
  "style": "code",
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
    "summary_threshold": 0.7
  },
  "llm_params": {
    "anthropic": {
      "temperature": 0.7
    }
  }
}
```
