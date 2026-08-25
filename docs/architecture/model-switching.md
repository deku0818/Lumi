# 模型：会话属性，不是全局状态

> 本文只讲**"哪个会话用哪个模型、哪个档位"如何被决定**。模型能力探测、档位的协议
> 写法、ultra 合成档见 [thinking.md](thinking.md)；两者的交界是 `provider_store.resolve()`。

## 一句话

**模型跟着会话走。** 你在某个会话里选的模型，就是这个会话此后一直用的模型——切走再
切回来还是它，别人改「新会话默认」也不动它。desktop 与飞书遵循同一条规则、同一份
代码（`lumi/sessions/session_model.py`），不各自一套。

这不是审美选择，是 prompt 缓存的要求：缓存按模型分桶，会话在用户背后换模型 = 整段
历史被新模型重读一遍，更慢更贵。所以模型必须钉在会话上，换它得是一次明确的决定
（desktop 因此弹确认框）。

## 一、两层，就两层

```
会话模型（session_meta 按 thread_id）      ← 用户在这个会话里定的
   ↓ 没有
新会话默认（providers.active）             ← 新建会话时用哪个
```

单一实现点 `session_model.resolve(thread_id) -> SessionModel(model, provider, effort, pinned)`。

| 存储 | 落盘位置 | 粒度 | 写入者 |
|---|---|---|---|
| 会话模型 | `~/.lumi/checkpoints/session_meta.json` → `{thread_id: {model, model_provider, effort}}` | 每会话一条 | desktop ModelPicker → `set_session_model`；IM `/model`、`/effort`；轮首 `pin()` |
| 新会话默认 | `~/.lumi/lumi.json` → `providers.active` | 每台后端机器一份 | desktop 设置→模型「新会话默认模型」→ `set_provider` |

与会话无关的两个正交维度（不参与上面的优先级）：

| 维度 | 落盘位置 | 粒度 |
|---|---|---|
| 模型档位默认 | `providers.profiles[].effort = {model: level}` | 按 **(连接, 模型)**，所有没设过会话级档位的会话共享 |
| 用途指针 | `providers.classifier` / `providers.titler` | 按用途，独立于对话模型 |

**渠道层没有模型配置。** `ChannelRuntimeConfig` 只剩 `tool_mode` 与 `workspace`——渠道级
固定模型会把同一渠道下每个群绑死在一档，且与会话级设定构成两套优先级。飞书要换模型
就在那个群里 `/model`。

## 二、固化（pin）：模型如何"钉"在会话上

会话第一轮开跑时，`session_model.pin()` 把当时生效的模型写进 meta。此后：

- 改「新会话默认」→ 已聊过的会话岿然不动，缓存不在用户背后失效
- 没开跑过的空会话不固化 → 仍跟随默认（用户改了默认，还没说过话的会话跟着变，符合直觉）

固化是幂等的，且与显式切换写同一处——固化之后不再区分"你指定的"和"当时的默认"，
因为对会话来说已无区别。

**挂在哪**：`pin()` 在 `AgentBridge._stream_user_turn`，`align_session_model()` 在
`_stream_turn` 与 `compact_thread`。都是 bridge 自己的入口，不是各前端的调用点——
「跑图前先对齐」是跑图的不变量，按前端枚举必然漏（后台通知的合成轮就漏过一次）。
真人轮与合成轮的分野本就是 `_stream_user_turn` / `_stream_turn` 的分野，固化跟着它走，
不必再传一个手工维护的 `pin` 参数。

## 三、生效时机

根因：`context.model_name` 是建桥时快照的，而连接与档位是每轮现读的。所以**每轮开跑前
必须对齐**——`bridge.align_session_model()` 把 context 拨到 `resolve()` 的应然值。
没有这一步就会出现「显示新模型、实跑旧模型」。

| 改了什么 | 本会话 | 其他会话 | 新建会话 | cron |
|---|---|---|---|---|
| desktop 选择器切模型（`set_session_model`） | ✅ 下一轮 | ❌ 不动 | ❌ 不动 | ❌ |
| IM `/model`、`/effort` | ✅ 下一轮 | ❌ 不动 | ❌ 不动 | ❌ |
| 设置里改「新会话默认」（`set_provider`） | 仅当尚未固化 | 仅未固化的 | ✅ | ✅ 下次执行 |
| 改某模型的档位默认（`set_effort`） | ✅ 下一轮（未设会话级档位时） | ✅ 同左 | ✅ | ✅ |
| 改连接凭证（base_url / api_key） | ✅ 下一轮 | ✅ | ✅ | ✅ |

无内存缓存兜着这张表：`user_store.read_section` 每次读盘（`user_store.py:26`），
`create_llm` 的实例缓存以**全部最终参数**为键（`manager.py:262`）——盘上一变就是新实例。

**自愈**：会话模型指向的模型已不在任何连接下（用户删了模型或删了连接）时，`resolve()`
就地清除该覆盖并落回默认。不校验的代价是拿死模型名打空连接，会话永久卡死。

## 四、思考档位

档位依附模型，两级：

```
会话级档位（session_meta.effort）   ← IM /effort 设的
   ↓ 没有 / 不在当前模型的能力内
模型档位默认（profile.effort[model]） ← desktop ModelPicker 的 Effort 子菜单
```

`context.effort = None` 表示"跟随 profile"，非 None 才绕过（`manager.py:217`）。
换模型即清会话级档位（`set_model` 一并清）——旧档位多半不在新模型的能力内；
即便残留，`_valid_effort` 也会视同未设，不下发端点不认的值。

desktop 侧的 Effort 子菜单仍写 profile（按模型全局），这是有意的：thinking.md 的
产品原则「给 Claude 设 max、给 MiMo 设 off，切换互不干扰」按模型记忆才成立。飞书
另有会话级 `/effort`，因为同一模型在不同群可能要不同档（尤其 ultra）。

## 五、隔离矩阵

| 场景 | 互相影响？ | 为什么 |
|---|---|---|
| desktop 会话 A 切模型 → 会话 B | ❌ | 各自一条 meta；`set_session_model` 只写当前 thread |
| desktop 切模型 → 飞书任何群 | ❌ | 同上；desktop 不再写全局 active |
| 改「新会话默认」→ 已聊过的会话（任何端） | ❌ | 首轮已固化 |
| 改「新会话默认」→ 还没开口的空会话 | ✅ | 未固化，`resolve()` 落到默认 |
| 飞书 A 群 `/model` → B 群 | ❌ | 群按 `chat_id` 各自成 thread（`session_key_of`，`inbound.py:127`） |
| 飞书私聊 → 同一个人所在的群 | ❌ | 私聊按 `open_id` 成 thread，与群是两个会话 |
| 改某模型的档位默认 → 设过 `/effort` 的会话 | ❌ | 会话级档位优先 |
| 本机 desktop 改任何模型设置 → 远程后端上的会话 | ❌ | `lumi.json` 与 `session_meta.json` 都是**每台后端机器一份** |

派生入口的模型来源（都不走上面两层）：

| 入口 | 模型 |
|---|---|
| 子 agent | 其配置的 `model`（provider 留空，按名反查）；未配则跟随新会话默认（`graph.py:297`） |
| 会话标题 / auto 审批分类器 | `titler` / `classifier` 用途指针；未配或失效 → 回退新会话默认（`provider_store.py:327`） |
| 压缩摘要、结构化提取等内部链 | 跟随所在会话的模型，但 `apply_effort=False`——**不注入任何思考参数** |
| `/direct` 直连期的 `/model` | 写 relay 绑定的 `--model`，那是 **Claude Code 的模型**，与本文无关（`channels/relay.py`） |

## 六、UI 契约

`pinned` 随 `switch_session` / `new_session` / `set_session_model` 一并下发，前端只存
**已固化**的模型；未固化就把它留空，选择器自然落到「新会话默认」。于是「改了默认，
没开跑的会话跟着变」不需要任何轮询或额外 RPC——前端零推导，是否固化由后端裁决。

- **desktop 顶部选择器**显示本会话的模型（`store[key].model ?? defaultModel`），
  不是全局 active。项目主页那里还没有会话，选择器指向「新会话默认」。
- **切模型确认框**：仅当会话**已固化**时弹（= 开跑过、有缓存可废）。判据不能用
  「消息列表是否为空」——长会话历史加载完成前它也是空的，那一瞬会静默切掉。
  前端在 `turn.start` 时同步固化，与后端 `pin()` 同一时刻发生。
- **设置→模型**那一行是「新会话默认模型」，措辞明确它不影响已有会话。
- **飞书** `/model`、`/effort` 成功都回绿卡——IM 里静默会让人以为命令没被识别。

## 七、一个尚存的边界

`/clear` 与「清空会话」会连模型一起清：走 `delete_thread + delete_meta`，而会话模型与
pin / title / goal 同居一条 meta。清空后回落新会话默认，想保留得重新设一次。
若要改，得让 `delete_meta` 支持保留白名单——目前的取舍是「清空 = 会话重置」。

## 八、锁定测试

`tests/gateway/test_feishu_channel.py`：

- `test_session_model_precedence` — 两层优先级
- `test_session_model_pin_freezes_default` — 固化后改默认不波及，且幂等
- `test_session_model_stale_override_self_heals` — 失效模型就地清除
- `test_session_effort_scoped_and_model_bound` — 档位按会话、依附模型、越界视同未设
- `test_align_session_model_applies_to_bridge` — 轮首对齐写进 context，且不固化
- `test_only_real_user_turn_pins_the_model` — 固化只发生在真人轮，合成轮 / 压缩不钉死
- `test_cmd_session_model_switches_only_this_chat` / `test_cmd_session_effort_validates_against_current_model` — 飞书命令只动本会话
- `test_channel_runtime_config_inherited` — 渠道配置里**没有**模型字段
