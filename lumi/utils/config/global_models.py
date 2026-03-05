"""全局配置数据模型

定义 ~/.lumi/lumi.json 的 Pydantic 数据模型，
仅包含终端/全局层面的设置，不包含模型配置或助理配置。
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class GlobalConfig(BaseModel):
    """全局配置数据模型

    仅包含终端/全局层面的设置，不包含模型配置或助理配置。
    """

    model_config = {"extra": "ignore", "validate_assignment": True}

    initialized: bool = Field(
        default=False,
        description="是否已完成首次初始化引导",
    )
    theme_mode: Literal["dark", "light", "system"] = Field(
        default="system",
        description="TUI 主题模式",
    )
    checkpoint_dir: str = Field(
        default="",
        description="检查点存储目录，为空时使用默认路径 ~/.lumi/checkpoints/",
    )

    def get_checkpoint_dir(self) -> Path:
        """获取检查点存储目录的绝对路径

        Returns:
            检查点目录路径，默认为 ~/.lumi/checkpoints/
        """
        if self.checkpoint_dir:
            return Path(self.checkpoint_dir).expanduser().resolve()
        return Path.home() / ".lumi" / "checkpoints"
