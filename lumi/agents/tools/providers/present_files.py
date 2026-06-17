"""present_files 工具提供者 - 让 Agent 产出的文件对用户可见，供 desktop 前端渲染。

Lumi 是本地桌面应用，文件就在本机磁盘上，无需上传对象存储——本工具只校验路径、
收集元数据（名称 / MIME / 大小 / 类别），以 JSON 返回。desktop 前端按 ``kind``
渲染（图片走缩略图，其它文件走带类型图标的卡片，点击用系统应用打开）。
"""

from __future__ import annotations

import json
import mimetypes
import os
import stat

from langchain_core.tools import tool
from pydantic import BaseModel, Field

# kind 决定前端渲染形态：image=缩略图，其余=类型图标卡片。
# 扩展名优先（mimetypes 对 .md/.ts 等返回 None），mime 兜底。
_KIND_BY_EXT: dict[str, str] = {
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".webp": "image",
    ".bmp": "image",
    ".svg": "image",
    ".pdf": "pdf",
    ".mp4": "video",
    ".mov": "video",
    ".webm": "video",
    ".mkv": "video",
    ".mp3": "audio",
    ".wav": "audio",
    ".m4a": "audio",
    ".flac": "audio",
    ".zip": "archive",
    ".tar": "archive",
    ".gz": "archive",
    ".rar": "archive",
    ".7z": "archive",
    ".doc": "doc",
    ".docx": "doc",
    ".ppt": "doc",
    ".pptx": "doc",
    ".xls": "sheet",
    ".xlsx": "sheet",
    ".csv": "sheet",
}


def _categorize(path: str, mime_type: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in _KIND_BY_EXT:
        return _KIND_BY_EXT[ext]
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("text/") or mime_type == "application/json":
        return "text"
    return "file"


class PresentFilesInput(BaseModel):
    """present_files 工具的输入参数"""

    filepaths: list[str] = Field(
        description="要展示给用户的文件路径列表（建议绝对路径）"
    )


PRESENT_FILES_DESCRIPTION = """present_files 工具使文件对用户可见，以便在桌面界面中查看、打开或下载。

何时使用：
- 在创建了应当呈现给用户的文件之后（报告、图表、导出文件等）
- 一次性呈现多个相关文件
- 让用户能在界面上直接预览（图片）或用系统应用打开文件

何时不使用：
- 你只是读取文件内容供自己处理时
- 对于临时或中间文件，不打算给用户看的

工作原理：
- 接受文件路径数组，建议使用绝对路径
- 返回每个文件的元数据（path / name / mime_type / size / kind），顺序与输入一致
- 第一个输入路径应对应用户最需要首先看到的文件
- 不存在的路径会在对应项返回 error 字段"""


@tool(args_schema=PresentFilesInput, description=PRESENT_FILES_DESCRIPTION)
def present_files(filepaths: list[str]) -> str:
    """校验本地文件并返回元数据 JSON，供 desktop 前端渲染。"""
    results = []
    for raw in filepaths:
        path = os.path.abspath(os.path.expanduser(raw))
        # 单次 stat：避免 isfile→getsize 之间文件被删的 TOCTOU（一致快照，且不会抛）
        try:
            st = os.stat(path)
        except OSError:
            results.append({"path": path, "error": "文件不存在"})
            continue
        if not stat.S_ISREG(st.st_mode):
            results.append({"path": path, "error": "不是文件"})
            continue
        mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        results.append(
            {
                "path": path,
                "name": os.path.basename(path),
                "mime_type": mime_type,
                "size": st.st_size,
                "kind": _categorize(path, mime_type),
            }
        )
    return json.dumps(results, ensure_ascii=False)
