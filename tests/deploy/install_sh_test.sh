#!/usr/bin/env bash
# scripts/install.sh 的集成测试：用桩件替掉 docker / systemd / uv，真跑脚本查产物。
#
# 只能在一次性容器里跑——被测脚本会真写 /etc/lumi.env、/opt/lumi、systemd unit：
#   docker run --rm -v "$PWD:/src:ro" python:3.12-slim bash /src/tests/deploy/install_sh_test.sh
set -uo pipefail

if [ ! -f /.dockerenv ] && [ "${FORCE:-0}" != 1 ]; then
  echo "拒绝执行：本测试会写 /etc 与 /opt，请在一次性容器里跑（见文件头）" >&2
  exit 1
fi

SCRIPT=/src/scripts/install.sh
STUB=/stub
mkdir -p "$STUB"
export PATH="$STUB:$PATH"
pass=0; fail=0
ok()   { echo "  ✅ $1"; pass=$((pass+1)); }
bad()  { echo "  ❌ $1"; fail=$((fail+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1（条件不成立：$2）"; fi; }

# ── 桩件 ────────────────────────────────────────────────────────────────
# docker：info/compose 恒成功；exec 的 probe 按 $STUB_AUTH 决定是否校验令牌
cat > "$STUB/docker" <<'EOF'
#!/usr/bin/env bash
case "$1 $2" in
  "info "*) exit 0 ;;
  "compose version") exit 0 ;;
esac
if [ "$1" = compose ]; then echo "compose $*" >> /tmp/docker.log; exit 0; fi
if [ "$1" = exec ]; then
  shift
  [ "$1" = "-i" ] && shift
  echo "exec $*" >> /tmp/docker.log
  # probe：命令形如 `lumi python - <url>`，url 在末位
  url="${!#}"
  case "$url" in
    ws://*)
      cat > /dev/null                      # 吞掉 stdin 里的探针脚本
      [ "${STUB_AUTH:-1}" = 0 ] && exit 0  # 模拟「没设令牌的服务器」：谁来都放行
      case "$url" in *"token=$(sed -n 's/^LUMI_TOKEN=//p' /etc/lumi.env)") exit 0 ;; *) exit 1 ;; esac ;;
  esac
  exit 0
fi
[ "$1" = ps ] && { echo "容器 lumi: Up 3 seconds"; exit 0; }
exit 0
EOF

cat > "$STUB/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> /tmp/systemctl.log
exit 0
EOF

cat > "$STUB/runuser" <<'EOF'
#!/usr/bin/env bash
# runuser -u <user> -- cmd...  → 直接执行 cmd（容器内测的是脚本逻辑，不测降权）
shift 2; [ "$1" = "--" ] && shift
exec "$@"
EOF

cat > "$STUB/uv" <<'EOF'
#!/usr/bin/env bash
echo "uv $*" >> /tmp/uv.log
# uv tool install：造出假的 lumi 命令 + 同目录 python（探针要用）
if [ "$1 $2" = "tool install" ]; then
  venv=/opt/lumi/.local/share/uv/tools/lumi-harness/bin
  mkdir -p "$venv" "$UV_TOOL_BIN_DIR"
  printf '#!/bin/sh\necho "lumi $*" >> /tmp/lumi.log\n' > "$venv/lumi"
  cat > "$venv/python" <<'PY'
#!/bin/sh
cat > /dev/null
case "$2" in *"token=$(sed -n 's/^LUMI_TOKEN=//p' /etc/lumi.env)") exit 0 ;; esac
exit 1
PY
  chmod +x "$venv/lumi" "$venv/python"
  ln -sf "$venv/lumi" "$UV_TOOL_BIN_DIR/lumi"
fi
exit 0
EOF

chmod +x "$STUB"/*
touch "$STUB/../fake"; mkdir -p /usr/local/bin

reset_env() { rm -rf /opt/lumi /etc/lumi.env /etc/systemd/system/lumi.service /tmp/*.log; }

echo "── A. docker 模式全流程 ──"
reset_env
out=$("$SCRIPT" --mode docker --port 9001 2>&1); rc=$?
echo "$out" | sed 's/^/    /' | tail -6
check "退出码 0" "[ $rc = 0 ]"
check "令牌文件权限 600" "[ \"\$(stat -c %a /etc/lumi.env)\" = 600 ]"
check "令牌是 48 位十六进制" "grep -qE '^LUMI_TOKEN=[0-9a-f]{48}$' /etc/lumi.env"
check "已 seed config.json" "grep -q '\"style\": \"code\"' /opt/lumi/data/config.json"
check "compose 映射端口 9001→8765" "grep -q '\"9001:8765\"' /opt/lumi/docker-compose.yml"
check "compose 挂载数据卷" "grep -q '/opt/lumi/data:/root/.lumi' /opt/lumi/docker-compose.yml"
check "compose 挂载工作区（容器只看得见挂进去的）" "grep -q '/opt/lumi/workspace:/workspace' /opt/lumi/docker-compose.yml"
check "compose 用 env_file 传令牌" "grep -q 'env_file: /etc/lumi.env' /opt/lumi/docker-compose.yml"
check "compose 里没有明文令牌" "! grep -q \"\$(sed -n 's/^LUMI_TOKEN=//p' /etc/lumi.env)\" /opt/lumi/docker-compose.yml"
check "记住了部署状态" "grep -q 'MODE=docker' /opt/lumi/deploy.env && grep -q 'PORT=9001' /opt/lumi/deploy.env"
check "装了工具链" "grep -q 'lumi env install' /tmp/docker.log"
check "打印了连接串" "echo \"\$out\" | grep -q 'ws://.*:9001/ws?token='"

echo "── B. 令牌沿用与幂等（重跑 = 升级）──"
tok_before=$(sed -n 's/^LUMI_TOKEN=//p' /etc/lumi.env)
"$SCRIPT" upgrade >/dev/null 2>&1; rc=$?
check "upgrade 退出码 0" "[ $rc = 0 ]"
check "令牌未被换掉" "[ \"\$(sed -n 's/^LUMI_TOKEN=//p' /etc/lumi.env)\" = $tok_before ]"
check "端口从状态文件沿用（没退回 8765）" "grep -q 'PORT=9001' /opt/lumi/deploy.env"

echo "── C. 无鉴权服务器必须被拦下 ──"
reset_env
out=$(STUB_AUTH=0 "$SCRIPT" --mode docker 2>&1); rc=$?
check "退出码非 0" "[ $rc != 0 ]"
check "报出无鉴权" "echo \"\$out\" | grep -q '错误令牌也能连上'"
check "没打印连接串（不给人错觉）" "! echo \"\$out\" | grep -q '桌面端「设置 → 连接」'"

echo "── D. 宿主机模式 systemd unit ──"
reset_env
out=$("$SCRIPT" --mode native --port 9002 --user root 2>&1); rc=$?
echo "$out" | sed 's/^/    /' | tail -4
unit=/etc/systemd/system/lumi.service
check "退出码 0" "[ $rc = 0 ]"
check "生成了 unit" "[ -f $unit ]"
check "unit 指定 LUMI_CONFIG_DIR" "grep -q 'Environment=LUMI_CONFIG_DIR=/opt/lumi/data' $unit"
check "unit 读令牌文件" "grep -q 'EnvironmentFile=/etc/lumi.env' $unit"
check "ExecStart 不含明文令牌" "! grep '^ExecStart' $unit | grep -q token"
check "监听 0.0.0.0:9002" "grep -q 'ExecStart=.*--host 0.0.0.0 --port 9002' $unit"
check "cwd 指向数据目录（不假装有唯一工作区）" "grep -q 'WorkingDirectory=/opt/lumi/data' $unit"
check "宿主机模式不创建 workspace 目录" "[ ! -d /opt/lumi/workspace ]"
check "enable + restart 了服务" "grep -q 'enable lumi' /tmp/systemctl.log && grep -q 'restart lumi' /tmp/systemctl.log"
check "装的是 lumi-harness" "grep -q 'tool install --force lumi-harness' /tmp/uv.log"

echo "── E. uninstall 保留数据 ──"
"$SCRIPT" uninstall >/dev/null 2>&1
check "删掉了令牌文件" "[ ! -f /etc/lumi.env ]"
check "删掉了 unit" "[ ! -f $unit ]"
check "保留了数据目录" "[ -f /opt/lumi/data/config.json ]"
"$SCRIPT" uninstall --purge >/dev/null 2>&1
check "--purge 才删数据" "[ ! -d /opt/lumi ]"

echo "── E2. --workspace 指定宿主机目录（docker）──"
reset_env
mkdir -p /srv/我的项目
"$SCRIPT" --mode docker --workspace /srv/我的项目 >/dev/null 2>&1
check "compose 挂的是指定目录" "grep -q '/srv/我的项目:/workspace' /opt/lumi/docker-compose.yml"
check "deploy.env 记住工作区" "grep -q 'WORK_DIR=/srv/我的项目' /opt/lumi/deploy.env"
"$SCRIPT" upgrade >/dev/null 2>&1
check "upgrade 不带参数仍用它" "grep -q '/srv/我的项目:/workspace' /opt/lumi/docker-compose.yml"
"$SCRIPT" uninstall --purge >/dev/null 2>&1

echo "── F. LUMI_CONFIG_DIR 接管既有数据目录 ──"
reset_env
mkdir -p /srv/old-lumi && echo '{"style":"code"}' > /srv/old-lumi/config.json
touch /srv/old-lumi/老会话标记
LUMI_CONFIG_DIR=/srv/old-lumi "$SCRIPT" --mode docker >/dev/null 2>&1; rc=$?
check "退出码 0" "[ $rc = 0 ]"
check "没在 prefix 下另建 data" "[ ! -d /opt/lumi/data ]"
check "既有数据原样保留" "[ -f /srv/old-lumi/老会话标记 ]"
check "没覆盖既有 config.json" "[ \"\$(cat /srv/old-lumi/config.json)\" = '{\"style\":\"code\"}' ]"
check "compose 挂的是既有目录" "grep -q '/srv/old-lumi:/root/.lumi' /opt/lumi/docker-compose.yml"
check "deploy.env 记住了数据目录" "grep -q 'DATA_DIR=/srv/old-lumi' /opt/lumi/deploy.env"

# 关键回归：upgrade 不带环境变量，也必须继续用同一个数据目录
"$SCRIPT" upgrade >/dev/null 2>&1
check "upgrade 不带环境变量仍用原目录" "grep -q '/srv/old-lumi:/root/.lumi' /opt/lumi/docker-compose.yml"

out=$("$SCRIPT" uninstall --purge 2>&1)
check "--purge 不删接管来的数据" "[ -f /srv/old-lumi/老会话标记 ]"
check "--purge 明确告知未删" "echo \"\$out\" | grep -q '未删除'"
check "--purge 删掉自建的 prefix" "[ ! -d /opt/lumi ]"

echo
echo "通过 $pass，失败 $fail"
[ "$fail" = 0 ]
