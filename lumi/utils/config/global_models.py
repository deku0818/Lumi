"""全局配置数据模型

定义 ~/.lumi/lumi.json 的 Pydantic 数据模型，
仅包含终端/全局层面的设置，不包含模型配置或助理配置。
"""

from pathlib import Path

from pydantic import BaseModel, Field


class GlobalConfig(BaseModel):
    """全局配置数据模型

    仅包含终端/全局层面的设置，不包含模型配置或助理配置。
    """

    model_config = {"extra": "ignore", "validate_assignment": True}

    checkpoint_dir: str = Field(
        default="",
        description="检查点存储目录，为空时使用默认路径 ~/.lumi/checkpoints/",
    )
    max_checkpoints: int = Field(
        default=20,
        description="单个 thread 最多保留的 checkpoint 数量",
    )
    stale_thread_days: int = Field(
        default=30,
        description="自动清理超过指定天数未更新的 checkpoint thread 目录，0 表示不清理",
    )

    def get_checkpoint_dir(self) -> Path:
        """获取检查点存储目录的绝对路径

        Returns:
            检查点目录路径，默认为 ~/.lumi/checkpoints/
        """
        if self.checkpoint_dir:
            return Path(self.checkpoint_dir).expanduser().resolve()
        return Path.home() / ".lumi" / "checkpoints"
