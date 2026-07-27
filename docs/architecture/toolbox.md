# 环境工具箱（Toolbox）

Agent 任务工具链的探测与安装。实现在 `lumi/gateway/toolbox.py`（探测/安装）+ `lumi/gateway/env_rpc.py`（RPC 与进度广播），入口是桌面端「设置 → 环境」与「设置 → 渠道 → 飞书」的接入体检。

## 背景

打包版桌面 app 自带 PyInstaller 后端，Lumi 本体不依赖用户环境；但 agent 的任务工具链是真空的——非技术用户机器上通常没有 Python、Node、rg，飞书集成还需要 lark-cli + 技能包。

原则：**运行时框架全责（隔离目录、免 sudo、绝不碰系统全局）；缺失时优雅降级；安装由用户知情触发**。

## 分层

| 类别 | 内容 | 安装时机 |
| --- | --- | --- |
| 核心工具链 | uv、ripgrep、Node.js（含 npm） | 全 lazy：用户在「设置 → 环境」点「一键装齐」或逐项安装；首启不下载任何东西 |
| 飞书集成 | lark-cli（机器级）+ 技能包（**项目级**） | 「设置 → 渠道 → 飞书」接入体检的「本地环境」组就地一键装 |

- **uv**：一个二进制 = 整个 Python 生态（`uv run --python 3.12`、`uvx`）。
- **ripgrep**：现有纯 Python 降级链保留作兜底；装上后大仓库搜索快一个数量级。
- **Node.js**：官方 tarball（自带 npm），agent 的 JS 任务 + lark-cli 的 npm 安装路径。
- **飞书技能包**：占模型上下文（description 常驻注入），按「谁用谁装」装到渠道绑定项目的 `.lumi/skills/`，不进全局、不在环境页；未绑定项目时退回全局层兜底。

## 目录布局

跟随配置目录发现链（`--config-dir` > `LUMI_CONFIG_DIR` > cwd `.lumi/` > `~/.lumi/`；serve 恒钉 `~/.lumi`）——与 `skills/`、`cache/` 同源，测试/容器设 `LUMI_CONFIG_DIR` 时工具箱一并隔离：

```
~/.lumi/            # 即 <配置目录>（LumiConfig.bin_dir = config_dir / "bin"）
  bin/              # 统一入口：uv、rg 实体；node/npm/npx、lark-cli 为 symlink（Windows 为 .cmd shim）
  node/             # Node tarball 解压树（npm i -g 的全局包也落在这棵树里）
```

`npm i -g @larksuite/cli` 用工具箱 npm 时默认 prefix 即 `node/` 树，装出的 `lark-cli` 接入 `bin/`；卸载 = 删 `node/` + 清 `bin/` 链接，干净可逆。

## 探测与优先级

`detect(name) -> ToolStatus{source, version, path}`：

1. 系统 PATH 上已有 → `system`（**不重复装、不覆盖**，用户自装的永远优先；「一键装齐」自动跳过）；
2. `<配置目录>/bin/` 有 → `toolbox`；
3. 都没有 → `missing`。

用裸名走 `shutil.which`（Windows 自动遍历 PATHEXT，`npm`/`npx`/`lark-cli` 实为 `.cmd`，硬拼 `.exe` 会漏检）；判定来源时**只 resolve 目录不 resolve 文件**——`node`/`lark-cli` 在 `bin/` 里是指向 `node/` 树的 symlink，resolve 文件会把它们误判成 system。

**执行也必须用 which 解析出的完整路径**：`subprocess` 只会给裸名补 `.exe`，不遍历 PATHEXT，裸名跑 `lark-cli` 在 Windows 上必然 `FileNotFoundError`（表现为「已安装」与「不在 PATH」同时出现）。同理，收子进程输出恒显式 `encoding="utf-8"`——`text=True` 走系统 locale，中文 Windows 的 cp936 撞上 CLI 的中文输出即抛 `UnicodeDecodeError`。

**不做最低版本强制**：系统版本旧也如实展示、照常沿用，不判「过旧」、不用工具箱副本遮蔽（PATH 末尾追加下系统版本永远赢，并存只会困惑）。

## PATH 注入（单点）

`inject_path()` 把 `<配置目录>/bin` 追加到进程 `os.environ["PATH"]` **末尾**——bash 工具、`minutes.py` subprocess、MCP stdio 子进程全部自动继承；末尾追加保证系统同名版本优先，无影子冲突。调用点只有两处：`lumi serve` 与 headless 运行（`lumi/cli.py`）。

用户终端场景（如 `lark-cli auth login` 扫码）由体检 `fix_cmd` 给绝对路径 `~/.lumi/bin/lark-cli auth login`，粘贴即跑，不改 shell rc。

## 下载与校验

- 版本 **pin 在代码里**（`UV_VERSION` / `RG_VERSION` / `NODE_VERSION`），升级 Lumi 时更新 pin——不依赖 GitHub API 可达性，行为可复现。lark-cli 例外：npm 路径天然 latest；直下路径查 releases latest API。
- checksum 取产物同源文件（uv/rg 的 `<asset>.sha256`、Node 的 `SHASUMS256.txt`），与下载并发取；**拿不到就跳过校验**——它只防传输损坏，不值得为它让安装失败（这一支恒留 warning，否则「校验形同虚设」无人知晓）。排版按发布方各不相同（`<hash>  <file>`、`<hash> *<file>`、rg 的 Windows 产物是 CertUtil 三行版、哈希在第二行），故取首个 64 位十六进制串而非按空白切首段。
- 下载走 urllib 默认代理行为（尊重 `https_proxy`）；解压全用标准库（tarfile `filter="data"` / zipfile），无 shell out。压缩包落临时目录，装完即随目录删除。

## 安装路径

| 目标 | 流程 |
| --- | --- |
| `uv` / `rg` | 下载 → 校验 → 从包内提取单二进制到 `bin/`，chmod 755 |
| `node` | 下载 → 解压整棵树到 `node/`（剥单一顶层目录）→ `node`/`npm`/`npx` 链入 `bin/` |
| `all` | 串行只装 `missing` 的核心工具（进度清晰、失败好归因），`system`/`toolbox` 跳过 |
| `lark-cli` | ensure npm（缺则先装 node）→ `npm i -g @larksuite/cli`；npm 失败降级 GitHub Releases 直下单二进制 |
| `feishu-skills` | `lark-cli skills list/read` 导出到 `<项目>/.lumi/skills/`，按 frontmatter `version` 增量；子进程冷启动 ~200ms，共享线程池并发导出 |

技能清单读取（`lark_skill_versions`）**必须区分 `None` 与空 dict**：`None` = 清单读不到（cli 过旧等），当成「0 个技能待装」会让体检报 error 而安装是空操作，永远修不绿——此时正确出路是升级 cli，故不给 `fix_action`。同理 `skills_status` 把缺失项计入 `outdated`，否则 cli 升级新增的技能永远不会提示安装。

## 协议（`protocol/events.json` 单一事实源）

- **RPC** `env_status` → `{tools, installing}`：核心工具链全量状态（飞书组件有项目维度，归渠道体检）+ 进行中的安装 target，面板打开时据此恢复进行中态（对齐 `get_mcp_status` 的 loading 范式）。
- **RPC** `env_install(target, project?)` → `{started}`：安装是分钟级，立即返回不挂在响应里。
- **事件** `env.progress{target, phase, percent}`：节流到「阶段变化或整数百分比前进」才广播；`percent = -1` 表示进度不可知（解压 / npm 阶段）。
- **事件** `env.state{tools, target, error?}`：一次安装结束后的全量状态广播，所有连接同步刷新（与 `bg_tasks` 的「快照广播、前端过滤」同范式）。带 `target` 是因为多面板各自订阅——无 target 时一处的安装结束会误清另一处的进度、提前重跑无关体检。

**全局互斥**：`_installing` 单值即不变量本身。target 之间有重叠（`all` ⊃ uv/rg/node，`lark-cli` 内部装 node），并行会让两条线程写同一二进制 / rmtree 同一棵树，故任一安装进行中即拒绝新安装（返回 `started: false`）。

## 前端

- **设置 → 环境**（`EnvPanel.tsx`）：核心工具链行式列表，纯机器级视图。徽章三态——未安装（虚线）/ 系统 vX（蓝点，用户自装）/ 工具箱 vX（金点，Lumi 托管）；安装中整行换成进度光带。
- **设置 → 渠道 → 飞书**（`ChannelsPanel.tsx`）：接入体检一张清单，「本地环境」组（lark-cli / 技能包）+「机器人接入」组（凭证 / 权限 / 事件订阅 / 版本发布）。`Check.fix_action` 非空即渲染「一键安装」按钮，进度就地显示在该行——数据分层（机器级 / 项目级）不等于 UI 入口分散。绑定项目是体检输入（技能包按此项目检测与安装），故前置到凭证之后。
- 进度状态机复用 `useEnvInstall.ts`（订阅 `env.progress`/`env.state`、按 target 过滤、面板重开时 seed 恢复），进度条组件 `ProgressBar` 在 `SettingsKit.tsx`——光效样式只此一份。

## 与现有机制的衔接

- **rg 降级链**：保留，工具箱只是让降级不再发生。
- **妙记体检**：lark-cli 相关检查项与本地环境组共用同一套 `detect`，装完重跑即绿。
