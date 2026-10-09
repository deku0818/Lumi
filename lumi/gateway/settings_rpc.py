"""机器级运行设置：所有无人值守入口共享，不附着于单个任务。"""

from lumi.utils.config import GlobalConfigManager


async def get_runtime_settings(params: dict) -> dict:
    return {"unattended_tool_mode": GlobalConfigManager.load().unattended_tool_mode}


async def set_runtime_settings(params: dict) -> dict:
    mode = params.get("unattended_tool_mode")
    if mode not in ("auto", "privileged"):
        raise ValueError("unattended_tool_mode 必须为 auto 或 privileged")
    config = GlobalConfigManager.load()
    config.unattended_tool_mode = mode
    GlobalConfigManager.save(config)
    return {"unattended_tool_mode": mode}


HANDLERS = {
    "get_runtime_settings": get_runtime_settings,
    "set_runtime_settings": set_runtime_settings,
}
