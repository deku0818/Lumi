#!/bin/sh
# Lumi 后端一键安装（Linux）：装包 → 生成 systemd unit → 起服务 → 真握手验证。
#
#   curl -fsSL https://raw.githubusercontent.com/deku0818/Lumi/main/scripts/install.sh | sudo sh
#   curl -fsSL https://raw.githubusercontent.com/deku0818/Lumi/main/scripts/install.sh | sudo sh -s -- --port 9000
#
# 服务默认**以调用者本人的身份**运行（sudo 下取 SUDO_USER），数据落在这个人的
# ~/.lumi——项目、密钥、会话跟平时手动跑 `lumi serve` 完全一致，不另造 lumi 系统用户
# 也不另立数据目录。要换身份用 --user。
#
# 重跑即升级：沿用原令牌与数据，只换包版本并重启。
set -eu

PKG=lumi-harness
SERVICE=lumi
UNIT=/etc/systemd/system/lumi.service
ENV_FILE=/etc/lumi.env

PORT=8765
TOKEN=""
VERSION=latest
RUN_USER=""
DATA_DIR=""

log()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m警告：\033[0m%s\n' "$*" >&2; }
die()  { printf '\033[31m错误：\033[0m%s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Lumi 后端一键安装（Linux + systemd）

  curl -fsSL <本脚本地址> | sudo sh
  curl -fsSL <本脚本地址> | sudo sh -s -- [选项]
  sudo ./install.sh [选项]

选项：
  --user <用户名>    服务运行身份（默认：调用 sudo 的那个人）
  --port <端口>      监听端口（默认 8765）
  --token <令牌>     访问令牌（默认自动生成；重跑时沿用原令牌）
  --version <版本>   装指定版本（默认最新）
  --data-dir <目录>  数据目录（默认该用户的 ~/.lumi）

卸载：
  sudo systemctl disable --now lumi && sudo rm /etc/systemd/system/lumi.service /etc/lumi.env
  uv tool uninstall lumi-harness        # 数据仍留在 ~/.lumi
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --user)     RUN_USER="${2:?}"; shift ;;
    --port)     PORT="${2:?}"; shift ;;
    --token)    TOKEN="${2:?}"; shift ;;
    --version)  VERSION="${2:?}"; shift ;;
    --data-dir) DATA_DIR="${2:?}"; shift ;;
    -h|--help)  usage; exit 0 ;;
    *) die "未知参数 $1（--help 看用法）" ;;
  esac
  shift
done

# ── 环境 ─────────────────────────────────────────────────────────────────

[ "$(uname -s)" = Linux ] || die "本脚本装的是 systemd 服务，只支持 Linux。
       其他系统请手动装：uv tool install $PKG && lumi serve"
command -v systemctl >/dev/null 2>&1 || die "这台机器没有 systemd，装不了服务。
       手动跑：uv tool install $PKG && lumi serve --host 0.0.0.0 --port $PORT"
[ "$(id -u)" = 0 ] || die "需要 root 才能写 systemd unit 与 $ENV_FILE。
       重跑：curl -fsSL <本脚本地址> | sudo sh"

# 运行身份：默认是**调用 sudo 的那个人**，不是 root。sudo 下 \$USER 已经是 root 了，
# 真正的人只在 SUDO_USER 里；直接以 root 登录的机器则落回 root，同样是「当前用户」。
[ -n "$RUN_USER" ] || RUN_USER="${SUDO_USER:-$(id -un)}"
id -u "$RUN_USER" >/dev/null 2>&1 || die "用户 $RUN_USER 不存在"
HOME_DIR=$(getent passwd "$RUN_USER" | cut -d: -f6)
[ -n "$HOME_DIR" ] && [ -d "$HOME_DIR" ] || die "取不到 $RUN_USER 的家目录"
BIN_DIR="$HOME_DIR/.local/bin"
LUMI_BIN="$BIN_DIR/lumi"

# 以目标用户身份跑。HOME 必须显式给：runuser 不模拟登录、环境照抄调用方，不给的话
# HOME 还是 /root，uv 会把工具 venv 装进 /root/.local，服务用户根本读不到
run_as() {
  if [ "$RUN_USER" = root ]; then
    env HOME="$HOME_DIR" "$@"
  else
    runuser -u "$RUN_USER" -- env HOME="$HOME_DIR" "$@"
  fi
}

# ── 装 ───────────────────────────────────────────────────────────────────

ensure_uv() {
  # 检查用一个 PATH、执行用另一个 PATH 是找不到的经典写法，这里一律解析成绝对路径
  if [ -x "$BIN_DIR/uv" ]; then UV="$BIN_DIR/uv"; return 0; fi
  if command -v uv >/dev/null 2>&1; then UV=$(command -v uv); return 0; fi
  log "安装 uv 到 $BIN_DIR"
  run_as env "UV_INSTALL_DIR=$BIN_DIR" sh -c \
    'curl -LsSf https://astral.sh/uv/install.sh | sh' >/dev/null \
    || die "uv 安装失败（astral.sh 会跳去 GitHub Releases，网络受限的机器常卡在这）。
       变通：装好 Python 3.12 后 pip install uv，再重跑本脚本。"
  UV="$BIN_DIR/uv"
  [ -x "$UV" ] || die "uv 装完仍找不到 $UV"
}

ensure_uv

# 一律 install --force：当初若带过版本 pin，`uv tool upgrade` 会一声不吭什么都不做
# 还退 0（实测 "Nothing to upgrade"），重跑脚本就成了假升级
if [ "$VERSION" = latest ]; then SPEC="$PKG@latest"; else SPEC="$PKG==$VERSION"; fi
log "安装 $SPEC（身份 $RUN_USER）"
run_as env "UV_TOOL_BIN_DIR=$BIN_DIR" "$UV" tool install --force "$SPEC" \
  || die "安装 $SPEC 失败（PyPI 不通时给 uv 配镜像源后重试）"
[ -x "$LUMI_BIN" ] || die "装完仍找不到 $LUMI_BIN"

# 让这个人在自己的终端里也敲得到 lumi（改的是他自己的 shell 配置，失败无所谓）
run_as "$UV" tool update-shell >/dev/null 2>&1 || true

# ── 令牌 ─────────────────────────────────────────────────────────────────

# 重跑沿用原令牌：换令牌等于让所有已配好的桌面端同时失联
if [ -z "$TOKEN" ] && [ -f "$ENV_FILE" ]; then
  TOKEN=$(sed -n 's/^LUMI_TOKEN=//p' "$ENV_FILE")
fi
[ -n "$TOKEN" ] || TOKEN=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
printf 'LUMI_TOKEN=%s\n' "$TOKEN" > "$ENV_FILE"
chmod 600 "$ENV_FILE"

# ── 服务 ─────────────────────────────────────────────────────────────────

log "写 $UNIT"
{
  cat <<EOF
[Unit]
Description=Lumi backend (lumi serve)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
# 绝对路径：systemd 不读用户的 shell 配置，PATH 里没有 ~/.local/bin
ExecStart=$LUMI_BIN serve --host 0.0.0.0 --port $PORT
# 显式给 HOME：数据（密钥/会话/记忆/日志/工具箱）默认就落在 \$HOME/.lumi
Environment=HOME=$HOME_DIR
EOF
  # 只有显式指定了数据目录才写这行：不写就跟着 HOME 走，与手动跑 lumi serve 一致
  [ -n "$DATA_DIR" ] && printf 'Environment=LUMI_CONFIG_DIR=%s\n' "$DATA_DIR"
  cat <<EOF
# 进程从不 chdir（项目按会话绑定绝对路径），cwd 只需是个保证存在的落脚点
WorkingDirectory=$HOME_DIR
# 令牌经环境文件进来，不写进 ExecStart（否则 ps 里人人可见）
EnvironmentFile=$ENV_FILE
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
} > "$UNIT"

if [ -n "$DATA_DIR" ]; then
  mkdir -p "$DATA_DIR"
  chown "$RUN_USER" "$DATA_DIR"
fi

systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
systemctl restart "$SERVICE"

# ── 验 ───────────────────────────────────────────────────────────────────

# 不看 `systemctl is-active`：进程活着但令牌没生效、端口没监听，那玩意儿照样是绿的。
# `lumi status` 拿一个必然错误的令牌连一次 WS——被拒才说明服务活着且鉴权生效；
# 居然连得上（退 2）就是一台谁都能连的机器，绝不能报成「装好了」。
log "验证服务（真握手 + 错误令牌必须被拒）"
i=0
while :; do
  set +e
  OUT=$(LUMI_TOKEN="$TOKEN" run_as "$LUMI_BIN" status --port "$PORT" 2>&1)
  RC=$?
  set -e
  [ "$RC" = 0 ] && break
  if [ "$RC" = 3 ]; then
    echo "$OUT" >&2
    die "服务处于无鉴权状态：$ENV_FILE 里的令牌没生效，这台机器谁都能连。
       别把 $PORT 端口放到公网。看日志：journalctl -u $SERVICE -n 50"
  fi
  if [ "$RC" = 2 ]; then
    # click 的用法错误码。装上的 lumi 没有 status 子命令 = 版本太旧
    echo "$OUT" >&2
    die "装上的 lumi 不认 \`lumi status\`，版本太旧（当前装的是 $VERSION）。
       换新版：--version <较新版本>，或先 uv tool uninstall $PKG 再重跑。"
  fi
  i=$((i + 1))
  if [ "$i" -ge 20 ]; then
    echo "$OUT" >&2
    die "服务起来了但连不通。看日志：journalctl -u $SERVICE -n 50"
  fi
  sleep 2
done

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
cat <<EOF

$OUT

  运行身份  $RUN_USER
  数据目录  ${DATA_DIR:-$HOME_DIR/.lumi}
  令牌文件  $ENV_FILE（600）
  服务管理  systemctl restart|stop lumi ・ journalctl -u lumi -f
  升级      sudo -u $RUN_USER $LUMI_BIN update && sudo systemctl restart lumi

连接串：ws://${IP:-<本机IP>}:$PORT/ws?token=$TOKEN
  桌面端「设置 → 连接」填上面这串。模型 API Key 在桌面端里配，服务器上不用管。

⚠ 公网暴露前务必套 TLS —— Lumi 没有沙箱，agent 的 bash 与文件工具直接作用于这台机器，
  明文 ws 上的令牌被截 = 把这台机器交出去。Caddy 反代（自动签证书）：

    <你的域名> {
        reverse_proxy 127.0.0.1:$PORT
    }

  之后桌面端改填 wss://<你的域名>/ws?token=<令牌>，并把 $PORT 从公网防火墙关掉。
EOF
