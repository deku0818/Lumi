# config.json 配置说明

所有字段均可选，未配置时使用默认值。读哪份 config.json 取决于入口：

- **`lumi serve`（桌面端 / 服务器）**：只读用户级 `~/.lumi/config.json`（设了 `LUMI_CONFIG_DIR` 则读那里）；项目 `.lumi/config.json` 里只有 `style` 生效（按会话所属项目）。
- **命令行 `lumi` / `lumi -p`**：按当前目录向上发现，`./.lumi/config.json` 存在则用它，否则用户级。

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
    "max_tokens": 8192,
    "recursion_limit": 5000,
    "max_delegation_depth": 3,
    "checkpoint": "sqlite",
    "postgres_uri": ""
  }
}
```

字段说明：`max_tokens` 为模型单次输出 token 数的**兜底**值（优先用设置→模型里按模型配的覆盖值，其次 models.dev 探测到的该模型输出上限，两者都没有才用它）；`recursion_limit` 为 Agent 最大执行轮次；`max_delegation_depth` 为子 Agent 委派的最大嵌套层数（主 Agent 为第 0 层，到上限的子 Agent 不再能继续委派，0 = 禁止委派）；`checkpoint` 为检查点存储模式（`sqlite` | `memory` | `postgres`）；`postgres_uri` 为 PostgreSQL 连接 URI（仅 `checkpoint=postgres` 时需要）。

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
    "round_tool_ratio": 0.3,
    "context_length": 200000,
    "summary_threshold": 0.7,
    "summary_ptl_retry_max": 3,
    "summary_ptl_retry_drop_ratio": 0.3,
    "summary_failure_circuit_threshold": 3,
    "summary_circuit_reset_seconds": 600
  }
}
```

字段说明：
- `once_tool_ratio` / `round_tool_ratio`：单次工具结果 / 单轮全部工具结果的最大占比（相对 `context_length`，按字节衡量）；超出的结果被截断或卸载到文件。
- `context_length`：上下文窗口的兜底值。工具结果上限恒以它为基准；压缩阈值优先用 models.dev 里该模型的真实窗口，查不到才用它。
- `summary_threshold`：上下文用量达到模型窗口的这个比例时自动压缩历史。
- `summary_ptl_retry_max` / `summary_ptl_retry_drop_ratio`：压缩请求本身超长时，最多截头重试几次、每次丢掉多少比例的最早轮次。
- `summary_failure_circuit_threshold` / `summary_circuit_reset_seconds`：同一会话压缩连续失败这么多次后暂停压缩，过多少秒再试。

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
    }
  }
}
```

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
