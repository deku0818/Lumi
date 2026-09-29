"""飞书消息解析与会话派生的纯函数：content → 正文 / 媒体引用 / @ 姓名，会话 key → thread。

无 IO、不依赖渠道实例，供入站处理（``inbound``）组合使用；单测直接覆盖。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from lumi.gateway.channels.commands import (
    CHANNEL_COMMAND_NAMES,
    RUNTIME_COMMANDS,
    SYSTEM_COMMANDS,
)
from lumi.utils.thread_id import sanitize_thread_id


def feishu_thread_id(session_key: str, prefix: str) -> str:
    """飞书会话 key → Lumi thread_id（key 由 session_key_of 定）。

    prefix 取机器人的 ``config.thread_prefix``，**必传**：多机器人时同一个群对每个
    机器人各是一个会话（chat_id 相同也不撞）。给默认值等于留一把哑枪——新增推送
    入口漏传编译照过，静默把某机器人的会话并进共享的旧命名空间。
    """
    return sanitize_thread_id(f"{prefix}{session_key}")


def feishu_p2p_thread_id(open_id: str, prefix: str) -> str:
    """某人私聊会话的 thread —— 主动推送（妙记）只有 open_id 时的入口。

    与入站私聊同源：都以 open_id 为 key。别退回裸的 ``feishu_thread_id(open_id)``，
    那样传进去的是什么 id 在调用点无从分辨，正是两端不同源裂出两个会话的老路。
    """
    return feishu_thread_id(open_id, prefix)


def session_key_of(chat_type: str | None, chat_id: str, open_id: str) -> str:
    """一条入站消息归属的会话 key：私聊按发送者 open_id，其余一律按 chat_id。

    只有精确的 ``"p2p"`` 用 open_id——未知 chat_type（lark 声明为 Optional[str]）
    按 chat_id 保住「一 chat 一 thread」，最坏只是没合并。别和群策略的
    ``chat_type == "group"`` 并成一个谓词：那里未知类型应当响应，方向相反。
    完整取舍见 docs/architecture/feishu.md。
    """
    return open_id if chat_type == "p2p" else chat_id


def channel_env(
    chat_type: str | None, chat_id: str, open_id: str, title: str | None
) -> str:
    """本会话在 <env> 块里的条目行：模型据此知道自己在哪、拿得到发消息要用的 id。

    分层：一行"会话来源: 飞书" + 一级缩进子项（场景 / 群名或对方 / id），与系统那几
    行区分开。``title`` 为 None（无 im:chat:read / 解析失败）时整行省掉——兜底名
    「群_a1b2c3」不是真名，模型会当真名复述给用户。发言人**不进**这里：群里每条消息
    都在换人，写进来等于每轮 digest 变、每轮重发整块，而"谁在说话"已由 <sender>
    标签逐条带着。p2p 判定与 session_key_of 同口径（只有精确 "p2p" 算私聊）。
    """
    if chat_type == "p2p":
        sub = [
            ("场景", "私聊"),
            ("对方", title),
            ("chat_id", chat_id),
            ("对方 open_id", open_id),
        ]
    else:
        sub = [("场景", "群聊"), ("群名", title), ("chat_id", chat_id)]
    return "\n".join(["- 会话来源: 飞书"] + [f"  - {k}: {v}" for k, v in sub if v])


def help_line(name: str, description: str) -> str:
    """单条命令行：`/名字` + 描述首行（超长截断，保住每行一条的可读性）。"""
    desc = description.splitlines()[0] if description else ""
    if len(desc) > 60:
        desc = desc[:60] + "…"
    return f"`/{name}` {desc}".rstrip()


def help_markdown(commands: list[dict]) -> str:
    """/help 卡片正文：技能命令 / 会话控制两组，`/名字` code 高亮 + 灰字组标题。

    ``commands``（来自 ``list_commands``）按 ``type`` 分流：``skill`` 进「技能命令」，
    ``system``（dream / compact 等 agent 层命令）与渠道 ``SYSTEM_COMMANDS``（/stop /clear
    /help）同归「会话控制」——system 命令不是技能，不该混进技能分组。

    分割线前后必须留空行：--- 紧贴上一行会按 markdown setext 规则把前面整段
    渲染成大字标题（飞书真机如此），换行也一并被吞。
    """
    # 渠道命令遮蔽同名 bridge 命令：渠道层先拦截，同名技能/agent 层命令不可达，
    # 列表只显示真正可用的那一个（遮蔽集含 relay 命令名，与技能匹配守卫同源）
    commands = [c for c in commands if c["name"] not in CHANNEL_COMMAND_NAMES]
    skills = [c for c in commands if c.get("type") == "skill"]
    systems = [c for c in commands if c.get("type") != "skill"]
    lines: list[str] = []
    if skills:
        lines.append("<font color='grey'>技能命令</font>")
        lines += [help_line(c["name"], c["description"]) for c in skills]
        lines += ["", "---", ""]
    lines.append("<font color='grey'>会话控制</font>")
    lines += [help_line(c["name"], c["description"]) for c in systems]
    lines += [help_line(n, d) for n, d in (SYSTEM_COMMANDS | RUNTIME_COMMANDS).items()]
    return "\n".join(lines)


def extract_post_text(content_json: dict) -> str:
    """从飞书 post（富文本）消息中提取纯文本：标题独占首行，段内片段直接相连，段间换行
    （图片另由 extract_post_images 取）。"""

    def _parse_block(block: dict) -> str | None:
        if not isinstance(block, dict) or not isinstance(block.get("content"), list):
            return None
        lines: list[str] = [title] if (title := block.get("title")) else []
        for row in block["content"]:
            if not isinstance(row, list):
                continue
            parts: list[str] = []
            for el in row:
                if not isinstance(el, dict):
                    continue
                tag = el.get("tag")
                if tag in ("text", "a"):
                    parts.append(el.get("text", ""))
                elif tag == "at":
                    parts.append(f"@{el.get('user_name', 'user')}")
            lines.append("".join(parts))
        return "\n".join(lines).strip() or None

    root = content_json
    if isinstance(root, dict) and isinstance(root.get("post"), dict):
        root = root["post"]
    if not isinstance(root, dict):
        return ""
    if "content" in root and (text := _parse_block(root)):
        return text
    for key in ("zh_cn", "en_us", "ja_jp"):
        if key in root and (text := _parse_block(root[key])):
            return text
    for val in root.values():
        if isinstance(val, dict) and (text := _parse_block(val)):
            return text
    return ""


_CARD_AT_TAG = re.compile(r"<at\b[^>]*\bmention_key=(@\S+?)\s*>\s*</at>")
"""卡片 DSL 里的 @ 标签。id= 是**发送方应用**的 open_id（open_id 每应用一套，拿到我们
这侧无意义），故只保留 mention_key，交 resolve_mentions 按事件的 mentions 换成姓名。"""


def extract_card_text(content_json: dict) -> str:
    """从 interactive（卡片）消息里取正文。

    正文在 ``user_dsl``（发送方卡片 DSL 的 JSON 串）里；同级的 ``elements`` 是给老客户端
    的降级占位（一张图 +「请升级至最新版本客户端」），当正文用等于把噪音喂给模型，故只
    读 user_dsl、读不到就返回空串（该消息按无正文跳过，与改动前同）。

    机器人之间的对话几乎全是卡片（流式回答落地就是 interactive），不解析这一类等于
    「别的机器人 @ 我」整条路只通到一半。
    """
    dsl = content_json.get("user_dsl")
    if not isinstance(dsl, str):
        return ""
    try:
        card = json.loads(dsl)
    except json.JSONDecodeError:
        return ""

    texts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            content = node.get("content")
            if isinstance(content, str) and content.strip():
                texts.append(content.strip())
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(card.get("body"))
    return _CARD_AT_TAG.sub(r"\1", "\n".join(texts)).strip()


def parse_content(raw: str | None) -> dict:
    """消息 content（JSON 串）→ dict；空或非法 JSON 给空 dict。"""
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def message_text(msg_type: str, content_json: dict, mentions: list[Any] | None) -> str:
    """按消息类型取正文并把 @ 占位换成姓名；image / file 等无正文返回空串。"""
    if msg_type == "text":
        text = (content_json.get("text") or "").strip()
    elif msg_type == "post":
        text = extract_post_text(content_json)
    elif msg_type == "interactive":
        text = extract_card_text(content_json)
    else:
        return ""
    return resolve_mentions(text, mentions)


def resolve_mentions(text: str, mentions: list[Any] | None) -> str:
    """把飞书的 ``@_user_n`` 占位符替换为 ``@姓名``。"""
    if not mentions or not text:
        return text
    # 长 key 先换：@_user_1 是 @_user_10 的前缀，先换短的会把后者换坏
    for mention in sorted(
        mentions, key=lambda m: len(getattr(m, "key", "") or ""), reverse=True
    ):
        key = getattr(mention, "key", None)
        if not key or key not in text:
            continue
        name = getattr(mention, "name", None) or key
        text = text.replace(key, f"@{name}")
    return text


def extract_post_images(content_json: dict) -> list[str]:
    """递归取出 post 富文本里所有内嵌图片的 image_key。"""
    keys: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("tag") == "img" and node.get("image_key"):
                keys.append(node["image_key"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for it in node:
                walk(it)

    walk(content_json)
    return keys


def image_keys_of(msg_type: str, content_json: dict) -> list[str]:
    """按消息类型取出其可下载图片的 image_key（image / post）。"""
    if msg_type == "image":
        ik = content_json.get("image_key")
        return [ik] if ik else []
    if msg_type == "post":
        return extract_post_images(content_json)
    return []


def file_ref_of(msg_type: str, content_json: dict) -> tuple[str, str] | None:
    """file 消息 → (file_key, file_name)；否则 None。"""
    if msg_type == "file":
        fk = content_json.get("file_key")
        if fk:
            return (fk, content_json.get("file_name") or "")
    return None


def safe_filename(file_key: str, name: str) -> str:
    """生成安全落盘名：{file_key}_{清洗后的原名}，防路径穿越。

    用完整 key：飞书 key 的前缀几乎是固定的，截断会让不同文件同名时互相覆盖。"""
    base = os.path.basename((name or "").strip())
    base = re.sub(r"[^\w.\-]+", "_", base, flags=re.UNICODE).strip("._")
    return f"{file_key}_{base}" if base else f"{file_key}.bin"


def build_content(text: str, image_blocks: list[dict]) -> str | list[dict]:
    """无图 → 纯文本字符串；有图 → Anthropic 多模态 content blocks（与 desktop 同构）。"""
    if not image_blocks:
        return text
    blocks: list[dict] = []
    if text:
        blocks.append({"type": "text", "text": text})
    blocks.extend(image_blocks)
    return blocks
