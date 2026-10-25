# 思考档位与模型适配

当前实现包括模型能力目录、按模型保存的默认档位、会话级覆盖和四种接口的思考回传。模型选择与固化规则见 [会话模型](model-switching.md)；这里维护能力、参数构造与历史发送的契约。

## 档位与 Auto

界面只展示后端 `manager.allowed_levels()` 返回的档位，值沿用模型原生英文，不做跨厂商档位翻译；用户未设会话档位时沿用该模型 profile 中的默认值。

| 能力形态 | 可选项（另有 Ultra 时见下文） | Auto / 未设置的语义 |
|---|---|---|
| Anthropic effort 型 | Auto、原生档位、Off | Auto 开启 adaptive thinking，由模型决定深度；Off 不注入 thinking |
| OpenAI effort 型 | Auto、原生档位；模型支持时附 Off | Auto 不传档位参数，使用端点默认 |
| toggle 型 | On、Off | 未设置时不传参数，界面按 On 展示；不单列 Auto |
| 无控制、未匹配或目录不可用 | 仅内部 auto，界面隐藏控制 | 不传档位参数 |

Auto 的具体写法由所选协议与兼容规则决定，不能将它统称为“总是不传参数”。无效或失效的非 auto 值不下发。内部链默认 `apply_effort=False`，不注入主对话档位；需强制工具输出时可用 `force_no_thinking`，关闭能力与工具选择限制均由适配层判断。

## 能力目录与解析

能力来自 models.dev，由 `lumi/models/catalog.py` 缓存到数据根 `cache/models_dev.json`（TTL 24 小时）。后端启动可后台刷新，离线沿用缓存；无缓存、无匹配时不展示思考控制。目录同时提供上下文与输出上限，用户显式覆盖优先。

按模型名手动指定 → 精确 → fuzzy 匹配。多 provider 同名时选择 reasoning_options 最完整的单个条目，不拼接各端点的档位；`toggle_anywhere` 另汇总“是否有可关闭思考的通道”，仅用于关思考判断。运行时不逐模型发请求探测能力。

`provider_store.resolve(model_name, provider)` 同时解析模型、连接、默认档位与限额。provider 非空时精确选择 profile；失效则按模型名回查，active 优先。会话 provider 来自 session meta，用途模型来自 classifier / titler 指针，渠道配置不保存模型字段。

## 配置与调用链

```json
{
  "providers": {
    "profiles": [{
      "id": "example", "name": "Example", "base_url": "https://api.example.com/v1",
      "api_key": "sk-xxx", "protocol": "openai", "models": ["example-model"],
      "effort": {"example-model": "high"}
    }]
  }
}
```

`effort` 存在数据根 `lumi.json` 的 profile 中；只设置当前模型实际支持的值。顶层全局 effort 已废弃。主对话 `call_model → tool_call_chain → create_llm(apply_effort=True)` 使用会话覆盖或 profile 默认，`effort_params` 是档位到协议参数的映射入口。

| 协议 / 兼容规则 | 非自动档位的参数 |
|---|---|
| Anthropic effort | adaptive thinking + output_config.effort；Off 不注入 |
| OpenAI Chat effort | reasoning_effort |
| Responses | reasoning.effort |
| Gemini 原生 | reasoning_effort；On/Off 使用 thinking_budget |
| OpenRouter | reasoning，Chat 接口通过 extra_body 发送 |
| Qwen toggle | extra_body.enable_thinking |
| DeepSeek / MiMo 等 toggle | extra_body.thinking.type |

具体方言由 `manager.py` 与 `adapters.py` 实现，接口类型优先使用 profile 的显式配置。思考参数与采样参数的兼容处理、强制 tool_choice 降级均在模型层，节点不重复维护兼容表。

不能关闭思考且拒绝强制 tool_choice 的模型，结构化链降级为绑定 schema 的 auto 工具选择；未得到结构化结果会抛错。审批分类器据此拒绝对应调用，其他内部用途由调用方处理错误。

## 桌面与飞书入口

- ModelPicker 按后端能力展示 Effort / Thinking 子菜单，无能力时隐藏；toggle 型提供 On / Off，有思考控制时另附 Ultra。
- 桌面 Effort 修改 profile 的按模型默认值；飞书 `/effort` 修改本会话覆盖，换模型清除覆盖。
- `list_providers` 返回模型能力，`set_effort` 保存默认档位；流式思考走 `thinking.delta`，历史回传规则见文末。

## 代理别名的手动映射

代理 / 网关的模型别名可能没有同名目录条目，fuzzy 匹配错误会同时影响上下文窗口、输出上限与思考档位。可在供应商设置中手动指定目录条目。

- **覆盖表**：`ProviderProfile.catalog`（model → 目录条目 id），与 `context` / `max_tokens`
  并列存在 profile 里、随模型增删一并清理；空串 = 恢复自动匹配。
- **生效是全局的**：`provider_store` 读盘 / 写盘时把各 profile 的覆盖拍平发布给
  `catalog.set_aliases`。目录能力仍按模型名查询；接口协议和兼容规则另按 profile 解析。
  「`plan-glm-5.2` 到底是哪个模型」是名字的属性，不是连接的属性。代价：同名别名在两个
  profile 指向不同条目时后写者覆盖前者。
- **匹配来源随结果一起上报**：`catalog.match()` 返回 `Match(entry, kind)`，
  `kind ∈ manual | exact | fuzzy | none | stale`。`stale` = 指定的条目已不在目录里
  （数据更新后消失），`entry` 仍是回落的自动匹配结果——运行时照常工作，但「你的指定
  没生效」要报出去而不是静默。`lookup()` 是丢掉 kind 的薄包装，只要条目的调用方继续用它。
- **界面**：设置 → 模型 → 编辑供应商，每个模型名右侧常驻一枚徽标显示解析结果与来源。
  只有 `fuzzy` / `stale` 报警（金点 / 红点呼吸），其余安静——每个代理别名都亮一下的话，
  警示很快就没人看了。点开是 `search_catalog` 的搜索式选择器（整串无果时逐段剥前缀重试，
  否则最需要帮忙的别名反而搜不出东西）。

## Ultra：Lumi 合成顶档（思考 + 编排能力）

`ultra` 不是 models.dev 的原生档位，是 Lumi 自造的「能力档」，对标 Claude Code 的
ultracode。选中后做两件事：**原生思考拉到该模型最高档** + **解锁 workflow 多代理编排**。

- **档位枚举**：`allowed_levels()` 对有思考能力的模型（control ≠ none）在末尾统一追加
  `"ultra"`；none 型不渲染（无思考子菜单可挂）。
- **思考映射（唯一别名点）**：`effort_params(model, "ultra")` 委派给该模型最高原生档
  （`_native_max_level`：effort 型取 `values[-1]` 如 Claude→max / GPT→high；toggle→on）。
  下游协议分支无需感知 ultra。
- **编排解锁（缓存安全）**：允许委派的主会话中，workflow 工具不随档位增删；委派深度门控仍可移除该工具（增删工具会废 prompt
  缓存前缀）。Ultra 信号经**边沿触发 system-reminder** 传达——`FolderManager.ultra_note()`
  与模型已知的档位做差（取自历史里最近一条真实用户消息的 `lumi_reminded` marker，rewind /
  压缩 / 重连后自动重发），仅在 off↔ultra 切换那轮前置到当轮真实消息（非 meta 轮），开注入
  「已开启」、关注入「已关闭」，无变化不注入（reminder 进历史即长驻，多次抖动收敛为净差）。
  **不碰系统提示词**，故 toggle Ultra 只动最便宜的每轮消息尾、不废 system+tools 缓存前缀。
  workflow 工具描述里写死「仅 Ultra 或用户明确要求时使用」。详见 [workflow.md](workflow.md)。
- **UI**：Effort 子菜单底部分隔线 + 呼吸金光点 + 副标题（ModelPicker.tsx）；chip 上金字。

## 供应商接口与思考回传

供应商配置保存 `protocol`（openai / responses / anthropic / gemini）、
`compatibility` 和界面预设 `preset`。选择预设后填写连接，只有自定义显示接口类型；
兼容规则位于默认折叠的高级设置。连接测试与实际模型调用共用这些参数。
旧 profile 的接口字段为空时保留原来的名称判定、显式 API 参数和 SDK 自动路由，
不批量改写用户配置。新 profile 的显式接口选择优先于旧参数。

`models/reasoning.py` 是历史发送投影的唯一入口，四种客户端共用：
- 完整回复标记来源 `(profile id, base_url, protocol, model)`，随 checkpoint 保存。
- 同来源原样回传 thinking、reasoning、签名及加密块；其它来源只在发送副本里剔除，
  正文与工具调用保留。过滤后为空的纯思考消息不发送；外来响应的服务端 ID 也去掉，
  防止 `previous_response_id` 自动载入被过滤的上下文。
- A→B→A 时原历史仍在，A 的思考可重新使用；来源不明的升级前消息不猜测归属。
- 原生 Anthropic / Responses / Gemini 的序列化由 LangChain 处理；OpenAI Chat
  兼容接口在 `models/adapters.py` 补齐思考字段的流式、非流式接收与回传。
  结构化 reasoning 只拼接相邻同源文本/摘要，加密块保持独立：OpenRouter 保留
  index，Kimi 的 summary/encrypted 块回传时去掉流式 index。Gemini 经 Chat
  兼容接口返回的工具签名单独保存，按工具 ID 回传，切模型时与思考一起过滤。
  DeepSeek / Kimi / MiMo 需要字段但历史没有思考时发送空格占位，绝不伪造丢失的思考。
- Qwen 已支持的型号开启 `preserve_thinking`，GLM 使用 `clear_thinking: false`，
  Kimi K2.6 / K2.7 使用 `keep: all`；OpenRouter 通过统一 `reasoning` 参数控制思考。
  Responses 请求加密 reasoning，默认关闭服务端存储；显式启用服务端续接时默认开启存储。

这里只保证本地历史和请求构造的契约，不代表所有代理端点都遵循厂商原生规则。
