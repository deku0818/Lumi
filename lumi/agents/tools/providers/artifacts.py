"""artifacts 工具提供者 - 把 Agent 产出的文件作为「制品」呈现给用户，供 desktop 前端渲染。

Lumi 是本地桌面应用，文件就在本机磁盘上，无需上传对象存储——本工具只校验路径、
收集元数据（名称 / MIME / 大小 / 类别），以 JSON 返回。desktop 前端按 ``kind``
选图标渲染文件卡片，点卡片在右侧预览面板内嵌预览（不能内嵌的兜底用系统应用打开）。
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


class ArtifactsInput(BaseModel):
    """artifacts 工具的输入参数"""

    filepaths: list[str] = Field(
        description="要呈现给用户的制品文件路径列表（用绝对路径）"
    )


ARTIFACTS_DESCRIPTION = """把你产出的文件作为「制品」呈现给用户：在 Lumi 界面里显示成文件卡片，点开即预览。

何时使用：
- 产出了用户要的成果文件之后（报告、表格、图表、生成的图片 / PDF、导出件等）——交付时一并呈现
- 一次调用传入本轮全部相关文件，不要逐个文件反复调用

何时不用：
- 只是读文件内容供自己处理
- 临时文件 / 中间产物，不打算给用户看

用户会看到什么：
- 聊天流里每个文件一张卡片：类型图标 + 文件名 + 类型/大小 + 「在文件夹中显示」
- 点卡片在右侧展开预览面板：图片 / PDF / HTML / Markdown / 文本与代码直接内嵌渲染；
  docx / xlsx / pptx 在窗口内渲染；视频 / 音频 / 其它类型给出「用系统应用打开」

约定：
- 传绝对路径；文件须在当前工作区内（受工作区边界约束，越界会被拒）
- 顺序即展示顺序，把用户最该先看的放第一个
- 只呈现、不改动文件——文件留在原路径，不复制、不上传
- 返回每个文件的 path / name / mime_type / size / kind，顺序与输入一致；
  路径不存在或不是常规文件的那项返回 error 字段"""


@tool(args_schema=ArtifactsInput, description=ARTIFACTS_DESCRIPTION)
def artifacts(filepaths: list[str]) -> str:
    """校验本地文件并返回制品元数据 JSON，供 desktop 前端渲染。"""
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
