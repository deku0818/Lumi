# SSH 远端 Lumi

Lumi 0.3 桌面远端入口统一为原生 SSH。Agent、模型、工具、MCP、定时任务和会话运行在目标机器。桌面通过 SSH 数据通道连接常驻进程，不需要额外的 Lumi TCP 端口、服务地址或令牌。

## 连接与认证

从新会话机器菜单或「设置 → 连接」添加主机。自动读取本机 `~/.ssh/config` 的明确 Host 别名并递归处理 Include，通配规则不列为具体机器。选中后由系统 `ssh -G` 解析 Match、IdentityFile、ProxyJump 等语义。手动添加支持主机、用户、SSH 端口和私钥；SSH 端口默认沿用配置或 22。

认证可沿用 SSH config/ssh-agent、私钥文件、粘贴私钥或密码，口令按需输入。粘贴私钥默认仅当前连接，临时目录 0700、文件 0600；主动保存写入本机应用专用密钥目录，普通主机配置只保存引用。退出、取消和断开清理临时文件，密码与口令不持久化。本机认证询问通过 Unix socket（Windows 为命名管道）传递。

连接窗口展示 SSH 验证、检查环境、安装/启动和建立数据通道的真实阶段。首次安装先说明数据目录并等待确认；同版本常驻实例直接复用。首次连接确认指纹，身份变化由 OpenSSH 拒绝，不自动覆盖 known_hosts。添加主机不改写 SSH config。配置解析只针对选中的主机，不后台执行所有 Match exec 条目。

## 远端条件

需 Python 3 和 Python 包网络。Linux 需 systemd 用户服务与 linger，可检查：

    systemctl --user show-environment
    loginctl show-user "$USER" -p Linger

没有 linger 时需有权限用户在目标机器启用 `loginctl enable-linger`，Lumi 不擅自 sudo。macOS 使用登录用户 LaunchAgents，需可用 GUI 用户域。Windows 远端宿主暂不支持。

## 安装与实例

确认首次安装后，桌面上传随附 wheel。优先使用已有 uv，缺失时从官方安装器装到本实例 tools 目录。Python、缓存与虚拟环境均位于本实例，不升级全局 Python 或其他应用。

默认数据目录始终为 SSH 用户的 `~/.lumi`，高级设置可显式选择独立目录：

    <data>/
      lumi.json、checkpoints、uploads、memory 等用户数据
      remote-runtime/
        agent.sock             # 私有 Unix socket，0600
        instance.lock          # 常驻进程持有的独占锁，0600
        instance.json          # 运行版本、协议、PID、程序路径，0600
        bootstrap.lock         # 安装和启动串行化
        versions/<version>/    # 独立运行环境
        tools/、cache/、python/

常驻进程在整个生命周期持有实例锁，同一数据目录只能运行一个原生远端实例。进程退出后操作系统释放锁；下一次启动取得锁后才能清理遗留 socket。仅管理由该数据目录哈希派生的用户服务：Linux unit 在 `~/.config/systemd/user/`，macOS plist 在 `~/Library/LaunchAgents/`。

每台机器共用一条 SSH 标准输入/输出数据通道，远端 attach 进程转接到上述 Unix socket。会话、目录操作、上传和预览都在同一通道传输，不建立 TCP 转发。常驻进程不监听 Lumi TCP 端口。

## 项目与续接

新会话发送前可切换机器和项目且保留草稿，首次发送才创建会话；已有会话不被重定向。项目页、项目菜单与新会话统一使用 C 文件夹选择器；目录浏览通过目标机器 `list_dir` / `make_dir`，支持上一级、主目录、面包屑、输入路径、新建和确认。「不在项目中工作」使用所选机器的主目录，不登记为项目。

文件分块传输，每块最多 1 MiB，单文件最多 128 MiB。未完成的上传在断连时清理。远端文件预览通过桌面的 `lumi-remote:` 协议读取；HTML 预览使用隔离的 blob iframe，不能借此访问其他远端文件。

关闭桌面只关闭 SSH，远端用户服务继续运行。重连复用进程并恢复历史、审批；活跃/挂起轮保留最多 8 小时。后端进程重启不保留内存审批 Future，也不自动重放副作用请求。

## 版本边界

桌面与原生远端必须为同一版本和同一传输协议，运行版本由 socket 握手确认。不同版本明确报错，不自动替换正在运行的实例。0.2 的服务地址连接、端口探测和自动迁移不再支持。

不会自动停止旧 Docker/系统服务、删除旧数据或合并实例。若旧服务仍在使用同一个数据目录，请先由用户停止它再使用原生实例，避免两个进程写同一数据；实例锁仅约束原生远端进程。

CLI、HTTP API 与容器仍是独立接口，不提供桌面服务地址回退。
