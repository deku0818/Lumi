"""全局配置数据模型

定义 ~/.lumi/lumi.json 中 "settings" 分区的 Pydantic 数据模型，
仅包含终端/全局层面的设置，不包含模型配置或助理配置。
"""

from pathlib import Path

from pydantic import BaseModel, Field

from lumi.utils.paths import lumi_home


class GlobalConfig(BaseModel):
    """全局配置数据模型

    仅包含终端/全局层面的设置，不包含模型配置或助理配置。
    """

    model_config = {"extra": "ignore", "validate_assignment": True}

    checkpoint_dir: str = Field(
        default="",
        description="LangGraph checkpoint 与会话元数据的存储目录，为空时用 ~/.lumi/checkpoints/",
    )

    def get_checkpoint_dir(self) -> Path:
        """获取检查点存储目录的绝对路径

        Returns:
            检查点目录路径，默认为 ~/.lumi/checkpoints/
        """
        if self.checkpoint_dir:
            return Path(self.checkpoint_dir).expanduser().resolve()
        return lumi_home() / "checkpoints"
