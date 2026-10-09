"""Filesystem 工具函数 - write / edit

@tool 装饰的工具函数及其 Pydantic 输入模型。
这些工具通过 get_backend() 调用 LocalFilesystemBackend 执行底层操作。

read 工具因涉及多模态 dispatch 与测试 monkeypatch，留在包 __init__。
"""

from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from lumi.agents.tools.providers.filesystem.backend import get_backend

# ============================================================================
# Pydantic Schemas
# ============================================================================


class WriteInput(BaseModel):
    file_path: str = Field(description="要创建的文件路径")
    content: str = Field(description="要写入的文件内容")


class EditInput(BaseModel):
    file_path: str = Field(description="要编辑的文件路径")
    old_string: str = Field(description="要替换的文本(必须完全匹配)")
    new_string: str = Field(description="替换后的新文本")
    replace_all: bool = Field(default=False, description="是否替换所有匹配项")


@tool(args_schema=WriteInput)
async def write(file_path: str, content: str) -> str:
    """创建新文件并写入内容。父目录会自动创建。
    用法：
    - 如果文件已存在会报错,应使用edit修改。
    - 始终优先编辑代码库中已有的文件。除非明确要求，否则绝不要创建新文件。
    - 不要主动创建文档文件（*.md）或 README 文件。仅在用户明确要求时才创建文档文件。
    - 仅在用户明确要求时才使用表情符号。除非被要求，否则避免在文件中写入表情符号。"""
    backend = get_backend()
    result = await backend.write(file_path, content)
    return (
        f"错误: {result['error']}" if result["error"] else f"成功写入文件: {file_path}"
    )


@tool(args_schema=EditInput)
async def edit(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """对文件执行精确的字符串替换。

    用法：
    - 在进行编辑之前，你必须在当前对话中至少使用一次`read`工具。如果在未读取文件的情况下尝试编辑，此工具将返回错误。
    - 当基于 read 工具的输出编辑文本时，务必保留与其一致的缩进（制表符/空格），以 read 输出中"行号前缀"之后的内容为准。行号前缀的格式为：行号（右对齐，前导空格填充）+ 制表符。制表符之后的所有内容才是需要匹配的实际文件内容。绝不要在 old_string 或 new_string 中包含任何行号前缀的部分。
    - 始终优先编辑代码库中已有的文件。除非明确要求，否则绝不要创建新文件。
    - 仅在用户明确要求时才使用表情符号。除非被要求，否则避免在文件中添加表情符号。
    - 如果`old_string`在文件中不是唯一的，此次编辑将失败。请提供包含更多上下文的更大字符串以确保其唯一性，或使用`replace_all`来替换文件中所有出现的`old_string`。
    - 使用 `replace_all` 可在整个文件中批量替换或重命名字符串。例如，当你需要重命名变量时，这个参数非常有用。"""
    backend = get_backend()
    result = await backend.edit(file_path, old_string, new_string, replace_all)
    if result["error"]:
        return f"错误: {result['error']}"
    return f"成功编辑文件: {file_path} (替换了 {result['occurrences']} 处)"
