# Changelog

## [0.2.110] - 2026-08-19

### Fixed
- **飞书流式卡片被 5 分钟闲置清扫误伤** — `_evict_stale` 只驱逐真正的孤儿 buf（闲置超 TTL **且**该 chat 无轮在跑）。此前直连 Claude Code 深度思考 / 限流重试 / 子 agent 期间几分钟没有外显输出，buf 被当孤儿驱逐、卡片关掉，后续输出重新建卡、终态只剩来源行——一轮答案被切成几张只有 footer 的残卡。

## [0.2.109] - 2026-08-17

### Added
- **飞书 `/direct` 可指定模型与思考档位** — `--model 别名或id`（`fable`/`opus`/`sonnet`/`haiku` 或完整 id）、`--effort low|medium|high|xhigh|max`，与 `--dir` 同一套首行旗标语法（任意顺序、值取到下一个 `--`），三者均粘性；`/direct` 状态卡与进入确认卡显示「模型：opus · high」。换模型**不**开新会话——实测模型是每轮属性，`--resume` 时切换生效且记忆不丢。`--effort` 本侧枚举校验（实测 cc 对无效值静默接受），`--model` 交 cc 报明确错误经红卡透传。旗标前缀认 `--` 的破折号 / 全角变体（`—dir`）：中文输入法常把连打短横转成破折号，不认它整段会被当任务喂给 cc。不支持 `--key=value`。
- **直连中的 Lumi 通知只读提示** — 妙记纪要 / 后台任务完成 / 日程提醒在直连期间照常推送，紧跟一张「⚡ 直连中」黄卡说明此刻回复会直达 Claude Code、需要 Lumi 跟进先 `/direct exit`。同一时刻只有一个工人在听，路由零特判。

### Changed
- `split_dir_arg` 泛化为 `parse_direct_args`：统一旗标行解析，未知旗标 / 缺值 / 非法 effort 抛人话 `ValueError` → 一张「选项有误」红卡。

## [0.2.108] - 2026-08-17

### Added
- **飞书 `/direct` 直连 Claude Code**（`lumi/gateway/channels/relay.py`、`feishu/relay_turn.py`）— 飞书消息直达本机 claude CLI，Lumi 不参与；thread 级路由开关，Lumi 会话原封不动，`/direct exit` 无缝回到原对话。走无头 `claude -p --output-format stream-json --resume` 子进程（每条消息一轮，跑完即退），不用 tmux/PTY 刮屏——cc 会话文件是共享基质，终端 `claude --resume <sid>` 打开的就是同一段对话（双向接管，卡片 footer 与 `/direct` 状态卡给完整 sid 可整段复制）。stream-json 事件折叠回现有流式卡片：token 打字机、`tool_use`/`tool_result` 配对驱动忙碌状态行、init/result 的 sid 即时落盘（sidecar `~/.lumi/channels/relay.json`，`active` 持久化 → serve 重启不静默失效）。命令面：`/direct` 状态/用法、`/direct claude [任务]` 续接进入、`/direct new [任务]` 新会话、`/direct exit` 退出；`--dir 路径` 紧跟子命令独占到行尾指定项目目录（粘性；换目录自动开新会话）。直连中 `/stop` `/clear` `/help` 作用于 cc，其余文本（含 cc 自身斜杠命令）原样透传；`--permission-mode bypassPermissions`（与渠道无人工审批现状同语义），root 运行未设 `IS_SANDBOX=1` 时进入即被 `relay_precheck` 拦下。
- **飞书系统直发卡片体系** — 22 处渠道层生成、不经模型、不进上下文的消息（/stop /clear /help /direct 应答、排队提示、错误）统一「语义色 header + 正文 + 灰字 note（下一步提示）」：green 完成 / red 错误 / orange 提醒·Lumi 面板 / blue 信息 / yellow 直连；`_markdown_card` / `send_markdown` 加 `template` `note` 参数。note 用 `<font color='grey'>` 而非 note 组件——schema 2.0 真机报 230099/200861 "unsupported tag note"。

### Fixed
- 直连轮 cc 出错时已流出的正文不再随 `aborted=True` 收尾一并丢失（正常关卡保住答案再发红卡）；子进程秒退（未登录）时 stdin `ConnectionResetError` 不再穿透成泛化"出错"，改用 stderr 尾部合成可读原因；单行 stream-json 超 10MB 只丢该事件不死整轮。
- 排队期间切换 `/direct` 不再把发给 Lumi 的消息改道给 cc（反之亦然）：路由在入队那一刻定格到 `_Pending.relay`，`_drain` 按连续段各走各的且保到达顺序；`/direct new` 在跑轮时清 sid 被写回覆盖（"全新会话"静默变续旧）加忙判守卫；直连模式图片文件名从 `image_key[:12]`（飞书 key 前 12 位几乎全是固定前缀，同批多图撞名互覆）改为完整 key；媒体-only 消息下载全失败不再静默吞掉；群聊直连多条合并保留发言人。

### Changed
- `lumi/utils/json_sidecar.py`：`session_meta` 与 `relay` 两份逐行相同的按 key 落盘 JSON sidecar 读写归一；`bg_process._terminate_group` 公开为 `terminate_group` 供直连子进程组终止复用（连带白得 Windows 分支）；`outbound.turn_closer` / `tool_activity` 抽出，`run_turn` 与 `run_relay_turn` 共用；`BridgePool.busy()` 收敛忙判写法。

## [0.2.107] - 2026-08-14

### Fixed
- **飞书一轮超过 10 分钟后，后半段回答静默丢失**（`lumi/gateway/channels/feishu/streaming.py`）— 飞书规定流式卡片的更新模式「距上次开启 10 分钟后自动关闭」，长轮次必然撞上：超时那一刻报 `200850 card streaming timeout`，此后每次 content 更新都是 `300309 streaming mode is closed`。这两个码此前不在任何恢复分支里，卡片就此定格在前 10 分钟的内容；又因为前半段渲染成功过（`rendered_len ≥ 0`），连「降级重发普通卡」的兜底都不触发，用户只看得到半截答案。现按语义分成两类：卡片失效（`_is_card_invalid`）才换新卡重发，**流式模式关闭（`_is_stream_closed`）则经 settings 把 `streaming_mode` 重开回 `true`，原卡继续写**——实测（含真等满 10 分钟自然超时）重开后同一张卡恢复可写，无需换卡也不重发已有内容。
- **终态那一刷失败时不再无声吞掉整轮答案**：`_flush_end` 的降级判据从「全程未渲染成功」放宽到「卡片上没有完整答案」（全程未渲染成功 **或** 终态刷新失败），任何未知错误码导致的收尾失败都会降级到普通 markdown 卡重发全文。
- **发号时机修正，消除「重开续写把在途更新的号越过去」**：`sequence` 原先在 `_enqueue_render` 入队时预分配，而入队到发出之间隔着 await，重开续写在这个窗口里取走的两个号会越过它，那一刷被飞书按「sequence 未递增」拒掉、卡片停在旧快照。现所有发往飞书的操作统一经 `buf.take_sequence()` 在**发出前一刻**取号。

### Changed
- 流式卡片的 settings 调用开关同源：`_close_streaming_mode_sync` 并入 `_set_streaming_mode_sync(card_id, enabled, sequence)`；content 发送收敛为唯一出口 `_send_content`（取号 + 渲染 + executor 调用），`_push_update` 与重开重试共用它。

## [0.2.106] - 2026-08-14

### Changed
- **多机标识从「纯色点」换成「机器类型图标」**（`desktop/src/components/MachineTabs.tsx`）— 侧栏里本地与远程只靠颜色区分，得先记住「金色=本机」才读得懂；现在形状直接说话：本地=笔记本、远程=云，颜色继续承担「是哪一台」（`machineColor` 未变，与会话行同源）。一个元素同时给出类型与身份，故取代了原先的纯色状态点。覆盖侧栏机器分组头、会话行（最近 / 搜索结果）、定时任务行、设置各面板的机器选择条、设置→连接的机器卡（原来是所有机器一律 `Server` 图标）、输入框模型 chip 与其下拉机器头。
- **渲染本体收敛为一个 `MachineMark`**：`MachineDot` / `ColorDot` 两个组件与四处手写的「图标 + 着色 span」合并，`machineGlyph`（`id === 'local'` 的判定）退为模块私有，跨处漂移的尺寸与 tooltip 归一。`MachineIcon` = `MachineMark` + 连接态，`{ id, name, color }` 三元组具名为 `MachineMarker`，`CronJobRow` / `SessionRow` 的 `dotColor`+`dotName` 两参并作一个 `machine`。
- **带连接态的图标按 `useMachine` 的 scope 判，不再裸读 `conn`**：断线机器在退避期是 `closed ↔ connecting` 来回跳，裸判会让图标在灰色静止与机器色呼吸之间一眨一眨；颜色现在是「这行属于哪台机」的唯一载体，不能跟着眨。改为重试中恒保持机器色 + 呼吸，只有 `stopped`（退避耗尽 / 令牌无效）才一次性转灰。
- dev server 端口 5173 → 5175（`vite.config.ts` / `package.json` 的 `wait-on` / `main.cjs` 的 `DEV_ORIGIN` 三处同步），避开常被其它工具占用的 5173。

## [0.2.105] - 2026-08-11

### Fixed
- **批准越界写入后仍被拒，且「始终允许」永远不生效**（`docs/architecture/permissions.md`「边界与审批是两道正交的门」节）— 权限规则与工作区边界是两道**各自独立否决**的门，而三条授权路径此前只过第一道。后果有两层：`write`/`edit` 通过审批后在执行期照样撞 `filesystem/backend.py` 的 `workspace.validate_path` 抛 `PermissionError`；更糟的是 default / auto 模式下 `boundary_ok` 恒为 `False`，同一个调用**每轮重新回到审批**，用户点多少次「始终允许」都出不来——写进 `permissions.local.json` 的 allow 规则被边界这道门无声吃掉。现三条授权路径（人工审批 / auto 分类器 / privileged）在放行前同调 `nodes._widen_boundary_for()`，把本批越界路径所在目录纳入本会话工作区，落点与用户手动「添加文件夹」完全相同（`add_ephemeral_workspace`，仅内存不持久化），模型下一轮经 `drain_folder_note` 收到目录变更提醒。
- **auto 模式每轮白付一次分类器调用**：同上一条的同一根因在 auto 模式的表现——分类器裁决通过但边界未放宽，下一轮又被送回 `AutoClassify`。分类器裁决与人工审批同权：AI 判断即用户授权。

### Changed
- **放宽面刻意收窄**，只覆盖本批里 `is_local_path_tool()`（`write`/`edit`/`bash`）∩ `is_write_tool()` 的调用，两个条件缺一不可：批次是混合的，批准一次越界 `read` 不该换来该目录的**写**权限；而 `is_write_tool()` 对未知工具 fail-closed 恒 `True`，不显式限定工具名就会把每个带 `path` 参数的 MCP 调用都算进来——外部工具的 `path` 含义未知（可能是 URL、库名、远端路径），据此开本地写权限没有根据。代价：`artifacts` 等其余受边界约束的工具越界时不放宽，需用户显式「添加文件夹」。
- **`is_local_path_tool()` 新入 `tools/capability.py`** —— 与 `is_file_edit_tool` / `is_write_tool` 同处，工具能力的声明归口在一个模块。
- 授权目录取法（`folders._enclosing_dir`）取**最近的已存在祖先**而非直接 parent——越界路径常常整条尾巴都还不存在（`write /x/new/deep/a.txt`），拿不存在的 parent 去 `add_folder` 只会失败；一路走到文件系统根仍不存在则放弃，把 `/` 纳入工作区等于关掉边界。

### 已知未处理
- **无 bridge 的 headless 路径不放宽边界**：回调由 bridge 在 `initialize` 注入，故分界是「有没有 bridge」而非「是不是 cron」——`lumi serve` 下的 cron 整个 job 跑在 `AgentBridge` 上且 `tool_mode="privileged"`，已覆盖；落空的是 workflow、后台子代理和无 serve 的 cron fallback。刻意不打通：无人值守的任务自行扩大文件系统访问面，出问题时没人在场。这些场景请把目录预先写进 `permissions.json` 的 `workspaces`（持久化、跨 run 生效）。

## [0.2.104] - 2026-08-10

### Changed
- **`present_files` 工具更名为 `artifacts`（制品）**（`docs/architecture/desktop.md` artifacts 制品文件预览节）— 「制品」一词直指这个工具真正在做的事：把 Agent 产出的成果件交付到界面上，而 `present_files` 只描述了动作不描述交付物。后端 `providers/present_files.py` → `providers/artifacts.py`（`PresentFilesInput` → `ArtifactsInput`、注册名与工具名同步），前端 `PresentedFiles.tsx` → `Artifacts.tsx`（类型 `PresentedFile` → `Artifact`、`parsePresentedFiles` → `parseArtifacts`）。参数名 `filepaths` 刻意不动——`boundary.py` 的 `_PATH_LIST_ARG_KEYS` 按它做工作区边界检查，改名会静默失去约束。**行为无变化**，返回 JSON 形状不变。
- **工具描述重写**，补齐与实际能力的落差：原描述只说「使文件可见 / 可打开下载」，未提预览面板的存在。新描述交代用户实际看到什么（聊天流文件卡片 + 右侧预览面板：图片 / PDF / HTML / Markdown / 文本代码内嵌渲染，docx / xlsx / pptx 窗口内渲染，视频 / 音频 / 其它兜底「用系统应用打开」）、受工作区边界约束、顺序即展示顺序、只呈现不改动文件，并明确「一次调用传本轮全部文件」避免逐个刷屏。

### Fixed
- **改名后旧会话的文件卡片丢失**（`desktop/src/App.tsx` `groupItems`）— 历史条目的工具名直取 checkpoint 里当时记录的 `tool_call.name`（`gateway/session.py` 的 `_history_items`），改名前的会话重开时带的是 `present_files`。只认新名会让这些文件从卡片段掉回灰色工具组、右侧预览面板对它们彻底不可达。改为两个名字都认——输出 JSON 形状未变，认名即可复原。

### 已知未处理
- 改名前用户存盘的权限规则按老工具名匹配：`always_allow_*` 写在 `.lumi/permissions.local.json` / `~/.lumi/permissions.json` 的授权会失效需重授一次，手写的 `deny present_files` 规则则静默不再匹配。未加别名表——为一次改名留永久兼容包袱不划算，且 `artifacts` 只 `stat` 文件不读内容，丢一条 deny 损失的是界面可见性而非数据。

## [0.2.103] - 2026-08-09

### Added
- **对话时间旅行：重新生成 / 原地编辑重发**。用户气泡 hover 出操作条——**重新生成**原样重答，**编辑**把气泡原位换成可编辑框（Cancel 零副作用，Save 才提交）。两者语义相同：以该条消息为锚，**它之后的历史全部消失**（后端同步删 checkpoint 消息），不是分支。后端 `rewind_before_message` 走 `OfflineFlush` 锚点 + `RemoveMessage`，与 `compact_thread` 同一条离线写回路径；同一次 `aupdate_state` 里清空 `todos`（被删轮次建立的任务列表不该带进重答轮）并剥掉新末条的 `ctx_digest` marker（不剥会让 `context_inject` 误判「已注入过」而漏注上下文）。
- **`turn.start` wire 事件**：真实用户轮开轮即广播该轮用户消息的落库 id，前端据此给乐观气泡上锚。走事件而非 RPC 返回值——id 是「轮的事实」而非「轮的结果」，中途 stop 的轮同样需要它，且不必让每个流式入口都记得回传。
- **`regenerate` / `edit_resend` 两个流式 RPC**：各自是单个原子调用，截断与重发共处一轮、持同一把 `run.lock`。拆成「截断 RPC + send」会留出竞态窗口，中间任何失败都让编辑文本连同被删历史一起丢失。
- **`load_history` 的 user item 带 `message_id`**：时间旅行的锚点只认消息 id，不做序号 / 文本猜测——本地列表可能含后端没有的条目（发送失败残留、系统命令气泡），按序号对齐会指错消息、静默截断错误的轮次。

### Fixed
- **重答会叠加上下文注入块**：checkpoint 里的 content 已烤入原轮注入块，原样重投会与新一轮注入叠加，**每重答一次全量 env/agents/skills/记忆负载就翻一倍**，且新旧 env 块互相矛盾。改为重建——`strip_injected_prefix` 回到用户原样输入后交 `_build_user_message` 走与新消息完全相同的构造流水线，附件标签由显示声明重新派生，构造对重发幂等。
- **同一气泡第二次重新生成必失败**：截断后消息以新 id 重挂，而前端气泡仍持旧 id，第二次点击会先乐观删掉刚拿到的回复、再报「目标消息不存在」。改为重挂时清 `messageId`、由本轮 `turn.start` 重新上锚。
- **`send()` 的乐观气泡插在斜杠命令分流之前**（与原注释所述相反）：`/compact`、`/dream` 这类系统命令会留下后端永远列不出的幽灵气泡，回合结束刷新时当着用户的面消失，并破坏本地与后端 user 消息的对应关系。
- **`EditBubble` 的 Enter 缺输入法守卫**：中文 / 日文输入法按 Enter 确认候选词会直接触发截断重发，把半截拼音当成新消息发出去。补 `isComposing` 守卫（与 Composer 同款）——编辑提交是破坏性操作，误触代价远高于普通发送。
- **失败路径不自愈**：后端拒绝（「已有任务在执行」等）时只复位 `running`，乐观截断永不恢复、UI 与后端永久分叉。`reportSendFailure` 统一补 `reloadHistory` 对齐后端真相。
- **`resolveMessageId` 的 await 期间不复查在途态**：期间用户可能已发出新消息，会误删其气泡并清掉在飞轮的 `running`。id 改由事件下发后该异步窗口整体消失。

### Changed
- **`meta_message.py` 收拢消息元数据契约**：新增 `strip_ctx_digest`（压缩 `_reattach` 与时间旅行截断共用，marker 键名单源）、`strip_injected_prefix`（`visible_user_text` 与重发重建共用，「前 N 块是注入」只此一处解读）、`declared_file_paths`（`items[].files[].path` 形状契约单源）。
- **`AgentBridge` 分出 `_stream_turn` / `_stream_user_turn` 两层**：前者是「以一条消息起一轮」的底层（合成轮直接用），后者叠加真实用户轮的开轮设置（checkpoint、文件夹/Ultra 边沿提醒、`turn.start`）。此前合成轮内联复制底层三行，且重答路径漏掉了边沿提醒注入。
- **前端时间旅行合一**：`regenerate` / `saveEdit` 合并为 `timeTravel(itemId, newText?)`，截断与重挂气泡合为一次 `setStore`；新增 `userBubble` 工厂与 `startTurn`，消除气泡字面量重复与 `itemId === null` 哨兵。
- **两处 hover 操作条合并**为共用的 `HoverActions` + `IconAction`，用户气泡不再绕过既有 `copyMap`/`activeKey` 机制自建一套；`CopyButton` 加 `memo`（现在每条用户气泡都常驻挂它，流式期间每个 delta 都会重渲染）。
- **编辑框沿用气泡几何**（共享 `USER_BUBBLE` token）与 `.composer` 的 `field-sizing`，进出编辑态不跳变；提示改用 Radix Tooltip 与全应用一致。

## [0.2.102] - 2026-08-07

### Added
- **代理别名的目录条目手动指定**：代理 / 网关暴露的模型名（`plan-glm-5.2`、`aidong-claude-sonnet-5`）在 models.dev 里没有同名条目，只能靠 fuzzy 猜，猜错则**上下文窗口、输出上限、思考档位一起取自另一个模型**且界面上完全看不出来——实测 `plan-glm-5.2` 会猜中 `umans-glm-5.2`（`plan`/`umans` 的偶然字母重叠让它比真名 `glm-5.2` 分还高），窗口从 1,048,560 缩到 405,504、档位从七档缩成两档且丢掉 Off。新增 `ProviderProfile.catalog` 覆盖表（model → 目录条目 id，与 `context`/`max_tokens` 并列、随模型增删清理、空串=恢复自动），由 `provider_store` 读写盘时拍平发布给 `catalog.set_aliases`——按名而非按 profile，因为思考链（`allowed_levels`/`effort_params`/`rejects_forced_tool_choice`）全程只有模型名拿不到 profile。
- **匹配来源随结果上报**：`catalog.match()` 返回 `Match(entry, kind)`，`kind ∈ manual|exact|fuzzy|none|stale`。`stale` = 指定的条目已不在目录里（数据更新后消失），`entry` 仍是回落的自动匹配结果——运行时照常工作，但「你的指定没生效」由后端判定并下发，不留给客户端各自反推。`lookup()` 保留为丢掉 kind 的薄包装。
- **设置 → 模型的映射徽标与选择器**：每个模型名右侧常驻徽标显示解析结果与来源，只有 `fuzzy`/`stale` 报警（金点/红点呼吸），其余安静——每个代理别名都亮一下的话警示很快就没人看了。点开是 `search_catalog` 搜索式选择器（新增 RPC），整串无果时逐段剥前缀重试，否则最需要帮忙的别名反而搜不出东西。

### Fixed
- **结构化输出链回散文时 `AttributeError` 崩整轮**：`_require_structured_result` 守卫此前只挂在软引导（`tool_choice=auto`）分支上，走强制 `tool_choice` 的模型（如经 LiteLLM 代理的 `plan-glm-5.2`，代理层可能把该参数丢掉）拿到的 `None` 会直接交给调用方——`auto_classify` 的 `verdict.decision` 在 `try` 外面炸 `AttributeError`，设计好的「分类器失败 → fail-closed 转人工审批」一次都跑不到。守卫移到链尾，两条分支同受保护，三个消费方（分类器 / 判官 / titler）一并覆盖。

### Changed
- **`manager.levels_for(entry, name)`**：`allowed_levels(name)` 退化为薄包装。描述目录条目本身（如映射候选列表）时不再把 id 当模型名重新解析——那一圈会经过别名表，可能算出别的条目的档位。
- **`catalog._norm()` 统一归一规则**：索引键 / 别名键 / memo 键 / 搜索串此前四处写法各异，任一处漂移都会让手动指定静默失配。
- **`provider_store._coerce_str_map`**：目录条目与思考档位两张按模型的字符串覆盖表共用一份 coercion。
- **`match()` memo 改单次 `get`**：`set_aliases` 会在任意读 providers 分区的线程里清 memo（渠道线程首次 `load()` 即触发），「先 in 再取」两步之间被清掉会抛 `KeyError` 进 LLM 调用路径。
- **徽标状态表合一 + 复用 `StatusDot`**：`tone`/`dot`/`hint` 三张平行表并作一张（漏改其一 = 圆点对了颜色不对且不报错）；圆点改用 `SettingsKit.StatusDot`，找回全应用统一的光晕。
- **`conftest` 新增 `_aliases` autouse 重置**：别名表是进程级全局，不清会让先跑的测试污染后续 `lookup`/`allowed_levels` 断言，结果随测试顺序漂移。

## [0.2.101] - 2026-08-06

### Fixed
- **「复制」按钮无声失效**：Electron 的权限收口（`defaultSession` 的 request / check 两个 handler）只放行 `local-fonts`，把 `clipboard-sanitized-write` 一并拒了，`navigator.clipboard.writeText` 的三处调用——消息正文复制、设置页凭证复制、文件卡片「复制路径」——因此写不进剪贴板。两个 handler 改共用一份 `ALLOWED_PERMS` 白名单并加入该权限；camera / mic / geolocation 等其余权限维持一律拒绝。

## [0.2.100] - 2026-08-05

### Fixed
- **中断不再丢上下文（全链路）**：此前点停止会丢掉整轮内容——已跑完的子 agent/工具结果被下一轮的 stale 恢复回退掉，屏幕上已流出的半截回复也从未落库。三层修复：① stale 恢复从「回退到轮前干净 checkpoint」改为就地修复（已验证 LangGraph 对 stale checkpoint 带新输入会丢弃残留任务从 START 起新 run，工具结果天然保住），悬空 tool_call 补配对合成 ToolMessage（措辞不断言「未执行」——cancel 可落在工具已完成但超步未提交的窗口）、残留 `ptl_retry` 顺手清掉（防下轮无条件有损压缩）；② 半截回复经 `persist_partial_reply` 写回 checkpoint（带 `lumi.interrupted` 标记），与悬空配对合并为一次有序写入；③ 防写重按消息 id（流式 chunk id 与落库消息同 id），id 缺失的方言 provider 退回 `extract_text_content` 文本判重——旧的 `str(content)` 子串检查对 block-list/多行必失效。
- **`aupdate_state(as_node="CallModel")` 在真实图上必炸被吞**：CallModel 条件边 `is_use_tool` 需要 Runtime 注入而 `aupdate_state` 无法提供（玩具图测试测不出，真实图上 100% 失败只留一条 ERROR 日志）。新增 `OfflineFlush` 锚点节点（无入边、出边直达 END）：三处离线写回（半截回复、悬空修复、IM dream 离线压缩摘要——后者同款隐性炸弹一并排掉）统一挂它，写完 `next` 即空。真实图契约测试锁住可写性 + 「CallModel 依旧不可写」哨兵（LangGraph 行为变化时提示可撤绕道）。
- **取消收尾统一且防打断**：`finalize_cancelled_stream`（先确定性 aclose 生成器防 GC 延迟关图与下一轮竞争、再写回中断残留，shield 内置防二次取消打断）内置于 pump——desktop 用户轮、后台通知轮、飞书 `/stop`、各异常路径全部继承同一收尾，且发生在持 run.lock 段内（通知轮无从插队重置 buffer / 并发写 checkpoint）。双重取消守卫（`cancelling()`）落到 desktop 与飞书两端。
- **通知轮两处生命周期缺陷**：裸 `await task` 的 waiter 被取消会经 `_fut_waiter` 连坐取消合成轮——detach 停通知循环就顺手杀掉挂审批的合成轮，违反其「run task 与挂起 Future 原样存活」契约（老行为，`asyncio.shield(pump)` 修复 + 回归测试）；finally 无条件抹 `_run.task` 会把收尾窗口里新挂上的用户轮句柄抹成 stop 杀不掉的野轮（改按归属校验）。
- **workflow 工具子代理未登记归属**：其后台派生的子代理图节点同名 CallModel，会被判为主链事件（流错挂主气泡、文本混入半截 buffer）。派生子代理的工具清单单源化为 `bg_tasks.SUBAGENT_SPAWNING_TOOLS`。

### Changed
- **`build_reject_messages` 提升公开**（原 `_build_reject_messages`）：bridge 的悬空补配对与审批拒绝/取消路径共用同一构造器，消除首日分叉（`tc.get` vs `tc["id"]`）。
- **checkpoint 回溯 helper 迁居**：`_find_clean_checkpoint_id`/`_extract_cp_ids` 随唯一消费者迁入休眠的 `bridge/checkpoint.py`（模块 docstring 记录 rewind 与「保留中断轮」的语义分歧，接线前需同步）；活代码里不再有回退机械。
- **半截 buffer 列表化**：逐 delta 字符串累加（属性目标 += 全量拷贝，O(n²)）改分片列表 + 写回时一次 join；流正常结束即清（闲置会话不再钉住末条长回复）；成对重置收敛单一入口。
- **`_RunState` 拥有生命周期方法**：`cancel_once()`（取消防重复）/`clear_if(owner)`（句柄归属）两条 asyncio 不变量各写一次。
- **测试脚手架**：中断类测试共用 `toy_graph.py`（与主链同名节点 + 事件驱动取消，消掉 ~2s 固定 sleep）；自写慢速流式 fake 模型换 langchain 现成 `FakeListChatModel(sleep=...)`。

## [0.2.99] - 2026-08-03

### Added
- **任务进度右栏**：`todos` 工具的任务列表现在在桌面统一右栏里实时显示为「任务进度」节（与后台任务同挂 `RightRail`）——in_progress 呼吸光点、completed 金色 ✓ 弹入 + 文字淡化，全部完成整节灰化保留，空列表不渲染。新增 wire 事件 `todos.update`（走协议单一事实源 `protocol/events.json`，`EventKind` 值即 wire 名），`load_history` 一并带回 `state.todos` 快照——事件与历史同一真相源、共用 `todos_payload` 投影（吃工具原始 dict 入参与 checkpoint 往返回来的 `Todo` 实例两种形状），故压缩 / 切会话 / 重连都从权威快照还原而非回放事件流反推。子代理的 todos 更新其独立图状态，不外发。

### Fixed
- **远程 Office/HTML 预览的 token 泄漏**：带 serve token 的 `/file` URL 原本直接喂进 `sandbox="allow-scripts"` 的 iframe，页面脚本可读 `location.href` 偷出 token，再借端点的 CORS `*` 读任意远程文件外传。改为父应用鉴权 fetch 落 `blob:` URL 再喂——帧内 `location` 变 `blob:null`（opaque origin，无 token、连远程 host 都不知道），链路彻底断掉；本地 lumi-file 无 token，行为不变。
- **`/file` 把权限不足误报「文件已删除」**：`os.stat` 的 EACCES 原本一律映射 404，前端据此显示「文件已被移动/删除」。改按 errno 细分（ENOENT/ENOTDIR→404、EACCES→403、其余→500），前端存在性探测失败也不再谎报「不存在」，交由预览区报加载错误。
- **通知点击破坏跨机预览不变量**：点系统通知切会话走的是裸 `setActive`，绕过了 `activate` 的 `setPreview(null)`，导致已打开的预览对着新会话的机器重新取同路径文件（跨机内容错配）。改为统一经 `activate`。
- **一键装齐进行中点单项安装静默无反应**：`env_install` 全局互斥返回 `started=false` 时前端只清进度不反馈。新增 `onBusy` 通道（与自由文本的 `onError` 分开，不让「busy」控制态渲染成 EnvPanel 的错误横幅），Office 预览的安装按钮给出「安装进行中，装完会继续」提示。

### Changed
- **`/file` 端点不再阻塞事件循环**：改同步 `def`（FastAPI 丢线程池执行），慢 / 网络文件系统上的 `os.stat` 不再卡住承载全部 WS 会话的单个事件循环；并把首次 `stat_result` 传给 `FileResponse` 免二次 stat。
- **远程文本预览按需分段拉取**：`TextPreview` 远程 fetch 带 `Range: bytes=0-499999`（CORS 安全列表头不触发预检、`FileResponse` 回 206），不再为显示头 500KB 而整块下载数十 MB。
- **Office 渲染省一次子进程**：新增 `toolbox.locate`（只解析来源 / 路径、不跑 `--version`），`detect` 在其上叠版本号，`render_office` 改用 `locate`——每次缓存未命中少一次 .NET 自包含二进制的启动。
- **`useEnvInstall` 的 'all' 关联改 opt-in**：机器级一键装齐的终态 `env.state`（target='all'）原本触发所有 scoped 订阅方的 `onState`，会误跑渠道体检等副作用；新增 `watchAll`，仅显式 opt-in（如 Office 预览需 officecli 装完重渲）才响应 'all'。
- **README 重写 + 英文版**：首页重新定位为「开源 AI 同事」，突出日常任务与飞书深度集成（会议妙记自动纪要推送、更多渠道路线图），新增 `README.en.md` 与中英语言切换。

## [0.2.98] - 2026-08-02

### Added
- **Office 文件窗口内预览**：agent `present_files` 的 docx/xlsx/pptx 现可直接在右侧预览面板渲染——后端新增 `render_office` RPC，经 OfficeCLI（开源 .NET 单二进制，pin v1.0.143）转成完全自包含的 HTML 走现有 iframe 沙箱通道；产物按「路径哈希-mtime-渲染版本」缓存于 `~/.lumi/cache/office_preview/`，写临时文件成功才原子改名（失败/超时的半截产物不投毒缓存）。未装组件时预览面板就地引导安装（约 34MB 一次性），装完自动重试渲染；转换失败时把 officecli 的具体报错（如缺 libicu）原样透出可复制。xlsx 产物注入列头拖拽调宽 + 双击自适应脚本，补上静态渲染没有的 Excel 交互。旧版 .doc/.xls/.ppt 保持「用系统应用打开」兜底。
- **远程后端文件预览通道**：整条预览栈原本只能读本机盘（lumi-file 协议），远程机器的文件全部 404。serve 新增 `GET/HEAD /file` 端点（与 WS 同 token 鉴权、128MB 上限、带 CORS 供 fetch/HEAD 探测读状态码），前端按会话所在机器自动选通道：本地零拷贝、远程流式取回（含 Office 渲染产物）；远程文件隐藏「在访达中显示/用系统应用打开」等本地语义入口，存在性探测改 HEAD（仅 404 视为不存在，超限 413 正确显示「文件过大」）。预览面板只认目标机器的连接，缺位报加载失败而非静默落回本地盘读同路径文件。
- **环境页「可选增强」分栏**：OfficeCLI 进「设置 → 环境」，与 ripgrep 同列可选栏（缺了自动降级：rg→内置搜索、officecli→系统应用打开），uv/Node.js 留核心栏；一键装齐覆盖两栏全部缺失项，CLI `lumi env install` 同步认全量工具集（`ALL_TOOLS` 单源）。旧版后端未上报的工具行也给安装按钮，安装请求被拒时错误上横幅不再静默。
- **文件预览面板浮框化**：对齐后台任务侧栏的浮卡语言——玻璃材质、18px 圆角、上下 10px/右缘 4px 缝，顶边与左侧栏齐平（与右栏同级挂载），仍占位挤压聊天区、宽度可拖。

### Fixed
- **Docker/无 ICU Linux 上 Office 转换启动即崩**：officecli 是 .NET 自包含二进制，slim 镜像缺 libicu 时 Abort。镜像补装 ICU 运行时库（构建期动态解析包名，不随 Debian 版本漂移，不拖 -dev 头文件）；代码层撞 ICU 缺失自动以 invariant globalization 降级重试并记住结论，任何主机都不会因此渲染失败。

## [0.2.97] - 2026-07-31

### Changed
- **依赖全量升级**：langchain 1.3.14 / langgraph 1.2.10 / langgraph-sdk 0.4.2 / langchain-anthropic 1.5.3 / langchain-openai 1.4.1 / anthropic 0.120.2 / openai 2.51.0，及 fastapi、uvicorn、ruff 等约 40 个包升至最新。全量测试与 MCP 真连（tavily/plane）验证通过。
- **mcp 钉在 1.x**（现 1.29.0）并补成直接依赖：`langchain-mcp-adapters` 0.3.1 声明 `mcp>=1.24.0` 无上限，但 import 了 mcp 2.0 已删除的 `RequestContext`，装 2.0 即 ImportError；且 `providers/mcp.py` 本就直接 `from mcp import ClientSession`，不该靠传递依赖。待上游适配 2.0 后再放开。
- **ruff 排除 markdown**：0.16 起 ruff format 会重排 md 内的 Python 代码块，docs 里的示意片段（对齐注释、紧凑字面量）被拆散，`extend-exclude = ["*.md"]` 保持手写排版。

## [0.2.96] - 2026-07-31

### Added
- **`lumi mcp` 命令行**（对齐 `claude mcp` 的命令面）：`add`（stdio 命令或 URL 自动分流，`-e KEY=VALUE` 传密钥、`-H` 传鉴权头、`-t sse` 选 SSE，兼容 claude 风格的 `--` 分隔符）/ `add-json`（README 里的 mcpServers 片段原样照抄）/ `list`（分层展示）/ `get`（合并语义取生效配置）/ `remove`（自动定位所在层，两层同名要求显式 `--scope`）/ `test`（真连一次列出工具清单）。scope 默认项目层（与桌面 MCP 面板一致），与面板读写同一份 `mcp_server.json`，防抹除严格读 + 0600 原子写同源（`mcp_rpc` 的 read/write_servers）。
- **MCP 配置进程外写入热生效**：`lumi mcp` CLI、手改文件这类写入没有 RPC 通知，运行中的会话现在每轮首自查 merged 配置 hash（mtime 缓存，未变零成本），变了即换代重建工具列表——agent 在会话里自己 add 完，下一条消息新工具就出现，不用重启。加载失败的池只在配置真变了时才重试，不会每条消息对着坏 server 重新 spawn。
- **lumi-config 技能**（原 setup-env 改名重构）：SKILL.md 变薄成路由（触发词 + 三篇索引 + 共用约定），执行依赖 / 飞书接入 / MCP 接入各自成篇按需读（`references/env.md|feishu.md|mcp.md`）——新增的 mcp.md 定好作用层怎么选、add→test→生效的全流程和连不上的排查路径，agent 对话内代劳接 MCP。

### Changed
- **MCP 池失效判定单源化**：RPC 作废（`invalidate_mcp_pools`）与轮首自查收敛到 `McpPool.sync_config`，比对恒用 `attempted_hash`（失败也记），唯一策略差异是「是否打断在途加载」一个显式布尔；顺带修正冷池配置未变时的过宽换代。

## [0.2.95] - 2026-07-30

### Fixed
- **飞书体检在中文 Windows 上崩在输出上**：`✓`/`✗` 不在 GBK 字符集里，stdout 被管道接走（desktop 拉起 `lumi serve`、agent 的 shell 跑 `lumi feishu diagnose`）时编码退回 locale，写第一行就 `UnicodeEncodeError`。两层修复：CLI 入口把标准流恒定成 UTF-8（覆盖整个后端进程，dream 的 🌙、审批的 ⚠、cron 的 ✅ 等一并兜住，且与读侧一律按 UTF-8 解码的约定自洽）；记号换成 GBK 字符集内的 `√`/`×`，旧版控制台按 GBK 配字体时不会掉成方框。
- **bash 工具在中文 Windows 上收发全是乱码**：命令按 UTF-8 写进 cmd.exe 的 stdin、输出按 UTF-8 解码，而 cmd 用的是 OEM 代码页（简中 cp936）。改为 shell 起在 `/k chcp 65001>nul` 上并给子进程带 `PYTHONIOENCODING=utf-8`，整条链统一到 UTF-8。
- **Windows 上四处功能根本不通**：定时任务（`fcntl` 是 Unix 专有，`import` 即失败、异常被吞成一条 warning）改用 `msvcrt` 字节区间锁；后台任务超时/取消（`os.killpg` 不存在，且 `start_new_session` 在 Windows 被 CPython 忽略）改用 `taskkill /T /F` 按进程树终止；MCP 子进程清理依赖的 `wmic` 自 Win11 24H2 起默认不装、25H2 升级即移除，改为 PowerShell `Get-CimInstance` 一次取回进程表、本地建树（顺带把逐层 spawn 压成 1 次）；shell hook 的绝对路径校验用 `startswith("/")`，把 `C:\...` 一律判成非法，改用 `os.path.isabs`。
- **模型被告知的 shell 与实际执行的不是同一个**：Windows 上探测到 pwsh 就上报 pwsh，但 bash 工具恒 spawn `cmd.exe`，模型会写出 PowerShell 语法丢进 cmd；改为恒报 `cmd`（顺带省掉每轮两次 `shutil.which` 的 PATH 扫描）。同时 `get_cwd()` 在 Windows 改用不带参数的 `cd`——`pwd` 在 cmd 里不存在，查失败会让后台任务起在模型 `cd` 之前的目录。
- **文件编辑会顺手改写行尾**（不只 Windows）：读取走通用换行、写回用默认 `newline`，导致 Windows 上改一行 LF 文件整份变 CRLF、POSIX 上改 CRLF 文件整份变 LF——一次小改动放大成全文件 diff。改为在原文上替换、只把待匹配串对齐到文件行尾，未命中的部分逐字节不动（混合行尾的文件同样只改命中那段）。
- **桌面端不认 Windows 路径**：`basename` 只按 `/` 切，项目卡片名、新建项目存下的项目名、工具标题里的文件名在 Windows 上全是整条路径；文件预览的 `lumi-file://` 把盘符吃进 host 必然 404。两处判据统一到 `lib/utils` 的一份 `WIN_PATH`（POSIX 下反斜杠仍按合法文件名字符对待）。
- **密钥一键复制的未处理 promise rejection**：`writeText` 在文档失焦 / 权限被拒时是 reject，`void` 只骗过 lint，补上 `.catch`。

### Changed
- **读非 UTF-8 文件不再直接报错**：中文 Windows 上大量 txt/csv/bat 是 GBK，现回落到系统本地编码并在正文前声明（行号不受影响）；`edit`/`write` 保持严格 UTF-8，避免一次编辑悄悄换掉整个文件的编码。二进制文件仍明确拒读——回落编码几乎吃得下任意字节，不挡住会把一屏乱码当文件内容喂给模型。

## [0.2.94] - 2026-07-30

### Added
- **凭证输入框小眼睛 + 一键复制**（demo 定稿 `.demos/api-key-reveal.html` 方案 A）：设置里的三处密钥输入（模型凭证 api_key / 远程机器 token / 飞书 app_secret）统一换用新 `SecretInput`——右侧常驻小眼睛切换明文/密文、复制按钮一键拷贝，复制成功金色对勾 + 「已复制」1.5 秒；反馈门控在剪贴板写入成功之后，失败不假装成功。
- **MCP 设置默认落项目作用域**：进面板即「项目」+ 自动选中默认项目（未设默认则取最近使用的），不再需要「切到项目→再挑一次项目」两步；「新建项目」失败改为 toast 报错，不再无声。

### Fixed
- **设置面板断线期间加载失败后永不重试**：各面板的取数 effect 在 `MachineScope` 之上，机器没连上时请求立即被拒且无人重跑，面板会钉死在空态直到手动换机器。新增 `useConnectedEffect` 让加载只在已连接时跑、重连成功自动重拉，供应商 / MCP / 环境三页接入。

## [0.2.93] - 2026-07-30

### Fixed
- **「一键装齐」进度不再串行显示同一个工具**：此前装齐的进度事件 target 恒为 `all`，三行缺失工具共用一条进度——装 uv 时 ripgrep、Node.js 行也写着「下载 uv」。现在装齐逐工具安装、进度按工具名下发，各行各显示各的：还没轮到的行停留在「准备…」排队态，装完的行定格「完成 100%」，全部结束后统一翻成徽章。单独安装某一行的行为不变。

## [0.2.92] - 2026-07-30

### Changed
- **聊天区正文比 UI 大一号**：聊天流（用户气泡 + 助手 Markdown）字号改为「设置→字号 + 1px」（默认 14px），跟随字号设置联动；流内工具卡、思考块等保持原密度不受影响。
- 项目主页提示词空态文案精简为「未配置」，去掉括号里的补充说明（中英同步）。

## [0.2.91] - 2026-07-30

### Changed
- **设置页实体列表统一为一套 EntityCard 语法**（demo 定稿 `.demos/settings-unify.html`）：渠道 / MCP / 模型供应商 / 远程机器四个面板的"一行一个实体"共用同一解剖——36px lucide 图标 chip、标题行（名称自动截断 + Pill 标签 + 6px 状态点）、副题（mono/错误原因就地替换，截断时悬停看全文）、操作图标 hover 浮现（未悬停时不可命中，卡片右缘不再藏隐形删除键）、Switch 恒在最右竖向对齐。此前四个面板四套写法：状态点 6/8/10px 漂移、操作常显/悬停混用、供应商卡没图标、远程机器是裸行。配套统一：空态一律虚线框（`Empty`）、「添加」恒在分区标题右上、徽章胶囊归一族（`Pill`：金点=Lumi 托管 / 蓝点=系统 / 虚线=缺失）、卡壳描边填充单 token（`cardShell`）、状态点单组件（`StatusDot`，机器色/呼吸/空心全走它，侧栏机器点同源）、环境面板 emoji 图标换 lucide 线性。
- **飞书配置表单重组为中性分组卡**（demo 定稿 `.demos/feishu-form-hierarchy.html`）：应用凭证（含绑定项目）/ 接入体检 / 消息行为 / 会话运行时 / 妙记 / Dream 六组同一卡片语法（图标头行 + hairline + 内容），妙记与 Dream 的开关在头行右侧、开启即卡内展开；原先 info 蓝底、金底、emoji 图标混排全部退场，层次靠结构不靠颜色。
- **体检面板改「整卡开合」**：详情装回卡片内部——头行整行可点、chevron 旋转指示开合（带 `aria-expanded`）、卡顶 2px 状态色线替代整条彩底横幅、收起时缩略点阵一眼看到每项检查的红绿；「查看检查详情」悬空按钮删除。收起态详情 `inert`，隐形的「一键安装」按钮不再留在 Tab 序里；「重新检查」恢复为真按钮，键盘可达。

## [0.2.90] - 2026-07-29

### Added
- **`lumi feishu` CLI**：`config`（key=value 读写渠道配置，`app_secret=-` 走 stdin 不留 shell 历史，显示与写入语法可往返）、`diagnose`（接入体检三组并行跑：本地环境 / 凭证权限事件发布 / 妙记四项——妙记启用时才追加；任一 error 退出码非零）、`sync-skills`（飞书技能包导出到绑定项目）。与 desktop 渠道页读写同一份数据，供 agent 在对话里代劳接入。
- **channels 配置热重载**：`lumi feishu config` 等进程外写入没有 RPC 通道，serve 侧新增 `watch_store` 轮询（3 秒查 mtime + 与最后应用的配置比对，内容真变了才 reload）——CLI 写完几秒内生效不用重启；lumi.json 其他分区的写入不会误弹飞书长连接；单轮失败不退出、不吞变更。
- **setup-env 技能**（default 风格内置）：面向零基础用户的环境安装引导（uv / node / rg 体检-征得同意-逐个安装-复检），渐进式披露 `references/feishu.md` 承载飞书全程接入剧本——用户只做代劳不了的三件事（粘凭证 / 开放平台点批准发布 / 妙记设备码授权），其余 agent 跑命令；含 lark-cli 双侧配置（Lumi + `lark-cli config init`）、体检循环（可选权限也引导开通）、妙记设备码两段式（恒发 `verification_url` 链接不发二维码，明确压制 lark-cli 输出里要求展示二维码的 `hint` 指令）。

### Fixed
- **Docker 容器里装不上 lark-cli**：`@larksuite/cli` 的 postinstall 用系统 `curl` 下载真实二进制（无 wget/Node 兜底），而镜像基于 `python:3.12-slim` 没有 curl——失败时包打印的还是「配代理/公司镜像」的网络受限文案，把人引去查网络。Dockerfile 补装 curl；toolbox 安装失败命中 `curl ENOENT` 时直接报「系统缺 curl」，不再透传误导原文。

## [0.2.89] - 2026-07-29

### Added
- **机器没连上时，设置里那台机器的配置不再可读可改**：设置的 供应商 / 渠道 / MCP / 环境 四页与项目页、定时页共用 `MachineScope`——内容只在连上时渲染，否则换成同尺寸占位（正在连接：光点呼吸、不给按钮；已停止重试：WifiOff + 原因 +「重新连接」）。此前选中一台连不上的机器，面板拉不到数据就静默清空，飞书卡片照样渲染成「未启用」、点编辑能填能存，保存请求发不出去还被静默吞掉。机器选择条的 pill 补上连接状态点（机器色实心=已连接 / 呼吸=连接中 / 空心=离线），与侧栏机器分组头同一套语言。
- **「连接」列表把失败原因就地写在机器行上**：连不上时副标题从地址换成人话（令牌无效，连接被拒绝 / 连不上，请检查地址与网络），地址退到悬停可见；显示的是实时连接态而非某次测试的快照，机器自愈后红字自己消失。

### Fixed
- **「测试连接」不填令牌也报成功**：服务端是「先 accept 再校验 token」（accept 前 close 客户端只见 1006，分不清鉴权失败与不可达），而前端在 `onopen` 就判成功，随后的 1008 被丢掉——token 空着、填错、填对，一律「连接成功」。改为取「收到服务端首帧」与「open 后 1.5 秒没被 close」之先：既不会被 open 抢答，也不会把 bridge 冷启动慢的健康机器误报成超时。
- **点「重新连接」界面闪烁**：离线判定只认 `closed/failed`，重连瞬间进 `connecting` 就把空面板渲染出来，失败后又切回占位——Gateway 退避重试 5 轮，点一次闪五次。改为内容只在 `open` 时渲染，未连上时占位框始终在原地只换文案。
- **删掉/停用当前选中的机器后，那一页永久卡死**：机器的连接态记录被一并清掉，界面据此永远显示「正在连接」，而 pill 又因为只剩一台被整条隐藏，没有任何出口。现在自动落回第一台可用机器。
- **瞬断会吃掉正在输入的凭证**：飞书配置弹窗、MCP 服务器表单原本在作用域内，服务端重启/笔记本唤醒导致的一次断连就会把它们连同已填内容卸载——正是渠道页当初加 `forceMount` 要防的事。弹窗移到作用域外。
- **开关（Switch）看着是歪的**：轨道 32×18.4（`h-[1.15rem]`）里塞 16px 滑块，上下只剩 1.2px 的小数余量，落到设备像素上取整后一边贴边一边露白。几何全改整数（外框不变，滑块四周恒 2px、行程 14px 正好到另一端）。
- **飞书绑定项目改为必选、无兜底**：未绑定时保存按钮禁用（列表开关打开改为弹配置引导绑定），后端拒绝启用态落盘、channel 直接不连并把原因报到状态灯，技能包体检也不再退回全局层安装——此前留空会让飞书在 serve 进程当前目录里读写文件、装技能包。内联「新建项目」登记失败也不再无声。
- **Docker 镜像装出本地从未跑过的依赖组合**：`uv pip install "."` 不带锁重新解析，这次解到 `mcp 2.0` 而 `langchain-mcp-adapters` 还在 import 1.x 的符号，容器启动即 ImportError 重启循环（`docker compose down && up` 重来多少次都一样）。改为 `uv sync --frozen` 装 `uv.lock` 那套版本。

### Changed
- Docker 依赖安装分两段（`--no-install-project` 先装依赖再拷代码），改一行代码不再重下重装近百个包；实测依赖层命中缓存、只跑 9 秒的项目安装。
- 机器表与连接态改由 context 一次性下发：六个面板不再各自透传 `machines`，侧栏的状态点/重连按钮与设置页共用同一份实现（此前是逐字复制），`Machine`/`ConnState`/`ConnError` 收进 `types.ts`。
- 渠道页 3 秒轮询在机器没连上时停掉（此前每 3 秒重建空数组、把整个面板连同离线占位重渲一遍）。

### Tests
- 飞书绑定项目必选三条锁定用例：未绑定时 channel 拒绝启动并报原因、`save_feishu` 只在启用态卡校验（老配置仍可关掉）、体检报「未绑定项目」且不给一键安装。

## [0.2.88] - 2026-07-29

### Added
- **`lumi env` 命令行入口**：`lumi env status` 列出核心工具链（uv / node / rg）各自来源与版本，`lumi env install [uv|node|rg]` 装缺失项。与桌面「设置 → 环境」同一套 toolbox 实现，供无 GUI 的 serve 机器使用，也让 agent 能在会话里自助把环境装齐——打包版后端不在 PATH 上，故 serve/headless 启动时把自身可执行入口导出为 `LUMI_BIN` 供子进程回调。

### Fixed
- **工具箱位置会跟着启动目录漂移**：桌面端装好的工具，在带 `.lumi/` 的项目里跑命令行却报「缺失」。根因是 `bin_dir` 由配置目录派生，而配置目录走 cwd 发现链——同一台机器于是有了好几个工具箱，用户重装第二份到谁也不用的地方。工具箱改为**机器级**（`LumiConfig.toolbox_dir`：显式 `--config-dir` / `LUMI_CONFIG_DIR` 优先，否则恒 `~/.lumi`），配置层本身仍按发现链走。此前 headless 运行（`lumi -p`）从未做过任何兜底，是这条链上最先受害的入口。
- **lark-cli 装不上且看不出原因**（Windows 尤甚）：npm 安装成功后，接入工具箱的链接目标是按「npm 全局 prefix = 工具箱 node 树」硬拼的，而 prefix 可被用户级 `.npmrc` 改掉（Windows 常指到 `%APPDATA%\npm`）——于是建出一个探测得到、一跑就报「找不到路径」的幽灵 shim：体检显示 lark-cli 已安装，技能包同步与妙记取数却全部静默失败。改为向 `npm prefix -g` 问真实位置并在链接前校验存在。
- 同一处的失败原因也一直被吞：npm 失败后静默降级去 GitHub 直下二进制，而降级分支的 `next(...)` 抛的是 `StopIteration`，UI 上只剩一句「安装失败:」后面空白。现在只走 npm（它本就是 lark-cli 的官方分发渠道），失败带出 npm 原文。
- **单项安装不跳过已装的工具**：`install()` 是无条件覆盖，系统已有 Node 时逐项安装会白下几十 MB，并在工具箱留一份 PATH 上永远轮不到的影子副本。「已装的跳过」下沉到 `install_missing`，命令行与桌面按钮共用，探测阶段顺带改为并行。
- **面板显示的路径可能说谎**：机器不可达或请求失败时只清了列表、没清路径，文案会变成「凭证存该机器的 <上一台机器的路径>」。
- `load_skills/load_agents(directory=...)` 的语义与 `change_detector` 的 `explicit_dir`（「只扫这一个目录」）不符——它只覆盖全局层，风格内置层照样合并进来。

### Changed
- **缺 npm 时不再在渠道页代装 Node.js**：接入体检那一行改为把用户引到「设置 → 环境」（新增 `Check.fix_nav`，跳转带上体检所在的机器）。核心工具链的安装入口只保留环境页一个，不在渠道页复制第二份。
- **配置文件路径由后端下发真值**（`get_channels.config_path` / `list_mcp_servers.path` / `env_status.bin_dir` / `env.state.bin_dir`），面板不再前端硬拼 `~/.lumi/…`——非技术用户看不懂，且 `--config-dir` 时会说谎。
- 设置弹窗的渠道面板改为常驻挂载（跳去环境页装 Node 再回来，编辑中的飞书凭证不会丢），取数与 3 秒轮询以「本 tab 是否可见」为门。

### Tests
- 新增 `lumi env` 命令行用例（状态呈现 / 未知工具 / 失败退出码 / 已装跳过 / 只导出 `LUMI_BIN`）与配置层的「工具箱是机器级」两条；lark-cli 安装补齐缺 npm、npm 报错、链接目标取自 `npm prefix -g`、产物不存在四种情形。
- `LumiConfig` 单例重置移入 `tests/conftest.py` 的 autouse 家族——此前任何用例取过某个配置目录，实例就留在进程里，后续用例的 `config_dir` / `bin_dir` / `config_layers` 全跟着它漂。

## [0.2.87] - 2026-07-28

### Fixed
- **Windows 右上角三键间歇性点不动（重启才恢复）** — Windows 客户反馈最小化/最大化/关闭偶发失灵但应用内部一切正常。根因：无边框窗口的自绘三键靠 `-webkit-app-region` 的 no-drag 命中矩形工作，页面缩放（Ctrl+±，且缩放级别会被持久化）或 DPI 变化后矩形过期失效（上游 electron#41695 家族），点击被当成标题拖拽、根本进不了页面。治本：Windows 改用系统原生 WCO overlay（`titleBarStyle: 'hidden'` + `titleBarOverlay`），三键由 OS 直接绘制与命中，天然免疫，顺带拿回 Win11 snap layouts 悬浮菜单。原生按钮底色/图标色随亮暗主题实时同步（theme.ts 在主题生效点推色值）；标题栏高度改 `env(titlebar-area-height)` 与 overlay 恒等，任何缩放下不错位。Linux 维持原自绘按钮不变。
- **在非激活供应商连接上填的限额覆盖被静默忽略** — 同名模型存在于多个 profile（如同一模型走两个代理）时，运行时只按模型名反查且恒偏向激活连接：在另一个连接的表单里填的上下文/输出覆盖，界面回显「已覆盖」但压缩阈值与 max_tokens 实际取的是激活连接的值，无任何报错。修复：(连接, 模型) 作为完整身份贯通运行时——`resolve()`/`create_llm`/`tool_call_chain`/`run_summary` 均可带 provider 精确定位 profile（连接/思考档位/限额三者一并归位，渠道会话早已存在的连接与档位串味同样治愈）；`LumiAgentContext` 携带 provider（desktop 从 active 指针带出，渠道会话存进 `ChannelRuntimeConfig.provider`，换 profile 也会触发会话池重建）；渠道设置的模型下拉选中态与保存改按 (连接, 模型) 判定，同名模型不再两处打勾。老渠道配置（无 provider）行为不变，重选一次模型自动补上归属。

### Tests
- 新增 `test_resolve_with_provider_pins_profile` 锁住：旧按名反查路径丢覆盖（bug 现场）、带 provider 时连接+覆盖+归属全部来自指定 profile、provider 失效回退按名反查不炸。

## [0.2.86] - 2026-07-28

### Added
- **按模型配置上下文窗口与单次输出上限**（设置 → 模型 → 编辑供应商 → 模型行的滑杆图标展开）。留空 = 跟随 models.dev 探测值（占位符即探测到的数），只有模型被代理改了名、或目录里查不到时才需要手填。输入框下方一行说明当前取的是哪一层的值（`自动探测：N` / `已覆盖 · 清空恢复自动` / `未探测到 · 兜底 N`）；填得比探测值大会就地警示——上下文填大了会该压不压直接撞超长，输出上限填超模型真实上限会被服务端拒绝。
- 取值链收口在 `provider_store.limits()` 单一实现：**用户覆盖 > models.dev 探测 > 兜底常量**。`ResolvedModel` 随之带出 `context_window` / `max_tokens`，四个消费者（主对话链输出上限、压缩阈值分母、`/goal` 判官转录预算、桌面上下文环）共用同一口径——界面上显示的窗口就是后台实际压缩用的分母。

### Changed
- **模型单次输出上限不再是全局 8192，改为按模型取真实上限**（`models.dev` 的 `limit.output`，如 claude-sonnet-4-6 = 64000、qwen3.7-plus = 65536）。`agents.max_tokens` 降级为**兜底**值，仅在既无用户覆盖、目录也未收录该模型时生效。此前所有模型一律 8192，写长文档（合同、报告、Word 文稿）时模型的 tool_call 参数会在中途被服务端截断：截断点落在参数名之前表现为 `content: Field required` 报错，落在字符串中间则更糟——半截 JSON 被 LangChain 的 `parse_partial_json` 补全成合法参数，`write` 照常执行并回报「成功」，**静默写出残缺文件**。
  - 已知代价：`catalog.lookup` 为支持代理改名的模型（`aidong-claude-sonnet-4-6` 这类）用了模糊匹配，可能命中输出上限更大的另一个模型，此时请求会被服务端以 400 拒绝（4xx 不在重试白名单内）。出路是在设置里给该模型手填一个正确的上限。
- `agents.max_tokens` 的 `Field.description` 与 `docs/guides/config.md`、`docs/user-manual.md`（「内存不足」调优建议）一并改写，不再把它描述为「模型输出最大 token 数」。
- wire 协议 `list_providers`：`profile.context` 的含义由「生效窗口」改为「用户覆盖值」，生效值移到新增的 `profile.context_window`，另加 `profile.probe`（探测值）与顶层 `fallback`（后端真正使用的兜底值，避免前端硬编码）。改名的动机是 `context` 此前在读/写两个方向上含义不同，`save_provider` 回传会串味；现在读写同名同义，列表结果可原样回填表单。

### Tests
- 锁住覆盖值生效、清空即恢复自动、非法值（0 / 负数 / 字符串 / `None` / 不存在的模型 / 非 dict）一律不落盘、wire 往返与 `fallback` 下发。
- 新增 `conftest.catalog_entry()` / `resolved()` 两个共享构造器，替换掉 `test_gateway_session` 里三个 `SimpleNamespace` 假目录条目——鸭子类型的假条目在 `ModelEntry` 新增字段时会静默通过，直到消费方读到不存在的属性才炸。`test_provider_store` 的限制用例改用钉住的目录条目，消除对本机 `~/.lumi/cache` 是否存在的依赖（此前在空 HOME 下会红）。

## [0.2.85] - 2026-07-28

### Fixed
- **`/goal` 判官在 1M 模型上只看得到尾部约 160K 转录** — 与 0.2.84 压缩阈值同类：判官转录预算的分母写死 `token.context_length`（默认 200000），而判官实际跑的是会话 active 模型（`resolve()`）。1M 窗口下预算恒为 `200000 × 0.8`，长会话里较早的证据被截头丢掉——判官按 default-deny 判「证据不足」，目标明明已达成仍反复拦截结束。改为分母取判官所跑模型的真实窗口（`catalog.context_window(resolve().model)`，与压缩阈值/上下文环同源），目录未收录才退回 `token.context_length` 兜底。

### Tests
- 新增用例锁住判官预算分母来源（1M 窗口下静态预算本会截断的转录不再截断）；截断类用例收敛到 `_pin_budget()` 帮手，同时钉住 `resolve`/`context_window`，消除对本机 `~/.lumi`（providers.json / catalog 缓存）的隐性依赖。两个变异体（分母退回静态配置、砍掉 `or` 兜底）均验证可被杀掉。

## [0.2.84] - 2026-07-28

### Fixed
- **自动压缩来得太早：1M 窗口的模型用量刚过 14% 就被压掉** — 压缩阈值的分母写死成 `token.context_length`（默认 200000），而界面上「上下文用量」环的分母走的是 models.dev 目录里该模型的真实窗口（如 `qwen3.8-max-preview` = 1000000）。两者不同源，于是阈值恒为 `200000 × 0.7 = 140000`，在 1M 模型上只相当于 14%——用户看到用量 6%~14% 就莫名触发压缩、丢历史。改为分母取会话**实际所跑模型**的窗口（`catalog.context_window(model_name)`，与上下文环同源），目录未收录的模型才退回 `token.context_length` 兜底。同一个模型下阈值从 140K 提到 700K。
- 顺带把两处重复的窗口查目录代码（`bridge/providers.py` 的私有 `context_of`、`gateway/session.py` 的两处 `lookup(...).context_length`）收敛到新增的 `catalog.context_window()`：压缩阈值、桌面上下文环、渠道旁观会话快照三个消费者现在读同一个函数。

### Changed
- `token.context_length` 的语义收窄并在 `Field.description` 与文档中写明：**工具结果大小上限恒以它为基准**（`once_tool_max_bytes` / `round_tool_max_bytes` 不随模型窗口放大——`_PTL_KEEP_TAIL_ROUNDS=2` 的保尾估算依赖它是绝对界），压缩阈值只在目录查不到模型时才退回它。`docs/architecture/summary.md`、`docs/user-manual.md` 中「减少上下文长度」的调优建议一并纠正。
- dream 整理提示词措辞微调（「顺手把」→「同时将」）。

### Tests
- 新增 3 个参数化用例锁住阈值分母来源（1M 模型用量 30% 不压 / 过 70% 才压 / 目录未收录退回 200K 分母）；两个变异体（分母退回静态配置、砍掉 `or` 兜底分支）均能被杀掉。
- `test_compact.py` 两个 summarizer 用例的 fixture 收敛为 `_pending_human_history()` / `_summarizer_env()`，token 段改用真实 `TokenConfig` 而非手搓 `SimpleNamespace`——以后给 `TokenConfig` 加字段不会因 stub 缺字段而假绿。
- `test_full_graph_ptl_roundtrip` 钉住 `context_window`，消除该用例对本机 `~/.lumi/cache` 目录内容的隐性依赖。

## [0.2.83] - 2026-07-27

### Fixed
- **中文 Windows 上飞书体检误报「lark-cli 不支持 skills 子命令，请先升级」** — `subprocess.run(text=True)` 按**系统 locale** 解码子进程输出，中文 Windows 是 cp936，而 `lark-cli skills list` 吐的是大段中文 UTF-8 JSON，一撞就 `UnicodeDecodeError`，被 `except` 吞掉后与「旧版 cli 不认这个子命令」不可区分。客户机上 cli 是好的（v1.0.77），`npm update` 修不掉。同卡上「已安装 v1.0.77」是绿的正是佐证：`--version` 输出纯 ASCII 解得开。收子进程输出改为显式 `encoding="utf-8", errors="replace"`；技能包导出（`skills read` 拿的是中文 SKILL.md）此前即便侥幸不报错也会写成乱码，一并修好。
- **同机妙记体检「lark-cli 已安装」与「不在 PATH」同时出现** — 检测走 `shutil.which`（遍历 PATHEXT，命中 npm 装出的 `lark-cli.cmd`），执行却传裸名 `"lark-cli"`，而 Windows 的 `CreateProcess` 对裸名只补 `.exe`，于是必然 `FileNotFoundError`。改为执行 which 解析出的完整路径。
- **Windows 装 rg 从来没成功过（checksum 恒不匹配）** — ripgrep 的 Windows 产物 checksum 由 CertUtil 生成，是三行格式、哈希在第二行，而解析用的是 `text.split()[0]`，取到的是字符串 `"SHA256"`。报错还引向「可重试 / 可设 https_proxy」，而这两条都无济于事。改取首个 64 位十六进制串，三种发布方排版通吃（另两种为 `<hash>  <file>` 与 uv Windows 的 `<hash> *<file>`）。

### Changed
- `lark_skill_versions` 与 `_fetch_checksum` 的失败分支补 warning 日志：前者此前把真实原因整个丢弃，体检 UI 只剩「请先升级」这句猜测；后者在「拿到响应却挑不出哈希」时会静默跳过完整性校验（旧代码此路径是报错中止），不留痕等于校验形同虚设。
- `toolbox._version_of` 改走 `_run`，模块内同一段 UTF-8 子进程调用由三份收敛为两份。

## [0.2.82] - 2026-07-27

### Fixed
- **历史压缩会删掉「模型正在回答的那句提问」** — CallModel 撞 prompt-too-long 触发的强制压缩多发生在工具循环中段，而 `split_into_rounds` 把 Human 并入**前一个** AI 的 round，工具循环长于保尾的 2 个 round 时那条提问必然落在待摘要一侧、被无差别删除。压缩后模型看到的是 `[System, Human(<summary>), AI(tool_use), Tool, …]`——还要跑完这个工具循环并作答，却已经看不到原始诉求，只剩摘要模型的转述。现在三条压缩路径都经 `find_pending_human` 认出「已发出、还没被回答完」的真人消息并原样重挂在摘要 carrier 之后（换新 id 才排得过去：`add_messages` 对「Remove + 同 id 重加」是原地更新）。内置 `SUMMARY.md` 并没有「原文列出所有 user message」这类要求，此前唯一的缓解是摘要模型自觉，不是结构性保证。
- **「已答完」的判定被 reminder 拉回骗过** — 结构化输出未按格式 / Stop hook remind 会在无 `tool_calls` 的 AI 之后追加 reminder 再回 CallModel，此时那条 AI 并非终态、用户诉求仍未被回答。倒扫若在它处停下就会返回「无待答提问」，提问照样被压掉——正是上一条要防的失败。`find_pending_human` 据 `is_hook_reminder` 图语义标记识别拉回。
- **压缩后会话对 dream 隐身** — dream 判活是「存在落库 ts 晚于 `dreamed_at` 的真实 human」，而压缩把真人消息全删、摘要 carrier 不带 ts，于是 `latest_human_ts` 恒返回 `0.0`、`0.0 <= dreamed_at` 恒真，该 thread 从此不再被整理进记忆，直到有新的真人消息进来。定时链路上因「先全部 dream → 屏障 → 再压缩」而无害，但手动 `/compact`（IM 亦可用）与 PTL 强制压缩都不在这个次序里：用户当天最后一条消息撞了 PTL，当晚 dream 就跳过，那天的对话不进记忆。现在 carrier 继承被删真人消息的最新 ts 作水位——那条消息确实在该时刻存在过；`latest_human_ts` 的判据随之收成「human 且带 ts」（ts 只由 bridge 构造真实用户消息时写，其余合成消息一律不带）。
- **`ctx_digest` marker 可能在压缩后幸存** — 不变量是「marker 存在 ⟺ 从上次全量注入起的完整 diff 链在上下文中可见」，此前靠「压缩把带 marker 的消息整体删除」维持。但 PTL 保尾的 round 里若含带 marker 的 Human（`select_for_ptl_compaction` 的既有形态之一），旧代码 `model_copy(update={"id": ...})` 原样保留 `additional_kwargs`，marker 活着而基线块已被删——模型此后拿不到全量重建，环境 / 技能 / 记忆索引停在一个自己看不见的状态上。规则收紧为「压缩后不得有 marker 幸存」（`compact._reattach`）。对应的旧注入块刻意留在 content 里：`injected_prefix` 计数与 `<attached-file>` 标签块共用，按计数剥会连用户附件路径一起丢，多留一个陈旧块只是噪音。
- **离线 `/compact` 漏掉最后一句助手回复** — `select_for_compaction` 把末条干净 AIMessage 排除在 `to_summarize` 之外却照样删除，那句回复既不在历史也不在摘要里。现在整段 body 进摘要。
- **末条是「发了没等到回答」的用户消息时压不动** — turn 中途崩掉 / 进程被杀 / 用户发完就断连留下的形态被结构性守卫挡在门外，这类会话可能已经很大却压不了，IM 每日整理静默跳过。放行该形态（它同样不会留下半截工具轮），原话由上面的重挂机制保住。
- **残留 tool_use 让离线压缩的摘要调用 400** — 上一轮工具执行中途被取消后用户又发了新消息时，历史里的 `AIMessage(tool_use)` 没有配对 `ToolMessage`，直发 provider 即 400（`/compact` 报错、IM 每日整理每轮复现）。剔除移进 `run_summary`——三条压缩路径的唯一入口，此前只有走 `nodes._summarize` 的在线 / PTL 两条有此保护。
- **删掉默认项目后永久没有默认** — `remove_project` 不重新指派，剩余条目都带着 `default: False`，`list_projects` 的回填哨兵（无任何条目带该键）不认，于是每次新建会话都退回阻断式项目选择器且无从察觉。现在删掉的若正是默认项目，把剩下最近使用的顶上来。
- **老数据回填哨兵会被一次 `add_project` 熄灭** — 非首个项目此前写 `default: False`，老用户（条目全无该键）只要先加一个项目就把哨兵灭了，从此再也等不到 v0.2.81 的回填。该键的语义是「此条目对默认表过态」，顺手写 `False` 等于替用户表了态——非首个项目不再写该键，`list_projects` 与 `add_project` 并发交错时也能自愈。

### Changed
- 在线 / PTL / 离线三条压缩路径的消息重写收敛到 `compact.build_compacted_update(removed, keep_tail, summary)`：删整段 → 摘要 carrier → 重挂原文，「待答提问必须保住」「重挂换新 id 并剥 marker」「carrier 继承 ts」三条规则各只写一处，调用点只声明要保留的尾部。`AgentBridge.compact_thread` 随之不再判断消息形态。

## [0.2.81] - 2026-07-27

### Added
- **首个项目自动成为默认项目** — 清单原本为空时 `add_project` 直接给新条目标 `default`，新用户建完项目就能开聊，不必再去项目卡「⋯」菜单里指定一次（v0.2.47 起「新建会话」在没有默认项目时是阻断式跳项目选择器）。
- **老数据一次性回填默认项目** — 清单非空但**所有条目都不含 `default` 键**（= 该机制上线前登记、从没碰过它）时，`list_projects` 把最近使用的项目回填为默认并落盘，复用 `set_default_project` 以继承「至多一个默认」的既有语义。判据刻意取「有没有这个键」而非「值真不真」：主动取消过默认的人条目上是 `default: False`，带键即不回填，用户意愿不会被这次迁移覆盖。

## [0.2.80] - 2026-07-27

### Fixed
- **桌面端右键全程无反应** — 输入框与回复正文右键都弹不出菜单，复制粘贴只能靠键盘。根因是 Electron 默认不提供右键菜单，而 `electron/main.cjs` 从未监听 `context-menu` 事件（编辑类命令此前只经隐藏原生菜单的 role 走键盘快捷键）。现按右键位置构建原生菜单：可编辑区给撤销 / 重做 / 剪切 / 复制 / 粘贴 / 全选（禁用态跟随 `params.editFlags`，无可撤销时 Undo 自动置灰），只读正文选中后给复制，链接给复制链接；什么都做不了就不弹空菜单。装在 `createWindow` 内 per-window——全仓只有一处 `new BrowserWindow`、无 webview，改 `app.on('web-contents-created')` 是为不存在的第二窗口买单。

### Added
- **原生菜单文案纳入 i18n**（`menu.undo` / `redo` / `cut` / `paste` / `selectAll` / `copyLink`，复制项复用既有 `common.copy`）。菜单在主进程构建但**主进程不留平行词表**：renderer 在语言 effect 里把翻好的 label 对象经 `lumi:menu-labels` 推过去，新增语言只改 `i18n.ts` 一处。方向只能是 renderer → main：真相源是 renderer 的 localStorage（主进程读不到），且实测 Electron 43 的 role 自带 label 是硬编码英文、不随系统语言变（`menu-item-roles` 里无 locale 查表），"不给 label 让它自己本地化"这条路不存在。

## [0.2.79] - 2026-07-27

### Fixed
- **Linux 包在 Ubuntu 22.04 等旧发行版上起不来** — 症状是 AppImage 里后端秒退循环重启、Electron 那半正常：`Failed to load Python shared library ... libpython3.12.so.1.0: /lib/x86_64-linux-gnu/libm.so.6: version 'GLIBC_2.38' not found`。Linux 构建机从 `ubuntu-24.04`（glibc 2.39）退回 `ubuntu-22.04`（2.35），x64 与 arm64 同改。

  病灶有两层，只堵一层不够：**一是 libpython**——PyInstaller 会把它所用解释器的 libpython 打进产物，而 uv 的默认 `python-preference = managed` 实际语义是「已装的 managed > 系统 > 下载新的 managed」，ubuntu-24.04 自带满足 `requires-python>=3.12` 的 `/usr/bin/python3.12`（deb 包，链 glibc 2.38），uv 就直接捡了它；打包命令加 `--managed-python --python 3.12` 强制走 python-build-standalone（实测其 libpython 最高只引用 GLIBC_2.17），顺带让三平台不再各捡各的（deb / python.org framework / hostedtoolcache），产物不随 runner 预装什么而漂移。**二是系统共享库**——后端依赖到的 libssl / liblzma 等同样是从构建机复制进 onedir 的，换掉 libpython 后水位仍停在 2.38（v0.2.78 实测），故 Linux 只能在承诺支持的最旧发行版上构建。该 label 供应到 2027-04-17，届时需改为在低基线容器（`container: ubuntu:22.04`）里打后端，而不是简单升 runner。

### Added
- **构建期 glibc 基线断言**（`Assert glibc baseline`）— 扫 Linux 产物内全部 ELF 的 GLIBC 版本符号，最高值超过 2.35（Ubuntu 22.04）即让构建失败，并列出触及该水位的文件。这类回归的要害是 CI 恒绿：构建机 glibc 永远比目标系统新，抬高下限在云端毫无症状，只有旧发行版用户会撞——v0.2.78 就是被这道断言拦住才没再发一个装不上的包。扫不到符号（产物结构变动导致检查失效）同样判失败，不静默放过。

### Note
- v0.2.78 的 Release 已撤：Linux job 被上述断言拦下 → 无 Linux 产物，且 `merge-update-metadata`（needs: build）随之 skipped，导致 `latest-mac.yml` 停在两个 mac job 互相覆盖后的状态、只列 x64，Apple Silicon 的应用内更新会降级到 x64 包。

## [0.2.77] - 2026-07-26

### Changed
- 右栏模块卡（执行记录 / 后台任务）改用专用圆角 `rounded-card`（新增 `--radius-card: 18px`）——卡只有 ~260 宽、90~290 高，套面板档的 12px 弧太短、观感接近直角；左侧栏那种整块高面板仍走 12px，大面板上 12px 已看得清，跟着加大会显胖。

## [0.2.76] - 2026-07-26

### Fixed
- **命令自足的 stdio MCP server 连不上** — 配置里没写 `args`（命令本身无参数可传）时，langchain adapter 的 `create_session` 直接抛「'args' parameter is required for stdio connection」。加载侧归一化补空列表，与既有的 `transport` 缺省推断同处一函数（会话池与连接测试共用，两路行为恒一致）；HTTP 条目不补，adapter 的 `**params` 全透传、混入未知键会 TypeError。

## [0.2.75] - 2026-07-26

### Fixed
- **thinking-only 模型（如 `qwen3.8-max-preview`）下 auto 审批分类器必然 400** — 症状是 `[AutoClassify] 分类器调用失败，fail-closed 转人工审批: The value of the enable_thinking parameter is restricted to True`，即 auto 模式实际退化成人工审批。实测该模型两条路全堵：思考关不掉（DashScope 限定 `enable_thinking=True`），而思考模式下又拒绝强制 `tool_choice`（`required`/指定工具一律 400）——`structured_output` 恒走 `force_no_thinking` + `with_structured_output(function_calling)` 默认注入强制 `tool_choice`，两个前提同时踩中。现在这类模型的 `effort_params(..., "off")` 返 `{}`（不再注入端点会拒的参数），且 `structured_output` 覆盖 `tool_choice` 为 `auto` 降级软引导（照旧绑定同一 schema 工具、只是不强制，实测模型仍会调用）；软引导下模型若改用散文回答，链尾显式抛错而不是把 `None` 交给调用方（分类器据此 fail-closed，titler / 判官照常上抛）。同链的 titler 与 goal 判官一并修好。
- **models.dev 同名条目择优丢掉了 toggle 能力** — 各 provider 只上报自己暴露的控制项，`qwen3.7-plus` 在一处只报 effort 档位、另一处只报 toggle，择优取了前者即误判为「思考关不掉」——这正是上一版不得不写「qwen 的 off 一律硬注入 `enable_thinking=false`」的原因，而该硬注入在真正关不掉思考的模型上就是 400。

### Changed
- `ModelEntry` 新增 `toggle_anywhere`（任一 provider 报过 toggle = 该模型存在关思考的通道），是「能否关思考」的唯一判定依据。刻意**不**并入 `has_toggle`：并集若参与 `allowed_levels`，61 个 openai 协议 effort 模型（`o4-mini` / `gemini-2.5-flash` / `grok-4.3` 等，某些聚合 provider 报了 toggle）会凭空多出 Off 档，且内部链会对它们注入 DeepSeek 系的 `thinking.type` → 端点不认即 400。档位枚举仍只看择优条目自身，跨 provider 混搭档位也一并排除（否则 `claude-opus-4.6` 会丢 `xhigh` 又混入 `none`）。全量 2751 个模型逐一比对：`allowed_levels` 零变化，仅 12 个 qwen 的 off 行为变化且方向正确。
- 新增 `manager.rejects_forced_tool_choice()`（端点怪癖 → 链构造策略）与 `_is_qwen_dialect()`，收掉散在三处的 `"qwen" in model_name.lower()`；`manager` 模块 docstring 写下边界规则——端点方言与怪癖归 `manager`，`catalog` 只放 models.dev 的能力数据。

## [0.2.74] - 2026-07-26

### Added
- **后台任务卡片：从一行路径到看得懂的进度** — 右栏后台任务卡片此前展开后最实的内容是一行输出文件裸路径，看不出任务在做什么、也看不到结果。现在每张卡回答两个问题：*它在做什么*（agent 显示交给它的 prompt、workflow 显示工具 `description`、bash 显示完整命令，统一 clamp 三行、超出可「展开全部」看全文）与*它跑出了什么*（bash 的 stdout 直写输出文件，运行中贴底实时流出最近三行；终态收成「查看输出」按需展开成可滚动的等宽框，只回末 8KB 且截断时标注完整体积）。
- **后台 agent 实时活动** — 后台子代理由一口闷的 `ainvoke` 改为逐超步流式，卡片实时显示「正在调用 grep · 已用 5 个工具」；计数单调递增，压缩删历史时不会当场回退。
- **workflow 进度带最新日志** — 脚本 `log()` 的最新一条随进度上报，进度条下显示「▸ …」；workflow 的输出文件完成时才写，运行中此前只有 phase 与计数。

### Changed
- 后台任务卡片移除「复制路径 / 在访达中显示」两颗按钮，路径让位给输出预览本身。
- `bg_tasks.update` 是全量快照广播，故随快照走的字段一律有界（`prompt` 1000 字 / `last_log` 300 字）；相同进度快照不再触发广播，workflow 的 `log()` 推送加 500ms 节流，前端逐条按值复用旧对象——避免后台 agent 逐超步上报时整列卡片跟着重渲染。
- 新增 `streams_output` 标记任务是否边跑边写输出文件：只有 bash 在运行中续拉尾部，agent / workflow 不再对必然为空的文件每 2s 空转；输出轮询与秒表一并以右栏展开为前提（收起时卡片仍挂载，此前会在没人看时持续拉取）。

### Fixed
- workflow 后台任务卡片的「任务内容」一直是空的——`workflow` 工具的 `description` 从未写入任务条目。
- 右栏节头在任务较多时把「后台任务」标题挤成两行。

## [0.2.73] - 2026-07-25

### Added
- **环境工具箱（Toolbox）** — agent 的任务工具链（uv / ripgrep / Node.js）不再依赖用户机器上装了什么：新增「设置 → 环境」页，一键装齐或逐项安装，产物落 `~/.lumi/bin`（免 sudo、不碰系统全局），装完 PATH 末尾注入即对 bash 工具 / MCP stdio 子进程全部可见。系统已有的工具永远优先且绝不覆盖，状态徽章区分「系统 vX」与「工具箱 vX」；下载走官方源 + 同源 checksum 校验、尊重 `https_proxy`，进度实时（`env.progress` / `env.state` 广播，面板重开可恢复进行中态，同时只允许一个安装在跑）。
- **飞书接入体检升级为一站式清单** — 原「机器人接入」四项（凭证 / 权限 / 事件订阅 / 版本发布）之前加「本地环境」组：lark-cli 与飞书技能包的安装状态，缺失或落后时行内 **一键安装**——cli 装机器级（无 Node 时先自动装入工具箱，npm 不可用降级官方单二进制直下），技能包按 `version` 增量导出到渠道绑定项目的 `.lumi/skills/`（技能包占模型上下文，按「谁用谁装」不进全局）。本地段与远程段并行探测，安装进度就地显示在对应检查行，装完自动重跑体检。

### Changed
- 飞书渠道表单里「绑定项目」从「会话运行时」块前置到凭证之后——它已是接入体检的输入（技能包按此项目检测与安装），所见即所得；体检改为打开弹窗即跑（本地两项不依赖凭证）、换绑定项目自动刷新。
- 文档：`docs/guides/feishu.md` 按新的体检流程重写配置步骤（不再有独立「测试连接」）；新增 `docs/architecture/toolbox.md`。

## [0.2.72] - 2026-07-25

### Added
- **记忆浮层 wiki 式互链** — MEMORY.md 里的 `.md` 文件链接直接在浮层内打开（本地/远程走同一条 gateway 读取链路，位置透明），非索引页头部滑入 ← 返回；读取中光点延迟 180ms 出现（本地毫秒级读取不闪、远程往返可见）；链接指向已删除文件时显示空态而非报错；CJK 记忆文件名端到端可用（前端还原 percent-encode + 后端记忆名校验放宽为纯路径安全检查）。

### Fixed
- 点击 markdown 里的相对链接（如 MEMORY.md 的文件互链）把整个窗口导航走，表现为应用整页刷新、WS 断连重连。链接现按去向分流：`#fragment` 页内滚动（脚注可用）、带协议链接（http/mailto/vscode…）经系统应用打开、裸 `.md` 文件名浮层内跳转、其余相对路径降级纯文本；`title` 悬停提示保留，应用内链接键盘可聚焦激活。
- Electron `will-navigate` 防线由黑名单改白名单：只放行应用入口自身（刷新/vite 全量重载），任何未来的裸 `<a href>` 都无法再把窗口导航走；dev 源三处硬编码收敛为 `DEV_ORIGIN` 单源。
- 流式渲染时 Markdown 的链接组件身份逐 token 变化，导致消息内所有链接 DOM 反复卸载重建（components 走 useMemo + 模块级组件）。
- 资源浮层读取失败或离线时永转加载光点，现区分「读取失败」空态。

### Changed
- ResourceSheet 改「Dialog 外壳 + 按资源 keyed 内层」：浮层内切换资源（wiki 互链/创建后转查看）状态自动全量重置、不重放弹窗开场动画，替代手工 reset effect。

## [0.2.71] - 2026-07-24

### Added
- **统一右栏（RightRail）** — 执行记录 / 后台任务收进同一套模块卡体系：定时任务会话置顶「执行记录」，「后台任务」模块两视图通用往下叠（节头计数 + N 运行中 + 清除已完成，可独立折叠、键盘可达）。整栏一颗收放钮：浮于聊天区右上角、与左侧栏收起钮同线同款式；收起时栏宽归零、聊天区铺到窗口边、钮贴右缘；开合与宽度（默认 256 同左侧栏，可拖拽 200-420）持久化且两视图共用。
- 聊天里首个后台任务出现时右栏动画入场（不再以最终宽度瞬间挤窄正在阅读的聊天栏）；收起态收放钮亮脉冲点（后台任务或直播中的定时执行）。

### Fixed
- 收放左侧栏时主区内容整体上下跳（mac 顶条高度曾随开合在 h-9↔h-14 切换）；展开钮按下瞬间上跳 8px（Button 按压态覆盖对齐位移，位移移至容器）。
- 悬浮面板「滑出」动画从未真正生效：Tailwind v4 的 `translate` 属性不在 `transition-[transform]` 覆盖内，一直是瞬移 + 淡出——右栏收起残影正源于此；左侧栏 / 右栏统一改 `transition-[translate,…]`，三处真滑出。
- 收放钮视觉断裂：不再跟着面板滑动横穿红绿灯区，改为面板滑动期间隐身、到位后原地淡入（交替 keyframes 重放动画，不重挂 DOM、不丢键盘焦点）。
- 定时任务会话右栏可能显示并误停**旧聊天会话**的后台任务（任务尚无执行记录时会话未切换，现按当前执行线程门控）。
- mac 收起侧栏后，顶条透明容器悬垂截击主区左上角的点击/拖选（pointer-events 穿透，按钮单独恢复命中）。
- 右栏拖宽把手错位摸不到（热区未贴到面板可见左缘）。

### Changed
- 右栏收起即停一切隐藏开销：任务秒表 tick、执行记录拉取、屏外无限动画合成（visibility）；执行记录 / 后台任务两模块组件 memo，流式期间不再逐 token 陪跑重渲染。

## [0.2.70] - 2026-07-23

### Added
- **定时任务执行实时观测 + 中断** — cron 执行现在改走 AgentBridge 流式：一开跑，「执行记录」顶部就冒出一条转圈的活条目，点进去像普通聊天一样**实时看到流**（思考/工具/正文逐步渲染，与聊天同一套渲染）；观测期间只读（输入禁用），输入框显示**停止键**可随时中断（`stop_cron_run` 按 job_id 取消，现场经 checkpoint 保留、可续聊），跑完转 idle 即可续聊。观测走新的按 thread 的 pub/sub（有界队列满即丢，**慢观测者绝不背压 run**、零观测者照常跑）。
- 新增 WS 方法 `stop_cron_run` + 事件 `cron.running` 携带 `{job_id, thread_id, started_at}`（前端据此显示活条目）。

### Changed
- **cron 执行一等会话化** — 由裸 `ainvoke` 改为经 AgentBridge（网关经依赖注入提供 runner，`agents/cron` 不反向依赖 `gateway`）：自动获得 `workspace_dir` metadata、持久记忆（`enable_memory=True`，共享项目记忆、运行中主动写）。cron 线程**不自主触发 autoDream**（人类聊天触发时仍连带综合），与 IM 渠道线程同构的类型闸。
- 定时任务失败判定修正：流式路径下 graph 出错如实记 `failed`（此前被 `stream_response` 吞成 ERROR 事件而误记 success、且不重试）；中断改按 `job_id` 引用取消（不再靠 task 名字符串匹配）。

## [0.2.69] - 2026-07-23

### Fixed
- **定时任务执行线程在桌面续聊被拒「请先选择项目」** — cron 直接 `ainvoke` 运行（不走 AgentBridge），从不给 checkpoint 写 `metadata.workspace_dir`，于是 desktop 打开该线程续聊时工作区未绑定、被边界关卡拦下。现 cron 运行时对齐 AgentBridge 记录项目元数据；`switch_session` 在前端未带 workspace 时从线程自身 checkpoint 恢复项目绑定（通用机制、仅对显式传了 thread_id 的既有线程生效）
- **会话里让 agent 创建定时任务后桌面列表不刷新（需手动 Ctrl+R）** — 任务增删改只落盘、无事件通知前端。现 `JobStore` 增删改经观察者广播 `cron.jobs` 信号（tool / desktop UI 两条路唯一的落盘 choke point），前端据此只重拉来源机器的任务列表（本机双投递按机器去重）；退避错误计数等内部记账 `notify=False` 不触发刷新

### Changed
- 定时任务单次执行超时上限由 600s 提高到 6000s（长耗时的开发型任务需要更宽的执行窗口）

## [0.2.68] - 2026-07-22

### Changed
- **项目主页输入岛复用标准输入栏** — 此前项目主页是另写的一个极简 `<textarea>` + 纸飞机键，缺模型选择、审批模式、附件、斜杠命令，与聊天页输入框明显不一致。现改为复用同一套 `composer`（新增 `project` 模式）：附件、模型选择、审批模式、斜杠命令高亮/补全、圆形发送键全部对齐。`ProjectHomePage` 删掉本地 `draft`/`submit`/bespoke 输入框，改由 App 传入 `composerSlot`（`useMemo` 稳定，保住其 `memo` 不被后台流式 token 击穿）。项目模式因发送前尚无活动会话，解耦三处与会话绑定的部件：隐藏文件夹菜单（会话级授权）、不显示上下文用量环（不读 `cur.ctx`）、永远显示发送键；`SendOverride` 扩展为可携带附件（原先默默丢弃）
- 重写 README（桌面优先，删除已下线的 TUI 内容）+ 新增项目主页 / MCP 面板截图

### Fixed
- **点项目偶发跳到「只有输入框」的欢迎页** — 连接波动导致启动/新建会话时没解析到默认项目就进入 `needProjectHint` 态，此态下点项目卡片被送去「新建会话欢迎页」而非项目主页。现点项目一律进项目主页（主页输入岛本就能在此项目开聊），落点一致
- **离线时从项目主页发送会吞掉已输入文本** — 机器离线时建连接会一直悬挂，而旧逻辑先清空输入再 `await newSession`，文字就此丢失且无提示。现发送前先查该机器控制连接状态（未连接则提示并保留输入），且把清空移到发送派发之后——建会话失败/悬挂时输入仍可重试

## [0.2.67] - 2026-07-22

### Fixed
- **技能详情浮层查看 references 文件时编辑/删除按钮消失** — 弹窗（grid 布局）的 track 被正文里超长的不可断行 token（无空格的行内代码）按 min-content 撑到比弹窗还宽，头部按钮随行尾被推出 `overflow-hidden` 裁剪区，看起来像「莫名没了」。正文行加 `min-w-0` 阻断 track 膨胀；`.md code` 加 `overflow-wrap: anywhere`，超长行内代码在窄容器里可断行（阅读视图与聊天流通用）

## [0.2.66] - 2026-07-22

### Fixed
- **项目主页提示词卡片预览露出 frontmatter 原文** — 给 SOUL/AGENTS 写了 frontmatter 时，卡片预览直接把 `---` 围栏当正文显示。`overview` 的 prompts 现随带后端剥好的 `body`（`read_resource` 的 prompt 分支顺势与 `_prompt_info` 合一），预览与详情浮层同源，frontmatter 不再渲染、不再展示；编辑视图仍操作含 frontmatter 的原文
- **`test_project_config` 未隔离进程配置目录** — 本机 `~/.lumi/prompts` 存在时层序断言误红（global 盖过 style）。测试文件加 autouse fixture 把 `LUMI_CONFIG_DIR` 钉到空临时目录并重置配置单例

### Changed
- 项目主页资源浮层：编辑保存成功后直接关闭浮层（原先留在浮层内切回阅读态）；提示词空态文案精简为「未配置（将以无系统提示词运行）」，去掉「点铅笔为此项目撰写」尾巴（该行仍可点击进入撰写）

## [0.2.65] - 2026-07-22

### Added
- **桌面「项目主页」** — 点项目卡片不再直接开聊天，而是进入项目落地页：输入岛（发送即在此项目新建会话并携带首条消息，不借道主输入框——别的会话暂存的附件不会被顺带发出）+ 该项目的置顶/最近会话流 + 右列五卡（提示词 SOUL/AGENTS、记忆、定时任务、技能、子 Agent）。技能/Agent 支持项目内**新建、编辑、删除**；内置与全局层资源只读，可「复制到项目以自定义」（同名覆盖随即生效）。详情浮层阅读视图渲染后端剥好 frontmatter 的正文，编辑视图操作原文；被「新建会话」阻断跳到项目页的场景仍直接开会话不多绕一步。新增 5 个 WS 方法：`project_overview` / `project_resource_read|write|delete` / `project_copy_builtin`
- **配置三层加载（style 内置 < 全局 `~/.lumi` < 项目 `.lumi/`，逐层同名覆盖）** — 此前 skills/agents/prompts 全部锚定 serve 启动时发现的单一配置目录，会话切到哪个项目都加载同一份：项目自己 `.lumi/` 里的技能/Agent/提示词**从不生效**（项目主页若照此展示即形同虚设）。现在会话按绑定项目走三层链：`loader.config_layers`（skills/agents）与 `manager.prompt_layers`（prompts）是层序单源，变更检测器按 子类×项目 一实例，skill/agent 工具经 `runtime.context.project_dir` 取层并走 detector 缓存（文件未变只 stat 不重解析，顺带删掉了 skill 工具每次调用的第二遍全量目录扫描）；风格判定支持项目 `.lumi/config.json` 的 `style` 声明（`active_style_for`）。无项目场景与 `lumi -p` 行为不变

### Fixed
- **serve 的全局配置层随启动目录漂移** — dev sidecar 从 Lumi 仓库拉起时，仓库自己的 `.lumi/`（30 个 lark 技能）被发现链当成全局层，泄漏进所有项目的会话与项目主页（Rabbit Hole 里看到一整排 lark 技能即此因）。`lumi serve` 现恒把进程配置层钉在用户级 `~/.lumi`（显式 `LUMI_CONFIG_DIR` 仍最高优先）；各项目专属配置归项目层。附带效果：项目自带的 `.lumi/skills` 首次真正被其会话加载
- **技能/Agent 写入不校验 frontmatter 产生幽灵文件** — 加载侧对缺 frontmatter/缺 name 的定义文件静默跳过，此前写入成功的坏文件会「列表里消失、无删除入口」。现落盘前校验（须含 name/description 且 name 与文件身份一致，`loader.validate_definition`），坏格式当场报错；同时堵住「UI 按目录名、运行时按 frontmatter name」两套身份背离的口子
- **项目主页若干实现层问题**（code-review 10 项全修）— `api` 引用不稳定导致后台流式期间每 token 重发 overview RPC 且覆盖编辑中的文本；名字合法性前后端规则不一致 + 写操作失败静默（现同一正则把关 + toast 报错）；`copytree` 撞残留目录抛 FileExistsError（`dirs_exist_ok`）；symlink 项目路径下保存「写成功却报错」（入口统一 `resolve()`）；CRLF 文件前端剥不掉 frontmatter（改由后端下发 `body`，前端删掉自备正则）；远程机器控制连接缺位时文件写操作回退到错误机器（项目主页 API 只认目标机器，缺位明确报错）

### Changed
- 加载响应判废（切项目时旧响应不再倒灌）、会话行相对时间走 `timeAgo` 本地化、侧栏「项目」高亮覆盖项目主页视图、`ProjectHomePage` memo 化（后台流式期间不再整页 reconcile）

## [0.2.64] - 2026-07-21

### Fixed
- **Windows 安装时无法选择安装盘** — `win` 只声明了 `"target": ["nsis"]`，而 electron-builder 的 NSIS **默认 `oneClick: true`**：双击后没有任何向导，直接静默装进 `%LOCALAPPDATA%\Programs\Lumi`，用户既选不了盘也看不到进度。现显式配 `oneClick: false` + `allowToChangeInstallationDirectory: true`，走完整安装向导（模板链路：`ONE_CLICK` 未定义 → `assistedInstaller.nsh` → `MUI_PAGE_DIRECTORY`）。**已发布的 0.2.63 及更早的 Windows 包仍是一键版**，本修复要到用户升级至本版后才可见。另：`perMachine` 保持 `false`（当前用户安装）—— 置 `true` 会让每次自动更新都弹 UAC，与后台静默更新冲突；选定目录会被 `instFilesPre` 自动补上 `Lumi` 子目录，更新时则由 `skipPageIfUpdated` 跳过目录页，不会反复追问

### Added
- **应用内自动更新**（`electron-updater` + GitHub Releases）— 状态机全在主进程（`desktop/electron/updater.cjs`），renderer 只订阅 `UpdateState` 并触发检查/安装；入口为「设置 → 关于」与侧栏底部提示条（仅在**此刻装得上**时出现，检查中/下载中一律静默）。启动 15s 后首检、此后每 6h 一次。**Windows / Linux 全自动**（后台下载 → 就绪 → 用户点重启安装）；**macOS 停在「发现新版」把下载交给浏览器** —— CI 未做代码签名，而 Squirrel.Mac 校验新旧版本签名同源，未签名的包一定装不上，故 mac 设 `autoDownload = false`，只调 `checkForUpdates()`（该阶段纯读 `latest-mac.yml`，不启动 Squirrel 代理）。拿到 Developer ID 证书后把 `MANUAL_DOWNLOAD` 改 `false` 即转全自动，CI 无需再动
- **Linux arm64 构建** — 新增 `ubuntu-24.04-arm`，五个 build job 覆盖 Windows x64 / macOS x64+arm64 / Linux x64+arm64。**Windows arm64 试过但走不通**：后端依赖链上的 `sqlite-vec`（`langgraph-checkpoint-sqlite` 的传递依赖）既无 `win_arm64` wheel 也无源码分发包，uv 在建 venv 阶段即失败；Windows on ARM 的 x64 模拟层足以跑本应用，ARM 用户装 x64 包即可，待上游发 wheel 再加回

### Changed
- **产物命名加入平台标识**：`${productName}-${version}-${os}-${arch}.${ext}`（如 `Lumi-0.2.63-mac-arm64.zip`）。平台原先全靠扩展名隐式区分，而 mac 更新通道用的 `.zip` 是通用扩展名，与其他平台的 zip 必然撞名；`${arch}` 则是硬要求 —— **electron-updater 靠文件名里的架构串选包**（`Provider.findFile` 优先匹配含 `process.arch` 的文件，mac 另有 `MacUpdater.filterFilesForArch`）。**一次性代价**：差量下载靠在新文件名上替换版本号推导旧包的 blockmap 地址，跨越本次改名的这一版推出的是从未存在过的旧文件名 → 404 → 回退全量下载，升级到 0.2.63 的用户需重下完整包
- **Release 补齐更新元数据**：`latest*.yml`（客户端比对版本）、`.blockmap`（差量下载）、macOS 的 `.zip`（Squirrel 只认 zip，dmg 仅供人工首次安装）。少传任一，应用内更新就查不到新版本。**故本版之前的所有 Release 都不含元数据，自动更新要到下一个版本发布后才真正开始工作**
- **新增 `merge-update-metadata` job 合并双架构元数据** — Windows（`latest.yml`）与 macOS（`latest-mac.yml`）的两个架构共用同一文件名，附到 Release 时后者覆盖前者，留下的那份只列一种架构，直接后果是一半用户的更新指向错误架构的包。该 job 在全部 build 结束后把同名元数据的 `files` 并成一份，两种机器各取所需。Linux 不在此列 —— `electron-builder` 只给非 x64 的 linux 加架构后缀（`updateInfoBuilder.getArchPrefixForUpdateFile`，与读取端 `Provider.getChannelFilePrefix` 对应），`latest-linux.yml` 与 `latest-linux-arm64.yml` 本就是两个文件
- **CI runner 一律钉死具体 OS 版本**，不再用 `-latest`：`-latest` 的迁移是 1~2 个月内静默完成的，而我们分发的是安装包，构建必须可复现（官方亦建议 pin）。代价是 OS 弃用时需手动升这份清单

### Fixed
- **安装失败会让后端永久死亡**（本版新代码的自查修复）— `quitAndInstall` 之前必须先收走 sidecar（防新旧实例抢同一 checkpoint 库），可一旦它没能让进程退出，就停在「窗口还在、后端已死」的状态里，而 `stopping` 标志恰好锁死了 exit 回调里的自愈重启。现由 `resumeSidecar()` 显式回滚，同步抛异常与异步 `emit error` 两条失败路径都覆盖，状态退回 `ready` 保留重启入口
- **已下载完成的更新会被一次网络错误冲掉** — `error` 事件无条件覆写状态，包已在本地（`ready`）时若周期检查撞上断网，侧栏提示条与「重启更新」入口一并消失；连锁后果是 `install` 的 `status !== 'ready'` 前置检查随之提前返回，用户连安装都触发不了。守卫从触发端补到了处理端
- **过期错误信息渗入成功状态** — `setState` 的 patch 式合并不会清除上一次的 `error`，导致「已是最新版本」下面挂着一条上次的网络失败提示

## [0.2.62] - 2026-07-21

### Added
- **飞书机器人接入体检** — 权限没开、事件没订、版本没发布，三者的表现完全一样：长连接照常连上、一条消息都收不到、开放平台不报任何错。现于「设置 → 渠道 → 飞书」凭证下方逐项体检（凭证 / 机器人权限 / 事件订阅 / 版本发布），失败项带**预填全部所需 scope 的开放平台直达链接**，点过去确认即可，不必自己对照清单勾。四项数据由一次 `application/v6/applications/me/app_versions` 查询给全（`scopes` / `event_infos[].event_type` / `status`）——注意 `events` 字段返回的是中文显示名，事件 code 只在 `event_infos` 里
- **权限与事件清单单一事实源**（`feishu/scopes.py`）：`BOT_SCOPES`（缺任一收发不了）/ `OPTIONAL_SCOPES`（缺了只丢一项体验，带「缺了会怎样」的说明）/ `SETUP_SCOPES` / `MINUTES_SCOPES`，诊断比对、修复链接、文档三者同源。事件常量无法与 `channel.py` 共用定义（lark SDK 只认 `register_p2_xxx` 方法名），改由 `test_event_constants_match_registered_handlers` 断言 `handler._processorMap` 锁住——改了注册点没同步常量，测试即红

### Changed
- **删除「测试连接」按钮**（RPC `test_channel` + `test_credentials` 一并移除）— 接入体检第①项验的是同一件事且信息更全，两者并存还会互相矛盾：凭证正确但未开 `app_version:readonly` 时，`test_channel` 走 `bot/v3/info` 报成功而体检报失败，用户不知该信哪个
- **`lark_call_classified` 返回 `(resp, code, reason)`** — 原先异常与「未知」都压成 code 0 且异常信息只进日志不回传，这正是体检一度自写 try/except 绕开底座的原因。现补上 `NETWORK_ERROR` 哨兵与给用户看的 `reason`，`setup.py` 退化成一次调用。**破坏性**：`lark_call_classified` 的解包由二元组改三元组（`lark_call` 不受影响）
- **诊断结果的三态由后端定**：`Check.tone ∈ ok|warn|error` 取代 `ok` + `warn` 两个布尔——两布尔能拼出 `ok=False,warn=True` 这种非法组合，且前端拿到后第一件事就是拼回三态。`warn`（能用但有功能降级，如缺 `cardkit:card:write` 只是失去打字机效果）汇总条显示「N 项功能降级」而非谎报「全部生效」
- **诊断结构与面板由接入体检、妙记链路共用**：后端 `feishu/checks.py` 的 `Check` + `blocked_tail`，前端 `CheckPanel` / `CheckRow` / `useDiagnose`。合并顺带补上妙记侧缺失的 error 态——它此前把失败折叠成 `null`，与「从未检查过」无从区分，用户只会反复空点按钮
- **体检的四种失败分开报**（请求未送达 / 缺体检权限 99991672 / 无任何版本 / 凭证真的无效）— 都归成「凭证无效」会把断网或缺权限的用户支去重抄 App Secret，而那对前三种毫无用处；断网时不给开放平台链接
- **文档修正**：配置实际存 `~/.lumi/lumi.json` 的 `channels` 分区而非 `~/.lumi/channels.json`（前端文案、`gateway.ts`、`events.json`、guides 共 5 处）；`docs/` 里重复列举的 scope 名改为指向 `scopes.py`，消除漂移源

## [0.2.61] - 2026-07-21

### Fixed
- **定时任务未读角标不显示** — 未读此前是**事件累积**的：只有 `cron.result` 到达那一刻才 +1，桌面端没开着（或那台机器的控制连接还没建立）时跑的执行永远不计，事后刷新也补不回来，`openCronJob` 的对账逻辑只会删不会补。现改为**派生**：后端 `list_cron_jobs` 带回 `run_threads`（近期可跳转的 run，窗口同 `MAX_CRON_RUN_THREADS`），侧栏角标 = 它减去本地已读集合 `readRuns`，与 Runs 栏蓝点同源。离线期间的执行重连后照样算未读。连带删掉整套 `cronUnread` 状态 / localStorage 持久化 / 事件累积 / 两处对账 / stale 回收 / `pruneJobUnread` 辅助（净减约 55 行），实时性靠原有的 `cronVersion` effect 拿到
- **远程机器的定时任务运行脉冲点从不亮、后台任务面板一直空着** — 控制连接的事件转发是逐个 `||` 枚举的，`cron.running` 与 `bg_tasks.update` 都漏在外面；而远程机器通常没有活跃会话连接、只有控制连接。后台任务的缺口更深：初始快照 `listBgTasks` 只挂在**会话连接**的 ready 上，所以远程机器的任务从初始拉取就没有。现把「进程级广播事件」显式化为 `PROCESS_EVENTS` 集合（`cron.result` / `cron.running` / `bg_tasks.update` / `mcp.status`），两条连接共用同一分发；控制连接的 `gateway.ready` 也拉一次后台任务快照
- **机器断连后任务永远显示「运行中」** — 这类按机器分段的进程级快照只在连接活着时被推送更新，连接一断就再也等不到「结束」那一帧。定时任务尤其致命：`CronJobRow` 是「运行脉冲点」与「未读角标」二选一渲染，卡住的运行态会永久顶掉未读角标（与上面那条是同一个症状的两个成因）。现由 `clearMachineSnapshots` 统一清理（`cronRunning` + `bgTasks`），断连与移除机器两条路径共用；移除机器时一并清掉此前遗漏的 `channels`（禁用后飞书渠道分组会残留在侧栏）
- **多机同名定时任务的运行态串号** — 运行态此前按 `job.name` 跟踪且是全局单一数组，A 机的广播会覆盖 B 机的运行态，同名任务还会互相点亮。改按 `job.id`（全局唯一），前端按机器分段存放

### Changed
- **协议**：`cron.running` 的 payload 由 `names`（任务名）改为 `job_ids`；`list_cron_jobs` 返回的任务新增 `run_threads` 字段（仅列表带，单个任务的增删改响应不带——前端用不到，不必为它读一遍日志）。**新前端配旧后端时运行脉冲点不亮、未读角标为 0**（不会崩，消费点有兜底），远程机器需同步更新后端
- **性能**：新增 `RunLog.recent_thread_ids()`，只反序列化日志文件尾部 N 行、不构造 `RunRecord` 也不排序——`list_cron_jobs` 位于每条 `cron.result` 都触发的热路径上，而日志文件上限 2MB、可达数千条记录；多任务改 `asyncio.gather` 并发读取
- **前端**：`openCronJob` 删掉一次 `listCronRuns` RPC 往返——未读对账代码移除后它只剩「找第一条有会话的 run」一个用途，而那按定义就是 `run_threads[0]`；`cron.running` 处理加等值短路（本机同一份快照经两条连接各来一次，不再每次都造新对象拖着 memo 化的 Sidebar 重渲染）；三处手写的 `backend || 'local'` 改用既有的 `beOf`

## [0.2.60] - 2026-07-21

### Added
- **框架内置默认 SUMMARY 提示词** — 压缩用的摘要提示词此前只从 `.lumi/prompts/SUMMARY.md` 读，用户没配就直接抛「未找到摘要提示词配置」，客户侧频繁撞到。现于 `lumi/prompts/SUMMARY.md` 内置一份（续接导向：用户意图 / 已完成工作含工具调用与失败尝试 / 关键事实与决策 / 当前状态 / 下一步，并强调「摘要是后续唯一可见的历史，具体标识原样保留」），随 wheel 与 PyInstaller 产物一并分发（已验证 `collect_data_files('lumi')` 递归收集到）

### Changed
- **提示词解析统一为三层链**：用户 `.lumi/prompts/` > 风格内置 > 框架内置，**空文件（或只剩 frontmatter）视同不存在、继续往下找**——否则一个被误清空的提示词会静默生效。`load_system_prompt` 改为逐个复用 `load_prompt`，删去原先那套并行的 resolved-dict 解析（净减 45 行），两条链再不会漂移。因内置兜底后「未配置 SUMMARY」不再可能，`nodes.py`（×2）与 `bridge/core.py` 的三处错误分支一并删除
- **妙记诊断：lark-cli 故障不再谎报未授权** — `_auth_status()` 此前把 `(ok, reason)` 塌成 `None`，CLI 超时 / 崩溃 / 旧版本不认 `--json` 三种故障全都走到「尚未授权」分支、引导用户去扫码，而扫码解决不了 CLI 跑不通。现签名改为 `tuple[dict | None, str]` 把真实原因带到 UI（与 `ensure_subscription` 同一范式），并单列成「lark-cli 状态读取失败」一步
- **CA bundle 兜底判据补上 capath** — 原本只看 `openssl_cafile`，在仅靠证书目录建立信任的系统上（capath 已被 `update-ca-certificates` 灌入企业自签 CA）会误判失效、用 certifi 把系统信任库整个换掉，内网 HTTPS 端点随之不可信。改为 cafile 与 capath 双双失效才回退

### Fixed
- **桌面端冷启动卡顿：登录 shell 求值不再阻塞主进程** — v0.2.58 引入的 `execFileSync($SHELL -ilc)` 排在建窗之前，rc 带 nvm/conda 初始化时常要 0.5~2s（卡死则吃满 5s timeout），期间点了图标没有任何窗口。现改为异步（`promisify(execFile)`）且缓存的是 Promise，`whenReady` 一开始即 kick off，与 `pickPort`/建窗/Electron 自身启动重叠，实际等待基本归零；`app.on('activate')` 的注册也提到拉 sidecar 之前。等待期间用户已退出时不再拉起孤儿 sidecar（`stopping` 守卫）
- **登录 shell PATH 改为合并而非替换** — rc 是「从头求值」的结果，取不到终端启动时已激活的 venv / direnv 注入，整体替换会让原本能解析的命令消失（dev 下 `uv` 可能 ENOENT）。现与当前 PATH 合并、登录 shell 的值在前、去重保序
- **测试环境变量泄漏** — `tests/test_cli_ca_bundle.py` 让 `_ensure_ca_bundle` 直接写 `os.environ`，而 `monkeypatch.delenv` 对「原本不存在」的变量不做记录、无从回滚，certifi 路径会泄漏给同会话后续测试。现用 autouse fixture 把整份 `os.environ` 换成副本

## [0.2.59] - 2026-07-20

### Fixed
- **打包版飞书渠道永远停在「连接中」（dev 模式正常）** — PyInstaller 冻结产物里 OpenSSL 的默认 CA 路径是**构建机**上的位置（CI runner），装到用户机上必然不存在，`ssl.create_default_context()` 于是一张 CA 都加载不到，任何证书链都被判成自签不可信。故障面极具迷惑性：requests/httpx 显式用 certifi，HTTP 调用（bot 身份获取、通讯录预热）全部正常且照打成功日志，只有走 ssl 默认上下文的连接失败——飞书 WS（lark SDK 的 `_ws_connect_kwargs()` 不传 ssl 参数）握手即挂，而真实报错 `CERTIFICATE_VERIFY_FAILED` 走 lark 自己的 logger 进 stderr、被 Electron 吞掉，Lumi 日志里一个字都没有。前端显示的「连接中」是如实反映（进程内确无到飞书的外网连接），非状态上报 bug。现于 `main()` 最前兜底：默认 CA 路径不存在时回退 `certifi.where()`，dev 与容器环境取值不变，显式设过 `SSL_CERT_FILE`（企业内网自签 CA）则尊重用户的

## [0.2.58] - 2026-07-20

### Fixed
- **从 Dock 启动时妙记诊断谎报「lark-cli 未安装」** — GUI 启动的 Electron 只继承 launchd 的默认 `PATH`（`/usr/bin:/bin:/usr/sbin:/sbin`），链路中间没有 shell，`~/.zshrc` 里 nvm、`~/.local/bin` 的注入全都没机会跑，`shutil.which("lark-cli")` 必然落空（终端启动则一切正常——PATH 继承自 zsh，故表现为「时好时坏」）。受害的不止 lark-cli：dev 模式的 `uv`、打包版 fallback 的 `lumi` 本身也在 PATH 上查找（已验证 `spawn` 用的是传入 `env` 的 PATH，非父进程的），从 Dock 起的 dev 版本连后端都拉不起来。现于拉起 sidecar 前借一次登录 shell（`$SHELL -ilc`）取回真实 PATH，结果缓存（自愈重启不重复付开销）；5s 超时或失败退回原 PATH，不阻塞启动。`gh` / `rg` 等外部命令一并受益
- **闲置超过 2 小时后妙记诊断谎报「授权已失效」** — 授权检查原本认死 `tokenStatus == "valid"`，但 access_token 到期（约 2h）后状态转 `needs_refresh`，此时 token 仍然可用：下次 user API 调用会自动刷新（已实测验证）。误判还会经 `_with_blocked_tail` 把权限/订阅两项一并标成「需先完成授权」，整条链看起来全断，并引导用户去做没必要的重新扫码。改判 `available` 字段——真未授权时 CLI 给 `available: false` 且不带 `tokenStatus` 字段，原条件靠 `.get()` 返回 `None` 才碰巧拦住，属巧合而非契约。刷新万一失败也漏不掉：第④步订阅是真 API 调用，会暴露

### Changed
- 妙记诊断测试的 fixture 改用两台机器实测的真实 CLI 输出形态（未授权态含 `status`/`available`、不含 `tokenStatus`），并新增 `needs_refresh` 回归测试锁住本次修复

## [0.2.57] - 2026-07-20

### Fixed
- **妙记主动推送另起一个新会话** — 此前入站私聊按 `chat_id`（`oc_`）派生 thread，而妙记推送事件只带订阅者 `open_id`（`ou_`）、按它派生，同一个私聊裂成两条互不相干的会话（表现为「agent 一主动推送就开新对话」）。飞书没有 open_id → p2p chat_id 的查询 API（`im.v1` 只有建群/查群），故改为**私聊直接以 open_id 为会话 key**（群聊仍用 chat_id），入站与推送从第一条起就同源，与先后顺序、有无历史都无关。投递地址仍走 `pool.chat_ids` 里回填的真实 `chat_id`，缺失时 open_id 直投也能到同一私聊。注意：已有的私聊会话历史会一次性断代（旧 `feishu-oc_*` thread 变孤儿），群聊不受影响
- **未知 `chat_type` 会把群消息按发送者拆散** — lark 把 `chat_type` 声明为 `Optional[str]`，会话 key 的判定改为「仅精确 `"p2p"` 用 open_id，群与任何未知值一律用 chat_id」。反过来默认按 open_id 时，一条 `chat_type` 缺失的群消息会让群里每人各得一条独立会话、回复却仍发回同一个群，且不会报错

### Changed
- 妙记待办队列从裸元组 `(token, open_id)` 改为 frozen dataclass `_MinuteEvent`；主动推送新增显式入口 `feishu_p2p_thread_id`，避免调用点分不清传进去的是哪种 id
- `BridgePool.chat_ids` 补明契约：值是 receive_id（`oc_`/`ou_` 均可，发送侧按前缀选 `receive_id_type`），不可当 chat_id 使用
- 飞书入站首次有了直接驱动 `on_message` 的测试，覆盖私聊/群两种 key 派生

## [0.2.56] - 2026-07-20

### Fixed
- **侧栏偶发「暂无会话」需手动重载才恢复** — 会话列表此前只在 `gateway.ready` 拉取一次、无重试，且多机分组的空态不检查是否加载成功，`ready` 首拉遇瞬时抖动（重连中 / 服务端 `initialize` 未就绪）时 catch 兜底写入空段就被渲染成确凿的「暂无会话」，直到 Ctrl+R。改为按机器记录 `list_sessions` 是否**成功返回过**（`loadedBackends`），未成功前空列表显示「连接中」而非空态；并新增窗口重获焦点时的兜底自愈刷新，首拉失败后切回窗口即恢复，无需手动重载
- **飞书渠道反复刷 `processor not found` ERROR** — 后台订阅了但 Lumi 不消费的事件（会议开始 `vc.meeting.all_meeting_started_v1`、已读回执、撤回等）会让 lark SDK 查不到 processor 而反复报错、并回 500 触发平台重推。此前靠逐个 noop 注册消音，每冒出一种新事件就得补一行。改为在事件分发入口通用吸收「未注册」异常（其余异常照常外抛），随后台勾选新增事件也无需再改代码

## [0.2.55] - 2026-07-19

### Fixed
- **渠道会话上下文用量环在代理模型下不显示** — 经 LiteLLM 等代理跑模型时，`response_metadata.model_name` 回传的是上游真名（如 Bedrock application-inference-profile 的 ARN），models.dev 目录查不到 → 窗口 0 → 前端隐藏环。`_snapshot_model_window` 现于 wire 名查不到目录时回退到渠道配置的模型别名（空 = 跟随 active profile）再查——与 desktop 环的别名查目录同一路径。回退顺位：wire 真名（权威）→ 渠道配置别名 → 仍未知才隐藏

## [0.2.54] - 2026-07-19

### Added
- **渠道卡片状态光点 + 自动刷新** — 渠道列表标题行新增状态光点（绿光晕=已连接、品牌金呼吸=连接中、红=连接失败、灰=未启用/停止，全走主题变量亮暗自适应）；面板打开期间每 3 秒轮询 `get_channels`，启用渠道后能看到完整的连接流转，不再需要切页再回来才更新（渠道连接是异步的，此前只在面板挂载时拉一次状态）

### Fixed
- **妙记授权引导修正** — 实际排障发现两处引导会把用户带进「反复重新授权仍缺 scope」的死循环：① `lark-cli auth login` 只请求勾选/参数指定的 scope（应用开通了也不会自动带上），「用户授权」「妙记权限」两个检查项的修复命令统一改为带显式 `--scope` 的完整登录命令（`--scope` 与 `--recommend` 叠加，不丢其他权限）；② 开放平台修复链接原带 `token_type=tenant`，会把用户引到「应用身份权限」tab，而 lark-cli 以 user 身份取数、scope 必须开在「用户身份权限」下，已改 `token_type=user`。`docs/guides/feishu.md` 体检表同步更新

## [0.2.53] - 2026-07-19

### Added
- **飞书妙记自动纪要** — 录音 / 会议（须开云录制）生成妙记后，Lumi 自动取回**逐字稿**（带说话人与毫秒时间戳）交 agent 整理，推送到与机器人的私聊。`minutes.minute.generated_v1` 事件复用现有 lark WS 长连接，注入对用户不可见的合成轮由 agent 自主取数生成。取数走 `lark-cli`（读妙记内容必须 user 身份，Lumi 只持 app 身份），故不需要在 Lumi 侧做 OAuth 与 token 管理。实测录完约 20 秒送达。飞书配置面板新增开关与链路体检：四个前置条件（lark-cli / 用户授权 / 妙记权限 / 事件订阅）逐项显示，任一未通给出对应命令或开放平台链接——这条链路任一环断裂的表现都是「静默收不到事件、零报错」，故必须能看出卡在第几步。channel 启动与每次体检都会幂等重建订阅（`lark-cli event consume` 优雅退出会主动 unsubscribe，是常见的静默失效来源）

### Changed
- **后台任务完成通知不再先发「✅ 后台任务已完成，正在整理结果…」占位消息** — 该消息原本只是流式卡片的锚点（CardKit 卡片过去只能 reply 到某条入站消息，而通知轮没有入站消息可回）。现无锚点时经 Create API 直投，用户看到的第一条即结果本身
- **`send_message_sync` 自行判定 `receive_id_type`** — 群 `oc_` / 用户 `ou_` 的 ID 格式规则收敛到一处，三个调用方不再各自重复推断（方法签名去掉该参数）

## [0.2.52] - 2026-07-19

### Added
- **执行记录栏可折叠** — 标题行整行可点，`grid-rows` `1fr↔0fr` 过渡出内容自适应高度的收拢动效，折叠后面板收成一条（不设 `bottom`，高度由标题行撑出）；折叠态挂 `inert`，避免内容只是被裁到 0 高、仍能 Tab 进去回车切走会话。`RunsRail` 以 `key={jobId}` 重挂，折叠态不跨任务残留

### Changed
- **三条悬浮栏顶边对齐** — 执行记录栏与后台任务栏从 `<main>` 内提到与左侧栏同级的 flex 行：留在 `<main>` 内会被其中的 topStrip 压低一截（侧栏展开时 36px），顶边对不上左侧栏。非悬浮的 `PreviewPanel` 仍留在 `<main>` 内，该归位规则已写入 `docs/architecture/desktop.md`
- **两栏标题样式统一** — 去掉 `uppercase tracking-wider` 的 11px 小字与条目计数，改 13.5px 加粗；chevron 平时隐身、hover 才现；滚动容器从整个面板收进列表区，标题行不再跟着滚。后台任务栏的收起只由右侧 chevron 触发（标题保持纯文本，避免整条空白成为误触关闭的热区），运行中计数仅在非零时显示
- **拖拽分隔条去掉 hover 金线** — 只保留不可见热区与 `col-resize` 光标；原细线按 flex 行全高绘制，会在悬浮面板的圆角外上下各多出 `FLOAT_GAP`
- **模型回复正文走衬线** — `.md-serif`（New York / 中文回退宋体），代码与行内代码因 `.md code` 自带等宽栈不受影响

### Refactored
- **`ResizeHandle` 的 `shift?: number` 收敛为 `floating?: boolean`** — 三个悬浮调用点原本各自传 `±FLOAT_GAP`，而符号完全由 `edge` 决定；改由组件自行推导，调用方无从传错（把手不可见，错位了也看不出来）
- **执行记录卡片复用 `CARD_L2`** — 原手写的 `border border-line/60 rounded-lg bg-surface/50` 与常量逐字符相同，改 `cn(CARD_L2, …)` 后状态分支只剩各自的覆盖项

## [0.2.51] - 2026-07-17

### Added
- **飞书等渠道旁观会话显示上下文用量环** — desktop 旁观 IM 渠道会话时右下角也能看到上下文用量环。原先 `ContextMeter` 嵌在 composer 里、被只读提示条替换故完全不可见，现挂到只读条右侧（无数据自隐藏、提示文字仍居中）；分母**不取** desktop 当前 activeModel（旁观会话跑的模型往往与 desktop 选中的不同，会算错百分比），改由 `_load_history` 快照新增的 `model`/`context_window` 提供——取会话末条 AI message 的真实 `model_name` 经 models.dev catalog 查窗口。每轮结束经 `channel.activity` 重拉快照刷新（旁观不订阅逐 token 流，故非实时）
- **IM 渠道可独立配置模型 / 思考档位 / 工具审批 / 绑定项目** — 抽 `ChannelRuntimeConfig` 基类（model/effort/tool_mode/workspace）供各渠道 config 继承，飞书不再被迫共用 desktop 全局 active 模型与思考档位；企微等新渠道接入直接继承同一组能力。新增 effort 覆盖链：`LumiAgentContext.effort`（None=跟随 profile，desktop 走这条）→ `call_model` → `tool_call_chain` → `create_llm(effort=)` 绕过全局 `provider_store`；`AgentBridge.initialize` 接收 model/effort 覆盖（连接由 `resolve(model)` 反查 profile，不改全局），`BridgePool` 透传、`ChannelManager` 按运行时三元组变更重建会话池；`ultra` 顶档的 workflow 编排提醒（`drain_ultra_note`）同步读该覆盖。desktop 渠道设置新增「会话运行时」通用组件 `ChannelRuntimeFields`（模型来源 + 模型下拉 + 思考控制 + 审批 + 项目），随模型能力变形（Effort 分段含 Ultra / Thinking 开关 / 无思考）

## [0.2.50] - 2026-07-17

### Changed
- **MEMORY.md 索引行即结论** — 索引行从「主题词钩子」改为「一句结论」（`- [标题](文件.md) — 该怎么做`），只看索引就知道怎么做，topic 文件只留细节；`type` 与写入日期移出索引、归位 topic frontmatter（新增 `date` 字段），矛盾裁决改按 frontmatter 的 `date` 比新旧。dream 提示词阶段 4 不再复述格式、回指系统提示「持久记忆」段（唯一事实源）
- **`normalize_memory_index` 职责翻转** — 从「补全索引行 `[type · 日期]`」改为迁移收敛：严格匹配的 legacy tag 才剥（正文方括号不碰），剥前把日期回填 topic frontmatter、**回填成功才剥**（文件缺失/无 frontmatter 则原行保留，信息不丢）；插入点与 `parse_frontmatter` 同套边界规则（容忍 BOM/前置空白，不会把 `date` 插到 frontmatter 外）；新格式行缺 `date` 时以 mtime 补近似值，闭合「date 仅提示词约束」的空窗

## [0.2.49] - 2026-07-16

### Fixed
- **summary 无条件剥图击穿热缓存** — `run_summary` 原先每次都先 `strip_images_from_messages` 把图替换为 `[image]`，导致送进摘要模型的 messages 从第一张图起偏离主循环写下的滚动缓存断点、砸掉在线 summarizer 本可命中的热缓存读（整段历史被迫全量重算）。现改为**首次带原图**（与主循环缓存字节一致、近乎免费），剥图降级为撞 PTL 时的**第一档缓解**（保全文字、只丢图、仅一次），仍 PTL 再按 round 截头
- **非 Anthropic 模型 summary 图片格式漏转** — 摘要路径此前靠无条件剥图顺带回避了多模态格式问题；去掉预剥后补上与 `call_model` 同法的 `message_transform`（对直连 Anthropic 恒等、缓存字节不变，对 OpenAI/Bedrock 归一化为各自图片格式），避免以 Anthropic 原生 `image` block 直发 OpenAI 触发 400

## [0.2.48] - 2026-07-15

### Fixed
- **侧栏项目分组不再因点击会话而跳动** — 原排序把"当前所在项目组"永远顶到最上面，点击别的项目会话时该组目录变成当前项目、整组被顶上去，侧栏顺序随选中跳变。改为按各组最近会话时间倒序排序（该键仅在有新会话时变化，单纯选中已有会话不影响），顺序稳定且最活跃项目自然靠上；顺带移除 Sidebar 已不再使用的 `workspaceDir` prop

## [0.2.47] - 2026-07-15

### Added
- **默认项目** — 项目清单条目可标记 `default`（`set_default_project` RPC，至多一个，设新的自动顶掉旧的）。"新建会话"每次都问后端要最新项目列表，有默认项目直接绑定新会话，没有才跳项目选择器；`ProjectsPage` 项目卡「⋯」菜单可设为/取消默认，卡片名旁挂静止金星标记（设为默认那一刻光环扩散一次）

### Fixed
- **聊天必须绑定项目——关闭"不选项目直接聊天"的权限边界绕过** — 不选项目直接"新建会话"曾以 `workspace=''` 建连，后端项目目录静默退回不可控的进程 cwd，而权限引擎的工作区边界检查对提取不到路径的工具调用（如非白名单 bash 命令）直接放行——组合起来是能绕过工作区边界的真实安全问题。现在 `GatewaySession.handle_frame` 对 `send_message`/`run_command` 在未绑定项目时一律拒绝，desktop 前端的"新建会话"（含 app 冷启动这条此前被漏掉的入口）统一改为无默认项目时阻断式跳转项目选择器，不再放行空 workspace 会话；发送/执行命令失败也从静默重置改为 toast 提示原因，避免拒绝被悄悄吞掉
- **删除项目书签误伤仍在使用中的会话** — 移除项目书签会连带清空前端 `workspaceDir`，导致仍绑定该项目、仍在正常工作的会话收不到 MCP 故障 toast（`workspaceDirRef` 与后端上报的 `mcp.status.project` 对不上，误判为"跨项目噪音"静默吞掉）；`gateway.ready` 未绑定会话也会写入 cwd 兜底路径污染侧栏项目分组
- **取消默认项目误清所有项目的默认标记** — `set_default_project(path, False)` 原逻辑对非目标条目一律清 `default=False`，不看参数本身——多窗口/多端并发操作，或对陈旧/无关路径取消默认时，会连带清掉别处刚设的真实默认；现在取消默认只清目标自身
- 会话切换 thread 时绑定态可能沿用上一个 thread 的陈旧值（未重新绑定就被误判为已绑定），加了显式重置

## [0.2.46] - 2026-07-13

### Added
- **`/goal <条件>` 目标驱动命令（移植自 Claude Code）** — 输入一个**可判定的条件**后，agent 被目标驱动持续工作：本质是给 Stop 事件挂一个 session 级 LLM 条件评估 hook。每次模型想结束时，独立无状态的判官（把对话转录渲染成纯文本、复用会话模型、`structured_output` 三态 `{ok, reason, impossible}`——全新 prompt 与主对话缓存零交集）判定条件是否成立：未成立注入 `<system-reminder>` 拉回继续、成立自动解除、永远达不成（impossible）放行结束。跨轮持续、跨重启存活（条件存 `session_meta` sidecar，非 LangGraph state——达成时清条件+返 None 不短路后续 `auto_dream`）。`/goal <条件>` 激活即跑一整轮、`/goal clear` 提前解除、裸 `/goal` 回显当前目标；desktop + IM 两端可用。子 agent（depth>0）免疫，不参与目标驱动。无拉回上限（靠 impossible + `/goal clear` + 中断兜底）

### Fixed
- **effort 型 qwen 关思考失效致结构化输出 400** — effort 型 qwen 思考模型（如 qwen3.7-plus，有 low/medium/high 档位但 catalog 无 toggle）的 `allowed_levels` 不含 `off`，`force_no_thinking` 被门控挡掉、返 `{}`，思考没关成——强制 `tool_choice` 的结构化输出链（判官 / 分类器 / titler）在 DashScope thinking mode 下即 `tool_choice=required` 400。现对**有思考能力**的 qwen（`allowed != ("auto",)`）在门控前直通 `enable_thinking=false`；无思考能力的 qwen（如 qwen3-max）仍返 `{}`，不注入它可能不认的参数

### Changed
- **转录导出换行转义** — `extract_messages_as_text`（dream 语料 + goal 判官共用）消息内换行由 `⏎` 折叠改为字面 `\n` 转义：仍保证一行一消息（grep 友好），但对 LLM 通读更自然易读

## [0.2.44] - 2026-07-12

### Changed
- **MCP 池对象化重构** — 五个平行的模块级 dict（`_pools`/`_pool_load_tasks`/`_pool_generation`/`_pool_used` + 全局锁语义）收拢为 `McpPool` 对象，生命周期不变量集中到方法里：`close()` 恒取消在途加载、换新一代 manager 并递增 generation——配置作废与 LRU 淘汰共用此路径，结构上杜绝漏 bump；加载任务的 finally 只清自己的登记，被取消的旧任务不再误删同池后继注册的新任务
- **单发路径等池语义下沉** — 删除 `await_pool_ready` 及其 4 个调用点（cron / workflow / 子代理 / headless CLI，含 cron 的循环导入 workaround）：`get_tools(wait_mcp=True)` 默认等冷池就位，交互 bridge 是唯一 opt-out（headless CLI 经 `initialize(wait_mcp=True)` 复用）；未来任何单发入口不必再记得等池的仪式。父级项目根改经 `LumiAgentContext.project_dir` 显式传递——原 contextvar 方案在子代理/workflow 调用点恒为 None，实际只会等错全局池并重复冷启一套 stdio 子进程，项目级 MCP 工具从未到达子代理
- **mcp.status 服务端按连接过滤** — 广播只发给绑定该池的连接（`""` = 全局池 ↔ 无项目连接），失败 toast 同内容 60s 去重且只对当前工作区弹（后台项目会话的常驻连接仍收到自己池的失败，跨项目弹红是纯噪音；面板刷新信号无条件发），bridge 轮首刷新与基线采样次序收进 `_build_tools()` 单点（`folders` 不再跨模块捅私有字段）。池绑定改为注册时声明（`hub.register(channel, mcp_key=...)`，DesktopDelivery 持 channel→key_fn 映射），不再事后往 channel 上贴属性；wire 投影 `project_wire_key`（`""` = 全局池）单点导出，payload 侧与连接侧同源
- **mcp.status 投递兜底** — 连接注册晚于 initialize 触发的后台加载：毫秒级快速失败（如 server 命令拼错）会在注册前广播、无人接收——注册/续接时补发已完成的池状态（detach 期间完成的广播同样由此找回）；MCP 面板浏览无绑定连接的项目时，loading 期间每 3s 轮询对账，徽标不再永远停在「正在后台连接…」
- **get_tools 白名单覆盖免等** — 白名单被现有工具全覆盖时自动跳过等冷池（MCP 只可能补充新名字，等池纯属白付），等池后仅在池版本号变化时重载一次；autoDream 的 `wait_mcp=False` 手工特例随之删除，机制泛化到所有内建白名单子代理。merged MCP 配置按两文件 mtime 缓存（同权限引擎热重载思路），每次建 agent 的重复读盘解析归零

### Fixed
- **孤儿子进程窗口** — 子进程 PID 改为逐 server 在 finally 内快照 diff：保存配置打断慢 server（如 npx 冷启）连接时，已 spawn 的子进程不再逃过 `close()` 的 SIGKILL 兜底（原快照在整个启动循环之后，取消即全部漏杀）；`close()` 取消在途加载后等其退出再关 manager（取消异步送达，不等则 finally 尚未记完 PID 就杀），快照复用上一轮 after 为下一轮 before（S+1 次进程树遍历而非 2S 次），快照下放线程（每个存活后代同步 spawn 一个 pgrep，原在持锁的后台加载里阻塞整个事件循环、拖停所有会话的流式输出）
- **切项目后子代理绑旧池** — `retarget_mcp` 同步更新 `context.project_dir`（项目状态三份一起切）：`set_workspace` 切项目后 spawn 的子代理/workflow 不再等待并冷启旧项目的 MCP 池
- **等池期间配置作废的静默降级** — `wait_ready` 感知池换代后对新一代重试，单发路径的「工具就位」承诺在配置保存竞态下不再静默失效（加载失败终态仍不重试）。`close()` 的换 manager 提前到首个 await 之前：停驻等待者按 done-callback FIFO 先于 close 被唤醒，换代滞后会让它把旧 manager 误判为现任、按终态静默返回零工具；排水窗口混进来的 `ensure_loading` 同理会对着将被关闭的旧 manager start（并发 start/close + 无人追踪的子进程），换代先行使两条竞态的 identity 校验都立即生效
- **shutdown 期间的加载逃逸** — `close_all_pools` 逐池改走 `close()`（换代使 identity 校验生效），并新增关停闩：闩落下后 `ensure_loading` 一律不再受理，清理期间/之后残存后台任务（detached run、迟到 cron tick）再触发也不会在 SIGKILL 扫尾后拉起无人回收的 MCP 子进程
- **首个 MCP server 添加后不生效** — `invalidate_mcp_pools` 原样跳过未启动的冷池（「空池无可作废」），从无到有添加首个 server 时 generation 恒 0，存活会话轮首版本号比对（0==0）永不触发重建，新 server 到应用重启前都不可用：现对配置非空的冷池直接换代，`get_mcp_tools` 把池对象登记提前到空配置早退之前（无配置的项目也在 `_pools` 挂名，invalidate 找得到）
- **连接测试假超时** — 探测计时从拿到 spawn 锁才起表（`asyncio.timeout(None)` + 拿锁后 reschedule）：后台池加载整程持锁（30s/server 串行），原来 15s 预算全部耗在排队等锁上，健康 server 被误报「连接超时」；`test_mcp_server` 恢复独立默认超时 15s，不再随后台加载常量翻倍到 30s
- **掉线期间的历史永久丢失** — 回合进行中 WS 闪断重连：历史快照因流式在途被 `hydrateHistory` 丢弃时不再置 loaded（原 RPC 成功即置，补拉的门永久关闭，掉线前所有消息直到应用重启都不可见），轮次收尾（turn.complete/error）后自动补拉找回
- **LRU 驱逐死工具** — 被淘汰池的 generation 现随 `close()` 递增，绑着它的存活会话轮首感知换代重建工具列表（原永不重建，此后每次 MCP 调用都打在已关闭的会话上直到重启）
- headless 冷启动首轮不再冗余重建一遍刚构建的工具列表（阻塞路径构建后重采样基线）
- 冷池非阻塞轮次落 warning 日志（IM channel / desktop 首轮无 MCP 工具可从日志追溯）；`test_pool_generation_bumps_on_invalidate` 假测试（只测 dict 读写）重写为真调 invalidate / evict 断言版本号递增，新增取消竞态回归测试

## [0.2.43] - 2026-07-11

### Added
- **MCP 后台加载（对齐 Claude Code 的 pending 语义）** — MCP 池加载彻底移出会话就绪关键路径：`gateway.ready` 不再等任何 server（实测毒配置下就绪 73ms，原为无限挂/15s），工具在池就位后的下一轮对话自动可用（bridge 轮首按 `pool_generation` 感知换代重建 `context.tools`，配置作废/`set_workspace` 切项目同样触发）。单发路径（cron / headless CLI / workflow / 子代理）无下一轮可自愈，经新增 `await_pool_ready()` 在建 agent 前等池就位（暖池零 I/O 早退）。单 server 连接超时对齐 CC 默认 30s，连接测试共用同一常量
- **MCP 状态可见性** — 新增 `mcp.status` 进程级广播 + `get_mcp_status` RPC（`protocol/events.json` 单一事实源）：失败的 server 按当前工作区过滤后 toast 轻提示（跨项目不打扰、全部成功保持安静）；设置 → MCP 的 server 卡片新增状态徽标（绿=已连接·悬停看工具数 / 红=失败·悬停看原因 / 灰呼吸=后台连接中），面板开着时经 window 信号即时刷新

### Fixed
- **配置作废与在途加载竞态** — `_load_pool` 进锁后校验 manager 身份，被作废的孤儿 manager 不再被 start（其子进程曾脱离一切清理追踪）；作废与进程退出均取消在途加载任务
- **轮首刷新基线竞态** — `_mcp_gen` 基线改在触发加载前采样：快 server 在 initialize 期间加载完成不再导致会话永远拿不到 MCP 工具
- **两个 server 启动循环合并** — 超时/异常/状态记录脚手架单份（`_start_servers(persistent=...)`），状态投影 `_server_status_list` 为 RPC 与广播共用形状；顺带修掉 scheduler→tools→cron provider 的循环导入

## [0.2.42] - 2026-07-11

### Added
- **macOS 天气式悬浮侧栏** — 左侧栏浮层化：四边留 10px、12px 圆角（与窗口外框弧度一致）、半透明玻璃面板 + 弥散阴影，红绿灯固定于面板内部 (26,20) 不随收放迁移；新增收起/展开（持久化）——收起后侧栏左滑淡出、顶条浮出与红绿灯同中心线的展开钮。右侧后台任务栏同步浮层化（与左对称），任务卡统一为 L2 卡片规范
- **Lumi Glass 设计语言落地（用户拍板 BAAAABBA 组合）** — 三级材质（L1 悬浮面板 / L2 卡片 / L3 轻浮层）与圆角四档收敛：Composer 升级玻璃悬浮岛（`.composer-glass`）、定时任务未读徽标改金字淡底胶囊、弹窗圆角统一 12px（`--radius-panel` → `rounded-panel` 令牌）、审批卡收敛为 L2 卡片；弹窗/菜单/Toast 刻意保持不透明。悬浮面板几何常数 `FLOAT_GAP` 单源化，`ResizeHandle` 新增 `shift` 贴回面板可见边缘
- **`lumi serve --exit-with-parent`** — sidecar 防孤儿：以 stdin 管道拉起，父进程死亡（含崩溃/强杀）即读到 EOF 数秒内自退，杜绝孤儿进程与新实例抢同一 checkpoint 数据库（读写悬挂表现为「会话打不开」）；Electron 端配套单实例锁（双开聚焦已有窗口）

### Fixed
- **MCP 服务器连接挂死拖垮会话就绪** — 单个服务器连接+加载加 15s 超时：端口被其它程序占用（TCP 可连但永不响应）或服务假死时，过去会无限挂起并持池锁堵死所有后续会话的 `gateway.ready`（前端表现为会话永远空白）；现在超时跳过该服务器并记录原因
- **后台子代理事件泄漏进主流** — agent 工具后台模式立即返回后，其 run_id 被过早移出活跃集合，子代理后续事件祖先匹配落空、以 `parent_id=""` 混入主流：截断主回复气泡（粗体从 `**` 中间腰斩）、散落孤立工具卡。改为 run 登记保留至轮末（`_track_agent_run` + 回归测试）
- **会话切换与连接生命周期加固** — activate 乐观切换（先落 UI 再建连，seq 判废晚到结果不回拽）；连接创建即登记 connsRef（快速连点不再开出重复 WS）+ 死连接驱逐重建；历史加载失败不再静默悬置（身份先行 + 重连补拉 + 空会话点击自愈重拉）；重连补拉不覆盖流式在途内容（`hydrateHistory` 统一水合）；连接指示灯按真实状态点亮
- **mac 顶条按钮"画得对、点不准"** — macOS 26 下根合成层内容在原生标题栏高度带内鼠标命中整体上移 ~14px；新增 `.titlebar-interactive`（no-drag + translateZ 独立合成层）修复，顶条重构为按钮区/纯拖拽条分离矩形（不再依赖挖洞）。诊断期顺带：Electron 33→43（Tahoe 适配）、`titleBarStyle` hiddenInset→hidden（视觉一致，均非修复本体、刻意保留）
- **设置导航选中框消失** — v0.2.38 引入的 shadcn tabs line 变体强制透明选中底、在产物中后排压掉调用方的 `bg-line`；改在组件层去掉强制项并把默认选中底按变体作用域化
- **打开定时任务瞬间可误发消息** — 历史加载完成前 cron 视图短暂显示欢迎页+可编辑输入框，手快会把消息发进执行线程；改为「加载中」占位

### Changed
- **定时任务执行会话不再显示任务 prompt** — cron 首条消息改经 `synthetic_human_message`（items:[] 声明制），run 视图直接呈现执行过程；prompt 本体在任务详情页可见

## [0.2.41] - 2026-07-10

### Added
- **MCP 连接测试** — 设置 → MCP 的 server 卡片新增雷达图标：点击实际连一次 server（临时会话，不动常驻会话池），弹窗展示握手信息（server 名/版本/耗时）与「工具 / 提示 / 资源」三类能力清单（tab 计数 + 过滤）；工具/提示条目点开看参数——名称、类型、必填/可选、描述，嵌套 object 经「N 个字段」胶囊逐层下钻（解析 `$ref`/`allOf`/`anyOf`，深度封顶 5 层防递归 schema 打转）。新增 `test_mcp_server` RPC（`protocol/events.json` 单一事实源同步）

### Fixed
- **缺 `transport` 的配置「测试绿灯、加载失败」分歧** — transport 推断（有 url → HTTP，否则 stdio）下沉到加载侧归一化点 `_normalize_server_config`，会话池与连接测试共用：Claude Desktop 风格配置（不写 transport）两路行为恒一致，顺带修正其被误判为无状态会话的问题
- **stdio 静默补丁并发竞态** — `sessions.stdio_client` 的临时 patch/restore（会话池 start 与连接测试并发时互相恢复错原值）改为模块 import 时一次性永久包装
- **连接测试子进程可能被误杀** — 探测的 stdio spawn 与会话池 start 的 PID 快照互斥（只锁 spawn 一瞬），避免探测进程被误归入某池的 `_child_pids` 后遭清理误杀

### Changed
- **FormModal 支持 `bodyClassName`** — 内容区高度可按弹窗覆盖（默认仍 `max-h-[62vh]`），测试弹窗以固定高度呈现，切 tab / 展开条目时窗口尺寸不再跳动

## [0.2.40] - 2026-07-10

### Fixed
- **远程定时任务未读角标不显示** — `cron.result` 是进程级广播，过去只在会话连接消费，而远程机器通常只有控制连接（无活跃会话连接），远程任务完成后侧栏未读角标永远不 +1。改为控制连接也消费 `cron.result`（`seenCronRef` 按 `job_id:started_at` 去重，保证本地会话连接 + 控制连接双收也只算一次）
- **未读徽标可能永久卡死** — 「看一条消一条」下，被保留策略清空 `thread_id` 或超出 Runs 窗口的执行不可点开，其 tid 会永远滞留未读集合致角标归不了零；进任务视图时按当前可见 run 对账、剔除够不着的 tid，并给未读集合封顶 500，避免高频任务无界膨胀 localStorage

### Changed
- **定时任务未读改为按 run 追踪（看一条消一条）** — 侧栏「N new」从任务级整数计数（进视图即整批清零）改为按每次执行的 `thread_id` 记录的未读集合：点开某条执行消一条、全部看完才归零。`cron.result` 广播新增 `thread_id` 字段（`protocol/events.json` 单一事实源同步）
- **侧栏机器色点移到行首** — 多机时的机器环境色点从行尾移到任务名/会话名之前（行首），会话行同步移除 hover 淡出（不再与右侧 `⋮` 菜单冲突）

## [0.2.39] - 2026-07-09

### Added
- **Windows/Linux 自绘窗口标题栏** — 替换原生两行 chrome 为一体化标题栏：Lumi 图标 + 文件/视图/帮助下拉菜单（视图内含语言子菜单）+ 最小化/最大化/关闭按钮，整条为 `-webkit-app-region: drag` 拖拽区、按钮/菜单标 `no-drag`；mac 保持原生 `hiddenInset` 交通灯不变。窗口控制经 `lumi:window:*` IPC，最大化状态由主进程 `maximize`/`unmaximize` 事件单一推送（`onMaximizedChange`）。另保留一个**隐藏的原生菜单**专供键盘快捷键（Ctrl+N 新对话、Ctrl+, 设置、Ctrl+R 重载、缩放、Ctrl+Shift+I 开发者工具、Alt+F4 关闭），展示与快捷键分离
- 标题栏/菜单用系统 UI 字体栈（`titlebar-native-font`），观感贴近原生

### Changed
- **视图/帮助菜单动作单一实现** — 隐藏原生菜单的 click 与 `lumi:menu-command` IPC 共用 `runMenuCommand`，缩放/重载/devtools 逻辑不再两份
- **标题栏 memo 化** — `AppTitleBar` 用 `memo` 包裹 + 传入稳定 `startNewChat` 回调，流式 token 期间不再让标题栏子树陪跑重渲染（对齐 Sidebar 策略）；View 菜单重复项收敛为数据驱动表

## [0.2.38] - 2026-07-09

### Added
- **MCP 管理面板（desktop 设置 → MCP）** — 可视化增删改、启用/禁用 MCP server，覆盖 stdio / streamable_http / sse 三种传输，「表单 / JSON」双模式编辑。作用范围分**全局 + 项目两层**：全局层写该机器 `~/.lumi/mcp_server.json`（跨项目共享，尊重 `--config-dir` / `LUMI_CONFIG_DIR` 覆盖），项目层写 `<项目>/.lumi/mcp_server.json`（叠加/覆盖全局同名 server，仅绑定该项目的会话加载）。开关禁用 = 存 `disabled:true`，加载侧剥离该元字段（绝不下传 langchain adapter）；改动下次新会话加载生效
- **MCP 配置分层加载 + 按项目分池** — `_load_merged_mcp_config` 合并「全局 ∪ 项目」、项目同名覆盖；`MCPSessionManager` 单例改为按 `project_dir` 分池（`_pools`），不同项目各自一批持久会话；`project_dir` 经 `_current_project_dir` contextvar 从 `get_tools` 传到 MCP provider，`AgentBridge.initialize` 无条件预算工具并带上会话项目根（否则常见路径落到 `create_agent` 内部无 project_dir 的加载，项目级 MCP 加载不到）。子 agent / workflow / cron 不传 → 默认只看全局层
- **gateway RPC + 协议** — `list_mcp_servers` / `save_mcp_server` / `delete_mcp_server`（`lumi/gateway/mcp_rpc.py`），`protocol/events.json` 单一事实源锁一致

### Fixed
- **作废一个池不再误杀其它池的 MCP 子进程** — 旧 `close()` 用 `_kill_child_processes()` 扫杀整个进程后代，分池后作废一个池会连带 SIGKILL 别的活跃会话的子进程。改为每个 manager 只 SIGKILL 自己 start 期间记录的 PID（前后快照 diff 精确归属），优雅 `aclose()` 只拆本池；全进程 SIGKILL 兜底降级为仅进程退出（`close_all_pools`）
- **作废路径 resolve 口径对齐** — `mcp_rpc` 的 `project_dir` 与 `AgentBridge` 建池 key 同样 `expanduser().resolve()`，否则 symlink 路径（如 macOS `/tmp`→`/private/tmp`）下作废 pop 不中、面板改动对该项目静默不生效
- **损坏配置文件不再被 save 静默抹除** — save/delete 写前用 `_read_for_write` 区分「文件缺失=空」与「JSON 损坏=抛错中止」，绝不用 `{}` 覆盖已有配置；list 仍宽松（损坏显示为空，不阻断面板）
- **JSON 模式校验必须是对象** — 前端提交非对象（数组/标量/null）时报错拦截，避免加载侧静默丢弃
- **server 改名容错 + 密钥权限** — 改名的删旧键失败落 `reload` 回真实态、不误报成功；`mcp_server.json` 以 `0o600` 落盘（env/headers 可含密钥，与 channels 一致）；切机器不再多打一次错配的 `listMcpServers`

### Changed
- **配置变更精准作废（借鉴 Claude Code 的 config-hash diff）** — `invalidate_mcp_pools` 只关 merged 配置 hash 真变了的池，没变的（如某项目自己覆盖了被改的全局 server）原样保留、完全不打断；池数超上限（`_MAX_POOLS=16`）时优雅淘汰最久未用池，bound 住长跑 serve 多项目切换的子进程增长
- **清理** — 复用 `short_hash` 取代自造 sha256 截断；删死转发 `_load_base_mcp_config` 与 `start` 不可达的 `mcp_config is None` 分支；`close_all_pools` 去掉从未走过的 `kill_children=False` 默认参数

## [0.2.37] - 2026-07-09

### Fixed
- **Windows 目录选择器支持切换盘符**（#1）— 盘符根（如 `C:\`）的上级导航到虚拟「此电脑」节点，列出所有可用盘符（`os.listdrives()` 过滤已挂载卷），可从 C 盘切到 D/E 盘；`list_dir` 返回 `selectable` 标志，虚拟根不可选作项目目录、不显示新建文件夹。macOS/Linux 路径处理不受影响

### Changed
- **盘符根判定并入 `_parent_for_list_dir`** — 盘符根即 `ntpath.dirname(path) == path` 且有盘符，复用已算的 parent 比较，删掉独立的 `_is_windows_drive_root` 与 normcase/normpath 比对；`_windows_drive_roots` 删去 `requires-python>=3.12` 下恒为真的 `hasattr` 守卫与 A–Z 手写 fallback。前端条件渲染扁平化、`listDir` 返回类型去重复声明

## [0.2.36] - 2026-07-08

### Added
- **消息过长兜底：CallModel 撞 PTL 的反应式压缩回路** — 主对话链撞 `prompt-too-long`（400）不再直接抛给用户：`call_model` 返回 `Command(goto="Summarizer", update={"ptl_retry": True})` 折返 `Summarizer` 的 `_ptl_forced_compact` 绕阈值门强制压缩（`select_for_ptl_compaction` 按 API round 保留尾部 2 组、保住进行中的工具轮），经正常拓扑重试；成功清 `ptl_retry`，置位期间再撞直接抛原 PTL（每次 PTL 只换一次压缩机会，收敛不死循环）。摘要调用在 `Summarizer` 节点名下运行——gateway 的 `compaction.status` 拦截天然生效，摘要不外泄为助手消息。识别串补 Bedrock 的 `input is too long` 变体
- **单轮工具结果聚合上限** — 除单条 `once_tool_max_bytes` 外新增 `round_tool_ratio`（默认 0.3）：N 个并行工具各自合规但合计超预算时，单条上限收紧为公平份额（budget // 候选数，下限 `_MIN_PER_MSG_CAP`），只处理超份额的候选，每条至多处理一次（截断元信息恒描述真实原始输出、不产生指针套指针的二级卸载）
- **工具结果落盘附头部预览** — 卸载替换文本除路径 + 统计外附前 2000 字节内容预览（换行边界收口），多数场景模型看预览即可、省一次 read 往返

### Changed
- **`is_use_tool` PTL 路由守卫** — LangGraph 中节点返回 `Command(goto)` 与其条件边取并集，PTL 路由步的条件边仍被求值：`ptl_retry` 置位时返回 `END` 空分支，避免末条 `ToolMessage` 把 `OnAgentStop` 拉进同一 superstep 分发 Stop hooks
- **摘要核收敛为 `_summarize`** — `summarizer` 正常路径与 `_ptl_forced_compact` 共用「剔悬空 tool_use + `run_summary`」核，消除逐字重复；熔断包裹因失败语义不同（正常 raise、PTL 放行）留在各调用方

## [0.2.35] - 2026-07-08

### Changed
- **消息显示声明制：`lumi.items` 单一显示真源，content 只给模型** — 每条 HumanMessage 构造时声明显示（气泡条目 text/sender/ts/files，消息级 ts 单条下沉规则一并收在写侧 `_build_user_message`），显示侧零正则：`text_cleaning.py` 整个删除，`is_meta` 契约删除（`items: []` = 合成消息声明不可见，`synthetic_human_message` 构造；摘要 carrier / 后台通知 / read 工具回灌 / hook reminder 全部改声明）；未声明消息（cron / 子 agent / workflow / dream 直接构造）fallback 到 content 掉 `injected_prefix` 前缀块。**不兼容旧数据**：既有会话的内联注入块 / 旧 is_meta 消息会裸显
- **注入块结构化标记** — `inject_text_into_message` 成为唯一注入原语（`prepend_reminder` 删除）：注入文本作独立 block 前置并累加 `additional_kwargs["injected_prefix"]` 计数，显示按计数掉块不再嗅探文本；计数放 kwargs 不放 block 自定义字段（langchain_openai 对 text block 原样透传，多余字段会直达 provider API）
- **附件全链路结构化** — wire `send_message` 新增 `files` 路径数组参数（desktop 前端不再拼 `<attached-file>` 标签），`persist_image_blocks` 改返回 `(content, paths)` 与文件附件合流，bridge 统一拼标签块注入（模型侧）+ 写 `items.files`（chip 显示侧）；feishu 删 `attach_files_to_text` 改经 `run_turn(attachments=...)` 透传；`stream_response` 的 `is_meta` 参数改名 `synthetic`
- **checkpoint label 与气泡同源** — `_extract_label` 的 content 文本嗅探（command 标签正则 + `startswith("<")` 跳过）整个删除，Rewind 标签直接取 `visible_user_text`（命令轮即 `/name input`、IM 轮即用户原文）；`_user_items` 纯投影化（改用 `declared_items`，dict 形态消息 items 不再漏读），human 双形态判定收编为 `message_visibility.is_human_message` 单一实现

### Fixed
- **纯附件消息不再产生空 text 块** — 拖文件不打字发送时空串 content 被包成空 text block 永驻 checkpoint，Bedrock Converse / 严格 OpenAI 兼容端拒空白 text 块导致该会话每轮 400；`inject_text_into_message` 对空串 content 不再生成空块
- **auto 审批分类器不再拿陈旧意图** — `_latest_user_intent` 停在最近一条应显示的用户消息（纯附件轮返回空意图保守裁决），不再上溯把上一轮的旧指令当本轮意图喂给安全分类器
- **空消息不再渲染空气泡** — 空文本无附件的 wire 消息声明 `items: []` 不可见，不再产生 `[{}]` 的永久空用户气泡
- **图拓扑过时注释修正** — `compact.py` / `nodes.py` docstring 与 CLAUDE.md 流程图统一为实际拓扑 `Summarizer → PreprocessMessages → CallModel`（原注释写反会误导出「注入块被当轮压掉」的错误实现）；`format_system_reminder` 并入 `format_reminder` 消除同构 wrapper

## [0.2.34] - 2026-07-08

### Changed
- **上下文注入重构：turn_context → UserPromptSubmit hook + marker + 条目级增量 diff** — env / agent 列表 / skill 列表 / 记忆索引 / LUMI.md 从「每轮重建的瞬态前缀消息」改为**持久注入进末条用户消息**（新增 `preprocessing/context_inject.py`，注册为 UserPromptSubmit 内置 hook，`preprocess_messages` 新增该事件分发点）。`additional_kwargs["ctx_digest"]` marker 记录「模型已知状态」的条目级 digest：首轮全量注入；条目变更只注增量 diff（相对上一个 marker），diff 比全量长退化整块；变更源文件被本会话 write/edit 过则静默结算（marker 更新、不注通知文本）；全无变化零 update。收益：写记忆 / 改 skill 只动消息尾部，**前缀历史缓存不再整条作废**
- **删除 `my_trim_messages` 消息修剪** — 主对话链与 structured_output 链的 trim 全部移除（连带 config `token.trim_messages_ratio` 字段），上下文溢出控制全责交 Summarizer；`tool_call_chain` 的 `turn_context` 参数与 `_turn_context_inserter` 一并删除
- **detector 退化为纯加载缓存** — 变更判定状态移入消息 marker（per-thread、随 checkpoint 持久），`FileSetChangeDetector` 删除 `check()` 消费型 changed 语义与 `_INITIAL_DIGEST` 哨兵，只留 `peek()` + digest 缓存；`AgentConfig`/`SkillConfig` 新增 `path` 字段（自改静默判定的源文件映射）
- **压缩先于注入，在线/离线形态同构** — 图拓扑调整为 `Summarizer → PreprocessMessages → CallModel`：上下文注入永远发生在压缩后的世界里（旧注入块与 marker 随历史删除，hook 自动全量重建），根除压缩轮增量 diff 悬空与 orphan 残留；在线摘要改为独立 carrier 消息（`[Human(<summary>), 用户消息]`，`inject_summary_into_message` 删除），离线 `/compact` 去掉 AI tail 副本只留 carrier——两端压缩后同为 `[System?, Human(<summary>), Human(ctx+用户)]`
- **marker 每轮前移** — 无变化轮也把 marker 写到末条消息（content 字节不动、缓存无损）：自改静默的"写过"名单窗口每轮收口（防不改 digest 的写入永久滞留窗口、误静默后续外部变更），倒扫恒在上一条用户消息停下（消除长会话 O(n²)）

### Fixed
- **坏 skill/agent 配置文件不再炸穿上下文注入** — `SkillConfig`/`AgentConfig` 构造捕获 pydantic `ValidationError`（如 frontmatter 里 `name: 2024` 被 yaml 解析为 int），跳过该文件并告警；此前异常经 detector 穿透 UserPromptSubmit hook 被 dispatch 静默吞掉，导致整轮 env/skill/记忆等全部不注入且每轮复现
- **在线压缩的摘要 carrier 排序修正** — `add_messages` 对「Remove + 同 id 重加」是原地更新不改顺序，carrier 实际落到末条（全量注入和 marker 会打在摘要上而非用户消息）；重加的用户消息换新 id 成为真正的 append，测试改为过真实 reducer 断言合并后顺序
- **非法工具路径不再炸穿注入扫描** — `_scan_history` 对模型生成的 `file_path` resolve 加防护（null 字节等抛 ValueError/OSError 时跳过该条）；此前异常被 dispatch 吞掉且坏消息永留扫描窗口，该 thread 余下所有轮注入永久失效
- **悬空 tool_use 不再打挂摘要** — 拓扑调换后 Summarizer 先于 cleanup 运行，中断残留的 AIMessage(tool_calls) 直发摘要模型会被 Anthropic 400 拒绝并触发熔断；现喂给摘要链前从副本剔除（state 里的残留仍由压缩删除）
- **dream 语料与审批分类器不再被注入块污染** — 注入块持久进历史后，`extract_messages_as_text`（dream transcript 导出）与 `_latest_user_intent`（auto 审批意图提取）对用户消息剥 `system-reminder` 等注入块，真实用户输入不被系统注入文本淹没
- **摘要 carrier 显式 meta 化** — `build_summary_carrier`（summary.py，在线/离线共用的单一构造点）用 `meta_human_message` 打 is_meta 标记，不再依赖显示侧正则剥空的隐式路径；`short_hash`/`resolve_under_project` 收编 `workspace_id`/`validate_path` 的同构实现

## [0.2.33] - 2026-07-07

### Fixed
- **frontmatter 解析容忍 BOM 与开头空白** — Windows 编辑器写入的 UTF-8 BOM（不可见字节 `﻿`）或文件开头空行会导致 agent/skill 文件被误报「缺少有效 YAML frontmatter」而跳过加载（用户实际反馈），`parse_frontmatter` 解析前先剥离；YAML 语法错误不再静默吞掉，warning 带出真实错误原因便于定位

### Changed
- **清理最后一处旧式 `Optional` 注解** — `LumiConfig` 改用 `X | None` 写法（补 `from __future__ import annotations` 支持类体自引用），全仓库 ruff UP 规则零残留

## [0.2.32] - 2026-07-06

### Changed
- **设置页整体重构，统一排版规范** — 新增 `desktop/src/components/SettingsKit.tsx` 共享排版原语（`Section`/`SectionGroup`/`Row`/`Field`/`TextInput`/`Card`/`SegmentedControl`/`FormModal`），四个面板（通用 / 模型 / 渠道 / 连接）统一标题字号、卡片、输入框、分段控件与段间距；删除各面板各自造的 `Row`/`Field`/`Labeled`/`Seg`/`Segmented`/`ModelChip` 等重复实现。手写按钮改走 `Button` 组件
- **表单皆弹窗** — Provider / 飞书 / 远程机器的编辑与添加从「整页切换视图」改为次级 modal（`FormModal`），主面板只保留干净列表
- **模型页去平铺** — Provider 列表只显示「名 · Base URL · 模型数」不再铺开模型 chip 墙；会话模型 / 会话标题模型 / 审批分类器模型三处用途收为「当前值 + 更改」行，共用一个模型选择弹窗（搜索 + 按 provider 分组 + 指针类含「跟随会话模型」）
- **移除渠道连接状态灯** — 侧栏渠道组头与设置卡片上的 `.chan-orb` 黄点删除（含其 CSS），连接状态改用文字标签呈现（失败态文字转红并显示具体原因）

### Fixed
- **共享原语的样式覆盖失效** — `TextInput`/`Card`/`SegmentedControl`/`FormModal` 的 className 拼接改用 `cn`（clsx + tailwind-merge），调用方覆盖类（如模型行 `h-8` 覆盖 `inputClass` 的 `h-9`）正确生效
- **段间距在部分面板错位** — `Section` 不再依赖 CSS `:first-child`（模型/渠道页 `MachineTabs` 在前时首段顶距不一致），段间距统一由 `SectionGroup` 的 `space-y-7` 提供
- **同名模型未去重** — 保存 provider 时按名 `Set` 去重，杜绝模型选择弹窗里的重复 key 与双高亮

### Performance
- **ProviderForm 草稿改本地 state** — 编辑 provider 时键入不再 `setState` 到父级触发整个模型面板重渲染（与飞书 / 远程表单一致）

## [0.2.31] - 2026-07-06

### Fixed
- **刷新时侧栏不再闪现「暂无会话」** — 会话列表缺一个「首次 `list_sessions` 已返回」标志，`sessions=[]` 在连上到数据返回的空窗里被误判成空态。新增 `sessionsLoaded`（`refreshSessions` 全量刷新完成一轮即置位），未加载完成前空态位静默留白，加载后确实为空才显示「暂无会话」（recent tab 与单机 all tab 两处判定同步加守卫）

### Changed
- **侧栏 IM 渠道组头移除连接状态灯** — 「飞书 · 项目」组头旁的 `.chan-orb` 黄点删除，连接状态仍在「设置 → 渠道」ChannelsPanel 里展示，不受影响

## [0.2.30] - 2026-07-06

### Added
- **会话标题自动生成**（`gateway/titler.py`，对齐 claude-code sessionTitle 机制）— desktop 会话第 1 条可见用户消息发出时即后台生成标题（不等本轮跑完，几秒内上屏），第 3 条消息时用对话尾部 1000 字符再生成一次纠正话题漂移后定稿；结果存 session_meta sidecar 的 `auto_title`（展示优先级 手动 title > 渠道 channel_title > auto_title > 首条消息），经新事件 `session.title` 广播、前端就地更新侧栏。斜杠命令等合成消息不触发；IM 渠道会话有 `channel_title` 不生成；生成失败本连接内放弃不重试；写入前重查会话存在性（防删除竞态复活幽灵 meta 条目）与手动重命名（手动名永远优先，前端另有本窗口手动命名标记挡晚到广播）
- **titler 模型指针** — providers 分区新增顶级 `titler` 指针（`set_titler` RPC + 设置→模型面板「会话标题模型」区块，与分类器共用 `PointerSection` 选择 UI），未配置时跟随会话 active 模型；provider_store 的 classifier 专属存取泛化为命名指针（`get_pointer`/`get_pointers`/`resolve_pointer`/`set_pointer`，`_POINTER_KINDS`），互不丢失、删 profile 自动清失效指针
- **新会话侧栏即时可见** — 首条消息发出时前端乐观插入会话条目（此前首轮跑完前会话在侧栏缺席、切出去回不来）；`refreshSessions` 整表替换时保留「运行中或当前活动、但后端尚列不出」的条目，发送失败自然回收

### Changed
- **`ResolvedModel.conn_kwargs()`** — auto 分类器（nodes.py）、标题生成（titler.py）、视觉工具（vision.py）三处手写的 base_url/api_key 连接拼装收敛为一个访问器
- **`list_providers` 读盘 3 次 → 1 次** — 指针表经 `get_pointers()` 一次读盘取全；`AgentBridge.snapshot_messages` 支持显式 `thread_id`（后台任务不依赖 bridge 当前指向）

### Added
- **IM 长会话每日记忆整理**（`channels/feishu/daily_dream.py`）— 渠道到点对有新消息的常驻会话两阶段维护：先串行 dream 沉淀记忆（per-thread 运行锁 + 忙线程整批重试 3×180s），再并发限流 summary 压缩历史（`Semaphore` 防 429），让一群/一人一个的永久会话不无限膨胀。**次序不变量：dream 失败绝不压缩**——成功与否以快照时刻是否推进为准（bg-task 吞异常，返回值不可信），失败留到明天重试，未沉淀的历史不会被压掉。渠道 thread（`is_channel_thread` 前缀判定）同时退出 Stop 钩子的增量 dream。desktop 渠道设置新增 Dream 开关 / 执行时间 / Summary 并发配置
- **离线强制压缩 + `/compact` 命令** — `AgentBridge.compact_thread` 复用 summarizer 压缩核（`run_summary`）对空闲会话主动压缩：删除整段历史、重建为「摘要载体 + 末条 AI 副本」，经 `aupdate_state(as_node="CallModel")` 写回、全程不外泄到前端流。`/compact` 两端可用；末条副本刻意不带 usage（防压缩后误判仍超阈值）、摘要载体刻意不带 ts（防判活误报）
- **`/dream-session` 命令（仅 IM）** — 只综合当前永久会话的手动 dream；dream 系按载体分流：desktop 只见 `/dream`、IM 只见 `/dream-session`。飞书 `/help` 卡片将 system 类命令归入「会话控制」组不再混进技能组
- **desktop 上下文用量指示器还原** — `load_history` 随 items 返回末条 AI 的 usage，切会话/重启后指示器不再等下一轮才恢复

### Changed
- **dream 门控重构：计数游标 → 时间戳/会话数** — 旧「human 计数游标」与离线压缩互相打架（压缩后计数低于游标，dream 永不再触发）。现 desktop 短会话门 = 自上次 dream 以来活跃的其他会话数 ≥ `auto_dream.min_sessions`（新配置，默认 3；`min_human_messages` 删除）；IM 长会话判活 = 存在落库 ts 晚于该 thread 上次 dream 快照时刻的真实 human（`latest_human_ts`，压缩免疫）。成功后写回**快照时刻**而非完成时刻（dream 后台跑时新到的消息不被误判为已综合）；`dream_cursor` 表退役、`SessionSummary.human_count` / `count_human_messages` 删除
- **dream 互斥下沉到共享底座** — per-project `asyncio.Lock`（`dream_lock.project_lock`）移进 `_run_dream_fork` 内部，四个入口（Stop 钩子 / `/dream` / `/dream-session` / 每日定时）都绕不开，MEMORY.md 恒单写者；`_in_flight` 集合降级为手动命令入口的同步快返 UX
- **压缩后会话不再从列表消失** — `_summary_from_snapshot` 取不到首条 human（已并入摘要）时不再丢弃会话，`first_message` 留空、标题由上层 meta 兜住

## [0.2.28] - 2026-07-04

### Changed
- **工具结果 offload 落盘改到临时目录** — 卸载文件路径由 `<config_dir>/offload/` 改为 `lumi_tmp_dir("offload")`（POSIX 下 `/tmp/lumi-<uid>/offload/`），复用 `lumi/utils/paths.py` 的每用户私有临时目录约定（0700 + 属主校验，OS 自动清理），不再污染项目 `.lumi` 目录；回喂模型的仍是完整绝对路径，read/grep 命中不受影响

## [0.2.27] - 2026-07-04

### Changed
- **auto 审批分类器补反绕过条款**（参考 Claude Code 的 deny-rule circumvention guidance）— 分类器 prompt 新增：识别「换工具绕过限制」——被禁/被拦工具的活儿改用 bash `sed -i`/`cat >`/`tee`/重定向/`python -c`/heredoc 去做同一件事（如写/改一个 write/edit 被拦的文件）即属绕过，reject 并在 reason 点明；补上此前仅 `safety.py` 硬编码「bash 写保护文件」覆盖不到的通用绕过面
- **reject 回喂文案收紧** — auto 分类器拒绝后回喂模型的引导由「改用更低风险的方式完成目标」（易反向诱导模型找绕过路径）改为三段式：可改用自然完成同一目标的其他工具、但不得换工具绕过这条拦截、该能力确有必要则停下向用户说明并请求授权

## [0.2.26] - 2026-07-04

### Changed
- **auto 审批分类器由三档简化为二档** — 裁决从 `approve/ask/reject` 收敛为 `approve/reject`，去掉「回落人工确认」的 ask 档：可疑或意图不明确的操作由 AI 直接在 approve/reject 间裁决，不再打断用户。prompt 同步强化——判断重心放在会**修改真实环境**的操作上（写入/编辑/删除文件、有副作用的命令、网络提交等），只读/查询类直接放行；并新增 bash 后台运行须用 `run_in_background` 参数而非 `&` 的引导（命中即 reject 并在 reason 提示改用参数）。分类器调用失败仍 fail-closed 回落人工审批
- **`project_slug` 复用哈希单一事实源** — dream 导出目录名从 ad-hoc `str(project_dir).replace("/", "-")` 改用新增的 `project_slug()`（`<basename>-<哈希6位>`），哈希段复用 `workspace_id.get_workspace_id()`，消除并行的路径→id 方案

### Fixed
- **临时目录根创建的竞态与安全加固**（`lumi/utils/paths.py`）— 三处修复：① 根目录并发首建缺 `exist_ok` 导致 `FileExistsError`（bg_tasks / feishu inbound / dream 跨线程并发触发）；② POSIX 下 `/tmp/lumi-<uid>` 路径可预测，预建劫持时属主非本用户即 fail-closed 拒用、已存在目录显式收紧 `0700`，避免把含用户数据的产物写进他人目录；③ POSIX 分支硬编码 `/tmp`，改为仅 `/tmp` 可写时用短路径、否则回落 `gettempdir()`（尊重 `$TMPDIR`），覆盖只读 `/tmp` 的受限容器/沙箱

## [0.2.25] - 2026-07-03

### Changed
- **agent 工具默认后台执行** — `run_in_background` 默认值 False → True：子代理默认后台并行、完成时通知带回结果，多个独立子任务一次性并行派出成为自然路径；仅当单个子任务结果是继续推进的唯一前提时才传 `false` 同步等待。注意后台子代理无交互审批通道、固定 privileged
- **工具描述本地化与单源化** — bash/grep 描述里残留的 Claude Code 大写工具名（Glob/Read/Edit…）改为 Lumi 实际注册的小写名；skill 描述的系统命令例子从不存在的 `/skills /mcp` 纠正为 `/stop /clear /help`；bash 悬空引用的"git 安全协议"落实为具体规则；删掉 bash 描述内嵌参数表与 cron 调度格式的双源维护（参数细节归 Field description 单源）；ask 增加"何时不要问"节制条款（有默认按默认做、能验证的去验证）；agent 描述补"何时使用/不用"与并行派发策略；grep/skill 描述统一为中文；顺带修 edit docstring 断词、todos 措辞矛盾、glob 描述过简

### Added
- **IM 渠道斜杠命令** — 飞书消息以 `/命令` 开头即触发（群里 `@机器人 /命令` 亦可，显示名含空格也能正确识别）。命令按类别天然定可用范围：skill 命令（含 `/dream`）与 desktop 同一套，走 `bridge.stream_command`（仅单条成批 + 纯文本时识别，未知 `/xxx` 按普通文本喂模型）；渠道系统命令仅 IM 提供（desktop 有对应按钮）：`/stop` 停当前轮 + 并发停掉本会话全部后台任务 + 清积压队列，`/clear` 清空会话历史（与 desktop 删除同口径 + 广播），`/help` 直答彩色 header 命令卡片（不为此隐式建常驻 bridge）。解析在渠道无关的 `channels/commands.py`，第二个 IM 渠道可直接复用
- **`cancel_thread_bg_tasks` 共享原语**（`bg_process.py`）— 按 thread 并发停掉全部运行中后台任务，IM /stop 与未来"停止本会话全部任务"共用

### Fixed
- **`peek()` 绕过 digest 缓存** — `SkillChangeDetector.peek()` 此前每次全量重扫解析 SKILL.md；加载缓存下沉 `FileSetChangeDetector` 基类，peek/check 共享 digest 缓存且不影响 check 的变更注入语义（desktop 命令菜单同样受益）
- **desktop 删除渠道会话不广播** — 补 `channel.activity` 广播（复用 `_channel_of` 单点判定），其他连接/旁观视图即时刷新，与渠道侧 /clear 同口径
- **忙时队列消息搁浅** — /stop 取消窗口与 /clear 持锁窗口内入队的消息此前无人接手；所有"拿锁跑用户轮"入口统一 `_locked_drain`（登记 run_tasks 供 /stop 取消），命令收尾各自接手残留队列

## [0.2.23] - 2026-07-02

### Added
- **桌面安装包内嵌后端** — `scripts/build-desktop.sh` 一条命令出完整安装包（dmg/nsis/AppImage）：PyInstaller 打后端 onedir（`--collect-data lumi --copy-metadata lumi`，依赖严格来自 `uv.lock` 的一次性构建环境）→ 经 electron-builder `extraResources` 内嵌进 app → 版本号自动同步 pyproject。用户拖进 Applications 即用，无需装 Python/uv。打包版 sidecar 优先用内嵌后端（`Resources/lumi-backend/`），无则退回 PATH 上的 `lumi`（瘦客户端模式保留作兜底）；sidecar 注入 `PYTHONUNBUFFERED=1`，PyInstaller 产物接管道时日志不再滞留到退出才刷出

### Changed
- **`.dockerignore` 改白名单式** — 默认全排除、只放行 Dockerfile COPY 的三样（pyproject/README/lumi），build context 从 300M+ 降到 <1M；以后仓库新增目录不会再意外进 context
- **electron-builder 用本地 Electron dist** — `electronDist` 指向 `node_modules/electron/dist`，构建不再从网络下载 100M zip（曾被代理重置导致构建失败），可离线构建；前端依赖装配改 `npm ci`（清光 node_modules 按 lockfile 精确重装），保证打包环境干净

## [0.2.22] - 2026-07-02

### Changed
- **飞书私聊图标改绿色** — 侧栏渠道会话的私聊图标由蓝色改为主题绿（`text-success/80`，亮暗自适应），与蓝色群组图标形成区分

## [0.2.21] - 2026-07-02

### Added
- **飞书会话的后台任务完成通知** — 此前通知按归属 thread 入队后无人认领（desktop 通知轮对渠道会话刻意跳过、飞书侧无消费者），永久滞留。新增 `FeishuInbound.notification_loop`：会话空闲时持锁认领，先发「✅ 已完成」锚点卡（流式卡片必须回复某条消息才能创建；锚点失败不 drain 留队重试），再注入 meta 轮让模型读输出文件、结果经流式卡片推回群里。thread→chat 映射放 `BridgePool.chat_ids`（随配置热重载存活）；被取消（channel 停止/重载）时已 drain 的通知重新入队不丢结果；单 thread 异常只记日志不杀轮询；持锁期间排队的入站消息由 poller 兜底接手
- **bash 后台任务拒绝 shell 后台符 `&`** — `run_in_background=True` 且命令自带 `&` 时直接报错让模型改写（双后台机制叠加会让被追踪的 wrapper shell 瞬间退出、真实进程脱管：任务误报完成、真完成时无通知、取消杀不到）。检测器 `capability.has_background_operator` 做引号/转义/heredoc/herestring/`$((...))` 算术扩展感知，`&&`、`2>&1`、`&>`、`|&`、case `;;&`、位与等合法形态不误伤

### Fixed
- **后台任务按进程组终止** — `bg_process` 以 `start_new_session=True` 起独立进程组，取消/超时/清理走 `killpg` 连同命令内 fork 的后代一起终止（此前只杀 wrapper shell，管道子进程/自守护程序成孤儿）；`cleanup_all` 并发收尾（原串行最坏 5s×N）
- **desktop 通知轮不再空抢 run 锁** — `has_notifications` 快查改按 thread（与按归属认领配套）：渠道归属的通知在队列合法滞留期间，desktop 连接不再每 2s 白拿一次运行锁
- **飞书 channel stop 等待通知轮收尾** — cancel `_notify_task` 后 await 它，通知 meta 轮的流式卡片在 streaming 停掉前关闭（不再冻在「生成中」/ 产生关停噪音）

### Changed
- **通知队列按精确归属认领** — `drain_for`/`has_for` 精确匹配 thread（生产路径通知恒有归属，删除无归属兜底与 `include_unowned` 参数）；`compose_notification_hint` 归位 `bg_tasks.py` 与 `format_notification` 同居（通知生成与注入措辞单一契约）；bridge 三个通知方法 `thread_id` 改必填（TUI 已删，None 分支是死路径）

## [0.2.20] - 2026-07-02

### Fixed
- **飞书 WS 线程退出时优雅收尾** — `lumi serve` 关停 / 渠道 reload 时不再喷 `Task was destroyed but it is pending` / `Event loop is closed` / SSL fatal write 日志：WS 线程 finally 里先 cancel 并收完 lark 专属 loop 上的悬空协程（receive/ping/keepalive），再优雅关闭 WS 连接（3s 超时），最后才 close loop。任务收割对 `stop()` 排队的 `ws_loop.stop` 回调免疫（落在重连 sleep 窗口时重进驱动直至收完，异常不会逃出 finally 弄死线程）；`stop()` 打断 `start()` 的预期 `RuntimeError` 不再记为 WebSocket 异常

## [0.2.19] - 2026-07-02

### Added
- **飞书渠道会话在 desktop 区分呈现** — 侧栏「全部」树每台机器多一个「飞书 · 绑定项目」分组（A2 方案，带 `chan-orb` 渠道状态灯，群/私聊图标区分），渠道会话不再混进项目分组；「最近」流与搜索结果行首带渠道图标。会话名自动取**群名 / 私聊对方姓名**（入站同步进 session sidecar 的 `channel_title`/`channel_kind`，手动重命名永久优先、群改名自动跟随；解析失败的兜底名不落盘且有 5 分钟重试冷却）
- **只读旁观视图** — desktop 打开飞书会话顶部渠道横幅（群名 · 审批模式 · 绑定项目 · 直达渠道设置），输入区替换为只读提示。只读在服务端兜底：流式方法对渠道 thread 直接拒绝、后台通知轮对渠道 thread 不消费——desktop 与渠道 `BridgePool` 各持独立 bridge/锁，写入会绕过渠道的会话串行化并发写坏 thread
- **`channel.activity` 广播** — 飞书每跑完一轮通知所有 desktop：只刷该机器会话列表，正在旁观则重载历史（切回旁观会话也强制重拉，不再显示旧账）；`list_sessions` wire 新增 `channel`/`channel_kind` 字段（服务端 `_channel_of` 按 thread 前缀判定是唯一判定点，前端只消费 wire 字段）
- **`<sender>` 标签 + 消息时间统一落库** — IM 入站消息正文改为 `<sender>姓名</sender>\n正文`（渠道无关约定，纯给模型看，替代旧「姓名：」前缀与合并轮编号列表）；渲染数据（每条原始消息的 `{sender, ts, text}`）结构化存 `additional_kwargs["lumi"]["items"]`，desktop 气泡只读它、不反解析正文（字面标签无法伪造气泡）。消息级到达时刻在 `bridge.stream_response` 统一落库（渠道无关，desktop 消息也有），气泡头渲染「发送者 · 时刻」
- **渠道会话「清空会话」** — 替代「删除」文案（thread 按群确定性派生、删后下条消息原地重建，实际效果是抹掉对话历史）；删除前持渠道侧运行锁（`ChannelManager.thread_lock`，5s 超时如实报错），避开在途轮把删掉的历史写回

### Changed
- **`update_meta` 内置变更检测** — 合并结果与现状一致不写盘（飞书每条消息同步群名免高频整文件写，且「清空会话」删 sidecar 后能如实重建，不再有可失效的内存缓存）
- **`SettingsDialog` 支持 `initialTab`** — 旁观横幅「渠道设置」直达 channels tab；`refreshSessions`/`refreshChannels` 支持按机器刷新（`channel.activity` 只刷来源机器，多机 ready 不再 N² 扇出）

## [0.2.18] - 2026-07-02

### Changed
- **用户级配置合并为单文件 `~/.lumi/lumi.json`** — 原先分散的 `lumi.json`（全局设置）/ `projects.json` / `providers.json` / `channels.json` 四个文件，合并成 `~/.lumi/lumi.json` 的四个分区（`settings` / `projects` / `providers` / `channels`），由新增的 `lumi/utils/config/user_store.py` 统一读写（一次读盘 / section-patch 原子写 / 整体 chmod 600 / 值类型损坏时回落 default）。各领域模块（`global_manager` / `projects` / `provider_store` / `channels.store`）对外 API 不变，内部委托 user_store 读写自己的分区
- **项目配置改用 JSON** — `.lumi/config.yaml` → `.lumi/config.json`，运行时不再读取 YAML（`yaml` 依赖仅保留给 Markdown frontmatter 解析）
- **`provider_store` 写路径少读一次盘** — mutator 经 `_load_all()` 一次读出 `(profiles, active, classifier)` 并传给 `_save`，删除 `_KEEP_CLASSIFIER` 哨兵与 `_save` 内部为取 classifier 的重复读盘（单次 mutation 对合并文件的整文件解析 3 次→2 次）

### Added
- **一次性配置迁移脚本 `scripts/migrate_config.py`** — 把旧格式（四个独立文件 + `config.yaml`）迁到新布局；幂等可重跑，解析失败的旧文件不并入也不删除（保留供手动修复）。迁移逻辑刻意不常驻运行时代码

## [0.2.17] - 2026-07-02

### Added
- **运行中实时切换工具审批模式** — 顶部审批模式选择器（default/accept_edits/privileged/auto）现在改一下就立即推后端（新增 `set_tool_mode` RPC + `Gateway.setToolMode`），对**当前运行轮的后续工具**即时生效，不必等下一条消息。新 RPC 刻意不持 `_run.lock`、不持久化——单字段幂等赋值只影响后续 `is_use_tool` 路由，实时切换正是需求本身

### Changed
- **`tool_mode` 从 state 迁到共享 `LumiAgentContext`** — state 是每个 super-step 的快照，运行中改不动；context 是所有节点共享的可变引用，bridge 改它后下一个节点立即读到，这才让上面的「运行中实时切换」成立。`human_approval` 的 `set_tool_mode` 直接改 context（不再走 `Command.update`）；子 agent / workflow / cron / dream / 后台 agent 一律从/向 `context.tool_mode` 继承设值，不再经 `inputs["tool_mode"]`

### Fixed
- **超大/无法解析的上传图片不再 raw 内联转发** — 图片存盘失败（超 50MB 或 base64 解码失败）时，旧逻辑保留原始 image block、把未压缩 raw base64 直发模型，会超上游图片大小上限触发 API 400；改为丢弃原始块、留文本占位「[图片过大或无法解析，已跳过]」

## [0.2.16] - 2026-07-01

### Fixed
- **多 server 同名飞书群会话在 client 里塌缩成一条** — 本地 + 远程两台 server 都配飞书渠道并进了同一个群时，desktop client 把两台机器上「群 A 的会话」当成同一条（状态互相污染、发消息路由到错的 server、React key 冲突）。根因：IM channel 的 thread_id 按 `feishu-{chat_id}` **确定性派生**，同一个群在两台 server 上得到相同 thread_id，而前端一切（`store`/`connsRef`/`folderStore`/`active`/侧栏渲染/`activity`）都以裸 thread_id 为键。改为**前端会话身份 = `backend + thread_id` 复合键**（`sessionKey`/`keyThread`/`keyBackend`/`beOf` in `desktop/src/lib/utils.ts`），发给后端的 wire 仍是裸 thread_id；`handleEvent` 按连接所属机器归位事件，pin/重命名/删除/选中一律按 thread + backend 精确匹配，不再连带误伤另一台机器的同名会话
- **后台任务多机串号** — `bg_tasks.update` / `list_bg_tasks` 是各机器进程级快照，旧代码整列 `setBgTasks` 会互相覆盖；改为 `replaceBackendTasks` **按机器分段替换**（`BgTask` 前端加 `backend` 标记），`activeBgTasks` 按 thread + backend 双重过滤，stop/dismiss/clear 按当前机器圈定

## [0.2.15] - 2026-07-01

### Added
- **vision 视觉辅助工具（无视觉主模型也能看图/PDF）** — 主模型不具备视觉能力时，`read` 直接注入 image block 它看不懂；新增独立 `vision(file_path, question)` 工具（`lumi/agents/tools/providers/vision.py`），主模型带着自己的具体问题调用（如「这张发票总金额多少」），可对同一文件反复追问。`file_path` 支持本地路径与 http(s) URL（按 `%PDF-` magic 嗅探 PDF/图片），复用 `filesystem/media.py` 压缩管线转 base64、按视觉模型 provider 转格式后单次问答返回文字。**仅当 config.yaml 配了 `vision.model` 时才注册**（`get_vision_tools` 条件加载，`provider_store.resolve_vision` 解析模型+连接，`base_url`/`api_key` 留空复用 providers.json 该模型 profile 连接）
- **上传图片统一持久化** — 桌面/飞书上传的图片经 `gateway/uploads.py` 的 `persist_image_blocks` 统一存到 `~/.lumi/uploads/`（`global_manager.uploads_dir`）并换成 `<attached-file>` 路径引用（与普通文件一致，交 read/vision 消费）。`stream_response` 入口最前处理，裸 base64 不再直发模型。飞书入站图片改为只下载不压缩（压缩下沉到读取端 `media.py`，避免重复压缩）

### Changed
- **只读工具免工作区边界限制** — `read`/`vision`/`glob`/`grep` 等只读工具不受工作区边界约束（可跨项目、读 URL），混合批次（只读+写）里只读部分同样免边界；DENY 规则仍先于此拦截。新增 `capability.is_read_only`，routing 只读快路径与混合批次逻辑对齐
- **飞书依赖改为默认安装** — `lark-oapi` / `python-socks` 从可选 extra 移入主依赖，`uv sync` / `uv pip install .` 即装齐，不再需要 `uv sync --extra feishu`；删除 `feishu` / `all` optional extra，`Dockerfile` 改 `uv pip install "."`
- **移除 `vision_mode` 配置** — 旧的 `agents.vision_mode: model|tool`（把图片转占位文本）由更实用的 `vision` 工具取代，配置项与 `_convert_content_to_tool_mode` 一并删除

## [0.2.14] - 2026-07-01

### Changed
- **Dream 触发门：会话个数 → 新增 human message 数（per-会话游标）** — 会话门（`min_sessions`）反映不了真实内容量（5 个空会话也触发；一个老会话新加一句就用它全部旧消息撑过门 → 内容门形同虚设）。换成「自上次 dream 以来新增的真实 human message 数」`min_human_messages`（默认 10），用 **per-会话游标**算增量（`_human_delta` = Σ max(0, 当前−游标)），只数游标之后的新增、老会话旧消息不再污染。`SessionSummary` 加 `human_count`（搭 `list_sessions` 已有遍历便车、零额外 IO），`count_human_messages` 复用 `should_show_human_message` 排除注入、兼容 dict 格式消息。时间门（`min_hours`）+ 10 分钟扫描节流保留，把 human 门挡在每次 stop 的 hot path 之外
- **Dream 持久状态迁独立 sqlite** — `last_at` + 游标从记忆目录的 `.dream-lock` 文件（清理 `.md` 记忆时易误删）迁到 `~/.lumi/checkpoints/dream_state.db`（`dream_meta`/`dream_cursor` 两表，同步 `sqlite3`）：不误删、原子写、`last_at` 从「文件 mtime 隐式」变显式列。`record_dream` 一个事务原子更新 last_at + 游标（`INSERT OR REPLACE` upsert，**保留 dormant 会话游标**——不再覆盖式 DELETE 误删没参与本轮的老会话游标）

## [0.2.13] - 2026-06-30

### Added
- **`/dream` 斜杠命令（主动触发记忆综合）** — 记忆会话里输 `/dream` 立即在后台跑一次 dream（force 绕过时间 / 会话 / 节流门，仅 `_in_flight` 防重复），不阻塞对话、完成走 bg-task 通知。复用自动 dream 的同一 runner（抽出 `_spawn_dream` 供 auto hook 与 /dream 共用、`_run_dream` 加 `force` 参数跳过会话门），即便近期无其他会话也综合当前会话。命令仅在启用记忆的会话经 `list_commands` 下发（`type:"system"`，前端零改动自动补全）；`stream_command` 入口统一设 `current_thread_id`，保证内置命令的后台任务完成通知归属本会话

## [0.2.12] - 2026-06-30

### Added
- **后台 Dream（离线记忆综合）** — 会话结束的 Stop hook 按门控阶梯触发后台综合，把近期会话的零散记忆揉成连贯记忆（合并近重复、相对日期转绝对、规范化索引）。新增 `lumi/agents/memory/dream.py`（`auto_dream_stop_hook` 门控 + `_run_dream` runner）、`dream_lock.py`（per-project 锁文件 mtime=lastAt + 进程内 `_in_flight` 防并发 + 扫描节流）、`normalize.py`（`MEMORY.md` 索引行兜底补全 `[type · 日期]`）。综合方式：fork 主 agent（复用同一份 `system_prompt` + `enable_memory=True`，与主 agent 同构），喂入**当前会话完整 message** + 其他近期会话导出的扁平 text 供 grep。防自递归靠 `depth` 门（dream agent inputs 带 `depth=1`，其 stop 经首门放行）；全程 per-project 隔离（锁/会话门/导出/写入），reader checkpoint 与 bridge 同源（`agents.checkpoint`）。配置 `auto_dream`（`enabled` 默认 False / `min_hours` 24 / `min_sessions` 5）
- **召回端裁决** — `MEMORY.md` 索引行带 `[type · 写入日期]`，同主题多条记忆并排、日期不同则矛盾在索引层就可见；`build_memory_instructions` 加「面对矛盾记忆的裁决」「记忆新鲜度（不对称）」两段指引（user/feedback 取写入日期最新、project/reference 行动前验证现状）。把冲突裁决从离线整理挪到召回时手握当前 query 的活模型，dream 只管综合不做自由判决

### Changed
- **`on_agent_stop` 透传 runtime** — 签名加 `runtime: Runtime[LumiAgentContext]` 并塞进新增的 `HookContext.runtime` 字段，作为 Stop hook 取运行时 context（`system_prompt` / `permission_engine` / `memory_enabled`）的唯一通道；现有 `structured_output_stop_hook` 不受影响
- **提取 `parse_frontmatter` 共用** — `utils/config/manager.py` 新增 `parse_frontmatter(content) -> (metadata, 正文)`，统一 frontmatter 解析；`strip_frontmatter`、agent/skill 加载（`tools/loader.py`）、记忆索引规范化（`normalize.py`）三处共用，消除 `split("---")` + `yaml.safe_load` 的重复。`loader` 顺带升级为「独立成行 `---` 闭合」逻辑，正文里的分隔线 `---` 不再被误判
- **`extract_messages_as_text`** — `sessions/message_text.py` 新增，把消息列表导出为扁平一行一消息文本（`[user]/[assistant]/[tool:X]`，换行折叠为 `⏎`）供 dream 的窄关键词 grep；比 `messages_to_dict` 的嵌套 JSON 对 grep 友好

## [0.2.11] - 2026-06-30

### Added
- **飞书消息标注发送者姓名** — 新增 `channels/feishu/directory.py`（`FeishuDirectory`）+ `caching.py`（通用线程安全缓存 `CachingDirectory[K, V]`）：把 `open_id → 显示名`、`chat_id → 群名` 解析收敛到一处，群聊走群成员接口（`im.v1.chat_members.get`，不受通讯录可见范围限制、覆盖新人）、私聊走通讯录接口（`contact.v3.users.batch`），共享同一缓存。每条入站消息解析发送者挂到 `_Pending.sender_name`，合并渲染时以「姓名：」前缀标注（群聊与私聊都注入），让 agent 分得清谁说的。`channel.start()` 后台 `warmup()` 预热 bot 所在所有群 + 群成员（best-effort、不阻断启动），群成员补刷带 per-chat 冷却 + 空结果指数退避防狂刷。需应用权限 `im:chat` / `contact:user.base:readonly`，未授权则退化成兜底名 `用户_xxxxxx`

### Fixed
- **SOUL/AGENTS 提示词残留 frontmatter** — `load_system_prompt` 之前只 `.strip()` 不剥离 frontmatter，导致用户给 `SOUL.md`/`AGENTS.md` 加的 `---\nname/description\n---` 元数据被原样拼进系统提示词。抽出 `strip_frontmatter()`（`load_system_prompt` 与 `load_prompt` 共用），且把闭合 `---` 锚定到**独立整行**才剥离——正文里作分隔线用的 `---` 不再被误判截断
- **飞书 warmup 后台任务可能被 GC / reload 后成孤儿** — `create_task` 结果存入 `self._warmup_task` 持引用（事件循环只持弱引用），`stop()` 取消之，避免预热任务中途被回收或在将停的 loop 上残留

## [0.2.10] - 2026-06-28

### Added
- **持久记忆系统（仅主动写入）** — 新增 `lumi/agents/memory/`：模型在对话中自己 write/edit 记忆文件，按项目隔离落在 `~/.lumi/memory/projects/<项目>/`（`MEMORY.md` 索引 + 各 topic `.md`，frontmatter 分四类 user/feedback/project/reference）。记忆「行为说明」追加到主 agent 系统提示词，`MEMORY.md` 索引 + `LUMI.md` + env/agent/skill 列表每轮经 `turn_context` 作为一条 `HumanMessage` 注入（插在静态 system 之后、`trim` 之后；Claude Code 同构，免截断 + 静态 system 独立缓存）。写记忆目录的 `write`/`edit` 所有模式自动放行不打断对话（DENY / bypass-immune / 执行模式策略仍在其之前生效），记忆目录并入工作区边界使 `validate_path` 放行。移植自 Claude Code memdir 的精简版，**刻意不做**后台提取 / autoDream / 召回旁路
- **`LUMI.md` 项目根说明注入** — 类比 CLAUDE.md：读项目根的 `LUMI.md`，随上述 `turn_context` 块注入上下文（主 + 子 agent 均注入，与「是否启用记忆」解耦），承载「这个项目要什么」。`LUMI.md` 已加入 `.gitignore`（内容随项目/开发者而异，本地维护）

### Changed
- **`create_agent(enable_memory=...)` 默认 False（opt-in）** — 持久记忆有副作用（写盘 / 改 prompt / 注入上下文 / 写入免审批），只有面向用户的对话入口 `bridge` 显式 `enable_memory=True`；子 agent / workflow / cron 走默认 False 天然干净，新增调用方也默认安全

## [0.2.9] - 2026-06-27

### Added
- **飞书渠道工作目录改为「绑定已有项目」** — 飞书表单不再手填路径：`WorkspacePicker` 从该机器已登记的项目（`list_projects`）里下拉选择（项目名 + 路径），可内联「新建项目」（`DirBrowser` + `add_project` 登记后直接绑定）；无项目时空态引导新建，而非让用户填路径。切换已绑定项目会弹确认提醒（保存后回收进行中的飞书会话、历史不丢，下条消息在新项目目录接着聊）。空 = serve 进程当前目录（兜底）
- **`dev.sh` 桌面开发一键启动脚本** — 自检 uv/node、幂等装依赖（`uv sync` + 按需 `npm install`），再 `npm run dev` 起 vite + Electron（后端 sidecar 由 Electron 自行拉起）

### Fixed
- **已绑定项目在列表空/未连接/加载失败时被误显示为「未绑定」** — 空态判断由 `projects.length === 0` 收紧为 `&& !value`：已绑定 `value` 时始终走下拉分支显示当前绑定，断线（`gw` 为空 → `listProjects` 不触发）或请求失败不再把已有配置藏成空态

## [0.2.8] - 2026-06-27

### Changed
- **临时产物统一落到系统临时区单一事实源** — 新增 `lumi/utils/paths.py`（`LUMI_TMP_ROOT` + `lumi_tmp_dir(*parts)`）作为唯一入口，后台任务输出（bash / agent / workflow）从原本写进**工作区** `.lumi/bg_tasks` 改到 `<系统临时区>/lumi/bg_tasks`，飞书入站文件从 `/tmp/lumi-feishu/<thread>` 归位到 `<系统临时区>/lumi/feishu/<thread>`，不再污染项目目录与 `~/.lumi`。删除三处重复的 `_BG_TASKS_DIR` + `mkdir` 样板，收敛为 `bg_tasks_dir()`

### Fixed
- **临时根目录按 OS 用户隔离** — 根目录取 `tempfile.gettempdir()`（尊重 `$TMPDIR`）而非写死 `/tmp`：多用户共享主机上不再撞到他人创建的、本用户无写权限的 `/tmp/lumi`（避免后台任务/飞书下载因 `PermissionError` 全线失败），含用户数据的产物也不暴露在全局固定可读路径；macOS 上落在每用户私有的 `/var/folders/.../lumi`

## [0.2.7] - 2026-06-27

### Fixed
- **会话侧栏删除/置顶/重命名「到前端显示」卡顿 + 并发竞态** — 三个操作改为乐观更新：删除立即移列、pin/rename 立即改字段（Sidebar 按 `pinned` 即时重排），不再阻塞等后端往返。修掉两处竞态：① 删除当前会话时 `activate(null)` 触发的 `refreshSessions` 会与未提交的删除 RPC 抢跑、把会话读回——改为先 `await` 删除提交再清理本地/切会话，并在成功后再断言一次；② pin/rename 用 `.then` 成功后重新断言，纠正 RPC 在途时并发刷新读到旧值的回退；失败统一 `refreshSessions` 回滚。删除失败时本地连接/缓存保持不动，状态一致

### Performance
- **`list_sessions` 按 checkpoint_id 缓存，跳过重复反序列化** — 侧栏刷新原本每次都对最多 50 个会话完整反序列化（含图片/文档 base64），删除/置顶/重命名后的刷新尤其浪费。新增模块级 `_summary_cache`（`thread_id → (checkpoint_id, summary)`），内容未变（checkpoint_id 不变）即复用，仅真正变化的会话才重新加载——常规刷新降至接近零反序列化

## [0.2.6] - 2026-06-26

### Fixed
- **Qwen 思考模式下强制 tool_choice 报 400 修复** — `auto` 审批分类器及所有结构化内部链（`structured_output` / 受迫 `tool_call_chain`）走 `function_calling` 会强制 `tool_choice`，与「默认常开思考」的模型（Qwen toggle 型经 DashScope/百炼）不兼容，报 `InternalError.Algo.InvalidParameter: tool_choice ... not support ... in thinking mode`，分类器 fail-closed 退回人工审批。修复分两处：① `create_llm` 新增 `force_no_thinking` 入参，对强制 tool_choice 的链主动**关闭**思考（仅「不注入档位」对常开思考模型不够）；② `effort_params` 的 toggle 关思考按厂商分方言——Qwen 用扁平 `enable_thinking` 布尔（DashScope 实测），DeepSeek / MiMo 系沿用 `thinking.type`

### Changed
- **检查点默认存储 `memory` → `sqlite`** — `AgentsConfig.checkpoint` 默认改为 SQLite 文件持久化，会话跨重启保留、开箱即用 `/resume`；`memory` 保留为进程私有（连接间隔离）的开发调试选项。详见 `docs/guides/config.md`

### Build
- **新增多架构镜像构建脚本** — `scripts/build-image.sh` 用 buildx 一键构建 amd64 + arm64 Lumi 后端镜像并推送（版本号取自 `pyproject.toml`，可覆盖；`IMAGE` / `BUILDER` / `PLATFORMS` 可环境变量覆盖）

## [0.2.5] - 2026-06-26

### Changed
- **bash 后台任务默认不限时** — `timeout` 改为 `float | None`，语义重定义：**前台**省略回落 `120s`、**后台**省略即不限时（起常驻服务/长跑不再被墙钟误杀）；`timeout=0` 显式表示「不限时」**仅后台可用**，前台传 `0` 报错（无界阻塞会永久挂死当前回合且无 task_id 可取消）。`BashProcessHandle.timeout` / `start_task` 同步放宽为可空。详见 `docs/guides/bash.md`

### Build
- **Docker 镜像默认装全部可选依赖** — 新增聚合 extra `all`（含 `feishu`），`Dockerfile` 改 `uv pip install ".[all]"`，飞书等 channel 在容器内开箱即用；以后新增 extra 只需并入 `all` 即随镜像分发。本地 `uv sync` 仍按需，不受影响

## [0.2.4] - 2026-06-26

### Added
- **新增 `default` 风格并设为默认风格** — `Config.style` 默认值 `code → default`。`default` **不内置提示词**，系统提示词全部来自用户 `.lumi/prompts/`；可内置 skill / agent（当前为空占位）。面向非编程场景，提示词完全由用户掌控
- **风格统一支持内置 skill** — `load_skills` 重构为「风格内置 skills → 用户 `.lumi/skills/`（同名覆盖）」，与 `load_agents` 对称；新增 `get_style_skills_dir`。至此 prompts / agents / skills 三类资源加载优先级一致（用户覆盖内置）。详见 `docs/architecture/styles.md` / `docs/guides/styles.md`

### Changed
- **提示词组装去 XML 包裹** — `SOUL` / `GUARDRAILS` / `AGENTS` 三文件由原先的 `<SOUL>…</SOUL>` XML 标签包裹改为按序以 `\n\n` **直接拼接**，任一缺失即跳过该段（对所有风格生效）
- **`load_system_prompt` 软化为不再 fail-loud** — 风格无内置 prompts 且用户未配置 `.lumi/prompts/` 时返回空串（agent 以无系统提示词运行，`call_model` 的 `if system_prompt:` 自动跳过空 `SystemMessage`），不再抛 `ValueError`；使 `default` 风格开箱即用、不崩

## [0.2.3] - 2026-06-26

### Added
- **飞书（Lark）IM channel——首个 IM 接入** — 把 Lumi Agent 接到飞书机器人，私聊 / 群 @ 即可对话，复用与 desktop 完全相同的 Agent 运行时（`bridge.stream_response` 产 `BridgeEvent` 流）。lark-oapi **长连接**（无需公网 webhook），跑在独立 daemon 线程 + 独立 event loop（`patch lark_oapi.ws.client.loop` 与 uvicorn 主 loop 隔离），入站经 `run_coroutine_threadsafe` 投回主 loop。每个 chat → 一个常驻会话 thread（`feishu-{chat_id}`）+ `AgentBridge` + 运行锁（`BridgePool`）。回复用 **CardKit 打字机卡片**（`Throttle` 双阈值 250ms/64字 + `UpdateQueue` 合并 + 失效换卡 + 工具忙碌 spinner）。作为可选依赖 `uv sync --extra feishu`。详见 `docs/architecture/feishu.md` / `docs/guides/feishu.md`
- **桌面端「设置 → 渠道」UI 配置** — `ChannelsPanel` 渠道卡片列表 + 飞书表单（凭证 / 审批模式 / 群策略 / 白名单 / 工作区 + 测试连接 + 保存并重连）。配置存 serve 机器的 `~/.lumi/channels.json`（含密钥 chmod 600，照抄 `provider_store` 范式），`${ENV}` 注入；保存经 `save_channel` RPC 实时停旧起新，无需重启。状态灯走品牌「光」语言（`.chan-orb`，error 态显示具体失败原因）。新增 RPC `get_channels` / `save_channel` / `test_channel`（照抄 `cron_rpc` 进程级分发 + 协议契约）
- **进程级 `ChannelManager`** — `lumi serve` lifespan 经 `channels_runtime()` 起它；拥有跨「传输重连」存活的会话池（改凭证/拨开关只重启 WS、不清空进行中的会话），`reload()` 由 `_reload_lock` 串行化、停旧起新
- **入站媒体支持** — 图片（含被回复消息的图、post 内嵌图）→ 走仓库统一压缩管线（5MB/2000px + token 预算）→ base64 多模态 block，与 desktop 发图同构；文件 → 下载到 `/tmp/lumi-feishu/<thread>/` + `add_folder` 授权 + `<attached-file>` 注入供 `read`（PDF 渲染）
- **忙时排队 + 多条合并** — 同会话上一轮在跑时新消息排队（上限 10，满则丢弃提示），跑完把积压的合并成一轮（`<system-reminder>` + 编号列表，告知 agent 这是连发的几条、后面的可能更正前面），媒体并发下载
- **`AgentBridge.initialize(disabled_tools=…)`** — 透传到 `create_agent(tools=get_tools(disabled_tools=…))`，飞书会话默认禁用 `ask` 工具（IM 不弹询问卡片，遇需澄清时模型自行判断而非挂起）

## [0.2.2] - 2026-06-25

### Changed
- **Summary 从「并行 + 延迟替换」改为「串行 + 当轮就地压缩」** — `Summarizer` 节点移到 `PreprocessMessages → Summarizer → CallModel` 关键路径上：超阈值时当轮生成摘要并立即 `RemoveMessage` 删历史 + 摘要前置到末条 Human，即将溢出的这次调用立刻受益，不再等下一轮 `preprocess` 替换。移除 `state["summary"]` / `SummaryData` 与 preprocess 的延迟替换分支（详见 `docs/architecture/summary.md`）
- **Token 限制改字节计量，移除 tiktoken** — 新增 `lumi/utils/sizing.py`：阈值类（工具结果是否过大 / read 超限）用 UTF-8 字节衡量；上下文窗口预算（summary 触发 / trim）优先读真实 `usage_metadata`、退化时按字节粗估（`BYTES_PER_TOKEN=3`）。删除 `lumi/utils/token_counter.py`，`once_tool_max_tokens` → `once_tool_max_bytes`

### Added
- **Summary 鲁棒性：PTL 截头重试 + per-thread 熔断器 + 图像剥离** — 串行后 summarizer 在关键路径，失败会连带本轮失败；`lumi/agents/core/preprocessing/compact.py` 提供：摘要自身撞 prompt-too-long 时按 API round 从头部丢弃重试（`summary_ptl_retry_max` / `summary_ptl_retry_drop_ratio`）、同 thread 连续失败超阈值后短暂放行 CallModel（`summary_failure_circuit_threshold` / `summary_circuit_reset_seconds`）、摘要前 strip 图像防自身超长
- **压缩状态事件 `compaction.status`** — gateway 据 `langgraph_node == "Summarizer"` 拦截压缩节点内部的摘要 LLM 调用：`on_chat_model_*` 转成 `compaction.status {active}`、丢弃其 stream，前端显示「正在压缩对话…」而非把摘要全文渲染成助手回答

### Fixed
- **压缩的流式输出被当成助手回答** — `astream_events` 会把节点内任何 chat model 调用逐字浮现为 `on_chat_model_stream`（与 `streaming=False` 无关），bridge 无节点过滤时摘要全文经 `message.delta` 泄漏成助手输出 + 幽灵气泡 + 污染 token 统计；现按节点拦截隔离

## [0.2.1] - 2026-06-25

### Added
- **WS 断连续接(会话与 WS 解耦)** — WS 断开时若会话仍有活跃 / 挂起轮(典型:挂在工具审批 / ask 上),不再 aclose,而是把会话连同 `AgentBridge` / parked turn / `ApprovalBroker` / 挂起 Future 原地留存,等同 thread 的 WS 重连接回——renderer 重载(Ctrl+R)/ 网络抖动 / 休眠唤醒后审批仍在、运行轮继续,**无需 checkpoint 重放(Future 一直在内存里)**。新增 `gateway/session_registry.py`(进程内 detached 会话表)+ `GatewaySession.detach()` / `reattach()`(换 `_NoopChannel`、停 / 起通知轮、8h TTL 兜底回收)+ bridge 留底挂起审批事件供重发;前端连接 URL 带 `?thread=`(含重载后点回会话的初次连接)触发续接,`running` 据 `gateway.ready.running` 复位。仅 sidecar 存活的断连可救(Case 1);后端进程重启(Case 2)不幸存(详见 `docs/architecture/desktop.md`「断连续接」)
- **前端审批 / 澄清并发队列** — `approval` / `clarify` 由单槽改为按 `approval_id` 排队,渲染队首、逐个应答出队;后端并发解锁后(一条消息多个工具 / 多个前台子代理可同时挂起审批)不再互相覆盖丢失 Future,重连重发按 `approval_id` 去重(`enqueuePending`)

### Fixed
- **切回同会话误杀挂起审批 / 挂死** — `switch_session` 切回**同 thread**且有活跃轮时不再收尾本轮(早返回守卫):避免把正挂着的审批以「拒绝」收尾(「切走再切回审批还在」成立),并消除切回 re-bind 在子代理审批场景下的挂死
- **Ctrl+R 重载续接后 `running` 不恢复** — `gateway.ready` 帧带 `running=has_active_turn()`,前端重连 / 重载两路据此复位;否则续接的挂起轮被当空闲(stop 隐藏、输入栏启用、续跑正文以非运行态渲染)
- **`resume` 应答 RPC 失败丢失队首审批** — `resumeWith` 乐观出队后若 `resume` RPC 因连接抖动失败,回滚出队、保留队首卡片供重连重试(按 `approval_id` 去重);否则队首审批前端消失而后端 Future 仍挂、轮卡死
- **后台通知 meta 轮断连被误续接** — meta 轮也让 `has_active_turn()` 为真 → 新增 `should_detach()` 排除纯后台 meta 轮(无用户在等,除非它自身挂着审批),避免无人等待的会话占 registry / per-thread shell 满 8h
- **detach 期通知被丢弃** — `detach()` 取消 `_notification_loop`(`reattach()` 重起),避免无 WS 期间把本 thread 的后台任务通知 drain 进 `_NoopChannel` 白白丢失

### Changed
- **活跃轮判定收口 + 入队去重抽取** — 散在 3 处的 `_run.task is not None and not done()` 内联表达式统一为 `has_active_turn()`;前端 `approval` / `clarify` 入队去重提取为 `enqueuePending` helper
- **文档** — `docs/architecture/desktop.md` 新增「断连续接(会话与 WS 解耦)」节;`approval-inflight.md` 决策 #1 更新为「Case 1 已实现、Case 2 仍不救」,并记入「一个 thread 单活会话尚无强制」的多机待办

## [0.2.0] - 2026-06-25

### Changed
- **在途审批：审批 / ask 从 `interrupt()` 改为 `asyncio.Future` 请求-响应** — 工具审批与 ask 提问不再用 LangGraph `interrupt()` + checkpoint 重放，改为 `ApprovalBroker`（`gateway/bridge/broker.py`）按 `approval_id` 寻址的 Future 注册表：节点 `await broker.request(payload, reject_value)` 原地挂起，请求经 `adispatch_custom_event` 在 `astream_events` 以 `on_custom_event` 浮现成卡片，非流式 `resume(approval_id, value)` RPC 解 Future 续跑。一条用户轮全程一条不断的事件流，删去 `_check_interrupts` / `stream_resume` / `_subagent_marker` / `awaiting_resume` / `_INTERRUPT_TOOLS` 等中断擦屁股代码，`_active_agent_runs` 由 dict 瘦成 set（详见 `docs/architecture/approval-inflight.md`）
- **stop / 切会话 = 以「拒绝」收尾挂起审批（保留历史）** — 不再取消丢弃：每个 `broker.request` 自带 `reject_value`（tool_approval 拒绝 dict / ask 取消哨兵），stop 或切走时 `reject_all` 让本轮干净跑到 END、checkpoint 状态干净、被中止那一轮的用户消息保留在历史里（与旧 interrupt 行为一致）；仅无挂起审批（轮在流生成中途）才硬取消 task

### Added
- **子代理 / 并发审批解锁** — 旧 `interrupt()` 依赖 checkpointer、子代理无 checkpointer 故审批不可用；broker 机制下前台子代理传播 broker，其审批经父流 `astream_events` 浮现、白嫖 `parent_ids` 归属到子卡片，并发多审批靠 `approval_id` 区分。审批卡片与流式事件统一走 `_resolve_subagent_parent` 归属，并行兄弟靠各自 parent_ids 精确区分

### Fixed
- **headless（cron / workflow）碰审批 / ask 崩溃** — 这些路径 `create_agent` 不注入 broker（`approval_broker=None`），privileged 模式下 bypass-immune 工具仍走审批、ask 直执行 → 旧实现会 `AttributeError`；现 human_approval 无 broker 时 fail-closed 自动拒绝并回 `CallModel`，ask 无 broker 时返回提示让自治 agent 自行判断后继续

## [0.2.0a9] - 2026-06-25

### Added
- **设计文档：ACP client 接入** — `docs/architecture/acp-client.md`：让 `LumiAgent` 作为 Agent Client Protocol 的 client，把外部编程 agent（Claude Code 等）当进程外「工人」委派；委派复用现有 sub-agent 工具形状，权限走同一 `PermissionEngine`（设计定稿，待实施）
- **设计文档：在途审批改造** — `docs/architecture/approval-inflight.md`：把工具审批 / ask 从 `interrupt()` + checkpoint 重放改为 `asyncio.Future` 在途请求-响应，支持「节点原地挂起」与并发多审批，为 ACP 外部子进程审批铺路（设计定稿，待实施）

### Fixed
- **`ToolRuntime` 注入被 `from __future__ import annotations` 破坏** — 该 import 会把 `runtime: ToolRuntime` 注解字符串化，langchain 调用时认不出注入参数、不注入 → 运行时 "missing runtime"；移除 `agent.py` / `workflow.py` 的该 import，并在 `registry._collect_tools_from_module` 加载期新增 `_assert_runtime_not_stringized` fail-fast 守卫，把「每个文件记得别加 future import」的人工纪律换成统一强校验
- **并行兄弟子代理的中断归属错挂** — 同轮并行委派 ≥2 个顶层子代理、其一触发 ask / tool_approval 时，旧 `_subagent_marker` 取最早插入会把审批 / 提问卡片错挂到先启动的兄弟名下；改为靠存下的 `parent_ids` 判断祖先关系，仅唯一顶层时归属、并行兄弟无法区分时返回空串挂到主 agent（不自信错挂；仍能正常看到并回答，回答正确生效）。单链委派（祖→孙）不受影响

### Changed
- **`_active_agent_runs` 改存 parent_ids** — `dict[str, None]` → `dict[str, list[str]]`，活跃 agent run 一并记录其 `parent_ids`；中断归属（无 parent_ids 上下文）据此区分「单链委派」与「并行兄弟」，与流式路径「最浅祖先」同口径

## [0.2.0a8] - 2026-06-24

### Added
- **agent 工具动态加载** — agent 工具改为静态恒注册，可用代理列表经 `<system-reminder>` 动态注入（与 skill 一致）；`AgentChangeDetector` 检测 `.lumi/agents` 变更后热刷新，新增/删除代理无需重启或重建工具 schema
- **子代理可配置多层委派** — 新增 `agents.max_delegation_depth`（默认 3，主 agent 为第 0 层，每委派 +1）；达上限的子代理工具集剔除 `agent` 工具、不能再往下委派（`0` = 禁止委派），`depth` 经 `LumiAgentState` 逐层传播；注入门控以「工具集是否含 agent」为准
- **多层委派子代理事件归属** — 孙及更深活动按 `parent_ids` 最浅祖先确定性归并到顶层子代理卡片（仅展示用，不参与 interrupt/resume，错挂不影响功能）

### Changed
- **变更检测器去重** — agent / skill 检测器抽出共享 `FileSetChangeDetector` 基类（digest / 缓存 / 单例）；skill / agent 列表注入共用 `format_reminder`
- **`_active_agent_runs` set → 插入有序 dict** — 子代理事件归属改为确定性（流式取最浅祖先、中断取最早插入），消除从无序集合任取导致的随机错挂

## [0.2.0a7] - 2026-06-24

### Removed
- **Plan Mode 全栈移除** — EnterPlanMode/ExitPlanMode 工具、`plan` 执行模式策略（`PLAN_POLICY`）、gateway `plan.request` 事件与 desktop 计划审批 UI（PlanDialog）整体删除；通用 `readonly` 模式与 `tool_cancelled` 状态（ask 仍用）保留
- **工具描述 MD 配置机制移除** — 删除 `prompts/tools/*.md` 与 `load_tool_md`/`require_tool_field`；`default` 风格仅含工具模板、模板内联后已空，随之移除

### Changed
- **工具描述归位到代码** — 内置工具 description 改为模块常量 / 函数 docstring，`registry._collect_tools_from_module` 加载时统一 `inspect.cleandoc` 去缩进（外部 MCP 工具走异步 loader，不经此处）；不再可经 style/`.lumi` 覆盖
- **`WORKFLOW_SCHEMA` 裸 dict → `WorkflowInput(BaseModel)`** — 静态工具 schema 全部 BaseModel 化，与其余工具一致
- **默认风格 `default` → `code`** — `config.style` 默认回退改为 `code`

### Fixed
- **工具描述源码缩进泄漏** — docstring 形式的工具（bash / filesystem write·edit·glob）续行带 4 空格缩进进入模型描述、破坏 Markdown 渲染；加载时统一 `cleandoc` 修复

## [0.2.0a6] - 2026-06-23

### Fixed
- **工作区边界可被 bash `~` 绕过** — `cat > ~/secret.txt` 的 `~` 不被 shlex 展开，边界检查把它当作工作区内相对路径放行，但 shell 执行时展开到家目录外造成越界写入；边界检查改为先 `expanduser()` 再 `resolve()`，与执行语义一致
- **cron 一次性(AT)任务瞬时失败的重试永远丢失** — 重试已排程但任务被 `_deliver_and_log` 立即删除，重试触发的 `_fire_job` 读到 `None` 静默丢弃；`_handle_retry` 返回是否已排重试，有待定重试时保留任务
- **单次瞬时发送失败永久踢掉活连接** — `DesktopDelivery` 对任何 `send` 异常都 `discard` 连接，一次背压就让活连接收不到后续所有 cron / bg_tasks 广播；改为仅记录告警，连接生死交 `register`/`unregister` 管理
- **后台通知轮无法被 stop 取消且会卡死后续发送** — 通知 meta 轮直接在 `run.lock` 下跑、不挂 `_run.task`，stop 取消不到、新 `send_message` 卡在锁上 UI 挂死；改为挂到 `_run.task`，可被取消、运行期间新消息得到「已有任务在执行」而非卡死
- **删除 / 重命名项目在路径形态不一致时静默失效** — `add_project` 存 `expanduser().resolve()` 后的路径，`remove` / `rename` / `touch` 却用原始入参（`~` / 尾斜杠 / 软链）比较致匹配不到；统一经 `_resolve()` 规范化
- **权限 DENY 预检对 evaluate 异常 fail-open** — 评估抛错只记录后继续，可能被随后的只读短路跳过完整复检而绕过该工具的 DENY；改 fail-closed（异常即审批）
- **无 ripgrep 时 Python grep 回退缺陷** — 路径型 glob（`**/*.py`、`src/*.ts`）只比对文件名致匹配不到、`count` / `files_with_matches` 模式返回逐行内容的错误形状、不支持 `case_insensitive`；全部对齐 ripgrep 语义
- **启动时 models.dev 目录刷新任务可能被 GC** — `create_task` 未持引用，事件循环只弱引用可能在协程挂起前被回收；改为持强引用 + 退出兜底取消
- **desktop 完成通知显示会话首条消息而非本轮 prompt** — `.find` 取到最早的 user 项；改取最后一条
- **关闭机器连接残留 `machineConn` 态** — `close()` 不触发 `onState`，重新启用时会先闪一下旧的「已连接」；断开时一并清除残留态
- **`gateway` `teardown()` 未清待定重连计时器** — 旧退避计时器在 `connect()` 后仍会触发、弃用刚建好的 socket 另开一条造成 churn；`teardown()` 统一清除
- **权限引擎 `rebase()` 切项目丢失 `user_config_dir`** — 退回默认 `~/.lumi`，丢掉自定义目录的用户级规则；存字段后 rebase 复用
- **结构化输出用户 schema 含 `tool_call_id` 字段时被注入覆盖** — 该字段被剔出模型可见 schema 致 required 校验永不通过、循环到 abort；注入字段名避开用户已有属性
- **边栏项目折叠态未持久化** — 与机器折叠不一致、重挂载即丢失；改写入 localStorage

### Changed
- **边栏项目分组重做** — 项目名与「显示全部」主次配色对调（项目名加深为主、「显示全部」变浅缩小为次）、项目名可点击折叠展开、与机器段同级缩进
- **simplify 清理** — 提取 `session._finish_cancelled_turn`（用户轮 / 通知轮共用取消收尾）、边栏 `usePersistedToggle`（机器 / 项目折叠样板合一）；完成通知改 `reverse().find`；测试去除 `import X as Y` 别名

## [0.2.0a5] - 2026-06-23

### Added
- **desktop 聊天流「回到底部」浮钮** — 未贴底时聊天流底部居中浮出一枚暖金光点按钮，点击即回到最新；出现一瞬一圈光环涟漪一次后静止（复用 proj-dot 同款 `lumi-ripple-once` 光语言，一静一动）

### Fixed
- **流式输出抢占界面 → 改「粘底跟随」** — 原本每段流式输出都无条件把聊天流拽回底部，用户上滚看历史时被反复打断；改为仅当用户贴在底部时才跟随（距底 80/30px 滞回判定，避免边界抖动反复触发），上滚即放手。切会话归位与贴底跟随合一到单个 `useLayoutEffect`（绘制前同步滚动消除切会话/流式时的错位闪帧、思考流也跟随、并免去多 effect 读 `pinnedRef` 的顺序依赖）；主动发送消息强制回到底部，确保自己的消息与随后的回复都在视野内

## [0.2.0a4] - 2026-06-22

### Added
- **项目随会话绑定 + open 握手携带 workspace** — 连接 URL 新增 `?workspace=`（与 `?token=` 同机制），`bridge.initialize(project_dir=...)` 据此在建引擎时直接 pin 到本会话项目，省掉 ready 后再 `set_workspace` rebase 的来回；前端「打开项目」改为经 open 握手开一条绑定到该项目的新会话
- **远程机器连接开关 + 手动重连 / 离线态** — 远程机器可「已配置但不连接」（enable 开关持久化进 backends.json，关闭则不开控制连接、侧栏隐藏）；自动重连超 `MAX_RETRY` 转 `failed` 态停在等用户手动重连（侧栏重连按钮 / 离线提示）；编辑机器地址 / token 经 `setUrl` 换址重连

### Changed
- **项目从进程级改为会话级绑定** — 工作目录不再是进程级单一 `os.chdir`：每条 WS 连接一个引擎、pin 到本会话项目，`set_workspace` 只 rebase 本 bridge 引擎 + 重载本会话 hooks + 重置本会话 shell，**不动进程 cwd、不影响其它会话**；删除进程级 `_active_bridges` rebase-all。同进程多会话可各绑各项目、并发互不串扰
- **filesystem / bash 授权目录与 hooks 改 per-run 隔离** — 授权目录来源、config hooks 改为 per-run contextvar（覆盖进程全局兜底），bridge / cron 在 run 起点注入本会话引擎的来源；hooks config 去进程单例（`build_config_hooks` 返回式构造 + per-run 注入，builtin 仍全局）；会话级「添加文件夹」改存引擎独立字段 `_ephemeral_workspaces`（与从磁盘重载的 `_config.workspaces` 分离，跨 reload / 项目切换存活）；`system_info` 的 cwd、bash 工作目录、`workspace_dir` 元数据均改取本会话
- **bash 持久 shell 按会话 / 子代理隔离** — shell 不再全进程共用一个 `"default"`，按 `current_thread_id` 分（会话私有，`cd`/env 不串），断连 / 删会话时回收；子代理经 `shell_session.run_with_shell` 在 `copy_context` 副本里拿独立 shell（`cd` 不污染父 / 兄弟、用完即弃）
- **架构文档对齐** — 重写 `desktop.md` / `permissions.md` / `hooks.md` / `cron.md`（原文描述的是已改掉的进程级 cwd / hooks 单例模型）

### Fixed
- **scheduler 顶层 import 触发循环导入致 `lumi serve` 启动失败 / 本地会话连不上** — `cron.scheduler` 在 tools / permissions 初始化前被加载，顶层 import `permissions.workspace` / `core.hooks` 形成 `permissions → engine → tools → cron → scheduler` 环；改为 `_invoke_agent` 内延迟 import，新增「全新解释器导入 serve 入口」冒烟测试守住此类只在 serve 导入顺序下复现的回归
- **前端 `new URL(wsUrl)` 对非法远程地址抛错致连接卡死** — 弱校验（`startsWith('ws')`）入库的畸形 URL 会让 `new URL` 抛进 `openConnection` 的 IIFE、Promise 永不 resolve、UI 卡在 connecting；改 try/catch 退回原始串交 WebSocket 层走重连 / failed 优雅降级
- **cron 直接 ainvoke 不注入 per-run 授权来源** — 降级落回被并发 WS 会话 `set_workspace` 清洗过的进程全局，可能在错项目执行；cron 自注入本项目来源（含 engine-None 降级兜底，与 bridge 对称）
- **子代理共用父 shell / shell 永不回收 / set_workspace 关错 thread shell** — 子代理 `cd` 互串父与兄弟（改独立 shell）；按 thread 分 shell 后断连 / 删会话不回收致孤儿 bash 进程累积（补回收）；`_switch_session` 中 `set_workspace` 在 `switch_thread` 前跑导致关到切出会话的 shell（调换顺序）

## [0.2.0a3] - 2026-06-22

### Added
- **多机 / 远程 serve（同一 client 连本地 + N 台远程）** — `lumi serve --token` 鉴权（URL query `?token=`，空 token 放行、非空 hmac 比对，错 token 干净 1008）；桌面端设置→连接管理远程机器（backend 注册表存 userData，本地 sidecar 注入随机 token）；每台机器一条「控制连接」fan-out `list_sessions` / `list_cron_jobs` 合并打机器标，会话列表升级为**机器→项目→会话**树（组头机器色点 + 连接光态，离线置灰）；新对话 / 项目 / 模型 / 定时任务全部 per-机器作用域（顶部「先选机器」）；新增 `list_dir` / `make_dir` RPC 驱动**远程目录浏览器**（在目标机器文件系统上浏览/建目录选项目，取代手填路径）；`switch_session` 接受 `workspace` 切后端进程 cwd（跨项目方案甲），`SessionSummary` 附 `workspace_dir`
- **分发 / 部署** — 新增 `Dockerfile`（slim + apt 装 `ripgrep` + uv 安装，内置 `style: code` + `agents.checkpoint: sqlite` 默认配置）+ `.dockerignore`；README 增「分发 / 部署」章节（后端 uv tool wheel / Docker，桌面 electron-builder `npm run dist`），并把启动说明从已删除的 TUI 改写为桌面应用 + `lumi serve` + headless
- **边栏「最近 / 全部」段式 tab** — 最近 = 所有机器会话扁平时间流（置顶优先），默认仅显示最近 N 条（设置可配，默认 20）；全部 = 机器→项目分组树 + 每项目限量「显示全部 / 收起」+ 定时任务组；新增搜索框（命中摊平高亮）

### Changed
- **`/simplify` 清理（多机前端去重）** — 抽 `MachineTabs` 组件收敛 ProjectsPage / CronPage / ProvidersPanel 三处逐字相同的「先选机器」chip（退化判断下沉组件内）；删 `machineName` 冗余字段，机器显示名改由 `backend + machines` 现算（新增 `machineName()` helper），连带移除失去读者的 `machinesRef` 影子 ref；`RunsRail` / CronPage `boundApi` 改 `useCallback` 稳定引用；`BackendsPanel` 机器色复用 `machineColor` 单一事实源；`projName` 复用 `basename`；删残留死 i18n key

### Fixed
- **多机重构回归（xhigh code-review 修复）** — 切到失效（被删/改名）项目目录的会话不再卡死（前端 `switchSession` 包裹 + 后端 `set_workspace` 失败降级不中断切会话）；后台任务停/清改走当前会话连接（原误发控制连接致空操作、任务回弹）；某机器瞬时抖动时保留它上一轮的会话 / 定时任务（不整列抹掉闪没），cron 未读仅全机响应才回收；远程会话斜杠命令改走该会话连接（原取本地命令）；错 token 收到 1008 停止无限重连；远程目录浏览器建文件夹失败给出原因（原静默）

## [0.2.0a2] - 2026-06-20

### Added
- **desktop markdown 代码块语法高亮** — 接入 `rehype-highlight`（highlight.js），新增「暖砂·高对比」双主题配色：`--hl-*` token 变量按 `:root` / `:root.light` 切换，暖金统一色相、注释最弱、关键字/字符串/函数名拉开、亮色过 AA，diff 增删与链接复用语义色 success/error/info。带语言标记的代码块上色，未注册语言降级为纯文本
- **行内代码 `xx` 暖金字** — `.md code` 文字改用 `color-mix(accent 80% + ink)` 暖金色（亮暗自适应）呼应代码块高亮；`.md pre code` 锁回 ink，保证块内纯文本不吃这层暖金

### Fixed
- **用户气泡长 URL / 长文本溢出气泡边界** — 气泡补 `wrap-anywhere`（`overflow-wrap: anywhere`），在 `max-w-[80%]` 约束内断行；`.md a` 同步补断行

### Changed
- **markdown 渲染收口到 `<Markdown>` 组件** — 三处 `ReactMarkdown` 调用（聊天消息 / 计划弹窗 / 文件预览）统一经 `desktop/src/components/Markdown.tsx`，插件配置（GFM + 代码高亮）集中一处，避免多点漂移

## [0.2.0a1] - 2026-06-19

### Changed
- **`server/` 整体并入 `lumi/gateway/`，确立传输无关的多 channel 抽象** — 新增 `Channel` 协议（仅需 `send(frame)`）+ `GatewaySession`（吸收原 ws 端点的 run.lock 并发协调 / RPC 分发 / 通知轮询 / 中断状态机）+ `gateway/bootstrap.py` 进程级启动上下文；`ws.py` 退化为薄 `WsChannel`（737→约 90 行）。新增 IM channel（飞书 / 企业微信 / Telegram）只需实现传输并调 `session.handle_frame`，不碰 bridge / session / services / protocol。`BroadcastHub` 从 ws 模块全局抽出，cron.running / bg_tasks.update 去抖广播跨 channel 共享
- **`bridge.py`（1377 行 god object）拆为 `lumi/gateway/bridge/` 包** — 瘦 `AgentBridge`（流式 + 会话生命周期）+ `ProviderService` / `ApprovalEnricher` / `CheckpointService` / `FolderManager` 四个可组合服务，`lumi.gateway.bridge` 导入路径不变
- **模型层抽出 `lumi/models/`** — `catalog` / `manager` / `chain` / `provider_store` 迁入，`CACHE_CONTROL` 移到 `models/cache`，彻底消除 `utils → agents` 分层倒置（分层归正为 `utils ← models ← agents ← gateway`）
- **大文件按职责拆分（行为不变）** — `filesystem/__init__`(1073) → backend / ripgrep / tools；`checkpoint`(700) → checkpoint / serde / diff；`scheduler`(648) 抽出 retry / compensation / job_runner；`session`(660) 拆 `shell_session` + `bg_process` 并重命名（与聊天会话 `lumi/sessions/` 区分，`SessionManager` → `ShellSessionManager`）
- **`is_use_tool` 权限路由下沉为 `permissions/routing.route_decision` 纯函数** — `nodes` 不再依赖 `tools.capability`，减一条 core→tools 耦合；行为逐字保全（46 个表征测试 + 三视角对抗验证锁定，含「DENY 优先于只读短路」安全语义）
- **公共原语单一来源** — 新增 `utils/atomic_io`（原子写），checkpoint / provider_store / sessions / projects / model_catalog / cron 全部复用；cron CRUD 双实现统一为 `CronService`；统一 logger 获取、`PATH_ARG_KEYS` 单点
- **死代码清理 + lint 护栏** — 删 `lumi/api/` HTTP 入口、`APIDelivery`、`general_tools` / `clipboard` 等约 900 行死代码与残留；新增 `[tool.ruff]`（E/F/I/W/UP）+ `ruff format` 全仓护栏；消灭全部 `import as` 别名
- **前端协议类型化** — `protocol/events.json` payload 升级为带类型对象；`WireEvent` 改判别联合，消除 `desktop/src/App.tsx` 的 `payload: any`
- **文档与代码对齐** — 重写 `permissions.md`（原文档描述的是已重构掉的旧设计）、修正 11 处架构文档的失效路径；新增 `docs/architecture/refactor-plan.md` 记录整理方案与决策

### Fixed
- **取消运行中 BASH 后台任务时取消通知双重入队** — `cancel_task` 与 monitor 的 finally 都入队，模型在一次注入里被重复告知任务取消；删去多余的一次
- **AT 类型 cron 任务带时区 ISO 时间补偿判定崩溃** — `should_compensate` 的 AT 分支未 strip tzinfo，与 naive `now` 比较抛 `TypeError`（与 CRON 分支对齐修复）
- **glob 工具大目录遍历阻塞事件循环** — 同步全树 `rglob` 移入 `asyncio.to_thread`
- **grep 工具 `head_limit` / `offset` 对 `files_with_matches` / `count` 模式未生效** — 与工具文档承诺的行为对齐

## [0.1.0a25] - 2026-06-18

### Changed
- **移除 Python TUI（Textual），前端归一到 desktop** — 删除整个 `lumi/tui/` 包（app、event_router、widgets、renderers、screens、slash_commands 等约 70 个文件）及对应 TUI 测试。`lumi` 命令不再启动 TUI：去掉 `web-server` 子命令与 `_run_tui`，裸 `lumi` 改为显示帮助，交互入口归一到 desktop（经 `lumi serve` WebSocket）/ `lumi -p` headless / HTTP API
- **会话与消息逻辑下沉到 `lumi/sessions/`** — 原住在 `lumi/tui/` 实为后端逻辑的 `session_store` / `session_meta` / `message_visibility` / `text_cleaning` 迁入新包 `lumi/sessions/`（textual-free，由 `lumi/server/ws.py` 消费）；从已删的 `message_restore` 抽出纯文本提取函数到 `lumi/sessions/message_text.py`。`ws.py` 不再依赖 `lumi.tui.*`，相关惰性导入提升为模块级
- **移除 textual 依赖** — `pyproject` 删除 `textual` / `textual-serve`（连带一批传递依赖），并删去仅服务 TUI 的 `TUIDelivery` cron 投递通道
- **文档同步** — CLAUDE.md 去掉 TUI 架构段；删除 `docs/architecture/{tui-improvements,slash-commands}.md`（整篇讲已删 TUI）；更新 `agents` / `checkpoint` / `desktop` / `subagent-rendering`.md 及 `user-manual.md` 的陈旧路径与命令引用

## [0.1.0a24] - 2026-06-17

### Added
- **present_files 工具 + Desktop 文件预览**（`docs/architecture/desktop.md` present_files 文件预览节）— Agent 产出文件后调 `present_files` 把它们呈现给用户。后端 `lumi/agents/tools/providers/present_files.py` 只做本地元数据收集（无对象存储，区别于 SaaS 的 MinIO 上传）：单次 `os.stat`（避免 isfile→getsize 的 TOCTOU）+ `mimetypes` + 按扩展名分类 `kind`，返回 `{path,name,mime_type,size,kind}` JSON（不存在/非常规文件返回 `{path,error}`，顺序保留）。常驻工具，走现有 `tool.start/complete` 事件流，**协议无新增事件**。前端 `desktop/src/components/PresentedFiles.tsx`：聊天里渲染成单色类型图标文件卡片（`FileCards`，按 `kind` 选 lucide 字形，不上彩色，卡片层不加载文件字节）+ 「Show in Folder」；点卡片在聊天区右侧滑出停靠预览面板（`PreviewPanel`，可拖宽持久化 `lumi-preview-width`、Esc/✕ 关、切会话自动关）。预览分型：图片/PDF/HTML 经 `lumi-file://` 协议内嵌，文本/Markdown 经 `fetch().text()` 渲染；视频/音频/Office/未知类型 → 统一 `NoPreview`（提示 + 用系统应用打开）
- **`lumi-file://` 本地文件协议**（`electron/main.cjs`）— `registerSchemesAsPrivileged` + `protocol.handle` 让 renderer 在 http origin 下安全引用本地文件（绕过 `file://` 限制），供预览面板 `<img>`/`<iframe>` 加载。URL 形如 `lumi-file://local/<abs-path>`（固定 host=local，自定义 standard scheme 不允许空 host；各路径段 `encodeURIComponent`）。新增 IPC `lumi:open-path`/`lumi:reveal-path`/`lumi:path-exists`（经 `preload.cjs` 暴露为 `window.lumi.{openPath,revealInFolder,pathExists}`）

### Changed
- **present_files 受工作区边界约束**（`docs/architecture/permissions.md` 边界检查节）— `boundary.py` 新增 `_PATH_LIST_ARG_KEYS`（`filepaths`），列表型路径参数逐项提取参与边界检查，与 `bash`/`filesystem` 同等受限，堵住经 present_files 绕过边界读任意文件的缺口（含 2 个回归测试）
- **大文件 / 媒体不内嵌预览** — 预览面板按元数据 `size` 判定 `>50MB`（UI 阈值，不读文件）→ 显示「文件较大」提示 + 用系统应用打开；视频/音频一律走系统应用打开（协议缓冲 + 无 Range，不适合内嵌）。协议层另设 `MAX_SERVE_BYTES`(128MB) 硬上限返 413，兜底防超大文件读进内存撑爆主进程
- **文件缺失态** — 预览打开时经 `lumi:path-exists`（异步 `fs.promises.access`，避免离线网络盘同步阻塞主进程）探测一次：文件被移动/改名/删除 → `MissingState`（提示 + 重新检查）；卡片渲染不探测，零开销
- **HTML 预览安全** — iframe `sandbox="allow-scripts"`（不带 `allow-same-origin`）：脚本可运行让交互页正常，但 opaque origin 下对 `lumi-file` 的 fetch 跨域被拦，恶意页读不到本地文件外传
- **FontPicker 触发器收缩对齐** — 界面字体下拉触发器从 `min-w-44 justify-between`（短字体名时文字被顶到最左、留大空隙）改为 `inline-flex max-w-56`（按内容宽收缩，文字与箭头紧邻）

## [0.1.0a23] - 2026-06-17

### Added
- **Desktop 边栏可拖拽调宽**（`docs/architecture/desktop.md` 可调宽边栏节）— 三栏布局（左侧会话栏 + 右侧后台任务栏 / 任务执行记录栏）均可拖动边缘调整宽度，各自宽度存 localStorage（`lumi-sidebar-width` / `lumi-bg-width` / `lumi-runs-width`），越界或脏值回退默认值。统一封装在 `desktop/src/components/ResizeHandle.tsx`：`useResizableWidth(key, def, min, max)` 为单一事实源（lazy-init + 自带边界钳制的 setter + useEffect 持久化，与 `font.ts` / `theme.ts` 同构），`<ResizeHandle>` 作为 flex 兄弟节点的拖拽分隔条（`edge` 决定加宽方向，默认透明 hover 显品牌金细线）。拖拽期间给 `body` 挂 `resizing-col` 类全局停用过渡 + 统一 `col-resize` 光标，让边栏即时跟手并压制 `BgTasksDrawer` 开关动画（松手恢复）。后台任务栏拖拽条与抽屉、toggle 共用 `bgDrawerOpen && activeBgTasks.length > 0` 可见性条件，切到无任务会话时不留悬空拖拽条

## [0.1.0a22] - 2026-06-17

### Added
- **Desktop 界面字体设置**（`docs/architecture/desktop.md` 界面字体节）— 设置→通用页新增「界面字体」与「字号」：从**本机已装字体**里挑界面字体（经 `queryLocalFonts()` 枚举，每个字体名用自身字体预览，带搜索）+ `−/+` 字号步进器（11–20，点数字重置默认 13）。偏好存 localStorage（`lumi-font`，`{family, size}` JSON，自动迁移旧版裸字符串）。落地 `desktop/src/font.ts`（`useUiFont` hook，与 `theme.ts` 同构）+ `desktop/src/components/FontPicker.tsx`。覆盖机制：默认栈 `--font-fallback` 为唯一真相，`--font-sans = var(--ui-font, var(--font-fallback))`，故 `body` 与所有 `font-sans`/`font-heading` 工具类（含 Dialog 标题）一并跟随；字号走 `var(--ui-font-size, 13px)`。选字体经 `cssFamily()` 转义族名并追加回退保证中文不缺字

### Changed
- **Electron 权限收口** — `main.cjs` 的 `setPermissionRequestHandler`/`setPermissionCheckHandler` **仅放行 `local-fonts`**（字体枚举所需），其余权限（camera/mic/geolocation/clipboard…）一律拒绝，不再使用宽松默认
- **字体枚举健壮性** — `queryLocalFonts()` 需 user activation，故首次枚举在点击处理器内同步触发（非 effect），避免打包版丢激活；不可用/被拒时面板提示「无法访问本机字体」；列表渲染封顶 `MAX_VISIBLE` 行（多出靠搜索收窄），避免上百字体一次性挂载造成开屏卡顿

## [0.1.0a21] - 2026-06-16

### Added
- **Desktop 输入栏文件附件**（`docs/architecture/desktop.md` 输入栏文件附件节）— `+` / 拖拽 / 粘贴现在支持任意文件，不再限于图片。图片仍读成 base64 嵌入 `image` 块；其它文件（PDF / 视频 / docx…）经 Electron `webUtils.getPathForFile`（`preload.cjs` 新暴露，Electron 33 起 `File.path` 已移除）取绝对路径，发送时以 `<attached-file>路径</attached-file>` 注入消息让 Agent 用 `read` 读取——**不预授权**，能否读取交给现有权限引擎。`<attached-file>` 是显示层注入块：`text_cleaning` 从可见正文剥离（不污染气泡），`server/ws._extract_files` 用同一标签还原文件胶囊；标签名 `ATTACHED_FILE_TAG`（`lumi/utils/constants.py`）作单一事实源。气泡内附件渲染成品牌金描边胶囊（仅文件名 + hover 路径），输入栏 pill 为图标 + 文件名
- **应用内轻量通知通道**（`desktop/src/components/Toast.tsx`）— 模块级 store + 根部 `<ToastHost/>`，任意模块 `toast.error/success/info(msg)` 即可调用，无需 context / prop 透传。顶部居中细条幅，按 kind 上语义色，下拉淡入 + 自动消失，多条堆叠。首个接入点：文件附件取不到路径时提示失败（不再静默吞掉）

### Changed
- **添加文件夹按钮即时 tooltip** — `FolderMenu` 的 `FolderPlus` 从原生 `title`（约 1.5s 延迟）改用 Radix `Tooltip`（复用根部 `delayDuration=200` 的全局 Provider），悬停即出；菜单展开时不显示，避免与下拉重叠
- **`<attached-file>` 标签单一事实源** — display 剥离（`text_cleaning`）与历史还原（`server/ws`）的正则均由 `constants.ATTACHED_FILE_TAG` 构建，basename 取用 `Path(p).name`；i18n 键 `composer.removeImage` → `composer.removeAttachment`（图片/文件 pill 共用）

## [0.1.0a20] - 2026-06-16

### Added
- **Desktop 上下文用量指示器**（`docs/architecture/desktop.md` 上下文用量指示器节）— composer 右下角（发送键左侧）一粒圆环，实时反映「当前对话占用 / 模型上下文窗口」。占用量取最近一次模型调用的 `usage.input_tokens`（含缓存命中，即当前上下文），`App.tsx` 的 `ctxFromUsage` 从 `message.complete` / `turn.complete` 提炼写入 `SessionState.ctx`（回合中流式刷新）；窗口来自 `list_providers` 新增的 per-model `context: {model: context_length}`（models.dev catalog，与 `thinking` 同源 `lookup(m)`），前端按 `activeModel` 派生 `contextWindow`（`useMemo` 化避免流式逐 token 重渲染重跑 `providers.find`）。默认仅圆环，颜色即档位（绿 `<60%` / 金 `60–85%` / 红 `>85%`）；点击向上弹出明细（大进度条 + 已用/总量 + 输入/输出/缓存命中分项 + 当前模型与窗口），临界态发光呼吸 + 红色「上下文将满」提示条。数据未就绪时静默不渲染

### Changed
- **后台任务提示词收口** — `agent` / `background_task` / `bash` 三处后台任务工具描述统一加上「启动后等通知即可，不要轮询状态或读 output_file」的指引，避免子代理把后台任务的中间噪声拉进上下文、违背后台执行初衷
- **子代理统计行空态** — `agentStats` 无子工具且无 token 时返回空串，`DoneCard` / `SingleAgent` / `AgentFleet` 条件渲染，历史恢复的卡片与刚启动尚未调工具的瞬间不再显示误导性的「0 工具」

## [0.1.0a19] - 2026-06-15

### Added
- **Desktop 子代理执行反馈 UI**（`docs/architecture/subagent-rendering.md` Desktop 节）— 子代理跑起来时不再只有一个转圈的父工具行，而是把内部活动透出来。前端把带 `parent_run_id` 的子事件（`tool.start`/`tool.complete`/`message.complete`）经 `applyChildEvent` 归属到 `runId` 匹配的父卡片（子工具入 `children`、token 按 max 累计，与 TUI 同口径），逐字流（`message.delta`/`thinking.delta`）丢弃不渲染。`groupItems` 把连续 `agent` 工具合并成段：单个 → `SingleAgent`（头部统计 + 最近 3 个子工具的有限滚动窗口，新行推入/旧行淡出挤掉）；并发多个 → `AgentFleet`（「运行 N 个子 Agent」面板，每行一个 agent：光点 + 名称 + 当前动作 + 工具数）；完成后统一收成 `DoneCard` 单行。新增小光点 / 子工具进出场动画（`index.css`），遵循「光」品牌语言

### Fixed
- **子代理中断对话框被吞**（回归修复）— 事件路由原先无条件把所有带 `parent_run_id` 的事件转入 `applyChildEvent`，导致子代理的 `approval`/`clarify`/`plan` 中断事件被丢弃、对话框永不弹、会话卡死；现仅 `tool.start`/`tool.complete`/`message.complete` 走归属，中断类 fall-through 到主 switch 正常处理
- **底栏状态与子代理卡片重复** — 底部状态指示器扫描运行中工具时排除 `agent`，运行态交由专属卡片展示，不再同时显示冗余的「正在执行子任务…」
- **离场动画僵尸行** — `RunningWindow` 的挤出行除 `onAnimationEnd` 外加一次性 `setTimeout` 兜底，窗口后台化等场景浏览器不派发 `animationend` 时也能移除，避免隐形残留行无界累积

### Changed
- **`AgentGroup` memo 比较器修复 + 收口** — 原裸 `memo` 对每次 `groupItems` 新建的 `items` 数组浅比较永不命中、主流每个 token delta 全量重渲染；改用与 `ToolGroup` 共用的 `sameItems` 元素身份比较器
- **子代理事件定位反向扫描** — `applyChildEvent` 定位父卡片由全量正向 `findIndex` 改为从 `s.items` 尾部反扫，长会话 + 高频子工具调用下不再每次全扫主流
- **前端工具辅助函数去重** — 抽 `fmtTokens`（下沉 `lib/utils.ts`）/ `DoneCard` / `sameItems` / `asRecord`，消除完成态单行、memo 比较器、`args→Record` 解包等多处重复

## [0.1.0a18] - 2026-06-15

### Added
- **Workflow 多代理编排**（`docs/architecture/workflow.md`）— `workflow` 工具用一段确定性 Python 脚本编排子代理（移植自 Claude Code 内置 Workflow）。脚本在受限命名空间执行（禁 `import`/`open`，只防误触非安全边界），注入钩子 `agent()`（派 LLM 子代理，`schema` 强制结构化输出）/ `parallel()`（屏障）/ `pipeline()`（无屏障，默认优先）/ `phase()` / `log()` / `args`；并发上限 `min(16, CPU-2)`，终身上限 1000。子代理复用父 `PermissionEngine`（共享工作区边界，读得到父工作文件，review/audit 编排能跑的前提）、`checkpointer=None`、禁用 `agent/workflow/ask/cron/background_task` 防递归。后台 fire-and-forget：立即返回 task_id，跑完经 `NotificationQueue` 推完成通知。**本版不含 `run`/`sh`**（Lumi 无沙箱，确定性活交子代理 bash）
- **Ultra 思考档位**（`docs/architecture/thinking.md` Ultra 节）— Lumi 合成顶档（对标 Claude Code ultracode）：选中后**原生思考拉到该模型最高档**（`effort_params` 委派 `_native_max_level`，Claude→max / GPT→high，唯一别名点）+ **解锁 workflow 编排**。缓存安全三层：workflow 工具始终注册（不增删工具，prompt 缓存前缀恒定）+ 工具描述写死「仅 Ultra 或用户明确要求时用」+ Ultra 信号经轮内 `<system-reminder>`（`bridge._ultra_note`，前置当轮消息、不碰系统提示词）传达，toggle Ultra 不废 system+tools 缓存。ModelPicker 金光点 Ultra 行 + chip 金字
- **后台任务中心 drawer**（`docs/architecture/desktop.md`）— 右侧可开关面板，纳管 **bash / agent / workflow** 三类后台任务（`TaskRegistry` 单一注册中心，desktop 首次有了后台任务实时 UI）。头部 `PanelRight` 开关（运行中带脉动金点）；一摞可独立折叠的任务卡片（kind 分派详情，workflow 画实时聚合进度：phase + 进度条 + 在跑数）；终态卡片 hover 移除 ✕ / 头部「清除已完成」，每会话终态自动保留最近 20 条（`_TERMINAL_CAP`）。`TaskRegistry.on_change` 观察者 → ~100ms 去抖 → 广播 `bg_tasks.update`（全量快照，前端按 thread 过滤）；新增 RPC `list_bg_tasks` / `stop_bg_task` / `dismiss_bg_task` / `clear_finished_bg_tasks`

### Fixed
- **workflow 进度虚高** — `_dispatched` 计数移到子代理 build 成功之后自增：build 失败的 agent 不再计入 `total`，进度条能正常到 100%（之前 `bad agent_name` 一类失败会让 total 永久大于 done）
- **TUI 无法停止 workflow** — `bg_screen._stop_task` 只认 AGENT/BASH，workflow 静默 no-op；现统一经 `cancel_background_task` 按 kind 分派，三类都能停
- **跨会话 stop/dismiss** — `stop_bg_task` / `dismiss_bg_task` 加会话归属校验（`_owns_bg_task`），不再能停/移除其它会话的后台任务（`clear_finished` 本就按 thread 限定）
- **运行中 Duration 不实时** — drawer 加每秒本地 tick（仅面板打开且有任务在跑时计时），运行中任务的用时实时跳动，不再卡在上次事件的值

### Changed
- **后台任务停止/生命周期收口** — 三类后台任务的「按 kind 停止」从 ws / TUI / `background_task` 三处重复分派收口到 `session.cancel_background_task`（新增 TaskKind 只改一处）；agent / workflow 后台收尾骨架（写文件 / 状态 / 通知）抽成共用 `bg_tasks.run_background_task` + `make_bg_done_callback`，两个 provider 只剩差异化的 produce 闭包
- **`serialize_task` 字段派生** — 改为从 `BackgroundTaskEntry` dataclass 字段派生（排除 `async_task` / `prompt`），新增字段默认上线、不再因漏改被静默丢弃；前端 `BgTask` 类型是唯一「该不该收」的闸门
- **广播去抖** — 后台任务变更广播加 ~100ms 合并窗口（`_bg_flush`），workflow 扇出时的高频 `notify_progress` 不再每次全量序列化+广播；`_spawn_broadcast` 收口 cron / bg_tasks 共用的 fire-and-forget 广播模式
- **工具 description MD 加载收口** — `resolve_tool_md` / `load_tool_md` / `require_tool_field` 从 `plan.py` 提到 `tools/loader.py`，plan 与 workflow 工具共用；`allowed_levels` 的 ultra 追加从 3 处分支收成末尾一次

## [0.1.0a17] - 2026-06-14

### Added
- **Hook 机制**（`docs/architecture/hooks.md`）— 在 Agent 生命周期事件上注入外部逻辑，无需改内核。事件 Stop / PreToolUse / PostToolUse 已插桩（Stop 走独立 `OnAgentStop` 薄节点，因条件路由函数不能返回 `Command`）；返回值 `AdditionalContext`（注入 `<system-reminder>`）/ `Block`（拦截）/ `Command`（控制路由），dispatch 三模式 first_intercept / collect / side_effect，单 hook 抛错隔离不拖垮主流程
- **Shell hook + 三级 hooks.json 配置** — 决策协议（stdin/stdout JSON，`decision: allow/deny/passthrough`）；subprocess 5s 超时 + SIGTERM→SIGKILL、env 仅透传 `LUMI_HOOK_*` 前缀防 secrets 泄露、`matcher` 正则按工具名筛；配置走 `~/.lumi/hooks.json` + `.lumi/hooks.json` + `.lumi/hooks.local.json`（与 permissions 同级同模式，JSONC），单条坏配置 log 跳过不致命；desktop 切工作目录时 `reset_hooks` + `load_hooks` 重载

### Changed
- **结构化输出：伪工具拦截 → 真工具执行** — `__structured_output__` 改为真工具进 `tool_executor` 执行（删除 `ExtractStructuredOutput` 节点）：闭包内 jsonschema 校验，失败 return `ToolMessage(status=error)` 让模型修正重试；成功写 `Command(update={structured_output})` 不带 goto、模型自决 end_turn。新增 JSON Schema 校验、连续失败保护（`MAX_CONSECUTIVE_FAILURES=5` 强制 END 防烧 token）、Stop hook 兜底（`structured_output_stop_hook` 拉回，`MAX_STOP_PULLBACKS=3` 防死循环）；**移除硬编码 `tool_choice="any"`**（消除与 Anthropic thinking 的 400 冲突），改由模型自决 + hook 兜底
- 混合批次安全 — 内部伪工具与其他工具混合调用时不再绕过权限审批（`is_internal_tool` 收口「内部工具」判定，纯内部批次才走快速路径）；`__structured_output__` 不暴露给用户 hook payload
- 轮边界判定收口 — hook 注入的 reminder 带 `is_hook_reminder` 标记（区别于后台通知等真实 meta），连续失败计数 / 拉回计数 / accepted 判定复用共享遍历器 `meta_message.iter_current_turn`（跳过 reminder、真实 HumanMessage 为边界），避免跨轮泄漏

### Fixed
- **desktop 复制按钮位置/时机** — 复制按钮改挂在每轮「最后一个 segment」之后（整段助手输出底部，文字后跟工具如 ask 时落在工具下方），不再夹在文字与工具之间；只复制本轮最终那段助手文字（中间过程段不给）；**历史轮始终可复制**（修复：旧逻辑用会话级 `running` 门控会在跑新一轮时隐藏所有历史轮复制），仅对在飞末轮按 `running` 把关；错误气泡（notice）不占复制锚点

## [0.1.0a16] - 2026-06-13

### Added
- **desktop 项目管理**（`docs/architecture/desktop.md`）— **项目 = 工作目录**（会话隔离单位）。侧栏新增「项目」入口打开 `ProjectsPage`（搜索 + 排序 + 卡片，当前项目金描边 + 静止金点）；`NewProjectDialog` 选目录后以末端目录名预填名称（可改）；卡片 `⋮` 菜单重命名 / 移除（二次确认，只删清单不动磁盘）。项目清单纯手动登记、持久化在 `~/.lumi/projects.json`（`lumi/server/projects.py`），按最近使用降序
- **切换工作目录** — 点项目卡片经 `set_workspace` 切换：进程级 `os.chdir` + 重建权限边界 + 重置共享 shell；经 `_active_bridges` 弱引用注册表让每个存活 bridge 的引擎一并 `rebase`，避免其它会话边界与 cwd 脱节；切换后另开新会话
- **添加文件夹（本会话临时）** — composer 底栏 `FolderMenu`（文件夹图标 + 数量徽标 + 增减菜单）把目录临时加进本会话可访问范围（`engine.add_ephemeral_workspace`，仅内存、不持久化、连接断开即失效）；增减变更经 `<system-reminder>` 在下一条用户消息告知模型；WS 重连后前端按 `folderStore` 重放恢复后端状态
- 新增 RPC `list_projects` / `add_project` / `remove_project` / `rename_project` / `set_workspace` / `add_folder` / `remove_folder`；Electron `lumi:pick-directory` IPC 调原生目录选择器

### Changed
- `lumi/agents/permissions/engine.py` 新增 `rebase`（切项目根重载配置 + 重建边界）与 `add_ephemeral_workspace` / `remove_ephemeral_workspace`（临时目录，区别于持久化的 `add_workspace`）
- 复用收口 — 相对时间格式化 `timeAgo` 移入 `lib/utils.ts`（按语言缓存 `Intl.RelativeTimeFormat`）；`projects._load` 损坏文件读取对齐 `session_meta` 加日志告警；`projects.json` 走 `_atomic_write_json` 原子写

## [0.1.0a15] - 2026-06-12

### Added
- **思考管理全链路**（`docs/architecture/thinking.md`）— 思考能力由 models.dev 数据驱动（`utils/model_catalog.py`，141 provider / 5000+ 模型，缓存 `~/.lumi/cache/` TTL 24h，损坏自愈）：effort 型模型（Claude/GPT 系）按原生档位枚举渲染、toggle 型（MiMo/Kimi/GLM）仅 On/Off、无思考模型不渲染控制——用户永远选不到会报错的档位。档位按模型记忆（profile 的 `effort` dict），原生值直传无档位翻译；Claude 的 Auto = adaptive（自适应思考），Off 关闭
- **思考内容流式展示** — 新事件 `thinking.delta`（Anthropic thinking 块 + 方言 `reasoning_content`，`DialectChatOpenAI` 保留 ChatOpenAI 丢弃的非标字段）；desktop 状态指示器思考阶段可展开实时思考流
- **desktop 底部状态指示器**（参考 Claude）— 光点光晕（`.lumi-orb`，品牌「光」语言、一静一动）+ 阶段文案（思考/输出/动作级工具状态/等待确认）+ 本轮计时；运行全程常驻，完成后退化为无文字静止光点；审批/澄清/计划对话框移至输入框上方且不打断指示器
- **ModelPicker 重构**（Claude 式）— chip 显示「模型 + 档位」；一级菜单三行（当前模型 ✓ / Effort|Thinking › / More models ›），二级菜单互斥弹出；档位选项完全由后端 `list_providers` 的 thinking 数据（control/levels/effort）驱动，前端零推导
- **TUI `/effort` 命令** — 跟随当前 active 模型显示/设置思考档位，与 desktop 共享同一份能力数据

### Fixed
- **4xx 客户端错误不再重试** — 重试范围收窄为限流/5xx/连接超时；此前模型不支持的参数（400）会在指数退避里"卡住"数分钟伪装成思考中，现在秒级报错透传
- **方言思考模型假卡死** — MiMo 等默认思考的模型，思考增量（`reasoning_content`）被 langchain 静默丢弃导致 UI 长时间无反馈；现在思考流实时可见
- **供应商连接一致性** — 摘要、结构化提取、子 Agent 此前不携带自定义供应商的 base_url/api_key（providers.json 配置的模型在这些路径会打到 env 默认端点）；`provider_store.resolve()` 收口为「模型+连接+档位」单一事实源后全路径一致
- providers.json 的 `effort` 字段为非法类型时不再炸掉 `load()`

### Changed
- **模型模块重构** — `ModelManager` 类拍平为模块函数；`detect_model_type` 三值收为 `detect_protocol` 二值（bedrock 假分支消除）；`llm_chain` 瘦身（token 工具迁入 `token_counter`、retry 配置收口、删除无调用方的 `chat_chain`）；内置调参默认（temperature/timeout 等）移除，交给 SDK 默认值
- **思考注入翻转为显式 opt-in** — `create_llm(apply_effort=...)` 默认不注入思考参数，仅主对话链开启；摘要/结构化提取/连通性测试等内部链天然干净，原先散落的 thinking 对冲逻辑删除
- **模型元数据源 OpenRouter → models.dev** — context_length 同源迁移，`model_info.py` 删除

### Fixed
- **WS 连接断开不再拆除全局运行时** — 每连接的 `bridge.close()` 只清理自身；MCP 子进程、shell / 后台任务会话改由 `shutdown_shared_runtime()` 在进程退出时统一关闭（此前关任一会话会 SIGKILL 所有会话的 MCP 与 bash）
- **cron 跨进程调度互斥** — `Scheduler.start()` 经 `scheduler.lock` 文件锁（flock）保证同一 workspace 只有一个进程调度；TUI 与 `lumi serve` 并存时任务不再双跑（后启动者仍可管理任务）
- **非流式 RPC 不再阻塞 WS 接收循环** — `_dispatch` spawn 成独立 task，需等 `run.lock` 的方法（删会话 / 切模型）不再让 `stop` 帧在整轮结束前读不到
- **后台任务通知按归属投递** — 任务注册时经 `ContextVar` 捕获所属 thread_id，各连接只认领自己会话的通知；多会话时不再被任意连接抢走注入错误对话
- **cron 部分更新立即生效** — APScheduler 注册改为只携带 `job.id`，触发时从 JobStore 重读最新定义；仅改 prompt/name 的更新不再继续执行旧 prompt（RPC 与 agent 工具两条路径一并修复）
- **desktop 子代理事件不再混入主对话** — 带 `parent_run_id` 的流式/工具事件被过滤，子代理 token 不再拼进父气泡（审批/澄清等中断照常弹出）
- **WS 断开后会话不再永久卡死** — `send`/`resume` 的 RPC 拒绝复位 `running`；`error`/`turn.complete` 统一收尾残留的流式气泡，下一轮回复不再粘进死气泡
- **Gateway 关闭不再复活僵尸连接** — `close()` 取消退避中的重连定时器；macOS 关窗保留 sidecar，Dock 唤起后直接复用（此前对着死端口永久重连）
- **RunLog 并发写互斥** — `append` 与 `prune_thread_ids` 加写锁，Run now 撞上定时触发不再丢执行记录；cron 线程删除连带清理文件级 filediff checkpoint
- **`set_provider` 无效切换显式报错** — provider/model 不存在时抛错而非静默返回旧 active；`cron.running` 广播 task 自持引用防 GC

### Changed
- **协议层收口** — `protocol.event_frame()` 统一 wire 信封构造（4 处手拼消除）；`ws.py` 改为 `_RPC_HANDLERS` 分发表并导出 `IMPLEMENTED_METHODS`，契约测试直接断言真实实现而非手抄集合
- **delivery 改为值对象契约** — `deliver(record: RunRecord, text)` 取代 6 个 kwargs 的 5 份平行签名；`cron.result` 广播 output 截断 200 字符（详情走 `list_cron_runs`）
- **前端类型对齐真正生效** — `WireEventType` 移除 `(string & {})` 逃生口，`Gateway.request` 泛型化并以 `RpcMethod` 约束方法名（17 处 cast 消除）
- **渲染性能** — `ItemView`/`ToolGroup`/`Sidebar` memo 化 + `activity` 身份稳定化，流式期间不再每 token 重解析全部 markdown / 重渲染侧栏；会话列表只在回合结束时刷新，`list_sessions` 分批并发加载 state
- **复用收口** — 原子写 JSON 统一到 `_atomic_write_json`（带 `mode` 参数）；通知提示词收口 `bridge.drain_notification_hint()`（TUI/desktop 共用）；cron 删除级联收口 `Scheduler.delete_job`；连接激活握手收口 `activate()`；`clip`/`basename` 移入 `lib/utils.ts`
- **新增主题契约测试** — `tests/test_theme_contract.py` 锁住 `tui/theme.py` 色板与 `desktop/src/index.css` 逐色一致；修复亮色主题下 `bg-muted` 误指文字色导致弹窗 footer 发黑（`text-muted` 全量更名 `text-muted-foreground`）
- **依赖全量升级** — langchain-anthropic ≥1.4.0（1.4.4）、langchain-openai ≥1.3.0、langgraph 1.2.2、anthropic 0.109、openai 2.41、fastapi 0.136 / starlette 1.2、textual 8.2.7 等
- bash 工具图标改为带框的 `SquareTerminal`；移除 debug 日志块与死代码（`Gateway.newSession`、`lumi:log`/`lumi:focus` IPC、Sidebar 恒 false 的 `disabled` prop）

## [0.1.0a13] - 2026-06-10

### Added
- Desktop 定时任务管理 — 管理页（卡片网格 + 新建 / 编辑 / 删除 / 暂停 / 立即运行 + 详情）+ 侧栏「定时任务」分组（未读结果角标、运行中脉冲点、连续失败 ⚠）；分组与「最近」均可折叠（状态持久化）
- 任务会话视图 — 点击侧栏任务直接打开最近一次执行的完整对话，右侧 Runs 栏切换历次执行（蓝点 = 未读，点开即消失），composer 直接续聊
- 执行即会话 — cron 每次执行落在独立 `cron-` thread（Scheduler 常驻 checkpointer，`create_agent` 支持复用实例），超时/失败也保留现场；保留最近 50 次（`MAX_CRON_RUN_THREADS`），删除任务级联清理执行日志与全部会话 checkpoint；cron 线程不进会话列表（按 `CRON_THREAD_PREFIX` 过滤，续聊不"转正"）
- WS 定时任务 RPC — `list_cron_jobs` / `create_cron_job` / `update_cron_job` / `delete_cron_job` / `toggle_cron_job` / `run_cron_job` / `list_cron_runs`（run 含 `thread_id` 可跳转续聊）+ 进程级广播事件 `cron.result` / `cron.running`
- serve 接入 cron 子系统 — lifespan 经 `setup_cron()` 工厂（TUI 共用）启动调度器，`lumi/server/desktop_delivery.py` 把任务结果实时推给所有 WS 连接 + 系统通知
- 测试 — `tests/server/test_cron_rpc.py`（RPC CRUD / 校验 / DesktopDelivery 广播）、RunLog 保留策略与级联删除用例

### Fixed
- 会话切换 / 任务会话打开期间显示 `connecting` 状态 — sidecar 不可用时指示灯保持黄色而非静默无反应
- `update_cron_job` 空字符串字段从静默忽略改为显式报错（与 create 校验一致）
- 用户消息气泡、错误提示、任务内容等补充 `selectable` — 修复全局 `user-select: none` 导致发送内容无法选中复制
- composer 输入框滚动条不再超出 24px 圆角容器（容器裁剪 + 轨道留白 + 滑块内缩）
- Button `destructive` variant 改为实底红 — 删除确认弹窗的「删除」不再呈现为类似禁用态的弱化样式

### Changed
- `tui/app.py` cron 初始化收敛为 `lumi/agents/cron/runtime.setup_cron()` 工厂调用
- `RunLog` 新增 `get_all()` / `prune_thread_ids()` / `delete_log()`，复用 `job_store._atomic_write`；`close_checkpointer` 抽到 graph.py 共用；线程删除并发化（`asyncio.gather`）
- 侧栏条目文字加深（`text-ink/80`）、分组标题变浅，层级区分明确

## [0.1.0a12] - 2026-06-10

### Added
- 模型供应商管理 — 用户自定义「连接（`base_url` / `api_key`）+ 多模型」的 profile，持久化 `~/.lumi/providers.json`（明文 `chmod 600`），TUI 与 desktop 共享同一份配置；协议由模型名自动判定。`lumi/agents/runtime/provider_store.py` 负责读写（兼容旧格式、失效 active 自动归位）
- WS 模型供应商 RPC — `list_providers` / `test_provider`（连接可达性测试，15s 短超时不重试）/ `set_provider` / `save_provider` / `delete_provider`；`set/save/delete` 持 `run.lock` 与运行轮互斥
- 运行时连接覆盖 — `LumiAgentContext` 增加 `base_url` / `api_key` 字段，`call_model` 经 `_provider_kwargs()` 仅在非空时透传给 `create_llm`（空则沿用 env / SDK 默认）
- TUI `/model` 命令 — `ModelScreen` 模型切换弹窗（「供应商 × 模型」拍平为列表，仅切换；增删改在桌面端完成）
- Desktop 设置页 — `SettingsDialog` + `ProvidersPanel`（供应商增 / 删 / 改 / 测试）+ `ModelPicker` 顶栏快速切换
- 桌面系统通知 — 回复完成与待处理中断（审批 / 提问 / 计划）在窗口未聚焦或非当前会话时弹系统通知（经主进程 `Notification`），点击带回前台并切到对应会话
- 国际化（i18n）— `desktop/src/i18n.ts` 提供中文 / English 双语，`useI18n()` hook，偏好存 localStorage
- Desktop 斜杠命令 — `run_command` / `list_commands` RPC + 前端命令补全（`slash.ts` / `CommandMenu`）；`diff.ts` 工具 diff 视图（edit/write 前端就地算行级 diff）
- `docs/user-manual.md` — 完整用户手册
- 测试 — `tests/test_provider_store.py`、`tests/test_skill_command_blocks.py`

### Fixed
- gateway 断线时 reject 全部 in-flight RPC — 杜绝 `send_message` 等 Promise 永久挂起、会话卡在 running 态、输入框永久禁用
- gateway 重连不再新建幽灵会话 — 改为 `switchSession` 切回原 thread 恢复后端绑定（服务端每条连接是全新 bridge）
- 命令补全 `cmdSel` 越界钳制 — `commands` 异步刷新使 `matched` 缩短时不再取到 `undefined` 崩溃
- `bridge.delete_thread` 用 `try/finally` — `adelete_thread` 抛错也保证清理文件级 checkpoint，避免残留孤儿目录

### Changed
- Desktop UI 迁移到 shadcn/ui — `Dialog` / `Button` / `DropdownMenu` / `Switch` / `Tabs` / `Tooltip` 等组件，移除自研 `ModalShell`
- App.tsx provider 响应处理收敛为 `applyProviderResp` helper，消除三处复制粘贴

## [0.1.0a11] - 2026-06-09

### Added
- Desktop 应用（Electron + React/TS）— 经 WebSocket 复用后端 `AgentBridge`，与 TUI 共享同一套 Agent 运行时；聊天流渲染、审批/澄清/计划对话框、每会话一条 WS 连接的多会话并发
- 会话管理 — 侧栏 `⋮` 菜单（置顶 / 重命名 / 删除），重命名内联编辑、删除二次确认；置顶项稳定排到列表最前
- AI 消息复制按钮 — 悬停出现，复制 markdown 原文，复制后 1.5s 内显示「已复制」反馈
- `lumi/tui/session_meta.py` — 会话用户元数据 sidecar（`~/.lumi/checkpoints/session_meta.json`，按 thread_id 存 pinned/title），textual-free 可供 headless 服务直接使用
- WS 会话管理 RPC — `pin_session` / `rename_session` / `delete_session`
- `protocol/` — 前后端 WebSocket 协议的语言中立单一事实源（`events.json` + README）
- `checkpoint.delete_thread_checkpoint()` — 删除单个 thread 文件级 checkpoint 目录的公开 API
- `docs/architecture/desktop.md` — Desktop 应用架构文档

### Changed
- 协议单一命名 — `BridgeEvent.EventKind` 成员值直接采用对外 wire 命名（`namespace.verb`），`server/protocol.py` 只做 payload 重组，消除 BridgeEvent→wire 映射层；`tests/server/test_protocol_contract.py` 读 `protocol/events.json` 锁住两端事件名/方法名一致
- `bridge.delete_thread()` 统一清理两类 checkpoint — LangGraph 会话（`adelete_thread`）+ 文件级 checkpoint（`delete_thread_checkpoint`），用 `asyncio.to_thread` 避免阻塞事件循环
- `lumi/tui/agent_bridge.py` 下沉为 `lumi/agents/bridge.py` — 桥接层供 TUI / desktop WS 服务共用

## [0.1.0a10] - 2026-04-26

### Added
- `bash` 工具 stdout 字节级截断 — 单次执行累积超过 `BASH_MAX_OUTPUT_BYTES`（30 KB）后整行丢弃，末尾追加 `... [output truncated - N KB dropped]` trailer
- `_BoundedOutputBuffer` 流式累加器（`lumi/agents/runtime/session.py`）— 保头丢尾，超限后仍持续 drain pipe 直至 sentinel，避免 shell 因 stdout 阻塞挂起
- `docs/guides/bash.md` — bash 工具使用指南，覆盖持久化会话、输出截断、后台执行、超时与权限

### Changed
- `LocalShellSession._collect_output` 由聚合 `list[str]` 改为聚合 `_BoundedOutputBuffer`，sentinel 行不入 buffer 以保证 exit code 解析不受截断影响

## [0.1.0a9] - 2026-04-21

### Added
- 斜杠命令补全菜单支持 viewport 滑动 — 匹配项超过 12 项时，窗口跟随 `↑↓` 选中项自动滑动，选中项始终可见

### Changed
- `InputBar` `max-height` 由 14 提升至 24 — 容纳满高度的 ChatInput(8) + CompletionMenu(12) + 状态行(1)，避免菜单被截断
- `CompletionMenu._VIEWPORT_SIZE` 作为单一事实源，通过 f-string 注入 CSS `max-height`，消除 Python 常量与 CSS 值的重复
- `CLAUDE.md` 精简 —— "常用命令" 章节移除，代码风格原则合并重写
- `docs/guides/slash-commands.md` 补充长列表自动滑动说明

## [0.1.0a8] - 2026-04-21

### Changed
- `lumi/agents/` 结构重构 — tools 子系统职责收敛为装配 + 暴露，跨子系统的运行时状态与权限策略上提到 agents 根层，和 core/cron/tools 平级
  - 新增 `lumi/agents/runtime/`，收录 `session.py`（原 `tools/session.py`）、`checkpoint.py`（原 `tools/checkpoint.py`）、`file_tracker.py`（原 `tools/file_tracker.py`）、`bg_tasks.py`（原 `tools/task_registry.py`，同时更名消除和 `tools/registry.py` 的命名撞车）
  - `lumi/agents/tools/permissions/` 上提到 `lumi/agents/permissions/`——决策对象是工具调用，但作用域是 agent 整体（`core/nodes.py` 是核心消费者）
  - `tools/providers/filesystem.py` 升级为 package，原 `providers/_media.py` 移入 `providers/filesystem/media.py` 并去掉下划线前缀（原下划线用于补救"providers 目录下每个文件 = tool provider"这个承诺被破坏的语义问题）
- 所有外部 import 路径同步更新（`lumi.agents.tools.session` → `lumi.agents.runtime.session` 等），共 42 个文件；`StructuredTool` 名字、registry 注册名、配置文件引用的 tool 名全部保持不变
- `docs/architecture/permissions.md`、`docs/architecture/checkpoint.md`、`CLAUDE.md` 同步新路径

## [0.1.0a7] - 2026-04-21

### Added
- `read` 工具多模态支持 — 图片(PNG/JPG/JPEG/GIF/WebP)和 PDF 自动渲染为 image block 注入对话,让模型直接"看到"文件内容
- `lumi/agents/tools/providers/_media.py` 媒体处理模块 — 两阶段图片压缩管线(API 硬约束 5MB/2000px + token 预算 25k)、PDF 按页渲染(150 DPI)、magic bytes 校验防伪装文件污染 session
- PDF `pages` 参数 — 支持 `"1-5"` / `"1,3,5"` / `"1-3,7,9-10"` 等范围格式,单次最多 20 页;≤10 页 PDF 不传 `pages` 时整体渲染,>10 页必须分段
- `lumi/agents/core/meta_message.py` — 集中管理 meta human message 的构造和识别(`META_KEY` / `meta_human_message()` / `is_meta_message()`),取代分散在各处的 `additional_kwargs["is_meta"]` 操作
- `docs/guides/read-multimodal.md` — read 工具多模态读取使用指南

### Changed
- `call_model` 前对 `HumanMessage` 中的多模态 content 按 provider 做格式转换(Anthropic 原样 / OpenAI 转 `image_url` 支持 base64 data URL),统一内部走 Anthropic 风格 block
- `content_to_str` 对多模态 block(image / image_url / document)转为 `[image: media/type]` 占位,避免 base64 泄漏到摘要/日志中
- 消息截断 `_truncate_single_message` 跳过含多模态 block 的消息 — 图片已走过压缩管线,再截文本会破坏 block 结构
- `vision_mode` 配置从 `simple_agent.vision_mode` 读取迁移到 `agents.vision_mode`,与其他 agent 配置同组
- `tui/agent_bridge.py` 和 `tui/message_visibility.py` 改用 `meta_message` 模块

### Fixed
- OpenAI 格式转换现在正确处理 `image_url` 原样 block 和 `image` base64 source(此前只处理 URL source)

## [0.1.0a6] - 2026-04-10

### Added
- `accept_edits` 工具审批模式 — 文件编辑工具（`write`/`edit`）在工作区边界内自动放行，`bash` 等有副作用的命令仍需审批
- CLI `--accept-edits` flag 和 `Shift+Tab` 模式循环（`default` → `accept_edits` → `plan`）
- 工具审批对话框新增"本次会话自动编辑"选项 — 当所有待审批工具都是 `write`/`edit` 时显示，选中后批准当前调用并切换当前 run 和后续消息到 `accept_edits` 模式
- `is_file_edit_tool()` helper（`lumi/agents/tools/capability.py`）
- `human_approval` 节点支持 resume dict 中的 `set_tool_mode` 字段，用于从审批动作反向更新运行中的 graph state

### Changed
- `tool_mode` Literal 从 `"auto" | "privileged"` 改为 `"default" | "accept_edits" | "privileged"`，原 `auto` 重命名为 `default`，`auto` 保留给未来 AI 审批模式
- 工具审批对话框移除"始终允许：通配符模式"选项（如 `bash(echo *)`、`write(**/*.py)`），保留精确匹配（如 `bash(echo hello)`），避免过度授权
- `docs/guides/permissions.md` 和 `docs/architecture/permissions.md` 同步新的模式命名和 `accept_edits` 说明

### Removed
- 删除 `lumi/tui/_app_approval.py`、`_app_cron.py`、`_app_input.py`、`_app_screens.py` 共约 1000 行死代码 — 这些文件的函数早已全部内联到 `app.py`，无任何 import
- 精简 `lumi/tui/_app_lifecycle.py`（296 行 → 78 行），只保留 `apply_theme_mode` 等实际被 `app.py` 使用的主题检测函数

## [0.1.0a5] - 2026-04-09

### Added
- 后台任务管理界面 `BgScreen`（Ctrl+B），支持搜索、详情查看和停止任务
- `InputBar` 后台任务指示器，实时显示运行中的后台任务数量
- `is_meta` 消息机制 — 系统生成的消息（如后台任务通知）不创建 checkpoint，不在 Rewind 中显示
- `message_visibility` 模块，集中管理消息可见性判定逻辑
- `AssistantMessage.unfinalize()` 支持复用已结束的气泡，保持连续文本流
- `utils/jsonc.py` 单元测试（14 个用例）

### Changed
- `tools/runtime/` 扁平化到 `tools/`（checkpoint、file_tracker、session、task_registry）
- `SkillCommandExecutor` 从独立文件内联到 `providers/skill.py`
- `ToolArgsInterceptor` 从独立文件内联到 `providers/mcp.py`
- `permissions/jsonc.py` 迁移到 `utils/jsonc.py`
- `split_compound_command` 从 `permissions/matcher.py` 迁移到 `capability.py`，消除循环依赖
- `ToolRegistry` 从类级单例改为模块级 `get_tool_registry()` 函数
- `filesystem._get_backend` 改为公开 `get_backend`
- `inject_text_into_message` 保留原消息的 `additional_kwargs` 和 `id`

### Fixed
- Agent 注册时 broad `except Exception` 吞掉 `SyntaxError`/`ImportError` 等代码 bug，现分层处理
- `BgScreen._stop_bash` broad catch 收窄为 `OSError`/`ProcessLookupError` + 意外异常分层
- `_stop_task` 不检查 `cancel_agent_task()` 返回值，现记录 warning

## [0.1.0a4] - 2026-04-07

### Changed
- `FileCheckpointManager` 和 `cleanup_stale_threads` 直接读取 `GlobalConfig`，移除冗余的 `max_checkpoints`/`base_dir`/`stale_days` 参数传递链
- 新建 `lumi/utils/constants.py`，集中管理 16 个行为性内部常量（超时、限制、间隔、重试），消除跨模块散落的魔法数字
- `cleanup_stale_checkpoints` 错误日志级别从 `debug` 提升为 `warning`，避免后台清理失败被静默吞没

### Fixed
- `switch_thread()` 创建新 `FileCheckpointManager` 时未传递用户配置的 `max_checkpoints`，静默回退到硬编码默认值 20

## [0.1.0a3] - 2026-04-06

### Changed
- `ToolEffect` 五值枚举简化为 `is_write_tool(name, args) -> bool` 二值判定，消除 FILE_WRITE/SHELL_EXEC/STATE_MUTATE/INTERRUPT 的无意义分类
- `ModePolicy.allowed_effects: ToolEffect` 简化为 `allow_write: bool` + `path_filter`
- `cron` 工具按 operation 区分只读（list/runs）与写入（create/update/delete/run/pause），写入操作现在经过权限引擎评估
- `ask`、`todos` 归类为只读工具（原为 INTERRUPT/STATE_MUTATE）
- 文档重组为三层结构：`docs/guides/`（用户指南）、`docs/architecture/`（开发者文档）、`docs/reference/`（外部参考）
- `cache.md` + `cache_docs.md` 合并为 `reference/prompt-caching.md`

## [0.1.0a2] - 2026-04-06

### Changed
- 全局添加 `from __future__ import annotations`，精确化类型标注（`dict` → `dict[str, Any]` 等）
- 将不依赖 `self` 的实例方法提取为模块级函数（checkpoint、registry、loader 等）
- 移除冗余 docstring，保留类型自文档化
- EventRouter `_transition` 由 match/case 重构为 `_PHASE_MAP` 字典查找
- `CommandResult` 改为 `frozen=True, slots=True` 不可变数据类
- 子 Agent 不再继承 `execution_mode`（有意为之，子 Agent 独立运行）

### Fixed
- ExitPlanMode 拒绝时 `tool_cancelled` 标记丢失，导致用户拒绝 plan 后 Agent 继续执行
- 原子写入（job_store、run_log、checkpoint）`except BaseException` 被误改为 `except Exception`，`KeyboardInterrupt` 时临时文件泄漏
- `config_loader` 误删 `Permission.ASK` 配置解析，导致 settings 中 ask 规则被静默忽略
- Scheduler `start()`/`_compensate_missed_runs` 异常捕获过窄（`ValueError, KeyError`），APScheduler 异常导致整个调度器崩溃
- Scheduler `_deliver_and_log`/`_persist_consecutive_errors` 异常捕获过窄（`OSError`），非 IO 异常导致任务执行流中断
- Cron 工具移除 `KeyError` 捕获，job 未找到时返回通用错误而非友好提示
- `_read_text_safe` 文件读取失败时无日志，diff 统计静默不准确

## [0.1.0a] - 2026-04-02

### Changed
- 权限审批流重构：Graph 层 `human_approval` 简化为纯三态契约（approve/reject/cancel），权限评估、选项构建、规则持久化迁移至 Bridge/TUI 层
- `is_use_tool` 路由逻辑统一：bypass-immune → 权限引擎评估 → 模式分派，所有模式共用同一评估循环
- `stream_resume` 不再强制 `tool_mode="auto"`，由 Graph 状态自行维护
- 审批 resume 值从字符串改为 `dict`（`{"decision": ..., "message": ...}`），支持结构化拒绝原因
- `ToolApproval` 简化：单工具直接渲染参数，多工具使用缩进子标题；border_title 显示工具名而非固定文案
- `RuleMatcher` 通配符增强：`"ls *"` 同时匹配 `"ls"`（无参数）和 `"ls -la /dir"`

### Fixed
- **权限评估异常在 privileged 模式下穿透到自动放行**：异常时直接路由到 HumanApproval 而非继续执行
- **`human_approval` DENY 检查的 `except Exception: pass`**：改为记录日志并保守拒绝
- 边界检查异常时向用户展示警告（而非静默忽略）
- `add_allow_rule`/`add_workspace` 引擎不可用时记录 warning
- `_persist_allow_rule` 找不到 tool_expr 时记录 error（而非静默跳过）
- `engine is None` 路径恢复审计日志
- `_render_tool_args` 中 `get_renderer()` 移入 try 块防止注册表异常崩溃 widget

## [0.0.11] - 2026-04-01

### Added
- ASK 权限规则：支持 `ask` 级别配置（优先级介于 deny 和 allow 之间），适用于"允许但需确认"的操作如 `git push`、`npm publish`
- Bypass-immune 安全检查：即使 privileged 模式也不可跳过的保护层，覆盖 shell 配置（`.bashrc`/`.zshrc`）、SSH/GPG 密钥、项目权限配置等敏感路径
- Bash 命令安全警告：审批界面对 `git push --force`、`git reset --hard`、`curl | sh` 等危险模式显示警告辅助决策
- 复合命令拆分评估：bash 复合命令（`&&`、`||`、`;`、`|`）逐个子命令评估权限，取最严格结果
- 临时规则（CLI `--allow`）：支持会话级 allow 规则，不持久化
- 审批组件基类 `BaseApproval`：提取 ToolApproval 和 PlanApproval 的共享逻辑（键盘导航、选项渲染、滚动委派）

### Fixed
- `_check_bash_tool` 补充对 `.ssh/`、`.gnupg/` 前缀路径和项目级受保护路径（`.lumi/permissions.json`、`.git/config`）的写入检查
- `split_compound_command` 从 `shlex.split` 改为字符级状态机，修复引号内分隔符被错误拆分的安全问题
- bypass-immune 安全检查对非字符串参数保守标记为需审批（而非默认放行）
- `Path.home()` 模块级调用改为 try/except，避免 HOME 未设置时导入崩溃
- `human_approval` 中 `engine.evaluate()` 和 `get_boundary_violations()` 添加 try/except 保护
- privileged 模式下 `is_bypass_immune` 调用添加异常保护，防止路由崩溃
- `PermissionEngine.__init__` 的 `except Exception` 收窄为 `(OSError, json.JSONDecodeError, ValueError, KeyError)`
- 移除 `PermissionConfig.permissions is None` 死代码检查
- `_DANGER_PATTERNS` 的 level 字段标注为 `Literal["warning", "danger"]`，消除 type: ignore

### Changed
- 权限评估从两遍扫描（先 deny 后 allow）改为单遍扫描取最严格结果，支持三级优先级：deny > ask > allow
- `PermissionEngine.get_boundary_violations` 添加与 `check_workspace_boundary` 一致的防御性错误处理
- `ToolApproval._render_options` 去重，委派到基类 `BaseApproval._render_options(max_label_len)`

## [0.0.10] - 2026-03-31

### Added
- `LumiAgent.aprune_checkpoints_after()`：支持按 checkpoint_id 清理指定位置之后的 LangGraph checkpoint 数据（SQLite / Postgres / InMemory）
- ToolApproval 审批卡片内容区域支持滚动（`shift+↑↓` / `pgup/pgdn`），解决长内容审批时无法查看完整参数的问题
- PlanApproval 计划审批同样支持内容区域滚动

### Fixed
- Checkpoint 回退（rewind）现在正确恢复到目标轮次执行前的状态：收集目标及之后的变更进行恢复，meta 截断到目标之前
- Rewind 后清理目标之后的所有 LangGraph checkpoint，避免旧分支数据残留
- `_create_checkpoint_before_turn` 检测 stale checkpoint 时区分有 interrupt 和无 interrupt 的情况，仅对无 interrupt 的 stale 状态沿 parent 链回退到 clean checkpoint
- Rewind 回退到第一条消息之前时移除 `checkpoint_id` 并删除整个 thread，等效于空会话
- `_reset_run_state` 中清理 `_pending_system_commands`，防止残留命令影响下一轮

## [0.0.9] - 2026-03-30

### Changed
- Checkpoint 系统从 Shadow Git 重构为文件级快照：不再依赖 git，只追踪 edit/write 工具修改的文件，占用更少磁盘空间
- 新增 `FileChangeTracker`（`lumi/agents/tools/file_tracker.py`）：拦截文件操作记录修改前原始内容
- 新增 `FileCheckpointManager` 替代 `ShadowGitManager`：基于目录结构保存变更清单和原始文件副本
- `FileChangeTracker` 新增 `peek_changes()` 公共方法替代内部属性直接访问
- Checkpoint hash 生成从 `id(object())` + SHA1 改为 `uuid4`，消除碰撞风险

### Fixed
- Checkpoint 三处顶层异常捕获从裸 `except Exception` 收窄为 `(OSError, json.JSONDecodeError, KeyError, ValueError)`
- diff 统计计算中 4 处 `except Exception: pass` 收窄为 `(OSError, UnicodeDecodeError)` 并添加日志
- `_recover_stale_state` 裸 `except Exception: return` 添加 `logger.warning` 日志
- `restore_checkpoint` 部分文件恢复失败时正确返回 `False` 而非 `True`
- `_load_meta` 备份失败时尝试删除损坏文件而非静默忽略
- `_load_changes` 备份文件缺失时添加警告日志
- `shutil.rmtree(ignore_errors=True)` 改为显式 `try/except OSError` 加日志
- `record_pre_edit` 异常捕获收窄为 `(OSError, UnicodeDecodeError)`
- Shell 会话关闭时显式清理子进程 transport，修复 pytest 中 `RuntimeError('Event loop is closed')` 警告
- 用户提示中 "Shadow Git 未初始化" 更新为 "Checkpoint 未初始化"
- 移除 `_unsafe_filename` 死代码、冗余 `asyncio` 导入、无用注释
- 消除 `_compute_diff_stat` 与 `_compute_diff_stat_live` 的 ~30 行重复代码
- `create_checkpoint` 中双重 `_load_meta()` 调用优化为单次

## [0.0.8] - 2026-03-30

### Added
- Workspace 隔离：cron 定时任务和会话历史按工作目录隔离存储，不同项目互不干扰
- Cron 错过任务补偿：Scheduler 启动时自动检测并补偿执行离线期间错过的定时任务
- Cron TUI 执行状态指示器：InputBar 右侧显示正在执行的定时任务动画
- `lumi/utils/workspace_id.py`：基于 SHA256 的工作目录唯一标识生成
- `lumi/tui/text_cleaning.py`：统一的 XML 标签过滤和用户输入还原模块
- `RunLog.get_last_run_sync()`：同步获取最近执行记录，用于启动时补偿检查

### Fixed
- Checkpoint 截断 bug：`restore_to` 现在正确保留目标 checkpoint 记录而非截断它
- AgentBridge 错误信息增强：流式错误包含异常类型和 cause 链，图状态异常提供详细诊断
- MCP stdio 子进程 stderr 静默：避免 MCP 子进程日志输出污染 TUI 界面
- Headless 模式移除 stderr 重定向 hack，改为 MCP 层面解决
- Agent 响应多模态 content 提取：正确处理 list 类型的 content 块

### Changed
- TUI 审批对话框（AskDialog、ToolApproval、PlanApproval）从 ToolBlock 内部挂载改为 InputBar 前挂载
- 只读工具（read、glob、grep）不再检查工作区边界，仅写操作受边界保护
- AgentGroup `add_agent` 改为 async，直接 await mount 替代 `call_after_refresh`
- Agent 工具 schema 延迟初始化：避免模块导入时重复加载配置
- LLM 超时从 120s 增大到 300s
- `structured_output` 和 `chat_chain` 新增 httpx 网络错误重试（RemoteProtocolError、ConnectError、ReadError）
- 权限检查日志改为始终输出（不仅在需要审批时），便于排查
- TodosBar 操作统一使用 `_query_safe` 替代 try/except NoMatches
- AgentGroup 统一 `_get_entry` 方法减少重复代码，统计摘要在无数据时省略括号
- `_prepend_plan_reminder` 泛化为 `_prepend_text_block`，消除重复的 content 注入逻辑
- `docs/cron.md`、`docs/permissions.md` 文档更新

## [0.0.7] - 2026-03-28

### Added
- 新增 Style 系统：支持通过 `style` 配置切换系统提示词风格，内置 `default` 和 `code` 两种风格
- 新增 `lumi/styles/` 目录，包含风格内置的 prompts、tools、agents 配置
- 新增 CLI `--style / -s` 参数，运行时覆盖 config.yaml 中的风格配置
- 新增 CLI `--privileged-danger` 参数，启动时进入特权模式跳过所有审批
- 新增 `docs/styles.md` 文档
- Plan Mode 支持用户手动开启（Shift+Tab 切换 `⏸ plan` 指示器）

### Changed
- 移除 `approve` 工具模式，简化为 `auto` / `plan` / `privileged` 三种状态指示
- Plan Mode 工具提示词从硬编码迁移到 style MD 文件加载，缺失时抛出 RuntimeError 而非静默回退
- 系统提示词加载逻辑重构：先从 style 内置目录读取，再用用户 `.lumi/prompts/` 覆盖
- `BridgeEvent` 移除 `approval_mode` 字段
- `InputBar` 重构：Shift+Tab 改为切换 plan mode，移除 tool_mode 循环切换
- `PlanApproval` 组件布局优化，计划文件名突出显示
- `docs/config.md`、`docs/permissions.md`、`docs/plan.md` 文档更新

## [0.0.6] - 2026-03-28

### Added
- 新增 Plan Mode（计划模式）：Agent 可在执行非平凡任务前进入只读规划阶段，设计方案后提交用户审批
- 新增 `EnterPlanMode` / `ExitPlanMode` 工具，支持从 `.lumi/prompts/tools/EnterPlanMode.md` 自定义提示词
- 新增 `PlanApproval` TUI 审批组件，展示计划文件内容并提供批准/拒绝操作
- 新增 MIT LICENSE
- 新增 `docs/plan.md` 文档

### Changed
- `ToolApproval` 组件重构为圆角卡片布局（`╭│├╰`），标题嵌入顶部边框，提示嵌入底部边框
- `CLAUDE.md` 全面重写，补充架构概要、工具系统、权限系统、TUI 架构、子 Agent 等详细说明
- `README.md` 增强：新增徽章、Headless/浏览器模式说明、文档索引表，精简冗余内容
- `pyproject.toml` description 更新为中文描述

### Fixed
- 修复 `test_preprocess_skill_injection` 中因系统信息注入导致的测试不稳定
- 修复 `test_skill_injector` 中 system-reminder 格式断言与实际输出不匹配的问题
- 修复 `test_filesystem` 中空文件警告文本与实际返回值不一致

## [0.0.5] - 2026-03-25

### Added
- 工具结果卸载功能：大文件自动写入 `~/.lumi/offload/` 目录，消息中保留文件路径引用，避免占用过多上下文窗口
- 新增 `lumi.cli` CLI 入口模块，支持 `textual-serve` 集成

### Changed
- Token 配置从固定 token 数改为相对于 `context_length` 的比例配置（`once_tool_ratio`、`trim_messages_ratio`），更灵活适配不同模型的上下文窗口
- TUI 渲染架构重构：统一为 WidgetAssembler 模式，支持摘要层和懒渲染优化
- ToolGroup 和 AgentGroup 支持合并显示与轻量摘要模式，提升长对话可读性
- TUI 事件路由解耦，采用渲染器注册机制，提高可扩展性

### Fixed
- 修复 sub-agent 审批 replay 渲染与 todos-bar 持久化问题
- 修复 TUI ask 取消状态处理、ToolBlock 焦点样式与 resume 提示
- 修复 TUI 滚动异常、消息恢复分组逻辑
- 增强权限引擎异常处理与边界检查

## [0.0.3] - 2026-03-19

### Added
- 新增 Checkpoint 回退机制：自动快照工作区文件和 LangGraph 会话状态，支持一键回退到任意历史节点
- 新增 `/rewind` 命令和双击 Esc 快捷方式打开 Rewind 界面，选择并回退到历史 checkpoint
- 新增 `ShadowGitManager`：在项目目录外维护独立 git 仓库，追踪文件变更，不影响项目本身的 Git 历史
- 新增 `lumi/agents/tools/checkpoint.py` 模块，实现 checkpoint 创建、列表、恢复和 diff 统计
- 新增 `lumi/tui/screens/rewind_screen.py` 组件，提供 checkpoint 选择界面
- 新增 `docs/checkpoint.md` 文档，详细说明 Checkpoint 功能的工作原理和使用方式

### Changed
- `ListScreen` 支持配置初始选中项索引，适配 Rewind 界面自动选中最新 checkpoint 的需求
- `AgentBridge` 集成 Shadow Git 管理，在每轮对话前自动创建 checkpoint 并关联 LangGraph checkpoint_id
- `LumiApp._restore_messages` 支持指定 checkpoint_id 参数，用于回退后重建历史消息

## [0.0.2] - 2026-03-17

### Changed
- `recursion_limit` 默认值从 100 调整为 5000，适配复杂任务场景
- `apply_env` 环境变量注入策略改为始终覆盖系统环境变量
- scheduler、API、TUI 三处 agent 调用统一传入 `recursion_limit` 配置

### Removed
- 移除 `max_upload_size_mb` 配置字段

## [0.0.1] - 2026-03-17

首个正式发布版本。
