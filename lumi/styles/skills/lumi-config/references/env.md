# 执行依赖安装

把 agent 干活要用的工具装齐。

**在配置过程中可能会存在很多未知的问题，请主动解决。**

工具链装在 Lumi 自己的工具箱目录里，**不动系统、不需要 sudo**；系统里已经有的
版本永远优先，不会被覆盖。工具箱目录以体检末行打印的为准（默认 `~/.lumi/bin`，
配置目录被 `LUMI_CONFIG_DIR` 显式改过就跟着走）。

| 工具 | 干什么用 | 缺了会怎样 |
|---|---|---|
| **uv** | Python 运行时与包管理 | 需要 Python 的任务全跑不了 |
| **node**（含 npm / npx） | JS 运行时 | npm 生态用不了；接飞书要用的 lark-cli 也装不上 |
| **rg**（ripgrep） | 高速搜索 | **可选**——没有时通过 bash 使用 find / grep |
| **officecli** | Office 文件预览转换 | **可选**——没有时用系统应用打开 |

## 一、先体检

```bash
"${LUMI_BIN:-lumi}" env status
```

每行形如 `uv: 系统 v0.11.32 /opt/homebrew/bin/uv` 或 `node: 缺失`，
末行是工具箱目录。所需工具都不是「缺失」就已经齐了，直接告诉用户可以开始用。

## 二、征得同意再装

先用一句人话汇报：缺哪几个、各是干什么的、装到哪个目录、大概要等多久
（Node / OfficeCLI 等下载在网络慢时可能要等几分钟）。然后询问用户是否现在装。

## 三、安装缺失工具

```bash
"${LUMI_BIN:-lumi}" env install
```

- 后台执行，装齐缺失项，已安装的自动跳过，一项失败仍会继续安装其余工具。
- 等待完成通知后统一检查结果，再运行 `env status` 复检。
- 命令自己会打印阶段（`… 下载 node` / `… 安装 node`），结束时打印该工具的最终状态。
- **rg 失败不拦路**：告诉用户「搜索会慢一点，别的都不受影响」，继续往下。
- **node 失败要说清后果**：npm 生态和后续飞书接入都要它，问用户是重试还是先跳过。

## 四、复检收尾

再跑一次 `env status` 确认，用一句话汇报结果。新装的工具立刻可用，**不用重启 Lumi**。
环境齐了之后，告诉用户下一步可以配模型、接飞书机器人或接 MCP。

## 出问题时

- **下载超时 / 连不上 github.com**：多半是网络或代理。先试你能动手的路：用系统
  包管理器代装（`brew install uv`、`winget install …`——装完体检会认出来，来源
  显示「系统」）；还不行再问用户有没有代理，拿到地址带上 `https_proxy` 重跑。
- **`LUMI_BIN` 和 `lumi` 都不存在**（少见，此时你手里没有安装命令）：只能请用户
  在桌面端「设置 → 环境」点「一键装齐」——同一套安装逻辑，还带进度条。
- **显示已装但命令还是找不到**：请用户重启一次 Lumi 桌面端（PATH 在后端启动时注入）。

## 装不上 lark-cli 时

lark-cli 不归 `env install` 管（它管理 uv / node / rg / officecli），你直接动手装，装好验证完再交差：

1. **先看 node**：lark-cli 是 npm 包，node 缺失就先按上面流程装 node。
2. **安装到 Lumi 自有 prefix，再接入工具箱 bin**：

   ```bash
   npm install -g --prefix "<工具箱根>/npm-global" @larksuite/cli
   ln -sf "<工具箱根>/npm-global/bin/lark-cli" "<工具箱 bin>/lark-cli"
   ```

   `env status` 末行给的是工具箱 **bin** 目录（默认 `~/.lumi/bin`），其父目录才是工具箱根。两处占位符须替换为体检结果推导出的真实路径；不直接执行裸 `npm install -g`，避免写入系统全局目录。装完跑 `lark-cli --version` 验证。
   Windows 使用相同的自有 prefix，入口在 `<工具箱根>/npm-global/lark-cli.cmd`；可用其完整路径执行，不使用 POSIX 的 ln 命令。桌面接入体检的一键安装会自动处理入口。
3. **npm 报「配代理 / 公司镜像」（corporate npm mirror）**：先别去折腾网络——
   这个包的安装脚本用系统 `curl` 下载二进制，精简 Linux（Docker 常见）没有 curl
   时报的也是这段话，真实原因在上文的 `spawnSync curl ENOENT`。
   你先装 curl（`apt-get update && apt-get install -y curl` 或对应包管理器，
   全新容器不先 update 会找不到包）再重试；真是网络问题才问用户要代理。
