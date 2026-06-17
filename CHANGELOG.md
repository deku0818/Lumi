# Changelog

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
