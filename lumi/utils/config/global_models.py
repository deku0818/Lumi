"""全局配置数据模型

定义 ~/.lumi/lumi.json 的 Pydantic 数据模型，
仅包含终端/全局层面的设置，不包含模型配置或助理配置。
"""

from typing import Literal

from pydantic import BaseModel, Field


class GlobalConfig(BaseModel):
    """全局配置数据模型

    仅包含终端/全局层面的设置，不包含模型配置或助理配置。
    """

    model_config = {"extra": "ignore"}  # 忽略未知字段，保证向后兼容

    initialized: bool = Field(
        default=False,
        description="是否已完成首次初始化引导",
    )
    theme_mode: Literal["dark", "light", "system"] = Field(
        default="system",
        description="TUI 主题模式",
    )
