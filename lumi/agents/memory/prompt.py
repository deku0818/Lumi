"""记忆系统提示词与索引加载。

两部分：
- :func:`build_memory_instructions`：「行为说明」——四类 taxonomy、不该存什么、
  怎么存（两步：写文件 + 在 MEMORY.md 加索引）、何时召回、推荐前先验证。追加到
  **主 agent** 系统提示词尾部（子 agent 不带）。
- :func:`load_memory_index`：读 ``MEMORY.md`` 内容（截断到行上限），供首条消息注入。
"""

from __future__ import annotations

from pathlib import Path

from lumi.agents.memory.paths import (
    ENTRYPOINT_NAME,
    memory_entrypoint,
    read_text_or_none,
)
from lumi.utils.logger import logger

MAX_INDEX_LINES = 200
"""MEMORY.md 注入上下文的行上限；超出截断并附警告（索引应保持精简）。"""


def build_memory_instructions(memory_dir: Path) -> str:
    """组装记忆行为说明（主 agent 系统提示词尾部）。"""
    return f"""# 持久记忆

你有一套基于文件的持久记忆系统，位于 `{memory_dir}`（该目录已存在，直接用 write
工具写入，无需 mkdir 或检查存在性）。请逐步积累它，让未来的对话能快速了解用户是谁、
希望你如何协作、该重复或避免哪些行为，以及工作背后的上下文。

若用户明确要求记住某事，立即按最合适的类型存下；要求忘记则找到并删除对应记忆。

## 记忆的类型

只存「无法从项目当前状态推导」的信息，分为四类：

- **user**：用户的角色、目标、专长、偏好。用于让你的回应贴合这个用户（对资深工程师
  和初学者的协作方式应不同）。
- **feedback**：用户给的工作方式指导——纠正与确认都要存。正文先写规则，再写
  **Why:**（用户给的理由/过往教训）和 **How to apply:**（何时适用），以便日后判断边界。
- **project**：进行中的工作、目标、事故等代码与 git 历史看不出的背景。把相对日期转成
  绝对日期（如「周四」→「2026-03-05」）。正文同样带 **Why:** / **How to apply:**。
- **reference**：外部系统的指针（如「bug 在 Linear 的 INGEST 项目」「监控看板地址」）。

## 不该存什么

- 代码模式、架构、文件路径、项目结构——读当前项目即可得知。
- git 历史、谁改了什么——`git log` / `git blame` 才是权威。
- 调试修复配方——修复在代码里，原因在 commit message 里。
- 已写在项目说明（LUMI.md）里的内容。
- 临时任务细节、当前对话的过程状态。

即便用户明确要求保存上述内容，也先反问「其中哪里是出乎意料 / 不显然的」，只存那部分。

## 怎么存

分两步：
1. 把每条记忆写到独立文件（如 `user_role.md`、`feedback_testing.md`），用如下 frontmatter：

```markdown
---
name: {{记忆名}}
description: {{一行描述——未来对话靠它判断相关性，要具体}}
type: {{user, feedback, project, reference}}
---

{{记忆正文；feedback/project 类型在正文后接 **Why:** 与 **How to apply:** 行}}
```

2. 在 `{ENTRYPOINT_NAME}` 里加一行指针：`- [标题](文件名.md) — 一行钩子`。
   `{ENTRYPOINT_NAME}` 是索引不是记忆，每行一条、无 frontmatter，绝不把记忆正文写进去。

- `{ENTRYPOINT_NAME}` 始终注入你的上下文；超过 {MAX_INDEX_LINES} 行会被截断，保持精简。
- 按主题（而非时间）组织；保持 name/description/type 与正文一致。
- 不写重复记忆：写新文件前先看有没有可更新的已有记忆。发现过时或错误的记忆就更新或删除。

## 何时读取记忆

- 当记忆看起来相关、或用户提及过往对话的工作时。
- 用户明确要求「记得 / 回忆 / 查一下」时**必须**读取。
- 想翻找旧记忆时，用 grep 搜记忆目录的 `*.md`（按错误信息 / 文件路径 / 函数名等窄关键词）。

## 推荐前先验证

记忆是「写入时刻的真相」。若某条记忆提到具体的文件、函数或开关，那只是它**写入时**存在
的声明——可能已被改名、删除或从未合并。在据此向用户推荐或行动前：提到文件路径就先确认
文件还在，提到函数/开关就先 grep。「记忆说 X 存在」不等于「X 现在存在」；与现状冲突时
以现状为准，并更新那条过时记忆。"""


def load_memory_index(project_dir: Path) -> str | None:
    """读 ``MEMORY.md`` 内容供注入；不存在或为空返回 None，过长则截断并附警告。"""
    content = read_text_or_none(memory_entrypoint(project_dir))
    if content is None:
        return None

    lines = content.split("\n")
    if len(lines) > MAX_INDEX_LINES:
        logger.info("[memory] MEMORY.md 超过 %d 行，注入时截断", MAX_INDEX_LINES)
        content = "\n".join(lines[:MAX_INDEX_LINES]) + (
            f"\n\n> 注意：{ENTRYPOINT_NAME} 超过 {MAX_INDEX_LINES} 行，仅加载了一部分。"
            "把索引每行控制在一行内，明细移到 topic 文件。"
        )
    return content
