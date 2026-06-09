# protocol/

Lumi 前端与后端之间 WebSocket 协议的**语言中立单一事实来源**。

## 为什么独立

前端正在从 Python TUI 迁向 TS（desktop 已是 TS，未来 TUI 也可能 TS）。协议不应绑定任一端：
后端（Python，长期存在，协议的**唯一生产者**）和所有前端（TS，协议的**消费者**）都引用这同一份契约。

## 文件

- **`events.json`** — 唯一事实来源。列出所有 wire 事件（server→client）、RPC 方法（client→server）及其 payload/params 字段。

## 两端如何消费

| 端 | 角色 | 怎么用 |
|---|---|---|
| TS 前端 | 消费者 | `import events from '@protocol/events.json'`，`keyof typeof events.events` derive 事件名联合类型（见 `desktop/src/types.ts`）。零 codegen。 |
| Python 后端 | 生产者 | `lumi/server/protocol.py` 手写 BridgeEvent→wire 映射；`tests/server/test_protocol_contract.py` 读本文件断言事件名/方法名集合一致。漂移即测试失败。 |

## 改协议的唯一流程

改 `events.json` → 跑 `pytest tests/server/test_protocol_contract.py`（Python 端对齐）+ `tsc`（TS 端对齐）。两个都绿，协议就同步了。
