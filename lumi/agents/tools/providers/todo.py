"""旧 todo 数据的读取兼容；工具已移除。

保留 Todo 的原模块路径，供已有 checkpoint 反序列化使用。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Todo(BaseModel):
    """单个任务项"""

    content: str = Field(
        description="任务内容描述（祈使句形式，如：运行测试、构建项目）"
    )
    status: Literal["pending", "in_progress", "completed"] = Field(
        description="任务状态: pending(待处理), in_progress(进行中), completed(已完成)"
    )
