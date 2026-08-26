# 服务器部署

Lumi 后端是一个 PyPI 包（`lumi-harness`，命令叫 `lumi`）。`lumi serve` 起一个 WebSocket
网关，桌面端连它。

**进程怎么活着由你决定**——前台跑、systemd、Docker 都行。`lumi` 自己不做守护进程：一个
PyPI 包做不出崩溃拉起、开机自启、资源限制，做半套只会让人误以为它守着。它只管两件自己
才知道答案的事：这个包怎么升级（`lumi update`），以及那个跑着的服务通不通（`lumi status`）。

## 一行装完（Linux + systemd）

```bash
curl -fsSL https://raw.githubusercontent.com/deku0818/Lumi/main/scripts/install.sh | sudo sh
```

装包 → 生成 `lumi.service` → 起服务 → **真握手验证** → 打印连接串。带参数：

```bash
curl -fsSL https://raw.githubusercontent.com/deku0818/Lumi/main/scripts/install.sh | sudo sh -s -- --port 9000
```

| 选项 | 说明 |
|---|---|
| `--user <用户名>` | 服务运行身份（默认：**调用 sudo 的那个人**，不是 root，也不新建 lumi 用户） |
| `--port <端口>` | 监听端口（默认 8765） |
| `--token <令牌>` | 访问令牌（默认自动生成；重跑沿用原令牌） |
| `--version <版本>` | 装指定版本（默认最新） |
| `--data-dir <目录>` | 数据目录（默认该用户的 `~/.lumi`） |

服务以**你自己**的身份跑，数据就落在你平时的 `~/.lumi`——跟手动敲 `lumi serve` 完全一致，
不另造系统用户、不另立数据目录。重跑即升级：沿用原令牌与数据，只换版本并重启。

卸载：

```bash
sudo systemctl disable --now lumi
sudo rm /etc/systemd/system/lumi.service /etc/lumi.env
uv tool uninstall lumi-harness      # 数据仍留在 ~/.lumi
```

## 手动装

不想跑脚本，或机器上没有 systemd：

```bash
uv tool install lumi-harness   # 或 pipx install lumi-harness
uv tool update-shell           # 把工具目录（~/.local/bin）加进 PATH；装完敲不到 lumi 就是缺这步
```

## 配

机器级数据（密钥 / 会话 / 记忆 / 日志 / 工具箱）全在 `~/.lumi`，`LUMI_CONFIG_DIR` 可整体
改道到别处——服务器上常指到 `/opt/lumi/data`，机器上已有 `~/.lumi` 的话把变量指过去就是
原地接管，不必搬几百 MB 会话。


**模型 API Key 不在服务器上配。** 桌面端连上来之后在「设置 → 模型」里填，写进服务器的
`<数据目录>/lumi.json`（600）。

## 跑

```bash
LUMI_TOKEN=<你的口令> lumi serve --host 0.0.0.0 --port 8765
```

令牌只经环境变量进程内传递，不进命令行（`ps` 里看不到）。桌面端「设置 → 连接」填
`ws://<本机IP>:8765/ws?token=<你的口令>`。

## 验

```bash
lumi status
```

它不看「进程活着」这类指标——那种指标在一台**谁都能连**的机器上照样是绿的。真实检查是
拿一个必然错误的令牌连一次 WS：

| 结果 | 结论 |
|---|---|
| 被拒 | 服务活着**且**鉴权生效 ✅ |
| 居然连上还能 `list_sessions` | 服务活着，但没设令牌，**谁都能连** ⚠️ |
| 连不上 | 没在跑 |

加 `--token <口令>` 会再跑一条正向探针，证明这台服务真能干活（端口开着但引擎卡死时，
负向探针照样会痛快回绝）。

退出码给脚本用：**0** 在跑且鉴权生效 ・ **3** 在跑但没设令牌 ・ **1** 没在跑或异常
（安装脚本就是靠它判成败；2 留给 click 的用法错误，不占用）。

日志：`lumi logs -f`。

## 常驻（systemd）

要开机自启与崩溃拉起，自己写一份 unit。存成 `/etc/systemd/system/lumi.service`：

```ini
[Unit]
Description=Lumi backend (lumi serve)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=lumi
# ExecStart 必须写绝对路径：systemd 不读你的 shell 配置，PATH 里没有 ~/.local/bin。
# 路径取 `uv tool dir --bin` 的输出，注意那是**服务运行身份**的家目录，不是你的
ExecStart=/home/lumi/.local/bin/lumi serve --host 0.0.0.0 --port 8765
# 机器级数据（密钥 / 会话 / 记忆 / 日志 / 工具箱）全跟着这一个变量走
Environment=LUMI_CONFIG_DIR=/opt/lumi/data
# 进程从不 chdir（项目按会话绑定绝对路径），这里只是给个保证存在的落脚点，
# 免得 cwd 落在 / 上让相对路径写进根目录
WorkingDirectory=/opt/lumi/data
# 令牌经环境文件进来，不写进 ExecStart（否则 ps 里人人可见）
EnvironmentFile=/etc/lumi.env
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
echo 'LUMI_TOKEN=<你的口令>' | sudo tee /etc/lumi.env >/dev/null && sudo chmod 600 /etc/lumi.env
sudo systemctl daemon-reload && sudo systemctl enable --now lumi
lumi status                    # 别只看 systemctl is-active
```

服务日志 `journalctl -u lumi -f`；agent 侧日志 `lumi logs -f`。

## 升级

```bash
lumi update --check            # 只看有没有新版
lumi update                    # 升级；跑着的服务仍是旧代码，它会提示你
sudo systemctl restart lumi    # 你的进程你重启（unit 名 / 容器名 lumi 不知道）
lumi status                    # 复验
```

`lumi update --version <旧版本>` 可以回退到指定版本。

## Docker

```bash
docker run -d --name lumi --restart unless-stopped -p 8765:8765 \
  -e LUMI_TOKEN=<你的口令> \
  -v /opt/lumi/data:/root/.lumi \
  -v /srv/项目:/srv/项目 \
  ycw0818/lumi-harness
```

镜像随 `v*` tag 发布到 Docker Hub。容器**只看得见挂进去的宿主机目录**，所以至少要挂一个
项目目录——建议宿主机路径与容器路径写成一样的，桌面端登记项目时不用换算。

Docker 模式下升级走 `docker pull` + 重建容器，不用 `lumi update`（镜像里装的包重建即丢）。

## 项目目录（不是「工作区」）

`lumi serve` 是多项目网关：**项目按会话绑定绝对路径**，一台机器上可以有任意多个、放在任意
位置；未绑定项目的会话会被直接拒绝（"请先选择项目再开始对话"）。所以不存在一个全局的
"工作目录"要配——宿主机部署下项目建在哪都行，只要服务运行身份读写得了。

## TLS（公网必做）

Lumi **没有沙箱**：agent 的 `bash`、文件读写直接作用于这台服务器的真实环境。明文 `ws` 上的
令牌一旦被截，等于把这台机器交出去。用 Caddy 反代（自动签证书）：

```caddyfile
lumi.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

桌面端改填 `wss://lumi.example.com/ws?token=<令牌>`，并把 8765 从公网防火墙关掉。

## 网络受限的机器

国内服务器常见两种卡点：

- **装 uv 卡住**：`astral.sh/uv/install.sh` 会跳转到 GitHub Releases。变通：先装 Python 3.12，
  `pip install uv`（PyPI 通常可达）。
- **`lumi update` 连不上 PyPI**：给 uv / pip 配好镜像源即可，`lumi update` 用的就是它们。

## 相关

- 桌面端多机连接：[desktop 架构](../architecture/desktop.md)
