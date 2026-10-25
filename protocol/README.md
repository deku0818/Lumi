# protocol/

Lumi 前端与后端之间 Gateway JSON-RPC 协议的**语言中立单一事实来源**。

业务帧用于本地 WebSocket 和原生 SSH 的逻辑会话通道。SSH 长度前缀与管理帧见 `lumi/remote/protocol.py`、`desktop/electron/remote-wire.cjs`。

## 为什么独立

协议不应绑定任一端：后端（Python，协议的**唯一生产者**）和前端（TS，协议的**消费者**）都引用这同一份契约。

## 文件

- **`events.json`** — 唯一事实来源。列出所有 wire 事件（server→client）、RPC 方法（client→server）及其 payload/params 字段。

## 两端如何消费

| 端 | 角色 | 怎么用 |
|---|---|---|
| TS 前端 | 消费者 | `import events from '@protocol/events.json'`，`keyof typeof events.events` derive 事件名联合类型（见 `desktop/src/types.ts`）。零 codegen。 |
| Python 后端 | 生产者 | `lumi/gateway/protocol.py`：`EventKind` 值即 wire 名，只做 payload 重组；`tests/gateway/test_protocol_contract.py` 读本文件断言事件名/方法名集合一致。漂移即测试失败。 |

## 改协议的唯一流程

先改 `events.json`，再同步 Python 生产者、RPC handler 和 TS 消费者。运行 `uv run pytest tests/gateway/test_protocol_contract.py` 检查名称集合，并在 `desktop/` 运行 `npx tsc --noEmit` 检查类型。payload / params 的语义还需相关端点或行为测试验证；名称契约与编译通过不等于所有字段语义均已覆盖。

外部调用示例见 [API 指南](../docs/guides/api.md)。
