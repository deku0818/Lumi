import logging
import os
from logging.handlers import RotatingFileHandler

from lumi.utils.paths import lumi_home


class EventLoopClosedFilter(logging.Filter):
    """过滤掉 "Event loop is closed" 错误日志"""

    def filter(self, record):
        return "Event loop is closed" not in record.getMessage()


# 日志只写 <lumi_home>/logs/Lumi.log（不打控制台，免干扰终端渲染）；LOG_LEVEL 环境变量控制级别。
# 按 10MB×3 轮转：serve 常年不重启，不轮转会无界增长。同一文件还被 agent 经 LUMI_BIN
# 拉起的 lumi 子命令写着——Windows 上别的进程持有句柄时轮转会推迟到它退出，可接受
LOG_FILE = lumi_home() / "logs" / "Lumi.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(module)s.%(funcName)s:%(lineno)d - %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
)

logger = logging.getLogger("Lumi")
logger.setLevel(
    logging.getLevelNamesMapping().get(
        os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO
    )
)
logger.propagate = False
logger.addFilter(EventLoopClosedFilter())
logger.addHandler(_handler)

# 根记录器同样过滤，覆盖不经 logger 的第三方日志
logging.getLogger().addFilter(EventLoopClosedFilter())

# 抑制第三方库的 INFO 级别日志噪音
for _name in ("httpx", "httpcore", "mcp"):
    logging.getLogger(_name).setLevel(logging.WARNING)
