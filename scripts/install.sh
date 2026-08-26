#!/usr/bin/env bash
# Lumi 后端一键部署（Linux）：Docker 或宿主机 systemd，两种模式同一套目录、同一个服务名、
# 同一份令牌文件，客户换模式重跑即可，会话与配置不丢。
#
#   sudo ./install.sh                # 装（有 Docker 走 Docker，否则宿主机）
#   sudo ./install.sh --mode native  # 强制宿主机（uv tool install + systemd）
#   sudo ./install.sh upgrade        # 升到最新版（不动数据、不换令牌）
#   sudo ./install.sh status         # 服务状态 + 连接串
#   sudo ./install.sh uninstall      # 停服务删配置，默认保留数据（--purge 才删）
#
# 数据目录默认 <prefix>/data，用 LUMI_CONFIG_DIR 可指向已有的 ~/.lumi 原地接管。
set -euo pipefail

PKG=lumi-harness            # PyPI 发布名（命令仍叫 lumi）
IMAGE=ycw0818/lumi-harness   # Docker Hub 镜像
SERVICE=lumi                # systemd unit 名 / 容器名，两模式共用

ACTION=install
MODE=""
PORT=8765
TOKEN=""
VERSION=latest
RUN_USER=lumi
PREFIX=/opt/lumi
ENV_FILE=/etc/lumi.env
WITH_TOOLS=1
PURGE=0
UV_BIN=""
# 命令行给过的参数优先于 deploy.env 里记住的上次选择
MODE_SET=0; PORT_SET=0; USER_SET=0; WORK_SET=0

usage() {
  cat <<'EOF'
Lumi 后端一键部署（Linux）

  sudo ./install.sh [install|upgrade|status|uninstall] [选项]

选项：
  --mode docker|native   部署模式（默认自动探测：Docker 可用则 docker）
  --port <端口>          监听端口（默认 8765）
  --token <令牌>         访问令牌（默认自动生成；已装过则沿用原令牌）
  --version <版本>       镜像 tag / PyPI 版本（默认 latest）
  --user <用户名>        宿主机模式的运行身份（默认 lumi；传 root 则用 root）
  --prefix <目录>        安装根目录：部署状态与（Docker 模式的）工作区（默认 /opt/lumi）
  --workspace <目录>     仅 Docker：挂进容器 /workspace 的宿主机目录（默认 <prefix>/workspace）
                         多个项目目录请自行在 <prefix>/docker-compose.yml 里加挂载
  --no-tools             跳过 agent 工具链安装（rg / node / officecli）
  --purge                仅 uninstall：连同数据一起删除（只删本脚本建的那份）

环境变量：
  LUMI_CONFIG_DIR        数据目录，默认 <prefix>/data。已有 ~/.lumi 的机器指过去即可
                         原地接管，不必搬会话：
                           LUMI_CONFIG_DIR=/root/.lumi ./install.sh --user root
                         装过一次后会记进 deploy.env，之后 upgrade 不必再带
EOF
}

log()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m警告：\033[0m%s\n' "$*" >&2; }
die()  { printf '\033[31m错误：\033[0m%s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    install|upgrade|uninstall|status) ACTION="$1" ;;
    --mode)    MODE="${2:?}"; MODE_SET=1; shift ;;
    --port)    PORT="${2:?}"; PORT_SET=1; shift ;;
    --token)   TOKEN="${2:?}"; shift ;;
    --version) VERSION="${2:?}"; shift ;;
    --user)    RUN_USER="${2:?}"; USER_SET=1; shift ;;
    --prefix)  PREFIX="${2:?}"; shift ;;
    --workspace) WORK_DIR="${2:?}"; WORK_SET=1; shift ;;
    --no-tools) WITH_TOOLS=0 ;;
    --purge)   PURGE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数 $1（--help 看用法）" ;;
  esac
  shift
done

# 数据目录：环境变量 LUMI_CONFIG_DIR > 上次装的位置（deploy.env）> $PREFIX/data。
# 用的就是后端运行时那个变量，装的时候与跑的时候同一个概念——已有 ~/.lumi 的机器
# 直接 `LUMI_CONFIG_DIR=/root/.lumi ./install.sh` 原地接管，不必搬几百 MB 的会话。
DATA_DIR="${LUMI_CONFIG_DIR:-}"
# 工作区只在 Docker 模式有意义：容器只看得见挂进去的宿主机目录。宿主机模式下项目
# 在哪都行、有几个都行（按会话绑定绝对路径），脚本不规定也不创建。
WORK_DIR="${WORK_DIR:-}"
COMPOSE_FILE="$PREFIX/docker-compose.yml"
STATE_FILE="$PREFIX/deploy.env"   # 记住这台机怎么装的，upgrade/status/uninstall 免再传参
UNIT_FILE="/etc/systemd/system/$SERVICE.service"

# ── 通用 ─────────────────────────────────────────────────────────────────

apply_state() {
  if [ -f "$STATE_FILE" ]; then
    local s_mode s_port s_user s_data s_work
    s_mode=$(sed -n 's/^MODE=//p' "$STATE_FILE")
    s_port=$(sed -n 's/^PORT=//p' "$STATE_FILE")
    s_user=$(sed -n 's/^RUN_USER=//p' "$STATE_FILE")
    s_data=$(sed -n 's/^DATA_DIR=//p' "$STATE_FILE")
    s_work=$(sed -n 's/^WORK_DIR=//p' "$STATE_FILE")
    [ "$MODE_SET" = 0 ] && [ -n "$s_mode" ] && MODE="$s_mode"
    [ "$PORT_SET" = 0 ] && [ -n "$s_port" ] && PORT="$s_port"
    [ "$USER_SET" = 0 ] && [ -n "$s_user" ] && RUN_USER="$s_user"
    # 数据目录尤其要记住：upgrade 时忘了带 LUMI_CONFIG_DIR 就切回默认位置的话，
    # 客户看到的是「升级完会话全没了」
    [ -z "$DATA_DIR" ] && [ -n "$s_data" ] && DATA_DIR="$s_data"
    [ "$WORK_SET" = 0 ] && [ -n "$s_work" ] && WORK_DIR="$s_work"
  fi
  [ -n "$DATA_DIR" ] || DATA_DIR="$PREFIX/data"
  [ -n "$WORK_DIR" ] || WORK_DIR="$PREFIX/workspace"
  return 0
}

save_state() {
  cat > "$STATE_FILE" <<EOF
MODE=$MODE
PORT=$PORT
RUN_USER=$RUN_USER
VERSION=$VERSION
DATA_DIR=$DATA_DIR
WORK_DIR=$WORK_DIR
EOF
}

detect_mode() {
  [ -n "$MODE" ] && return 0
  if docker info >/dev/null 2>&1; then MODE=docker; else MODE=native; fi
  log "自动探测部署模式：$MODE"
}

read_token() { sed -n 's/^LUMI_TOKEN=//p' "$ENV_FILE"; }

ensure_token() {
  # 复装/升级沿用原令牌：换令牌等于让客户所有已配好的桌面端同时失联
  if [ -z "$TOKEN" ] && [ -f "$ENV_FILE" ]; then
    TOKEN=$(read_token)
  fi
  [ -n "$TOKEN" ] || TOKEN=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
  printf 'LUMI_TOKEN=%s\n' "$TOKEN" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

seed_config() {
  # 首次安装才写：style=default 无内置提示词、checkpoint=memory 聊完即丢，两个默认值
  # 对服务器部署都是错的。Docker 镜像里那句 seed 会被数据卷挂载盖掉，故两模式都要做。
  local target="$DATA_DIR/config.json"
  [ -f "$target" ] && return 0
  printf '{"style": "code", "agents": {"checkpoint": "sqlite"}}\n' > "$target"
  log "已写入默认配置 $target（style=code，会话落 sqlite）"
}

# ── 验证 ─────────────────────────────────────────────────────────────────
# 连上去发一条 list_sessions，读到 id 匹配的 result 才算成功。只看 systemctl is-active /
# 容器 Running 是假绿——进程活着但配置错、端口没监听、令牌没生效，全都照样「绿」。

PROBE_PY='
import asyncio, json, sys, websockets

async def main(url):
    async with websockets.connect(url, open_timeout=15) as ws:
        await ws.send(json.dumps({"id": "probe", "method": "list_sessions", "params": {}}))
        while True:
            frame = json.loads(await asyncio.wait_for(ws.recv(), 30))
            if frame.get("id") == "probe":
                sys.exit(0 if "result" in frame else "RPC 返回错误: %s" % frame.get("error"))

asyncio.run(main(sys.argv[1]))
'

probe() {
  local url="ws://127.0.0.1:$PORT/ws?token=$1"
  if [ "$MODE" = docker ]; then
    docker exec -i "$SERVICE" python - "$url" <<< "$PROBE_PY"
  else
    local py
    py="$(dirname "$(readlink -f "$PREFIX/.local/bin/lumi")")/python"
    "$py" - "$url" <<< "$PROBE_PY"
  fi
}

verify() {
  log "验证服务（握手 + list_sessions）"
  local i=0
  until probe "$TOKEN" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge 30 ]; then
      probe "$TOKEN" || true   # 让失败原因露出来
      die "服务起来了但握手不通。看日志：$0 status"
    fi
    sleep 2
  done

  # 负向验证：错误令牌必须被拒。正向探针通过 ≠ 鉴权生效——完全没设令牌的服务器
  # 同样会痛快回 result，那是一台谁都能连的机器，绝不能当成部署成功。
  if probe "wrong-token-$RANDOM" >/dev/null 2>&1; then
    die "严重：错误令牌也能连上，服务处于无鉴权状态。
       多半是镜像版本过旧（不认 LUMI_TOKEN 环境变量）。
       解法：--version 换用新版镜像，或改用 --mode native。
       在修好之前不要把 $PORT 端口暴露到公网。"
  fi
  log "握手通过，令牌鉴权生效"
}

install_tools() {
  [ "$WITH_TOOLS" = 1 ] || return 0
  log "安装 agent 工具链（rg / node / officecli → $DATA_DIR/bin）"
  # 装不上不致命：少几样工具只是 agent 少几分本事，服务本身照跑
  if [ "$MODE" = docker ]; then
    docker exec "$SERVICE" lumi env install || warn "工具链未装全，可稍后 docker exec $SERVICE lumi env install 重试"
  else
    run_as "$PREFIX/.local/bin/lumi" env install || warn "工具链未装全，可稍后重跑本脚本补装"
  fi
}

# ── Docker 模式 ──────────────────────────────────────────────────────────

docker_compose() {
  if docker compose version >/dev/null 2>&1; then docker compose "$@"; else docker-compose "$@"; fi
}

write_compose() {
  cat > "$COMPOSE_FILE" <<EOF
# 由 scripts/install.sh 生成；改完跑 \`install.sh upgrade\` 生效
services:
  $SERVICE:
    image: $IMAGE:$VERSION
    container_name: $SERVICE
    restart: unless-stopped
    ports:
      - "$PORT:8765"
    # 令牌只经环境变量传入（lumi serve --token 的 envvar），不进命令行，ps 里看不到
    env_file: $ENV_FILE
    volumes:
      - $DATA_DIR:/root/.lumi
      - $WORK_DIR:/workspace
EOF
}

install_docker() {
  docker info >/dev/null 2>&1 || die "Docker 不可用；装好 Docker 或改用 --mode native"
  write_compose
  log "拉取镜像 $IMAGE:$VERSION"
  docker_compose -f "$COMPOSE_FILE" pull
  docker_compose -f "$COMPOSE_FILE" up -d
}

# ── 宿主机模式 ───────────────────────────────────────────────────────────

run_as() {
  # HOME 必须显式给：runuser 不模拟登录、环境照抄调用方，不给的话 HOME 还是 /root，
  # uv 会把工具 venv 装进 /root/.local，服务用户根本读不到
  if [ "$RUN_USER" = root ]; then
    env HOME="$PREFIX" "$@"
  else
    runuser -u "$RUN_USER" -- env HOME="$PREFIX" "$@"
  fi
}

ensure_user() {
  [ "$RUN_USER" = root ] && return 0
  id -u "$RUN_USER" >/dev/null 2>&1 && return 0
  log "创建运行用户 $RUN_USER（家目录 $PREFIX）"
  # 给真实 shell：agent 的 bash 工具本就以这个身份跑命令，nologin 挡不住任何东西
  # （该用户没有登录凭证），只会让排查时 runuser 调试变麻烦
  useradd --system --home-dir "$PREFIX" --shell /bin/bash "$RUN_USER"
}

# 解析出 uv 的绝对路径：检查用一个 PATH、执行用另一个 PATH 是找不到的经典写法
ensure_uv() {
  if [ -x "$PREFIX/.local/bin/uv" ]; then
    UV_BIN="$PREFIX/.local/bin/uv"
    return 0
  fi
  if command -v uv >/dev/null 2>&1; then
    UV_BIN=$(command -v uv)
    return 0
  fi
  command -v curl >/dev/null 2>&1 || die "装 uv 需要 curl：apt install curl / yum install curl"
  log "安装 uv 到 $PREFIX/.local/bin"
  run_as env "UV_INSTALL_DIR=$PREFIX/.local/bin" sh -c \
    'curl -LsSf https://astral.sh/uv/install.sh | sh' \
    || die "uv 安装失败（astral.sh 会跳去 GitHub Releases，网络受限的机器常卡在这）。
       变通：装好 Python 3.12 后 pip install uv，再重跑本脚本。"
  UV_BIN="$PREFIX/.local/bin/uv"
  [ -x "$UV_BIN" ] || die "uv 装完仍找不到 $UV_BIN"
}

install_native() {
  command -v systemctl >/dev/null 2>&1 || die "宿主机模式需要 systemd；这台机器请改用 --mode docker"
  ensure_user
  chown -R "$RUN_USER" "$PREFIX"
  ensure_uv

  local spec="$PKG"
  [ "$VERSION" = latest ] || spec="$PKG==$VERSION"
  log "安装 $spec"
  run_as env "UV_TOOL_BIN_DIR=$PREFIX/.local/bin" "$UV_BIN" tool install --force "$spec" \
    || die "安装 $spec 失败（PyPI 不通时可给 uv 配镜像源后重试）"

  cat > "$UNIT_FILE" <<EOF
[Unit]
Description=Lumi backend (lumi serve)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
# cwd 对本服务几乎无意义：项目按会话绑定绝对路径，未绑定项目的会话直接被拒（见
# gateway/session.py），进程也从不 chdir。指向数据目录只为给个保证存在的落脚点，
# 免得 cwd 落在 / 上让相对路径写进根目录
WorkingDirectory=$DATA_DIR
# 机器级数据（密钥 / 会话 / 记忆 / 日志 / 工具箱）全跟着这一个变量走
Environment=LUMI_CONFIG_DIR=$DATA_DIR
Environment=HOME=$PREFIX
# 令牌经 LUMI_TOKEN 进环境，不写进 ExecStart（否则 ps 里人人可见）
EnvironmentFile=$ENV_FILE
ExecStart=$PREFIX/.local/bin/lumi serve --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "$SERVICE" >/dev/null
  systemctl restart "$SERVICE"
}

# ── 动作 ─────────────────────────────────────────────────────────────────

need_root() { [ "$(id -u)" = 0 ] || die "需要 root（写 /etc 与 systemd）：sudo $0 $ACTION"; }

do_install() {
  need_root
  apply_state
  detect_mode
  log "数据目录 $DATA_DIR"   # 环境变量能改它，明说出来，别让人猜装到哪去了
  mkdir -p "$DATA_DIR"
  if [ "$MODE" = docker ]; then
    mkdir -p "$WORK_DIR"
  elif [ "$WORK_SET" = 1 ]; then
    warn "--workspace 只对 Docker 模式有意义（容器只看得见挂进去的目录），宿主机模式已忽略"
  fi
  ensure_token
  seed_config
  if [ "$MODE" = docker ]; then
    install_docker
  else
    install_native
    if [ "$RUN_USER" != root ]; then
      chown -R "$RUN_USER" "$PREFIX" "$DATA_DIR"
    fi
  fi
  save_state
  verify
  install_tools
  summary
}

do_uninstall() {
  need_root
  apply_state
  detect_mode
  if [ "$MODE" = docker ]; then
    docker_compose -f "$COMPOSE_FILE" down || true
    rm -f "$COMPOSE_FILE"
  else
    systemctl disable --now "$SERVICE" 2>/dev/null || true
    rm -f "$UNIT_FILE"
    systemctl daemon-reload
  fi
  rm -f "$ENV_FILE" "$STATE_FILE"
  if [ "$PURGE" != 1 ]; then
    log "已卸载；数据保留在 $DATA_DIR（要一并删除加 --purge）"
    return 0
  fi
  rm -rf "$PREFIX"
  log "已卸载并删除 $PREFIX"
  # 只删自己建的：数据目录若是经 LUMI_CONFIG_DIR 指过来的既有目录（如 /root/.lumi），
  # 那是装之前就存在的用户数据，本脚本只是接管过它，没资格替人删
  case "$DATA_DIR" in
    "$PREFIX"/*) ;;
    *) warn "数据目录 $DATA_DIR 不在 $PREFIX 内（安装时接管的既有目录），未删除；确认不要了请手动删" ;;
  esac
}

do_status() {
  apply_state
  detect_mode
  if [ "$MODE" = docker ]; then
    docker ps --filter "name=^/$SERVICE$" --format '容器 {{.Names}}: {{.Status}}（{{.Image}}）'
    echo "日志：docker logs -f $SERVICE"
  else
    systemctl status "$SERVICE" --no-pager | head -5 || true
    echo "日志：journalctl -u $SERVICE -f"
  fi
  if [ -r "$ENV_FILE" ]; then
    TOKEN=$(read_token)
    connection_line
  fi
}

connection_line() {
  local ip
  ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  echo "连接串：ws://${ip:-<本机IP>}:$PORT/ws?token=$TOKEN"
}

summary() {
  log "部署完成（$MODE 模式）"
  local work_line="  项目目录  任意路径，桌面端登记后按会话绑定（服务用户需可读写）"
  if [ "$MODE" = docker ]; then
    work_line="  项目目录  $WORK_DIR（挂进容器 /workspace）；要开放更多目录就在 $COMPOSE_FILE 加挂载"
  fi
  cat <<EOF
  数据目录  $DATA_DIR
$work_line
  令牌文件  $ENV_FILE（600）
  服务管理  $0 status / upgrade / uninstall

$(connection_line)
  桌面端「设置 → 连接」填上面这串即可。模型 API Key 在桌面端里配，服务器上不用管。

⚠ 公网暴露前务必套 TLS —— agent 的 bash / 文件工具直接作用于这台机器的真实环境，
  明文 ws 上的令牌被截 = 把这台机器交出去。Caddy 反代（自动签证书）：

    <你的域名> {
        reverse_proxy 127.0.0.1:$PORT
    }

  之后桌面端改填 wss://<你的域名>/ws?token=<令牌>，并把 $PORT 端口从公网防火墙关掉。
EOF
}

case "$ACTION" in
  install|upgrade) do_install ;;
  uninstall)       do_uninstall ;;
  status)          do_status ;;
esac
