# config.yaml 配置说明

Lumi 的项目级配置文件位于 `.lumi/config.yaml`，启动时自动加载。所有字段均可选，未配置时使用默认值。

---

## style — 提示词风格

```yaml
style: code    # 默认值: "default"
```

指定系统提示词和子 Agent 配置的风格。详见 [styles.md](styles.md)。

CLI 参数可覆盖：`lumi -s code`。优先级：CLI > config.yaml > 默认值。

---

## env — 环境变量注入

启动时将键值对注入 `os.environ`，优先级高于系统环境变量。适合统一管理 API Key、模型名称等配置。

```yaml
env:
  LLM_MODEL_NAME: qwen3-max
  OPENAI_API_KEY: sk-xxx
  OPENAI_API_BASE: https://api.example.com/v1
  ANTHROPIC_API_KEY: sk-xxx
  ANTHROPIC_API_URL: https://api.example.com
```

---

## agents — Agent 配置

```yaml
agents:
  tools: []                # 启用的工具白名单，空列表 = 全部启用
  disabled_tools: []       # 禁用的工具黑名单，优先级高于 tools
  max_tokens: 8192         # 模型输出最大 token 数
  recursion_limit: 5000    # Agent 最大执行轮次
  vision_mode: model       # 图片识别模式：model | tool
  checkpoint: memory       # 检查点存储模式：memory | sqlite | postgres
  postgres_uri: ""         # PostgreSQL 连接 URI（仅 checkpoint=postgres 时需要）
```

### checkpoint 检查点持久化

| 值 | 说明 | 适用场景 |
|---|---|---|
| `memory` | 内存存储（默认），进程退出后丢失 | 开发调试、临时使用 |
| `sqlite` | SQLite 文件持久化 | 单机部署、需要会话恢复（[`/resume`](slash-commands.md)） |
| `postgres` | PostgreSQL 持久化 | 多实例部署、生产环境 |

---

## token — Token 处理配置

```yaml
token:
  once_tool_ratio: 0.1             # 单次工具调用返回结果最大 token 占比
  trim_messages_ratio: 0.96        # 消息修剪器最大 token 占比
  context_length: 200000           # 模型上下文窗口最大 token 数
  summary_threshold: 0.7           # 触发总结的阈值比例
```

---

## tool_args — 工具参数映射

```yaml
tool_args:
  extra_match:
    - knowledge_retrieval
    - qs_retrieval
```

---

## tool_offload — 工具结果卸载

将大量返回结果卸载到文件系统，避免占用过多上下文窗口：

```yaml
tool_offload:
  enabled: false
  token_threshold: 2000
  tools: []
```

---

## llm_params — LLM 参数配置

```yaml
llm_params:
  openai:
    temperature: 0.7
  anthropic:
    temperature: 0.7
```

---

## skill_execution — 技能命令执行

```yaml
skill_execution:
  enabled: true
  command_timeout: 10.0      # 超时时间(秒)
  max_output_bytes: 10000    # 输出最大字节数
```

---

## ptc — Programmatic Tool Calling

将 MCP 工具转换为可直接调用的 Python 函数：

```yaml
ptc:
  enabled: true
  tools: []              # 启用 PTC 的工具列表，空 = 所有 MCP 工具
  disabled_tools: []     # 排除的工具列表
```

---

## filesystem — 文件系统工具配置

```yaml
filesystem:
  grep_max_file_size_mb: 10   # grep 搜索时跳过的最大文件大小(MB)
```

---

## 完整示例

```yaml
style: code

env:
  LLM_MODEL_NAME: qwen3-max
  OPENAI_API_KEY: sk-xxx
  OPENAI_API_BASE: https://api.example.com/v1

agents:
  checkpoint: sqlite
  max_tokens: 8192
  recursion_limit: 5000

token:
  context_length: 200000
  summary_threshold: 0.7

llm_params:
  anthropic:
    temperature: 0.7
```
