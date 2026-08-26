#!/bin/sh
# scripts/install.sh 的集成测试：在一次性容器里**真跑**它。
#
# 真的部分：uv 安装、从 PyPI 装 lumi-harness、建 unit 文件、以目标用户身份起进程、
# WebSocket 握手验证。假的只有 systemd 本身（容器里没有 PID 1 init）——桩 systemctl
# 会解析生成的 unit 并照它写的 User / ExecStart / Environment / EnvironmentFile 真起进程，
# 所以 unit 写错了这测试就会红。
#
#   docker run --rm -v "$PWD:/src:ro" debian:12-slim sh /src/tests/deploy/install_sh_test.sh
set -u

[ -f /.dockerenv ] || [ "${FORCE:-0}" = 1 ] || {
  echo "拒绝执行：本测试会写 /etc 与用户家目录，请在一次性容器里跑（见文件头）" >&2
  exit 1
}

pass=0; fail=0
ok()  { echo "  ✅ $1"; pass=$((pass + 1)); }
bad() { echo "  ❌ $1"; fail=$((fail + 1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1（条件不成立：$2）"; fi; }

echo "› 准备容器（curl / ca-certificates / 测试用户 alice）"
apt-get update -qq >/dev/null 2>&1
apt-get install -y -qq --no-install-recommends curl ca-certificates procps >/dev/null 2>&1
id alice >/dev/null 2>&1 || useradd -m -s /bin/bash alice

# ── uv 桩：把 lumi-harness 换成本地源码树 ────────────────────────────────
# 被测的运维命令还没发到 PyPI，直接装 lumi-harness@latest 拿到的是旧版（没有
# `lumi status`）。桩只改「装哪个包」，装法、身份、路径解析全走真 uv。
echo "› 装真 uv + 准备本地源码树"
curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/opt/realbin sh >/dev/null 2>&1
[ -x /opt/realbin/uv ] || { echo "真 uv 装失败，测试无法继续" >&2; exit 1; }
mkdir -p /build && cp -r /src/pyproject.toml /src/README.md /src/lumi /build/
chmod -R a+rX /build /opt/realbin

mkdir -p /stub
cat > /stub/uv <<'STUB'
#!/bin/sh
# STUB_SKIP_INSTALL=1：装包变空操作，好让测试摆一个假的旧版 lumi 在那儿不被覆盖
[ "${STUB_SKIP_INSTALL:-0}" = 1 ] && [ "$1" = tool ] && [ "$2" = install ] && exit 0
ARGS=""
for a in "$@"; do
  case "$a" in lumi-harness@latest|lumi-harness==*) a=/build ;; esac
  ARGS="$ARGS $a"
done
# shellcheck disable=SC2086
exec /opt/realbin/uv $ARGS
STUB
chmod +x /stub/uv

# ── systemd 桩 ───────────────────────────────────────────────────────────
# 照 unit 里写的东西真起进程。STUB_DROP_TOKEN=1 时故意忽略 EnvironmentFile，
# 用来模拟「服务起来了但令牌没生效」——安装脚本必须逮住这种情况并失败。
mkdir -p /etc/systemd/system
cat > /stub/systemctl <<'STUB'
#!/bin/sh
UNIT=/etc/systemd/system/lumi.service
case "$1" in
  daemon-reload|enable|disable) exit 0 ;;
  restart|start)
    pkill -f "lumi serve" 2>/dev/null
    EXEC=$(sed -n 's/^ExecStart=//p' "$UNIT")
    RUNAS=$(sed -n 's/^User=//p' "$UNIT")
    HOMEV=$(sed -n 's/^Environment=HOME=//p' "$UNIT")
    DATAV=$(sed -n 's/^Environment=LUMI_CONFIG_DIR=//p' "$UNIT")
    ENVF=$(sed -n 's/^EnvironmentFile=//p' "$UNIT")
    TOK=""
    [ "${STUB_DROP_TOKEN:-0}" = 1 ] || TOK=$(sed -n 's/^LUMI_TOKEN=//p' "$ENVF")
    # shellcheck disable=SC2086
    setsid runuser -u "$RUNAS" -- env HOME="$HOMEV" LUMI_TOKEN="$TOK" \
      ${DATAV:+LUMI_CONFIG_DIR="$DATAV"} $EXEC >/tmp/serve.log 2>&1 &
    exit 0 ;;
  *) exit 0 ;;
esac
STUB
chmod +x /stub/systemctl
PATH="/stub:$PATH"; export PATH

# ── 用例 1：默认以调用者身份装（SUDO_USER=alice）────────────────────────
echo "› 用例 1：默认身份 = 调用 sudo 的人"
SUDO_USER=alice sh /src/scripts/install.sh --port 18700 > /tmp/install1.log 2>&1
RC=$?
check "脚本成功退出" "[ $RC = 0 ]"
[ $RC = 0 ] || { echo "--- 安装输出 ---"; cat /tmp/install1.log; echo "--- serve 日志 ---"; cat /tmp/serve.log 2>/dev/null; }

UNIT=/etc/systemd/system/lumi.service
check "unit 已生成" "[ -f $UNIT ]"
check "运行身份是 alice 而非 root/lumi" "grep -qx 'User=alice' $UNIT"
check "ExecStart 用绝对路径指向 alice 的 lumi" \
  "grep -qx 'ExecStart=/home/alice/.local/bin/lumi serve --host 0.0.0.0 --port 18700' $UNIT"
check "HOME 指向 alice 家目录" "grep -qx 'Environment=HOME=/home/alice' $UNIT"
check "未指定 --data-dir 时不写 LUMI_CONFIG_DIR" "! grep -q LUMI_CONFIG_DIR $UNIT"
check "令牌走 EnvironmentFile，不进 ExecStart" \
  "grep -qx 'EnvironmentFile=/etc/lumi.env' $UNIT && ! grep -q token $UNIT"
check "令牌文件权限 600" "[ \"\$(stat -c %a /etc/lumi.env)\" = 600 ]"
check "数据落在 alice 的 ~/.lumi" "[ -d /home/alice/.lumi ]"
check "lumi 装在 alice 名下" "[ -x /home/alice/.local/bin/lumi ]"
check "打印了连接串" "grep -q 'ws://.*:18700/ws?token=' /tmp/install1.log"

TOKEN1=$(sed -n 's/^LUMI_TOKEN=//p' /etc/lumi.env)
check "令牌非空" "[ -n \"$TOKEN1\" ]"

# ── 用例 2：重跑沿用原令牌（换令牌 = 所有桌面端同时失联）────────────────
echo "› 用例 2：重跑不换令牌"
SUDO_USER=alice sh /src/scripts/install.sh --port 18700 > /tmp/install2.log 2>&1
RC=$?
check "重跑成功" "[ $RC = 0 ]"
check "令牌沿用未变" "[ \"\$(sed -n 's/^LUMI_TOKEN=//p' /etc/lumi.env)\" = \"$TOKEN1\" ]"

# ── 用例 3：--user 指定身份 ──────────────────────────────────────────────
echo "› 用例 3：--user root"
sh /src/scripts/install.sh --user root --port 18701 > /tmp/install3.log 2>&1
RC=$?
check "指定 root 装得起来" "[ $RC = 0 ]"
check "unit 身份跟着改" "grep -qx 'User=root' $UNIT"
check "ExecStart 跟到 root 家目录" "grep -q 'ExecStart=/root/.local/bin/lumi' $UNIT"

# ── 用例 4：令牌没生效必须报错（这条红了才说明验证是真的）──────────────
echo "› 用例 4：服务无鉴权时必须失败"
STUB_DROP_TOKEN=1 SUDO_USER=alice sh /src/scripts/install.sh --port 18702 > /tmp/install4.log 2>&1
RC=$?
check "脚本非零退出" "[ $RC != 0 ]"
check "报的是无鉴权，不是别的错" "grep -q '无鉴权状态' /tmp/install4.log"

# ── 用例 5：装上的是旧版 lumi（不认 status）──────────────────────────────
# click 用退出码 2 表示用法错误，旧版没有 status 子命令敲上去正是退 2。这一条
# 曾经被误报成「服务处于无鉴权状态」——诊断南辕北辙，所以单独锁住
echo "› 用例 5：旧版 lumi 的诊断（放在最后，会毁掉安装）"
printf '#!/bin/sh\necho "No such command '\''status'\''." >&2\nexit 2\n' \
  > /home/alice/.local/bin/lumi
chmod +x /home/alice/.local/bin/lumi
STUB_SKIP_INSTALL=1 SUDO_USER=alice sh /src/scripts/install.sh --port 18703 \
  > /tmp/install5.log 2>&1
RC=$?
check "脚本非零退出" "[ $RC != 0 ]"
check "说的是版本太旧，不是无鉴权" \
  "grep -q '版本太旧' /tmp/install5.log && ! grep -q '无鉴权' /tmp/install5.log"

pkill -f "lumi serve" 2>/dev/null
echo
echo "通过 $pass，失败 $fail"
[ "$fail" = 0 ]
