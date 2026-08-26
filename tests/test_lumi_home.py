"""锁定 ``LUMI_CONFIG_DIR`` 对全部机器级用户数据生效（服务器/容器部署的搬家开关）。

取值点多为模块级常量（import 时求值），故必须在**子进程**里带着环境变量重新导入才
测得准——在本进程 monkeypatch env 是测不出来的（常量早已求值），那种「绿」是假的。
子进程同时验证了真实部署形态：systemd ``Environment=`` / docker ``-e`` 都是进程启动
前设好 env。

子进程的 ``HOME`` 指向一个空的假家目录：任何漏网的 ``~/.lumi`` 回退都会在那里留下
痕迹，比只断言「路径前缀对」更抓得住（也顺带保证跑测试不写真实 ``~/.lumi``）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# 探针在子进程里求值，覆盖 lumi_home() 的全部消费者：
# 密钥 / 会话 / 记忆 / 日志 / 工具箱 / hooks / 权限 / MCP / 上传 / 目录缓存。
_PROBE_SCRIPT = """
import json
from pathlib import Path

from lumi.agents.core.hooks.config_loader import _hooks_config_paths
from lumi.agents.memory import paths as memory_paths
from lumi.agents.permissions.config_loader import ConfigLoader
from lumi.agents.tools.providers.mcp import _global_mcp_config_path
from lumi.models.catalog import _cache_path
from lumi.utils.config import global_manager, user_store
from lumi.utils.config.global_models import GlobalConfig
from lumi.utils.logger import _LOG_DIR
from lumi.utils.read_config import get_config

project = Path.cwd()
probes = {
    "lumi.json": user_store.CONFIG_FILE,
    "global_dir": global_manager.GLOBAL_CONFIG_DIR,
    "uploads": global_manager.uploads_dir(),
    "checkpoints": GlobalConfig().get_checkpoint_dir(),
    "logs": _LOG_DIR,
    "memory": memory_paths.MEMORY_ROOT,
    "hooks": _hooks_config_paths(project, None)[0],
    "permissions": ConfigLoader(project)._config_paths[0],
    "mcp": _global_mcp_config_path(),
    "catalog_cache": _cache_path(),
    "toolbox_bin": get_config().bin_dir,
}
print(json.dumps({k: str(v) for k, v in probes.items()}))
"""


def test_lumi_config_dir_relocates_all_user_data(tmp_path):
    """设了 LUMI_CONFIG_DIR，机器级用户数据全部跟着搬家，假家目录一片空白。"""
    data_dir = tmp_path / "data"
    fake_home = tmp_path / "home"
    data_dir.mkdir()
    fake_home.mkdir()

    result = subprocess.run(
        [sys.executable, "-c", _PROBE_SCRIPT],
        capture_output=True,
        text=True,
        cwd=str(data_dir),  # cwd 无 .lumi/，排除配置发现链的干扰
        env={
            "LUMI_CONFIG_DIR": str(data_dir),
            "HOME": str(fake_home),
            "PYTHONPATH": str(_REPO_ROOT),  # 测工作区代码，而非 venv 里的旧副本
            "PATH": "/usr/bin:/bin",
        },
        timeout=180,
        check=True,
    )
    paths = json.loads(result.stdout.strip().splitlines()[-1])

    strays = {
        name: value
        for name, value in paths.items()
        if not Path(value).is_relative_to(data_dir)
    }
    assert not strays, f"未跟随 LUMI_CONFIG_DIR 的路径: {strays}"
    assert not list(fake_home.iterdir()), (
        f"有代码回退到了 ~/.lumi：假家目录被写入 {list(fake_home.iterdir())}"
    )
