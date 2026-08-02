"""工具箱：agent 任务工具链的探测与安装。

打包版桌面后端自带 Python，但 agent 干活所需的 uv / ripgrep / Node 在非技术
用户机器上通常缺失。本模块负责探测（系统 PATH 永远优先，绝不覆盖用户自装）
与安装（下载官方产物到 <配置目录>/bin，免 sudo、不碰系统全局）。飞书组件
（lark-cli + 技能包导出）作为可选集成走同一套机制。

版本 pin 在代码里：不依赖 GitHub API 可达性，升级 Lumi 时更新。校验用同源
checksum 文件（防传输损坏）；下载尊重 https_proxy 环境变量（urllib 默认行为）。
设计见 docs/architecture/toolbox.md。
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path

from lumi.utils.config.manager import parse_frontmatter
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config

UV_VERSION = "0.11.32"
RG_VERSION = "15.2.0"
NODE_VERSION = "24.18.0"
OFFICECLI_VERSION = "1.0.143"

# 工具链单枚举：探测（status_all）、装齐（env_rpc target=all）、CLI `lumi env install`
# 全部共用。后端不再分「核心/可选」——该区分自装齐覆盖全部后已无行为消费者，
# 环境页的两栏分组是纯展示概念，由前端 EnvPanel 独家持有
ALL_TOOLS = ("uv", "rg", "node", "officecli")

# 进度回调：(阶段描述, 0..1 或 None=不可知)
ProgressFn = Callable[[str, float | None], None]

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")
_SHA256_RE = re.compile(r"\b[0-9a-fA-F]{64}\b")
_CLI_TIMEOUT = 30


@dataclass(frozen=True)
class ToolStatus:
    name: str
    source: str  # system | toolbox | missing
    version: str = ""
    path: str = ""


# ── 平台与下载源 ──


def _plat() -> tuple[str, str]:
    """归一化 (os, arch)：os ∈ darwin|linux|win，arch ∈ x64|arm64。"""
    os_name = {"darwin": "darwin", "linux": "linux", "windows": "win"}[
        platform.system().lower()
    ]
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    return os_name, arch


def _rust_target(os_name: str, arch: str) -> str:
    """uv / ripgrep 发布产物的 Rust target 三元组。"""
    cpu = "aarch64" if arch == "arm64" else "x86_64"
    if os_name == "darwin":
        return f"{cpu}-apple-darwin"
    if os_name == "linux":
        # x64 用 musl 静态链接（rg 只发 musl 版 x64 静态产物，uv 两者都有）
        libc = "musl" if arch == "x64" else "gnu"
        return f"{cpu}-unknown-linux-{libc}"
    return f"{cpu}-pc-windows-msvc"


def _archive_ext(os_name: str) -> str:
    return "zip" if os_name == "win" else "tar.gz"


def download_url(tool: str, os_name: str, arch: str) -> str:
    """某工具在指定平台的官方下载 URL。独立成纯函数便于全平台矩阵测试。"""
    ext = _archive_ext(os_name)
    if tool == "uv":
        target = _rust_target(os_name, arch)
        return f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-{target}.{ext}"
    if tool == "rg":
        target = _rust_target(os_name, arch)
        return (
            "https://github.com/BurntSushi/ripgrep/releases/download/"
            f"{RG_VERSION}/ripgrep-{RG_VERSION}-{target}.{ext}"
        )
    if tool == "node":
        return f"https://nodejs.org/dist/v{NODE_VERSION}/node-v{NODE_VERSION}-{os_name}-{arch}.{ext}"
    if tool == "officecli":
        # 发布物是免压缩的单二进制，命名平台段用 mac 而非 darwin
        plat = "mac" if os_name == "darwin" else os_name
        suffix = ".exe" if os_name == "win" else ""
        return (
            "https://github.com/iOfficeAI/OfficeCLI/releases/download/"
            f"v{OFFICECLI_VERSION}/officecli-{plat}-{arch}{suffix}"
        )
    raise ValueError(f"未知工具: {tool}")


# ── 探测 ──


def _exe(name: str) -> str:
    return f"{name}.exe" if _plat()[0] == "win" else name


def _run(cmd: list[str], timeout: int = _CLI_TIMEOUT) -> tuple[bool, str]:
    """跑一次子命令，返回 (成功, stdout 或 stderr)。

    显式按 UTF-8 解码：``text=True`` 走的是系统 locale 编码，中文 Windows 上
    是 cp936，lark-cli 的中文 JSON 一到那儿就 UnicodeDecodeError（体检误报
    「不支持 skills 子命令」）。
    """
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return proc.returncode == 0, proc.stdout or proc.stderr
    except Exception as e:
        return False, str(e)


def _version_of(path: str, name: str) -> str:
    """跑 --version 解析版本号；失败返回空串（存在但版本未知）。"""
    ok, out = _run([path, "--version"], timeout=10)
    if not ok:
        logger.debug(f"取 {name} 版本失败: {out[:200]}")
    # 非零退出也照样解析：有的工具把版本打在 stderr 且退出码不为 0
    match = _VERSION_RE.search(out)
    return match.group(1) if match else ""


def detect(name: str) -> ToolStatus:
    """探测单个工具：系统 PATH 优先 → 工具箱 → missing。

    系统来源永不被工具箱遮蔽（PATH 末尾追加保证 which 先见系统副本），
    「一键装齐」据此跳过 system 项。用裸名 which：Windows 会自动遍历
    PATHEXT（npm/npx/lark-cli 实为 .cmd，硬拼 .exe 会漏检）。
    """
    bin_dir = get_config().bin_dir
    found = shutil.which(name)
    if found:
        # 只 resolve 目录、不 resolve 文件本身：node/lark-cli 在 bin_dir 里是指向
        # node/ 树的 symlink，resolve 文件会把它们误判成 system
        source = (
            "toolbox" if Path(found).parent.resolve() == bin_dir.resolve() else "system"
        )
        return ToolStatus(name, source, _version_of(found, name), found)
    # PATH 未注入时兜底查 bin_dir（安装刚完成的场景），同样交给 which 处理 PATHEXT
    box = shutil.which(name, path=str(bin_dir))
    if box:
        return ToolStatus(name, "toolbox", _version_of(box, name), box)
    return ToolStatus(name, "missing")


def status_all() -> dict:
    """工具链全量状态（核心 + 可选），dict 结构可直接下发前端。

    飞书组件不在此列——它的检测有项目维度（技能包按项目装），归渠道体检
    （diagnose_feishu_setup 的本地环境组），环境页保持纯机器级视图。

    bin_dir 下发本机绝对路径（Windows 上带盘符与反斜杠）：``~/.lumi/bin`` 这种
    写法非技术用户看不懂，路径该由知道真值的一侧给出，而非前端硬编码。
    """
    # 每个 detect 一次 --version 子进程，并行探测把墙钟压到单次
    with ThreadPoolExecutor(max_workers=len(ALL_TOOLS)) as pool:
        return {
            "tools": [asdict(s) for s in pool.map(detect, ALL_TOOLS)],
            "bin_dir": str(get_config().bin_dir),
        }


# ── 下载与解压 ──


def _pick_sha256(text: str) -> str:
    """从 checksum 文件里挑出那串哈希，容忍各家的排版。

    ``<hash>  <file>``（uv / rg 的 POSIX 产物、node 的 SHASUMS256.txt）之外还有
    第三种：rg 的 Windows 产物由 CertUtil 生成，哈希在第二行，首行是
    ``SHA256 hash of xxx.zip:``——按空白切首段会取到 "SHA256"，校验必然不匹配，
    Windows 装 rg 从来没成功过。取首个 64 位十六进制串则三种排版通吃。
    """
    match = _SHA256_RE.search(text)
    return match.group(0).lower() if match else ""


# 无逐产物 .sha256 的工具走发布目录下的汇总清单（按文件名挑行）；
# 不在表内 = 默认的 <asset>.sha256。与 download_url 同以工具名为派发键，
# 换镜像/发布方迁 host 不会让校验静默失效
_CHECKSUM_MANIFEST = {"node": "SHASUMS256.txt", "officecli": "SHA256SUMS"}


def _fetch_checksum(url: str, tool: str) -> str:
    """取产物的官方 sha256。拿不到时返回空串跳过校验：checksum 与产物同源，
    只防传输损坏，不值得为它让安装失败。
    """
    try:
        manifest = _CHECKSUM_MANIFEST.get(tool, "")
        if manifest:
            base, filename = url.rsplit("/", 1)
            body = (
                urllib.request.urlopen(f"{base}/{manifest}", timeout=30).read().decode()
            )
            text = next(
                (line for line in body.splitlines() if line.endswith(filename)), ""
            )
        else:
            text = urllib.request.urlopen(f"{url}.sha256", timeout=30).read().decode()
    except Exception as e:
        logger.warning(f"checksum 获取失败，跳过校验: {e}")
        return ""
    digest = _pick_sha256(text)
    if not digest:
        # 取到了响应却挑不出哈希（代理拦截页 / 发布方改排版），静默跳过校验就成了
        # 「校验形同虚设而无人知晓」——这一支必须留痕
        logger.warning(f"checksum 内容无可识别哈希，跳过校验: {url}")
    return digest


def _download(
    url: str, dest: Path, progress: ProgressFn | None, phase: str, tool: str = ""
) -> None:
    """分块下载到 dest 并校验 checksum。走 urllib 默认代理（https_proxy）。"""
    # checksum 只在下载完成后比对时才需要，与下载并行省一次串行网络往返
    with ThreadPoolExecutor(max_workers=1) as pool:
        checksum_future = pool.submit(_fetch_checksum, url, tool)
        digest = sha256()
        with urllib.request.urlopen(url, timeout=60) as resp, open(dest, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while chunk := resp.read(1 << 16):
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(phase, done / total if total else None)
        expected = checksum_future.result()
    if expected and digest.hexdigest() != expected:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"{url} checksum 不匹配，已删除下载文件")


def _open_archive(path: Path) -> zipfile.ZipFile | tarfile.TarFile:
    if path.name.endswith(".zip"):
        return zipfile.ZipFile(path)
    return tarfile.open(path)


def _extract_binary(archive_path: Path, name: str) -> Path:
    """从压缩包提取名为 name 的单二进制到 bin_dir（uv / rg / lark-cli 通用）。"""
    bin_dir = get_config().bin_dir
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe = _exe(name)
    dest = bin_dir / exe
    with _open_archive(archive_path) as arc:
        names = arc.namelist() if isinstance(arc, zipfile.ZipFile) else arc.getnames()
        member = next(m for m in names if m.rstrip("/").rsplit("/", 1)[-1] == exe)
        with (
            (
                arc.open(member)
                if isinstance(arc, zipfile.ZipFile)
                else arc.extractfile(member)
            ) as src,
            open(dest, "wb") as out,
        ):
            shutil.copyfileobj(src, out)
    dest.chmod(0o755)
    return dest


def _extract_tree(archive_path: Path, dest: Path) -> None:
    """解压整棵树到 dest，剥掉压缩包的单一顶层目录（node tarball 形态）。"""
    if dest.exists():
        shutil.rmtree(dest)
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        with _open_archive(archive_path) as arc:
            if isinstance(arc, tarfile.TarFile):
                arc.extractall(tmp, filter="data")
            else:
                arc.extractall(tmp)
        roots = list(Path(tmp).iterdir())
        src = roots[0] if len(roots) == 1 and roots[0].is_dir() else Path(tmp)
        shutil.move(str(src), str(dest))


def _link(target: Path, name: str) -> None:
    """在 bin_dir 建 symlink（Windows 无特权 symlink 不可靠，写 .cmd shim）。"""
    bin_dir = get_config().bin_dir
    bin_dir.mkdir(parents=True, exist_ok=True)
    if _plat()[0] == "win":
        shim = bin_dir / f"{name}.cmd"
        shim.write_text(f'@echo off\r\n"{target}" %*\r\n')
        return
    link = bin_dir / name
    link.unlink(missing_ok=True)
    link.symlink_to(target)


# ── 安装 ──


def install(name: str, progress: ProgressFn | None = None) -> ToolStatus:
    """安装（或升级）单个核心工具到工具箱。幂等：重复调用即覆盖为 pin 版本。"""
    os_name, arch = _plat()
    url = download_url(name, os_name, arch)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / url.rsplit("/", 1)[-1]
        _download(url, archive, progress, f"下载 {name}", name)
        if progress:
            progress(f"安装 {name}", None)
        if name == "node":
            node_root = get_config().toolbox_dir / "node"
            _extract_tree(archive, node_root)
            for cmd in ("node", "npm", "npx"):
                _link(_node_tool_path(node_root, cmd), cmd)
        elif name == "officecli":
            # 发布物是免压缩的单二进制，直落 bin_dir
            bin_dir = get_config().bin_dir
            bin_dir.mkdir(parents=True, exist_ok=True)
            dest = bin_dir / _exe(name)
            shutil.move(str(archive), str(dest))
            dest.chmod(0o755)
        else:
            _extract_binary(archive, name)
    if progress:
        # 终态：装齐流程里已完成的行定格在「完成 100%」，而非停在最后一条脉冲
        progress("完成", 1.0)
    return detect(name)


def _node_tool_path(node_root: Path, name: str) -> Path:
    """node 树内某工具的实际路径——平台产物布局的唯一权威。

    Windows 发行包是平铺的，且只有 node 是 .exe，npm/npx（及 npm -g 装出的
    lark-cli 等）都是 .cmd 脚本；POSIX 全部在 bin/ 下无后缀。
    """
    if _plat()[0] == "win":
        return node_root / (f"{name}.exe" if name == "node" else f"{name}.cmd")
    return node_root / "bin" / name


def install_missing(
    progress: ProgressFn | None = None, names: tuple[str, ...] = ALL_TOOLS
) -> list[ToolStatus]:
    """只装 missing 的工具，system / toolbox 项原样返回。

    「跳过已有的」这条规矩只此一处：``install`` 本身是无条件覆盖，各调用方各写一遍
    的话，漏写的那一个就会给系统已装的工具在工具箱里留一份 PATH 上永远轮不到的
    影子副本。names 收窄到单个工具即「装这一个」（CLI 与桌面的逐项安装走同一条路）。
    """
    # 探测各是一次 --version 子进程，并行把这段墙钟压到单次（同 status_all）
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        statuses = list(pool.map(detect, names))
    return [install(s.name, progress) if s.source == "missing" else s for s in statuses]


# ── 飞书组件（lark-cli + 技能包） ──

_LARK_PKG = "@larksuite/cli"


def lark_skill_versions(cli_path: str) -> dict[str, str] | None:
    """lark-cli 内嵌技能清单 {name: version}；命令失败/输出不可解析返回 None。

    None 与空 dict 必须区分：None = 清单读不到（cli 版本过旧等），不能当
    「0 个技能待装」处理，否则体检报 error 而安装是空操作，永远修不绿。
    """
    ok, out = _run([cli_path, "skills", "list"])
    if not ok:
        logger.warning(f"lark-cli skills list 执行失败: {out[:200]}")
        return None
    try:
        skills = json.loads(out).get("skills") or []
        return {s["name"]: s.get("version", "") for s in skills}
    except ValueError:
        # 体检 UI 只能给出「请先升级」的猜测，真实原因（崩溃栈 / 乱码 / 更新提示
        # 混进 stdout）唯有日志留得下
        logger.warning(f"lark-cli skills list 输出不可解析: {out[:200]}")
        return None


def resolve_skills_dir(project_dir: str = "") -> Path:
    """技能包落点：项目层 ``<project>/.lumi/skills``；未给项目退回全局层。

    技能包占上下文（28 条 description 常驻注入），按「谁用谁装」装到项目层；
    全局层仅作未绑定项目时的兜底。
    """
    if project_dir:
        return Path(project_dir).expanduser() / ".lumi" / "skills"
    return get_config().skills_dir


def _local_skill_version(name: str, skills_dir: Path) -> str:
    """已装技能的 frontmatter version；未装返回空。"""
    skill_md = skills_dir / name / "SKILL.md"
    if not skill_md.exists():
        return ""
    meta, _ = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    return str(meta.get("version") or "unknown")


def skills_status(embedded: dict[str, str], project_dir: str = "") -> dict:
    """技能包状态：对照内嵌清单统计项目层的已装数 / 落后数（含缺失，视同待更新）。

    missing 计入 outdated：cli 升级后新增的技能本地必然不存在，只看版本差
    会把「部分缺失」报成全绿，新技能永远不会提示安装。
    """
    skills_dir = resolve_skills_dir(project_dir)
    installed = 0
    outdated = 0
    for name, version in embedded.items():
        local = _local_skill_version(name, skills_dir)
        if local:
            installed += 1
            if local != version:
                outdated += 1
    if installed:  # 装过部分时，缺失项（cli 升级新增的技能）视同待更新
        outdated += len(embedded) - installed
    return {"total": len(embedded), "installed": installed, "outdated": outdated}


def _npm_global_bin(npm_path: str, name: str) -> Path:
    """npm 全局装出的可执行文件路径——**问 npm 要 prefix，不按 node 树硬拼**。

    prefix 可被用户级 `.npmrc` 改掉（Windows 上指到 `%APPDATA%\\npm` 很常见），
    猜错会链出一个探测得到、一跑就报「找不到路径」的幽灵 shim：体检显示 lark-cli
    已安装，而技能包同步、妙记取数全部静默失败。
    """
    ok, out = _run([npm_path, "prefix", "-g"])
    if not ok:
        raise RuntimeError(f"读取 npm 全局目录失败: {out.strip()[-200:]}")
    target = _node_tool_path(Path(out.strip()), name)
    if not target.exists():
        raise RuntimeError(f"npm 报告安装成功，但 {target} 不存在")
    return target


def install_lark_cli(progress: ProgressFn | None = None) -> ToolStatus:
    """安装 lark-cli（机器级），只走 npm —— 它的官方分发渠道就是 npm 包。

    缺 npm 不在此处代装：核心工具链的安装入口是「设置 → 环境」，在渠道页偷偷拉一个
    几十 MB 的 Node 下载，用户既没点过也不知道在等什么。故抛错，由体检把人引过去。
    """
    cli = detect("lark-cli")
    if cli.source != "missing":
        return cli
    npm = detect("npm")
    if npm.source == "missing":
        raise RuntimeError("未检测到 npm，请先在「设置 → 环境」安装 Node.js")
    if progress:
        progress("安装 lark-cli", None)
    ok, out = _run([npm.path, "install", "-g", _LARK_PKG], timeout=600)
    if not ok:
        # 包的 postinstall 用系统 curl 下载真实二进制，缺 curl 时它打印的却是
        # 「配代理/公司镜像」的网络受限文案——按原文透传会把人引去查网络
        if "curl ENOENT" in out:
            raise RuntimeError(
                "npm 安装 lark-cli 失败：系统缺 curl（安装脚本靠它下载二进制），"
                "请先安装 curl 后重试"
            )
        # 原文带出来：权限、代理、registry 不可达各有各的下一步，笼统一句「安装失败」
        # 只会让用户反复点同一个按钮
        raise RuntimeError(f"npm 安装 lark-cli 失败: {out.strip()[-300:]}")
    cli = detect("lark-cli")
    if cli.source != "missing":
        return cli
    # 装成功却探测不到 = npm 的全局 bin 不在 PATH 上（工具箱 npm 恒如此），接入 bin_dir
    _link(_npm_global_bin(npm.path, "lark-cli"), "lark-cli")
    return detect("lark-cli")


def sync_lark_skills(progress: ProgressFn | None = None, project_dir: str = "") -> int:
    """把 lark-cli 内嵌技能导出到项目层 skills（按 version 增量），返回更新数。"""
    cli = detect("lark-cli")
    if cli.source == "missing":
        raise RuntimeError("lark-cli 不可用，无法同步技能包")
    embedded = lark_skill_versions(cli.path)
    if embedded is None:
        raise RuntimeError(
            "无法读取 lark-cli 内嵌技能清单：版本过旧或安装不完整，请重装 lark-cli"
        )
    skills_dir = resolve_skills_dir(project_dir)
    updated = 0
    # 逐文件一次 `skills read` 子进程（npm 版 cli 是 Node 入口，冷启动 ~200ms），
    # 串行会把整包同步拖到分钟级——全程共享一个线程池，跨技能填坑
    with ThreadPoolExecutor(max_workers=8) as pool:
        for index, (name, version) in enumerate(embedded.items()):
            if progress:
                progress(f"同步技能 {name}", (index + 1) / len(embedded))
            if version and _local_skill_version(name, skills_dir) == version:
                continue
            _export_skill(cli.path, name, skills_dir, pool)
            updated += 1
    return updated


def _export_skill(
    cli_path: str, name: str, skills_dir: Path, pool: ThreadPoolExecutor
) -> None:
    """并发导出一个技能的全部文件。"""

    def read_one(rel_path: str) -> tuple[str, str]:
        ok, content = _run([cli_path, "skills", "read", rel_path])
        if not ok:
            raise RuntimeError(f"读取技能文件失败 {rel_path}: {content[:200]}")
        return rel_path, content

    for rel_path, content in pool.map(read_one, _lark_skill_files(cli_path, name)):
        dest = skills_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")


def _lark_skill_files(cli_path: str, root: str) -> list[str]:
    """递归列出一个内嵌技能下的全部文件路径（相对 skills 根）。"""
    files: list[str] = []
    pending = [root]
    while pending:
        ok, out = _run([cli_path, "skills", "list", pending.pop()])
        if not ok:
            raise RuntimeError(f"列举技能目录失败: {out[:200]}")
        for entry in json.loads(out).get("entries") or []:
            (pending if entry.get("is_dir") else files).append(entry["path"])
    return files


# ── PATH 注入 ──


def inject_path() -> None:
    """把 <配置目录>/bin 追加到进程 PATH 末尾（幂等）。

    bash 工具、lark-cli subprocess、MCP stdio 子进程全部继承；末尾追加
    保证系统同名版本永远优先，工具箱不产生影子副本。
    """
    bin_dir = str(get_config().bin_dir)
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if bin_dir not in parts:
        os.environ["PATH"] = os.pathsep.join([*parts, bin_dir])
