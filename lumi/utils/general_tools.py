import asyncio
import inspect
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

from lumi.utils.logger import logger


async def abatch_tasks(
    func: Callable[..., Any],
    task_args: list[dict[str, Any] | Any],
    return_exceptions: bool = False,
    max_concurrency: int = 50,
) -> list[Any]:
    """
    通用的异步并发执行函数，支持限制并发数量

    Args:
        func: 需要异步执行的函数（必须是异步函数）
        task_args: 任务参数列表，支持两种格式：
                  - List[Dict]: 每个字典会被解包为 **kwargs 传递给 func
                  - List[Any]: 每个元素直接作为单个参数传递给 func
        return_exceptions: 是否返回异常而非抛出异常，默认为False
        max_concurrency: 最大并发数，默认为50

    Returns:
        List[Any]: 执行结果列表，如果return_exceptions=True，失败的任务会返回Exception对象

    Raises:
        ValueError: 如果func不是异步函数
        Exception: 当return_exceptions=False时，任何任务失败都会抛出异常

    Example:
        >>> # 示例1: 使用字典传递多个参数
        >>> async def process_data(url: str, timeout: int, retry: bool) -> dict:
        ...     # 处理数据
        ...     return {"url": url, "status": "ok"}
        >>>
        >>> task_params = [
        ...     {"url": "http://example.com/1", "timeout": 10, "retry": True},
        ...     {"url": "http://example.com/2", "timeout": 5, "retry": False},
        ... ]
        >>> results = await abatch_tasks(process_data, task_params, max_concurrency=10)
        >>>
        >>> # 示例2: 使用单个参数
        >>> async def fetch_data(url: str) -> dict:
        ...     return {"url": url}
        >>>
        >>> urls = ["http://example.com/1", "http://example.com/2"]
        >>> results = await abatch_tasks(fetch_data, urls, max_concurrency=10)
    """
    # 验证func是否为异步函数
    if not asyncio.iscoroutinefunction(func):
        raise ValueError(f"func必须是异步函数，当前类型: {type(func)}")

    # 如果任务列表为空，直接返回空列表
    if not task_args:
        return []

    # 使用信号量限制并发数
    semaphore = asyncio.Semaphore(max_concurrency)

    async def task_with_limit(arg):
        async with semaphore:
            # 如果参数是字典，解包为关键字参数
            if isinstance(arg, dict):
                return await func(**arg)
            # 否则直接作为单个参数传递
            else:
                return await func(arg)

    # 创建所有任务
    tasks = [task_with_limit(arg) for arg in task_args]

    # 执行任务
    results = await asyncio.gather(*tasks, return_exceptions=return_exceptions)

    return results


def get_time(func):
    """测量同步函数执行时间的装饰器"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        end = time.perf_counter()
        logger.debug(f"[性能监控] {func.__name__} 耗时: {(end - start) * 1000:.2f}ms")
        return result

    return wrapper


def aget_time(func):
    """装饰器：计时异步函数或异步生成器函数的执行时间

    Args:
        func: 要包装的异步函数或异步生成器函数

    Returns:
        包装后的函数，计时信息通过logger输出
    """
    # 如果为异步生成器函数，返回对应的包装器
    if inspect.isasyncgenfunction(func):

        @wraps(func)
        async def async_generator_wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            try:
                async for item in func(*args, **kwargs):
                    yield item
            finally:
                end_time = time.perf_counter()
                logger.debug(
                    f"{func.__name__} took {(end_time - start_time) * 1000:.2f} ms"
                )

        return async_generator_wrapper
    else:

        @wraps(func)
        async def async_function_wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                end_time = time.perf_counter()
                logger.debug(
                    f"{func.__name__} took {(end_time - start_time) * 1000:.2f} ms"
                )

        return async_function_wrapper


def retry_on_failure(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    retryable_exceptions: tuple[type[Exception], ...] | None = None,
):
    """
    同步重试装饰器

    Args:
        max_retries: 最大重试次数
        delay: 初始延迟时间（秒）
        backoff: 退避倍数
        retryable_exceptions: 可重试的异常类型元组，None 表示重试所有异常
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None
            exceptions_to_catch = retryable_exceptions or Exception

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions_to_catch as e:
                    last_exception = e
                    if attempt == max_retries:
                        logger.error(
                            f"{func.__name__} 重试 {max_retries} 次后仍然失败: {e}"
                        )
                        raise e

                    logger.warning(
                        f"{func.__name__} 第 {attempt + 1} 次尝试失败: {e}，{current_delay}秒后重试"
                    )
                    time.sleep(current_delay)
                    current_delay *= backoff

            raise last_exception

        return wrapper

    return decorator


def aretry_on_failure(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    retryable_exceptions: tuple[type[Exception], ...] | None = None,
):
    """
    异步重试装饰器

    Args:
        max_retries: 最大重试次数
        delay: 初始延迟时间（秒）
        backoff: 退避倍数
        retryable_exceptions: 可重试的异常类型元组，None 表示重试所有异常
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None
            exceptions_to_catch = retryable_exceptions or Exception

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions_to_catch as e:
                    last_exception = e
                    if attempt == max_retries:
                        logger.error(
                            f"{func.__name__} 重试 {max_retries} 次后仍然失败: {e}"
                        )
                        raise e

                    logger.warning(
                        f"{func.__name__} 第 {attempt + 1} 次尝试失败: {e}，{current_delay}秒后重试"
                    )
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff

            raise last_exception

        return wrapper

    return decorator


def cache_with_timeout(cache_duration=3600):
    """
    缓存装饰器，用于方法级别的缓存控制（同步版本）
    """

    def decorator(func):
        cache_key = f"_cache_{func.__name__}"
        timestamp_key = f"_timestamp_{func.__name__}"

        @wraps(func)
        def wrapper(self, *args, **kwargs):
            current_time = time.time()

            # 生成缓存键，包含参数信息
            args_key = str(args) + str(kwargs)
            full_cache_key = f"{cache_key}_{args_key}"
            full_timestamp_key = f"{timestamp_key}_{args_key}"

            # 获取缓存
            cache = getattr(self, full_cache_key, None)
            timestamp = getattr(self, full_timestamp_key, None)

            # 检查缓存是否有效
            if (
                cache is not None
                and timestamp is not None
                and current_time - timestamp < cache_duration
            ):
                return cache

            # 获取新数据
            result = func(self, *args, **kwargs)

            # 更新缓存
            setattr(self, full_cache_key, result)
            setattr(self, full_timestamp_key, current_time)

            return result

        return wrapper

    return decorator


def acache_with_timeout(cache_duration=3600):
    """
    异步缓存装饰器，用于方法级别的缓存控制（异步版本）
    """

    def decorator(func):
        cache_key = f"_cache_{func.__name__}"
        timestamp_key = f"_timestamp_{func.__name__}"

        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            current_time = time.time()

            # 生成缓存键，包含参数信息
            args_key = str(args) + str(kwargs)
            full_cache_key = f"{cache_key}_{args_key}"
            full_timestamp_key = f"{timestamp_key}_{args_key}"

            # 获取缓存
            cache = getattr(self, full_cache_key, None)
            timestamp = getattr(self, full_timestamp_key, None)

            # 检查缓存是否有效
            if (
                cache is not None
                and timestamp is not None
                and current_time - timestamp < cache_duration
            ):
                logger.debug(f"缓存命中: {full_cache_key}")
                return cache

            # 获取新数据
            logger.debug(f"缓存未命中: {full_cache_key}")
            result = await func(self, *args, **kwargs)

            # 更新缓存
            setattr(self, full_cache_key, result)
            setattr(self, full_timestamp_key, current_time)

            return result

        return wrapper

    return decorator
