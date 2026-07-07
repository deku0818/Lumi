# 持久记忆 + 项目说明注入设计

> 状态：写入侧主动写入 + 注入**已实现**（2026-06-28，v0.2.10），移植自 Claude Code 的 `memdir`。
> **Dream（离线综合）+ 召回端裁决**亦**已实现**（2026-06-30，v0.2.12），见文末《Dream + 召回端裁决》。

## 两件事

| | 持久记忆 | 项目说明 |
|--|--|--|
| 文件 | `~/.lumi/memory/projects/<项目>/`（`MEMORY.md` 索引 + topic `.md`） | 项目根 `LUMI.md` |
| 谁写 | 模型在对话中自己 `write`/`edit` | 人手维护 |
| 承载 | 跨会话积累「用户是谁 / 怎么协作 / 项目背景」 | 「这个项目要什么」 |
| 注入范围 | 仅主 agent（`enable_memory`） | 主 + 子 agent |

二者与 style 系统提示词（`.lumi/prompts/` 的 SOUL/AGENTS，「Lumi 是谁」）正交。

## 存储结构

每条记忆 = 一个 `.md` 文件，带 frontmatter（`name` / `description` / `type`）。`type` 为封闭四类，
只存**无法从项目当前状态推导**的信息：

- **user** — 用户角色 / 专长 / 偏好
- **feedback** — 工作方式指导（纠正 + 确认都存，正文带 `Why:` / `How to apply:`）
- **project** — 进行中的工作 / 事故 / 决策（相对日期转绝对日期）
- **reference** — 外部系统指针（Linear、监控看板等）

`MEMORY.md` 是**索引不是记忆**：每行一个指针 `- [标题](文件.md) — 钩子`，无 frontmatter；
注入上下文时截断到 200 行。各条记忆正文不随会话注入，只有模型主动 grep/read 时才进上下文。

`~/.lumi/memory/projects/<项目>/` 的项目 key = 项目根绝对路径 sanitize（`/` → `-`，保留可读性，
与 Claude Code 一致），home 级、跨会话持久，与 checkpoints 同级。

## 模块（`lumi/agents/memory/`）

- **`paths.py`** — 记忆目录单一事实源：`memory_dir` / `memory_entrypoint` / `ensure_memory_dir` /
  `is_memory_path`（边界判定，resolve 两侧防 `..` 穿越与 symlink 逃逸）/ `read_text_or_none`（共用安全读）。
- **`prompt.py`** — `build_memory_instructions()`（行为说明：taxonomy / 不该存什么 / 两步存法 /
  何时召回 / 推荐前验证）+ `load_memory_index()`（读 `MEMORY.md`，200 行截断）。
- **`project_doc.py`** — `load_project_doc()`（读 `LUMI.md`，50KB 截断）。

## 三个接入点

1. **行为说明 → 系统提示词**：`create_agent(enable_memory=True)` 时把 `build_memory_instructions`
   追加到系统提示词尾部，并 `ensure_memory_dir`。
2. **`MEMORY.md` 索引 + `LUMI.md`（+ env/agent/skill）→ 持久注入 + 增量 diff**：
   `preprocessing/context_inject.py` 的 `context_inject_hook`（UserPromptSubmit 内置 hook，
   `preprocess_messages` 分发）把上下文块**注入进末条用户消息**（进历史、进 checkpoint），
   `additional_kwargs["ctx_digest"]` marker 记录「模型已知状态」的条目级 digest。无 marker
   （首轮 / 压缩后——marker 随旧消息删除）全量注入；条目变更只注**增量 diff**（相对上一个
   marker）、diff 比全量长退化整块；变更源文件被本会话 write/edit 过则**静默结算**（marker
   更新不注文本）；全无变化零注入、仅 marker 前移到末条。正确性不变量：历史只被压缩改写，
   且压缩恒在本 hook 之前（图拓扑 `Summarizer → PreprocessMessages → CallModel`）——hook
   永远在压缩后的世界运行，扫不到 marker 即全量重建，marker 存在 ⟺ 完整 diff 链可见。
   缓存收益：变更只动消息尾部，写记忆 / 改 skill 不再冲掉前缀历史缓存。
   `MEMORY.md` 受 `context.memory_enabled` 门控，`LUMI.md` 不受。

   > 变更判定的状态在**消息 marker 里**（per-thread、随 checkpoint 持久），不在进程单例——
   > detector（`FileSetChangeDetector`）退化为纯加载缓存，旧的单例 changed 失真问题不存在。
3. **写入免审批 carve-out**：`routing.route_decision` 在 bypass-immune 之后短路——写记忆目录的
   `write`/`edit` 所有 tool_mode 直接 `ToolExecutor`（项目根取 `get_authorized_directory()`，
   与注入同源）；同时 `engine._rebuild_boundary` 把记忆目录并入工作区边界，使 `validate_path` 放行。

   **顺序很关键**：DENY 规则、只读短路、执行模式策略守卫（plan/readonly）、bypass-immune 都在 carve-out
   **之前**，故用户的 DENY 规则与 readonly 模式仍能拦住记忆写入；carve-out 只免掉「本该问人」的审批。

## opt-in 语义

`create_agent(enable_memory=...)` **默认 False**。持久记忆有副作用（写盘 / 改 prompt / 注入上下文 /
写入免审批），故只有面向用户的对话入口 `bridge` 显式传 `True`；子 agent（`agent.py`）、workflow、cron
走默认 False 天然干净。这样「需要记忆的少数显式声明」而非「不需要的多数记得排除」，新增调用方默认安全。

## 演进方向

- **Dream（离线综合）+ 召回端裁决** — 见下方设计方案章节。
- **后台提取（extractMemories）** — 暂**搁置**。它是长会话（如飞书，一 chat 一长 thread）防 compact 遗忘的逐轮兜底；desktop 短会话由「主动写入 + Dream」两条腿覆盖，不需要。
- **召回旁路（side-LLM 选文件）** — **不采用**。索引注入按 marker 比对确定性结算，按 query 动态选文件会让注入内容每轮漂移；改走「全量索引常驻 + 索引带信号 + 活模型裁决」（见召回端设计）。

参考实现见 Claude Code 的 `src/memdir/` 与 `src/services/autoDream/`（`findRelevantMemories` / `extractMemories` / `autoDream` / `consolidationPrompt`）。

---

# Dream + 召回端裁决（已实现，v0.2.12）

> 状态：已实现（2026-06-30，v0.2.12）。范围**仅 desktop 短会话**（会话边界清晰）。长会话/飞书、extractMemories 搁置。
>
> 设计源于推倒 2026-06-30 删除的旧版 autoDream（综合质量不满意）。下文「三病根」即旧版与本方案的差异根因。
> **实现落点**：`agents/memory/dream.py`（hook 门控 + runner）、`dream_lock.py`（锁/lastAt/节流）、
> `normalize.py`（索引规范化）、`sessions/message_text.py`（`extract_messages_as_text`）、
> `utils/config/manager.py`（`parse_frontmatter` 共用）、`config/models.py`（`AutoDreamConfig`）。

## 定位：dream 做什么、不做什么

主动写入**及时但零散**（只看得到单轮）。Dream 是离线的一遍，把碎片**综合（synthesis）成连贯记忆**——合并近重复、相对日期转绝对、把散落的同类信号揉成一条。这是「睡眠固化」的隐喻本意：不是记新东西，是把碎片重新组织成长期记忆。

**承重墙 · dream 综合 / 召回裁决**：

| | 谁做 | 为什么 |
|--|--|--|
| **综合**（碎片→连贯） | Dream（离线、全局、跨会话） | 活模型在单 query 里看不到跨会话全貌 |
| **裁决**（哪条现在作数） | 召回时的活模型 | 它手握当前 query 上下文，比离线 dream 更有资格判 |

旧版「让 LLM 自由判决冲突」被一分为二：综合归 dream，**裁决归召回**。Dream 不再做它做不好的判决。

## 写入侧 · Dream 流程

1. **Stop hook** → 注册 `auto_dream_stop_hook` 进 `dispatch_hooks("Stop")`。廉价前置门：`memory_enabled` + 跳过 `output_schema` 轮 + config `auto_dream.enabled`。
2. **低成本门控**（return 早，全在导出/fork 之前，per-project）：
   - 时间门：距上次 dream（sqlite `dream_meta.last_at`）≥ `min_hours`（挡住绝大多数 stop，把下面的 DB 查挡在 hot path 之外）
   - 扫描节流：进程内 `_last_scan`，时间门长期满足时 10 分钟内不重复查 DB
   - **会话门**：`list_sessions(workspace=project)` 筛 `created_at > lastAt`（最新 checkpoint 时间 = 最后活动时间）的 recent，`len(recent) ≥ min_sessions`。老会话有新活动同样计入；不数消息、无游标——compact 增删历史不影响判定
   - 并发锁：进程内 `_in_flight`（入口同步快返）+ per-project `asyncio.Lock`（`project_lock`，正确性互斥，见下）
3. **达标 → 跑 dream；成功后 `record_dream`** 写回本次**快照时刻**（dream 后台跑时新到的消息不在快照内，记快照时刻才不会误判为已综合）。IM 长会话另有 per-thread 的 `dream_thread.dreamed_at`（`record_thread_dream`），判活 = 存在落库 ts（`additional_kwargs["lumi"]["ts"]`）晚于它的真实 human。
4. **导出其他近期会话为 text** → 临时 `transcriptDir`。扁平一行一消息（`[user]/[assistant]/[tool:X]`，换行折叠），**复用 `lumi/sessions/message_text`**。**当前会话不导**（靠完整 message 进 dream）。
5. **fork 当前主 agent → dream agent**：
   - system prompt = 主 agent **同一份**（`enable_memory=True`，记忆指令照常注入）—— **切病根①**
   - 初始 messages = 当前会话完整 `state["messages"]`（stop hook 当场可取，见 `on_agent_stop`）+ 末尾 consolidation `HumanMessage`（含 `transcriptDir` 路径）—— **切病根②**
   - 工具限只读（read/grep/glob）+ 写记忆目录；headless（无 approval broker，非记忆写入安全拒）
   - **派生 context 清掉 hooks**（沿用 subagent-hook 隔离原则）→ dream 自己 stop 不派发任何 hook，根上无自递归
   - 不共享 prompt 缓存（LangGraph 无 CC 的 fork-cache 机制；低频任务无所谓）
6. **后台任务** fire-and-forget，注册 bg-task（面板可取消）；stop hook 不阻塞，照常 `goto END`。
7. **dream 四阶段**：orient → gather → consolidate → prune。重心 **synthesis**，**不做自由判决** —— **切病根③**。
8. **收尾**：写/更新记忆 + 规范化 `MEMORY.md`（顺手补全索引行的 `type`+日期，见召回端）；清 `transcriptDir`。

**Dream 全程 per-project**：lastAt、游标、human 门、导出、写入全部按当前 project（sqlite 按 `project_key` 列隔离）。
- lastAt/游标存独立 `~/.lumi/checkpoints/dream_state.db`（`dream_meta`/`dream_cursor` 两表，同步 `sqlite3`）：**不放记忆目录**避免清理 `.md` 时误删；原子写；`last_at` 从「文件 mtime 隐式」变显式列。丢失是软失败（退化重数、最坏多跑一次幂等 dream）。
- 传 `list_sessions(workspace=...)` 用**原始 workspace 串**（config metadata 的 `workspace_dir`），**不 `resolve()`**——SQL 按存储串精确匹配，resolve 改写路径会一个会话都捞不到（旧版 code-review 踩过）。

## 读取侧 · 召回端裁决

把「记忆会过时/会矛盾」从整理端挪到召回端，让活模型就着当前 query 当场裁决：

- **索引行带 `type` + 写入日期**：`- [标题](文件.md) [feedback · 2026-06-20] — 钩子`。同主题多条并排、日期不同 → 矛盾在索引层就**自动可见**。用**绝对日期**（非「N 天前」）保证索引行内容确定、不触发无谓的 diff 重注入。
  - 填法：**主 agent 手写为主 + dream 兜底规范化**（dream prune 阶段本就重写 `MEMORY.md`，顺手统一/补全格式；手写出错由 dream 兜底修）。
- **判断指引进系统提示**（`build_memory_instructions`）：同情境多条 → 取写入日期最新；据 project/reference 行动前先验证现状。
- **不对称衰减**（落召回，不落 dream）：
  - `user`/`feedback`：**不衰减**，靠「取最新日期」自然覆盖，纯时间流逝不触发任何处理。
  - `project`/`reference`：召回时才提示「验证现状」（CC `memoryAge` 式新鲜度，但只对这两类）。

## 三病根对照（旧版 → 本方案）

| 病根（旧版 autoDream） | 后果 | 本方案 |
|--|--|--|
| ① 非 fork、taxonomy 内联进 dream prompt | 记忆格式定义两份，随时间漂移 | 复用主 agent system prompt（单一事实源） |
| ② 所有会话靠 grep 碎片（含当前会话） | dream 对刚结束的对话只有扁平片段，无完整上下文 | 当前会话喂**完整 message**，其他会话 grep text |
| ③ 四阶段含「自由判决冲突」 | LLM 离线瞎判，越整理越乱 | 综合归 dream、**裁决归召回** |

> 旧版用 `enable_memory=False` 防自递归——同时关掉了记忆指令注入，逼出病根①的内联 taxonomy。本方案**解耦**：防递归交给 hook 隔离，`enable_memory` 回归单一职责（只管指令注入），故 dream 能 `enable_memory=True` 复用 system prompt 又不自递归。

> 导出格式选 text 而非 `messages_to_dict` 的 JSON：实测一条消息展开 8+ 行嵌套 JSON（`additional_kwargs:{}`/`response_metadata:{}`/`*:null` 等噪音），dream 的 gather 是「grep 窄关键词」，一行一消息的 text 对 `grep -A/-B` 友好得多；JSON 的保真（`tool_calls.args`）对综合偏好无价值反成噪音。`messages_to_dict` 留给需要保真序列化的场景（调试/回放）。

## 主动触发（`/dream`）

记忆会话里输 `/dream` 立即触发一次综合（`gateway/bridge/core.py` 的 `stream_command` 内置命令分支 → `dream.start_dream`）：**force** 绕过时间/会话/节流门，仅 `_in_flight` 防重复，后台跑、不阻塞对话、完成走 bg-task 通知。与自动 dream 共用同一 runner（抽出的 `_spawn_dream`），区别只在手动触发、且即便近期没有其他会话也综合当前会话。命令仅在 `memory_enabled` 会话由 `list_commands` 下发（`type:"system"`，前端零改动自动补全）；`stream_command` 入口统一 `current_thread_id.set` 保证内置命令的后台任务完成通知归属本会话。

## 配置（`auto_dream`）

`enabled`（默认 **False**，opt-in）/ `min_hours`（24）/ `min_sessions`（3，自上次 dream 以来活跃的其他会话数）。均可配置。

## Dream 互斥

所有 dream（Stop 钩子 / `/dream` / `/dream-session` / IM 每日定时）都经唯一底座 `_run_dream_fork`，其内部持 per-project `dream_lock.project_lock`（`asyncio.Lock`）跑完整个综合——同一份 MEMORY.md 恒只有一个写者，**任何入口都绕不开**；迟到者原地排队。`_in_flight` 集合降级为入口层 UX：手动命令的同步快返（"已有一次整理在进行中"），堵 fire-and-forget 的 create_task 与任务实际拿锁之间的空窗。

IM 每日整理另有「先沉淀再压缩、dream 失败绝不压缩」的次序不变量与摘要载体不带 ts 的设计权衡，见 `feishu.md`《每日记忆整理》。

## 旧基建复用清单

- **回来（可更简）**：transcript 导出（改 text、只导其他会话、复用 `message_text`）、门控阶梯、dream 状态持久化（sqlite `dream_state.db`）、bg-task 注册、记忆写入 carve-out。
- **不回来**：内联 taxonomy（改复用 system prompt）、`enable_memory=False` 防递归（改 hook 隔离）、四阶段里的「自由判决」部分。
