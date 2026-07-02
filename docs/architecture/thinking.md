# 思考管理（Thinking / Effort）设计

> 状态：方案定稿（2026-06-11），待实施。取代 `docs/1.md`（外部参考资料，保留备查）。
> 本文档中所有数据源结论均经实测验证。

## 产品原则（以终为始）

1. **用户永远选不到会报错的档位**——UI 只展示当前模型真正支持的思考控制。
2. **没有思考能力的模型，思考配置整个不渲染**（不是置灰）。
3. **`Auto` 恒为默认与安全底座**：不下发任何思考参数，对任意模型零风险。
4. 档位按模型记忆：给 Claude 设 max、给 MiMo 设 off，切换互不干扰。
5. 档位值用英文原样展示（low/medium/high/max/xhigh…），不翻译。

## 能力数据源：models.dev

`https://models.dev/api.json`（MIT，社区维护，141 provider / 5000+ 模型）。
单文件缓存到 `~/.lumi/`（TTL 24h，离线沿用旧缓存），模型名匹配复用
`model_info.py` 的策略（精确 → 尾部 → fuzzy）。

每个模型的 `reasoning` / `reasoning_options` 直接决定 UI 与下发。
**Auto 的语义按形态不同**（核心修正：Auto = "让模型自己决定"，而非一律不传）：

| 形态 | UI 选项 | Auto 含义 | 关闭方式 |
|---|---|---|---|
| anthropic effort 型（Claude） | Auto / 原生档位 / Off | **adaptive**（开思考，深度自适应——这正是 Auto 的本义） | Off = 不传 thinking（API 默认不思考） |
| openai effort 型（GPT 系） | Auto / 原生档位（含 none 若有） | 不传 = 模型默认（推理模型默认即思考，本就"自动"） | 原生 none |
| toggle 型（MiMo/Kimi/GLM） | **仅 On / Off**（无 Auto——开关模型只有两种行为） | （未设置=不传参数；此类模型默认开思考，UI 按 On 展示） | Off |
| `reasoning: false` / 未匹配 / 常开型 | 不渲染控制 | 不传参数 | — |

`budget_tokens` 类型**忽略**（被 adaptive+effort 取代的旧范式，不进 Lumi）。

由此**不存在档位映射表**：models.dev 的 values 就是各模型原生值
（Claude 的 `max`、GPT 的 `xhigh` 直接透传），Cherry Studio 式的
人工兼容矩阵和 OpenRouter 数据源均不需要。

> 为什么不是 OpenRouter：其 `supported_parameters` 只有二值 `reasoning`
> 标志，无档位枚举（全量扫描验证）；`reasoning_effort` 标志严重漏报
> （338 个模型仅 4 个带标，Claude/GPT/MiMo 均缺失）。
> `model_info.py` 的 context_length 链路也一并迁到 models.dev
> （`limit.context`），OpenRouter 退役。

## 参数写法（唯一仍需代码维护的小表）

models.dev 描述能力，不描述直连端点的参数写法。写法按协议 + 控制类型：

| 控制类型 | anthropic 协议 | openai 协议 |
|---|---|---|
| effort 档位 | `thinking: {type: adaptive, display: summarized}` + `output_config: {effort: <值>}` | `reasoning_effort: <值>` + `use_responses_api=False` |
| toggle On/Off | （Anthropic 无 toggle 型） | `extra_body: {thinking: {type: enabled/disabled}}`（DeepSeek 系方言，MiMo 实测有效） |
| Auto | 不传任何参数 | 不传任何参数 |

思考开启时剔除 `temperature/top_p/top_k`（互斥）。遇到不吃 DeepSeek 系
toggle 写法的方言（如 Qwen 的 `enable_thinking`）再加一行特例——
失败模式安全：报错即时透传，用户切回 Auto 即恢复。

## 数据模型

```json
// lumi.json 的 providers 分区
{"profiles": [{
  "id": "…", "name": "MiMo", "base_url": "…", "api_key": "…",
  "models": ["mimo-v2.5-pro"],
  "effort": {"mimo-v2.5-pro": "off"}     // 按模型，只存非 auto
}]}
```

顶层全局 `effort` 字段废弃。存的值即原生值（`"high"` / `"xhigh"` /
`"on"` / `"off"`），校验规则：值 ∈ 该模型 models.dev options ∪ {auto,on,off}，
不合法（数据更新后失效）静默回退 auto。

## 注入链路（含已定的地基重构）

```
ResolvedModel(model, base_url, api_key, effort)   ← resolve() 一次读盘全返回
        │
create_llm(..., apply_effort=False)               ← 翻转默认：注入显式 opt-in
        │   仅 call_model 的 tool_call_chain 传 True；
        │   摘要 / 结构化提取 / test_provider / 内部链全部默认干净，
        │   llm_chain 中散落的 thinking=None 对冲逻辑整段删除。
        └─ apply_effort=True：查 models.dev 能力 → 按写法表生成参数
```

## 协议与 UI

- `list_providers` 为每个模型附带思考能力（由 models.dev 算出），
  **前端不再硬编码档位列表**，选项完全后端驱动。
- `set_effort(provider, model, level)`。
- 思考内容流式展示（`thinking.delta` 事件、`DialectChatOpenAI` 保留
  `reasoning_content`）与本设计正交，已实现，不变。

### Desktop 选择器（参考 Claude Desktop，demo 已确认：`.demos/lumi-effort-picker.html`）

- **Chip**（输入条右下角）：`<模型名> <档位> ▾`，Auto 时只显示模型名。
- **一级菜单仅三行**：当前模型 ✓（带供应商小字）/ `Effort ›`（右侧显当前值；
  toggle 型显示 `Thinking ›`；无思考模型该行不渲染）/ `More models ›`。
- **二级菜单**（两者互斥弹出）：
  - Effort：顶部一句说明 + `Auto（默认）` + models.dev 原生档位原样列出；
    toggle 型为 Auto / On / Off 三项。
  - More models：按供应商分组的模型列表，点选即切换。
- effort 与 toggle 并存的模型（如 deepseek-v4）：显示 effort 档位列表，
  并附加 Off 项（toggle 提供关闭能力）。
- TUI `/effort`：无参列出当前模型可用档位，带参校验后设置，同一份数据。

### models.dev 接入细节

- 缓存 `~/.lumi/cache/models_dev.json`（含 fetched_at，TTL 24h），
  `lumi serve` / TUI 启动后台刷新；无网络沿用旧缓存；无缓存时
  所有模型按「无思考控制」处理（安全降级，仅 Auto）。
- 匹配：全 provider 扁平化后按模型名 精确 → fuzzy；多 provider 同名时
  取 reasoning_options 最完整的条目。
- `model_info.py` 的 context_length 一并迁来（`limit.context`），OpenRouter 退役。

## Ultra：Lumi 合成顶档（思考 + 编排能力）

`ultra` 不是 models.dev 的原生档位，是 Lumi 自造的「能力档」，对标 Claude Code 的
ultracode。选中后做两件事：**原生思考拉到该模型最高档** + **解锁 workflow 多代理编排**。

- **档位枚举**：`allowed_levels()` 对有思考能力的模型（control ≠ none）在末尾统一追加
  `"ultra"`；none 型不渲染（无思考子菜单可挂）。
- **思考映射（唯一别名点）**：`effort_params(model, "ultra")` 委派给该模型最高原生档
  （`_native_max_level`：effort 型取 `values[-1]` 如 Claude→max / GPT→high；toggle→on）。
  下游协议分支无需感知 ultra。
- **编排解锁（缓存安全）**：workflow 工具**始终注册**，不随档位增删（增删工具会废 prompt
  缓存前缀）。Ultra 信号经**边沿触发 system-reminder** 传达——`bridge._drain_ultra_note()`
  与上次通知的档位做差，仅在 off↔ultra 切换那轮前置到当轮真实消息（非 meta 轮），开注入
  「已开启」、关注入「已关闭」，无变化不注入（reminder 进历史即长驻，多次抖动收敛为净差）。
  **不碰系统提示词**，故 toggle Ultra 只动最便宜的每轮消息尾、不废 system+tools 缓存前缀。
  workflow 工具描述里写死「仅 Ultra 或用户明确要求时使用」。详见 [workflow.md](workflow.md)。
- **UI**：Effort 子菜单底部分隔线 + 呼吸金光点 + 副标题（ModelPicker.tsx）；chip 上金字。
  demo `.demos/lumi-ultra-tier.html`。

## 不做

运行时能力探测、budget_tokens 预算制、ReasoningTrace 落库、
Cherry Studio 式人工兼容矩阵（AGPL，且 models.dev 已覆盖）、
OpenAI Responses API（钉死 Chat Completions）、静默降级（错误即时透传）。
