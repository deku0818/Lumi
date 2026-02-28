import logging
import os
from pathlib import Path


class EventLoopClosedFilter(logging.Filter):
    """过滤掉"Event loop is closed"错误日志的过滤器"""

    def filter(self, record):
        return "Event loop is closed" not in record.getMessage()


class Logger:
    """日志管理类，支持控制台和文件输出。

    支持通过环境变量 LOG_LEVEL 控制日志级别，默认为 INFO。
    可选择输出日志到指定目录。

    Attributes:
        name: 日志记录器名称
        log_dir: 日志文件输出目录
        logger: logging.Logger 实例
    """

    LOG_LEVELS = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }

    def __init__(self, name: str, log_dir: str | None = None):
        """初始化日志记录器。

        Args:
            name: 日志记录器名称
            log_dir: 日志文件输出目录路径，如果不指定则只输出到控制台
        """
        self.name = name
        self.log_dir = log_dir
        self.logger = logging.getLogger(name)

        # 从环境变量获取日志级别，默认为 INFO
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        self.logger.setLevel(self.LOG_LEVELS.get(log_level, logging.INFO))

        # 清除已存在的处理器
        self.logger.handlers.clear()
        self.logger.propagate = False

        # 添加事件循环关闭错误过滤器
        self.logger.addFilter(EventLoopClosedFilter())

        # 添加控制台处理器
        self._add_console_handler()

        # 如果指定了日志目录，添加文件处理器
        if log_dir:
            self._add_file_handler()

    def _get_formatter(self) -> logging.Formatter:
        """获取统一的日志格式。"""
        return logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(module)s.%(funcName)s:%(lineno)d - %(message)s",
            "%Y-%m-%d %H:%M:%S",
        )

    def _add_console_handler(self) -> None:
        """添加控制台输出处理器。"""
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(self._get_formatter())
        self.logger.addHandler(console_handler)

    def _add_file_handler(self) -> None:
        """添加文件输出处理器。"""
        assert self.log_dir is not None
        log_dir = Path(self.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(
            filename=log_dir / f"{self.name}.log", encoding="utf-8"
        )
        file_handler.setFormatter(self._get_formatter())
        self.logger.addHandler(file_handler)

    def debug(self, msg: str, *args, **kwargs) -> None:
        self.logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs) -> None:
        self.logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs) -> None:
        self.logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs) -> None:
        self.logger.error(msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs) -> None:
        self.logger.critical(msg, *args, **kwargs)


logger = Logger("Lumi", log_dir="./logs").logger

# 也为根日志记录器添加过滤器，以防有些日志不经过我们的Logger类
root_logger = logging.getLogger()
root_logger.addFilter(EventLoopClosedFilter())

# 抑制第三方库的 INFO 级别日志噪音
NOISY_LOGGERS = ("httpx", "httpcore", "mcp")
for _name in NOISY_LOGGERS:
    logging.getLogger(_name).setLevel(logging.WARNING)

# 测试代码
if __name__ == "__main__":
    # 创建一个带文件输出的日志记录器
    logger.info("测试日志输出")
    logger.error("测试错误日志")
