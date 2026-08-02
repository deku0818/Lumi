"""Office 预览 RPC：docx/xlsx/pptx → 自包含 HTML，供前端复用 iframe 渲染通道。

转换靠 officecli（toolbox 托管，机器级）；未安装返回 reason=missing，前端在
预览面板就地引导 env_install。产物按「路径哈希-mtime」落缓存目录，源文件一改
即失效；同路径的旧 mtime 产物顺手清掉，缓存不随反复编辑膨胀。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

from lumi.gateway import toolbox
from lumi.utils.hashing import short_hash
from lumi.utils.read_config import get_config

OFFICE_METHODS = frozenset({"render_office"})

OFFICE_EXTS = frozenset({".docx", ".xlsx", ".pptx"})
_RENDER_TIMEOUT = 120

# 注入逻辑变更时递增，使旧缓存产物失效重渲
_RENDER_VERSION = 2

# xlsx 增强脚本：officecli 忠实还原 Excel 列宽，窄列内容被截断后静态页无法像
# Excel 那样拖宽列——注入列头拖拽调宽 + 双击自动适应内容宽。初始列宽不动（保真），
# 交互仅是增强；iframe 沙箱为 opaque origin，父页够不到内嵌 DOM，只能在产物里注入。
_XLSX_RESIZE_JS = """
<script>
(function () {
  var ctx = document.createElement('canvas').getContext('2d');
  function syncWidth(table) {
    var sum = 0;
    table.querySelectorAll('colgroup col').forEach(function (c) {
      sum += c.getBoundingClientRect().width;
    });
    table.style.width = sum + 'px';
  }
  function autofit(table, col, idx) {
    var w = 30;
    table.querySelectorAll('tbody tr').forEach(function (tr) {
      var td = tr.children[idx + 1]; // +1 跳过行号 th
      if (!td || !td.textContent) return;
      var s = getComputedStyle(td);
      ctx.font = s.fontWeight + ' ' + s.fontSize + ' ' + s.fontFamily;
      w = Math.max(w, ctx.measureText(td.textContent).width + 14);
    });
    col.style.width = w + 'px';
    syncWidth(table);
  }
  document.querySelectorAll('table').forEach(function (table) {
    var cols = table.querySelectorAll('colgroup col:not(.row-header-col)');
    table.querySelectorAll('thead .col-header').forEach(function (th, i) {
      var col = cols[i];
      if (!col) return;
      var grip = document.createElement('span');
      grip.style.cssText =
        'position:absolute;right:-4px;top:0;width:9px;height:100%;cursor:col-resize;z-index:5';
      th.style.position = 'relative';
      th.appendChild(grip);
      grip.addEventListener('dblclick', function () { autofit(table, col, i); });
      grip.addEventListener('mousedown', function (e) {
        e.preventDefault();
        var startX = e.clientX;
        var startW = col.getBoundingClientRect().width;
        function move(ev) {
          col.style.width = Math.max(24, startW + ev.clientX - startX) + 'px';
          syncWidth(table);
        }
        function up() {
          document.removeEventListener('mousemove', move);
          document.removeEventListener('mouseup', up);
        }
        document.addEventListener('mousemove', move);
        document.addEventListener('mouseup', up);
      });
    });
  });
})();
</script>
"""


# 本机 .NET 需要 invariant globalization 模式（首次撞 ICU 缺失后置位，进程内记住）
_NEED_INVARIANT = False


def _run_officecli(cli_path: str, src: Path, out: Path) -> subprocess.CompletedProcess:
    env = None
    if _NEED_INVARIANT:
        env = {**os.environ, "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1"}
    return subprocess.run(
        [cli_path, "view", str(src), "html", "-o", str(out)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_RENDER_TIMEOUT,
        env=env,
    )


def _cache_dir() -> Path:
    # 机器级缓存，与 models_dev.json 同层（~/.lumi/cache/，测试经配置目录隔离）
    return get_config().toolbox_dir / "cache" / "office_preview"


def render_office(path: str) -> dict:
    """渲染一个 Office 文件为 HTML，返回 wire 结果（见 events.json render_office）。"""
    src = Path(path)
    if src.suffix.lower() not in OFFICE_EXTS:
        return {"ok": False, "reason": "unsupported"}
    try:
        mtime = src.stat().st_mtime_ns
    except OSError:
        return {"ok": False, "reason": "error", "message": "文件不存在"}
    # 缓存命中先于 detect：重开同一文档是常态路径，别为它白付一次
    # officecli --version 子进程（.NET 自包含二进制，启动不便宜）
    digest = short_hash(str(src), 16)
    cache = _cache_dir()
    out = cache / f"{digest}-{mtime}-r{_RENDER_VERSION}.html"
    if out.exists():
        return {"ok": True, "html_path": str(out)}
    cli = toolbox.detect("officecli")
    if cli.source == "missing":
        return {"ok": False, "reason": "missing"}
    cache.mkdir(parents=True, exist_ok=True)
    # 写临时路径、成功才原子改名进缓存命中路径：失败/超时留下的半截 HTML 不会被
    # 后续请求当有效缓存返回；并发渲染同一文件也不会交错写同一产物（各写各的临时
    # 文件，改名后到者胜，内容等价）
    fd, tmp_name = tempfile.mkstemp(dir=cache, suffix=".part")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        proc = _run_officecli(cli.path, src, tmp)
        if (proc.returncode != 0 or not tmp.stat().st_size) and "ICU" in (
            proc.stderr or ""
        ):
            # 无 libicu 的 Linux（slim/alpine 容器常见）：.NET 运行时启动即 Abort。
            # 降级到 invariant globalization 重试（不依赖 ICU；locale 敏感的格式化
            # 退化为固定文化，对渲染预览可接受），并记住该主机的结论避免每次双跑
            global _NEED_INVARIANT
            _NEED_INVARIANT = True
            proc = _run_officecli(cli.path, src, tmp)
        if proc.returncode != 0 or not tmp.stat().st_size:
            # 取头部：人话在 stderr 开头（如 .NET 的「Couldn't find a valid ICU
            # package…」），尾部往往是无用的调用栈
            message = (proc.stderr or proc.stdout or "").strip()[:300]
            return {"ok": False, "reason": "error", "message": message}
        if src.suffix.lower() == ".xlsx":
            with open(tmp, "a", encoding="utf-8") as f:
                f.write(_XLSX_RESIZE_JS)
        tmp.replace(out)
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "reason": "error",
            "message": f"转换超时（>{_RENDER_TIMEOUT}s）",
        }
    finally:
        tmp.unlink(missing_ok=True)
    for stale in cache.glob(f"{digest}-*.html"):
        if stale != out:
            stale.unlink(missing_ok=True)
    return {"ok": True, "html_path": str(out)}


async def dispatch_office(method: str, params: dict) -> dict:
    """执行一个 Office RPC 方法（method 已确认属于 OFFICE_METHODS）。"""
    return await asyncio.to_thread(render_office, params.get("path") or "")
