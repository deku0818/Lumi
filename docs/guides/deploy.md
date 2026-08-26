# 服务器部署

把 Lumi 后端（`lumi serve`）装到一台 Linux 服务器上，桌面端远程连它。两种形态：

| | Docker 模式 | 宿主机模式 |
|---|---|---|
| 装什么 | `ycw0818/lumi-harness` 镜像 | PyPI 包 `lumi-harness`（命令仍是 `lumi`） |
| 谁管进程 | `docker compose`（`restart: unless-stopped`） | systemd unit `lumi.service` |
| 适合 | 机器上已有 Docker，想要环境隔离 | 不想装 Docker，或 agent 需要直接操作宿主机 |

两种模式**目录、服务名、令牌文件完全一致**，换模式重跑脚本即可，会话与配置不丢。

## 一键部署

```bash
sudo ./scripts/install.sh            # 自动探测：Docker 可用则 docker，否则 native
sudo ./scripts/install.sh --mode native --port 9000
```

装完打印连接串 `ws://<本机IP>:<端口>/ws?token=…`，桌面端「设置 → 连接」填它。

**模型 API Key 不在服务器上配** —— 桌面端连上来之后在「设置 → 模型」里填，写进服务器的
`<数据目录>/lumi.json`（600）。脚本不碰密钥。

常用选项：

| 选项 | 说明 |
|---|---|
| `--mode docker\|native` | 强制部署模式 |
| `--port <端口>` | 监听端口（默认 8765） |
| `--token <令牌>` | 指定令牌（默认自动生成 48 位十六进制；复装沿用原值） |
| `--version <版本>` | 镜像 tag / PyPI 版本（默认 latest） |
| `--user <用户名>` | 宿主机模式的运行身份（默认新建系统用户 `lumi`，传 `root` 则用 root） |
| `--prefix <目录>` | 数据与工作区根目录（默认 `/opt/lumi`） |
| `--no-tools` | 跳过 agent 工具链安装（rg / node / officecli） |

## 装完的样子

```
/opt/lumi/data/        # 全部机器级数据：lumi.json(密钥,600) / checkpoints / memory / logs / bin
/opt/lumi/deploy.env   # 这台机怎么装的（模式/端口/身份/数据目录），upgrade 与 status 据此免传参
/etc/lumi.env          # LUMI_TOKEN=…（600），两种模式共用
/opt/lumi/workspace/   # 仅 Docker 模式：挂进容器 /workspace 的宿主机目录
```

宿主机模式靠 `LUMI_CONFIG_DIR=/opt/lumi/data` 让数据整体离开 `~/.lumi`；Docker 模式把同一个
目录挂到容器的 `/root/.lumi`。**所以两种模式读的是同一份数据。**

## 项目目录（不是「工作区」）

`lumi serve` 是多项目网关：**项目按会话绑定绝对路径**，一台机器上可以有任意多个、放在任意
位置；未绑定项目的会话会被直接拒绝（"请先选择项目再开始对话"）。所以脚本不规定"工作目录"。

- **宿主机模式**：不需要配。项目建在哪都行，桌面端「项目」里登记绝对路径即可，只要运行身份
  （默认 `lumi` 用户）读写得了。systemd unit 里的 `WorkingDirectory` 指向数据目录，只是给个
  保证存在的落脚点——进程从不 `chdir`，那个值只影响未绑定项目时界面上显示的兜底路径。
- **Docker 模式**：容器只看得见挂进去的宿主机目录，所以至少要有一个。默认挂
  `<prefix>/workspace`，用 `--workspace /srv/项目` 换成你自己的；要开放多个目录，就在
  `/opt/lumi/docker-compose.yml` 的 `volumes` 里继续加，例如：

  ```yaml
      volumes:
        - /opt/lumi/data:/root/.lumi
        - /srv/项目A:/srv/项目A      # 建议宿主机路径与容器路径一致，桌面端登记时不用换算
        - /srv/项目B:/srv/项目B
  ```

  改完 `sudo ./scripts/install.sh upgrade` 生效。

令牌只经 `LUMI_TOKEN` 环境变量进程内传递，不出现在命令行里（`ps` 看不到）。

## 接管已有的数据目录

机器上已经跑过 Lumi（数据在 `~/.lumi`）时，不必搬几百 MB 的会话——把 `LUMI_CONFIG_DIR`
指过去，原地接管：

```bash
LUMI_CONFIG_DIR=/root/.lumi ./scripts/install.sh --mode native --user root
```

用的就是后端运行时那个变量，装的时候与跑的时候同一个概念。几点行为：

- 已有的 `config.json` **不会被覆盖**（seed 只在文件缺失时写）；会话、密钥、记忆原样保留。
- 数据目录会记进 `deploy.env`，之后 `upgrade` / `status` 不必再带这个变量。
- 已有 `/etc/lumi.env` 里的令牌会**沿用**，客户端不用改配置。
- `uninstall --purge` **不会删**这种接管来的目录（它装之前就存在，不是本脚本建的），只会
  提示你手动处理。

身份要跟着数据走：数据在 `/root` 下（`700 root`）时必须 `--user root`，否则新建的 `lumi`
用户连目录都进不去。

## 日常运维

```bash
sudo ./scripts/install.sh upgrade     # 升到最新版：不动数据、不换令牌
sudo ./scripts/install.sh status      # 服务状态 + 日志命令 + 连接串
sudo ./scripts/install.sh uninstall   # 停服务删配置，数据保留
sudo ./scripts/install.sh uninstall --purge   # 连数据一起删
```

日志：Docker 模式 `docker logs -f lumi`，宿主机模式 `journalctl -u lumi -f`。

## 部署验证做了什么

脚本不看 `systemctl is-active` / 容器 `Running` 就下结论——进程活着但配置错、端口没监听、
令牌没生效，那些指标全都照样绿。真实检查是两条：

1. 连 `ws://127.0.0.1:<端口>/ws?token=<令牌>` 发一条 `list_sessions`，读到 id 匹配的 result；
2. **再用一个错误令牌连一次，必须被拒**。没设令牌的服务器同样会痛快回 result，只做第 1 条
   会把一台谁都能连的机器报成「部署成功」。

第 2 条失败时脚本直接退出并提示：多半是镜像版本过旧（不认 `LUMI_TOKEN`），换 `--version`
或改用 `--mode native`，修好前别把端口放到公网。

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

国内服务器常见两种卡点，脚本不做自动绕行，只会把错误原样报出来：

- **装 uv 卡住**：`astral.sh/uv/install.sh` 会跳转到 GitHub Releases。变通：先装 Python 3.12，
  `pip install uv`（PyPI 通常可达），再重跑脚本。
- **拉镜像失败**：`docker pull` 不通时配好镜像加速器再重跑，或改用 `--mode native`。

## 手动部署

不想用脚本时的等价命令：

```bash
# 宿主机
uv tool install lumi-harness
LUMI_CONFIG_DIR=/opt/lumi/data LUMI_TOKEN=<口令> lumi serve --host 0.0.0.0 --port 8765

# Docker
docker run -d --name lumi --restart unless-stopped -p 8765:8765 \
  -e LUMI_TOKEN=<口令> \
  -v /opt/lumi/data:/root/.lumi -v /srv/项目:/srv/项目 \
  ycw0818/lumi-harness
```

首次部署记得给数据目录写一份 `config.json`，否则风格是无提示词的 `default`、会话也不落盘：

```json
{"style": "code", "agents": {"checkpoint": "sqlite"}}
```

（Docker 镜像里预置的那份会被数据卷挂载盖掉，所以挂载部署同样要写。）

## 相关

- 脚本本身：`scripts/install.sh`；集成测试：`tests/deploy/install_sh_test.sh`（在一次性容器里跑）
- 桌面端多机连接：[desktop 架构](../architecture/desktop.md)
