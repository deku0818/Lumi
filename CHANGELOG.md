# Changelog

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
