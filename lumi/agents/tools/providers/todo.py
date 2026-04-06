"""Todo 工具提供者 - 任务列表管理

提供 write_todos 工具，返回 Command 对象直接更新图状态中的 todos 字段，
用于帮助 AI 代理管理和追踪复杂任务的执行进度。
"""

from __future__ import annotations

from typing import Annotated, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command
from pydantic import BaseModel, Field


class Todo(BaseModel):
    """单个任务项"""

    content: str = Field(
        description="任务内容描述（祈使句形式，如：运行测试、构建项目）"
    )
    status: Literal["pending", "in_progress", "completed"] = Field(
        description="任务状态: pending(待处理), in_progress(进行中), completed(已完成)"
    )


TODOS_DESCRIPTION = """使用此工具为当前会话创建和管理结构化任务列表。这有助于追踪进度、组织复杂任务，并向用户展示工作的完整性。它还可以帮助用户了解任务的执行进度以及整体请求的完成情况。

## 何时使用此工具
在以下场景中应主动使用此工具：

1. **复杂多步骤任务** - 当任务需要 3 个或以上不同的步骤或操作时
2. **非简单且复杂的任务** - 需要仔细规划或多次操作的任务
3. **用户明确要求** - 当用户直接要求使用 todo 列表时
4. **用户提供多个任务** - 当用户提供一组需要完成的事项（编号列表或以逗号分隔）时
5. **接收到新的指令后** - 立即将用户需求记录为待办事项
6. **开始执行某个任务时** - 在开始工作之前，将其标记为 in_progress（理想情况下，同一时间只应有一个任务处于 in_progress 状态）
7. **完成任务后** - 将其标记为 completed，并添加在实现过程中发现的任何后续任务

## 何时不使用此工具
以下情况应跳过使用此工具：

1. 只有一个单一、直接的任务
2. 任务非常简单，记录它不会带来任何组织上的收益
3. 任务可以在少于 3 个简单步骤内完成
4. 任务仅为对话型或信息型请求

注意：如果只有一个简单任务需要完成，请不要使用此工具。直接完成任务会更合适。

## 任务状态与管理

1. **任务状态**：
   - **pending**: 尚未开始的任务
   - **in_progress**: 正在进行的任务（理想情况下同一时间只有一个）
   - **completed**: 已成功完成的任务

2. **任务管理要点**：
   - 在工作过程中实时更新任务状态
   - 完成任务后立即将其标记为 completed（不要批量更新）
   - 任意时刻应有且只有一个任务处于 in_progress 状态
   - 在开始新任务前先完成当前任务
   - 将不再相关的任务从列表中完全移除

3. **任务完成要求**：
   - 仅在任务完全完成时才可将其标记为 completed
   - 如果遇到错误、阻塞或无法完成，应保持任务为 in_progress
   - 当受阻时，创建一个新任务来描述需要解决的问题
   - 在以下情况下绝不可将任务标记为 completed：
     - 测试失败
     - 实现不完整
     - 存在未解决的错误
     - 未能找到必要的文件或依赖

4. **任务拆分**：
   - 创建具体、可执行的任务项
   - 将复杂任务拆分为更小、可管理的步骤
   - 使用清晰、描述性强的任务名称

如有疑问，请使用此工具。主动进行任务管理可以体现专注度，并确保成功完成所有需求。"""


def _build_status_summary(todo_list: list[Todo]) -> str:
    """统计各状态的任务数量并生成摘要。"""
    pending = sum(1 for t in todo_list if t.status == "pending")
    in_progress = sum(1 for t in todo_list if t.status == "in_progress")
    completed = sum(1 for t in todo_list if t.status == "completed")
    return f"待处理: {pending}, 进行中: {in_progress}, 已完成: {completed}"


@tool(description=TODOS_DESCRIPTION)
def todos(
    todos: list[Todo],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """创建和管理任务列表，用于追踪当前工作进度"""
    summary = _build_status_summary(todos)

    return Command(
        update={
            "todos": todos,
            "messages": [
                ToolMessage(
                    content=f"已更新任务列表，共 {len(todos)} 项任务。{summary}",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )
