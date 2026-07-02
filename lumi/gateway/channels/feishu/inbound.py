"""飞书入站消息处理：解析事件 → 派生 thread → 忙时排队合并 → 驱动一次 agent run。

支持纯文本 / post 富文本 / 图片（下载为原始 base64 块，经 stream_response 的
persist_image_blocks 统一存盘转 <attached-file>，模型用 read/vision 读）/ 文件（下载到
/tmp/lumi 经 <attached-file> 供 read 读）；回复某条消息时一并带上被回复消息里的图片/文件。每条消息经身份目录解析发送者显示名
（``channel.directory``），正文加 ``<sender>姓名</sender>`` 标签（模型分清群聊里谁说的），
并把 {sender, ts, text} 结构化写进 additional_kwargs 供 desktop 气泡渲染。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lumi.gateway.broadcast import hub
from lumi.gateway.channels.feishu.directory import fallback_chat_name, fallback_name
from lumi.gateway.channels.feishu.outbound import run_turn
from lumi.sessions.session_meta import update_meta
from lumi.utils.constants import ATTACHED_FILE_TAG, FEISHU_THREAD_PREFIX, SENDER_TAG
from lumi.utils.logger import logger
from lumi.utils.paths import lumi_tmp_dir
from lumi.utils.thread_id import sanitize_thread_id

if TYPE_CHECKING:
    from lumi.gateway.channels.feishu.channel import FeishuChannel

# 入站去重 LRU 上限（飞书可能重复推送同一 message_id）
_DEDUP_CACHE_SIZE = 1000

# 同会话忙时的消息排队上限：跑完合并成一轮处理，超出此数的丢弃并提示
_MAX_QUEUE = 10

# 群名解析失败后的重试冷却（秒）：无名群/无权限的群不必每条消息都打一次 im.chat.get
_TITLE_RETRY_COOLDOWN = 300.0

# 多条消息合并成一轮时前置的提示：告知 agent 这本是连发的几条、后面的可能更正前面，
# 只告知不写死规则（有时是补充而非覆盖）。单条不加。
_MERGE_REMINDER = (
    "<system-reminder>\n"
    "以下是用户在你处理上一条消息期间连发的 {n} 条消息，已合并为本轮，按发送先后排列。"
    "后面的可能是对前面的补充或更正，请综合判断用户当前真实的意图。\n"
    "</system-reminder>\n"
)


@dataclass
class _Pending:
    """忙时排队的一条入站消息（媒体只记引用，下载延后到持锁处理时）。"""

    text: str
    image_refs: list[tuple[str, str]] = field(
        default_factory=list
    )  # (owner_mid, image_key)
    file_refs: list[tuple[str, str, str]] = field(
        default_factory=list
    )  # (owner_mid, key, name)
    reply_to: str = ""
    sender_name: str = ""  # 发送者显示名（身份目录解析），渲染为 <sender> 标签
    ts: int = 0  # 飞书 message.create_time（毫秒），经 additional_kwargs 供 UI 渲染


def feishu_thread_id(chat_id: str) -> str:
    """飞书 chat_id → Lumi 会话 thread_id（每 chat 一个常驻 thread）。"""
    return sanitize_thread_id(f"{FEISHU_THREAD_PREFIX}{chat_id}")


def extract_post_text(content_json: dict) -> str:
    """从飞书 post（富文本）消息中提取纯文本（忽略内嵌图片，v1 不支持媒体）。"""

    def _parse_block(block: dict) -> str | None:
        if not isinstance(block, dict) or not isinstance(block.get("content"), list):
            return None
        texts: list[str] = []
        if title := block.get("title"):
            texts.append(title)
        for row in block["content"]:
            if not isinstance(row, list):
                continue
            for el in row:
                if not isinstance(el, dict):
                    continue
                tag = el.get("tag")
                if tag in ("text", "a"):
                    texts.append(el.get("text", ""))
                elif tag == "at":
                    texts.append(f"@{el.get('user_name', 'user')}")
        return " ".join(texts).strip() or None

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


def resolve_mentions(text: str, mentions: list[Any] | None) -> str:
    """把飞书的 ``@_user_n`` 占位符替换为 ``@姓名``。"""
    if not mentions or not text:
        return text
    for mention in mentions:
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


def inbound_dir(thread_id: str) -> Path:
    """本会话飞书入站文件落地目录（/tmp/lumi/feishu/<thread>/），下载文件存这里。"""
    return lumi_tmp_dir("feishu", thread_id)


def safe_filename(file_key: str, name: str) -> str:
    """生成安全落盘名：{key 前缀}_{清洗后的原名}，防路径穿越。"""
    base = os.path.basename((name or "").strip())
    base = re.sub(r"[^\w.\-]+", "_", base, flags=re.UNICODE).strip("._")
    prefix = file_key[:12]
    return f"{prefix}_{base}" if base else f"{prefix}.bin"


def _media_placeholder(m: _Pending) -> str:
    """媒体-only 消息在合并文本里的占位（保住顺序与存在感），有文本则不用它。"""
    if m.image_refs and m.file_refs:
        return "［图片 + 文件］"
    if m.image_refs:
        n = len(m.image_refs)
        return f"［图片×{n}］" if n > 1 else "［图片］"
    if m.file_refs:
        names = ", ".join(fn for _, _, fn in m.file_refs if fn) or "文件"
        return f"［文件：{names}］"
    return ""


def _body(m: _Pending) -> str:
    """消息正文（媒体-only 用占位）：模型文本与 desktop 气泡 items 共用同一推导。"""
    return m.text or _media_placeholder(m)


def _render(m: _Pending) -> str:
    """单条消息渲染：有发送者则加 <sender> 标签行；媒体-only 用占位保住存在感。

    标签是渠道无关约定（见 constants.SENDER_TAG）：纯给模型看（分清群聊里谁说的）；
    desktop 气泡从 additional_kwargs 的结构化 items 渲染，不解析此文本。
    """
    if m.sender_name:
        return f"<{SENDER_TAG}>{m.sender_name}</{SENDER_TAG}>\n{_body(m)}"
    return _body(m)


def merge_messages(batch: list[_Pending]) -> str:
    """合并一批消息的文本：单条直接返回原文（仅加发送者标签）；多条加 reminder 顺次拼接。

    <sender> 标签本身就是消息边界（每条渲染必带，发送者解析恒有兜底名），无需编号；
    媒体-only 的消息用占位保住顺序，图片/文件实体仍另行附带。
    """
    if len(batch) == 1:
        return _render(batch[0])
    return _MERGE_REMINDER.format(n=len(batch)) + "\n\n".join(_render(m) for m in batch)


def attach_files_to_text(text: str, paths: list[str]) -> str:
    """把下载好的文件路径以 <attached-file> 标签拼到正文（agent 用 read 读取）。"""
    if not paths:
        return text
    tags = "\n".join(f"<{ATTACHED_FILE_TAG}>{p}</{ATTACHED_FILE_TAG}>" for p in paths)
    return f"{text}\n{tags}" if text else tags


def build_content(text: str, image_blocks: list[dict]) -> str | list[dict]:
    """无图 → 纯文本字符串；有图 → Anthropic 多模态 content blocks（与 desktop 同构）。"""
    if not image_blocks:
        return text
    blocks: list[dict] = []
    if text:
        blocks.append({"type": "text", "text": text})
    blocks.extend(image_blocks)
    return blocks


class FeishuInbound:
    """飞书入站事件处理 collaborator。"""

    def __init__(self, channel: FeishuChannel) -> None:
        self.channel = channel
        self._seen: OrderedDict[str, None] = OrderedDict()
        # thread_id → 忙时积压的消息；由当前持锁者跑完后合并处理
        self._queues: dict[str, list[_Pending]] = {}
        # chat_id → 群名解析失败（无名群/无权限，缓存不收兜底名）后的下次重试时刻，
        # 免得这类群每条消息都白打一次 im.chat.get
        self._title_retry_at: dict[str, float] = {}

    async def _sync_session_title(
        self,
        thread_id: str,
        chat_id: str,
        chat_type: str,
        sender_name: str,
        open_id: str,
    ) -> None:
        """把群名 / 私聊对方姓名写进 session sidecar，供 desktop 会话列表显示。

        channel_title 与手动重命名的 title 分开存：手动名永久优先，群改名自动跟随。
        update_meta 内置变更检测（无变化不写盘），故可每消息无脑调用；desktop
        「清空会话」删掉 sidecar 后，下条消息也能如实重写。解析失败的兜底名
        （群_xxx / 用户_xxx）不写盘——API 抖动不该覆盖已存的真实名字。
        """
        if chat_type == "group":
            if time.monotonic() < self._title_retry_at.get(chat_id, 0.0):
                return
            title, kind = (
                await self.channel.directory.resolve_chat_name(chat_id),
                "group",
            )
            if title == fallback_chat_name(chat_id):
                self._title_retry_at[chat_id] = time.monotonic() + _TITLE_RETRY_COOLDOWN
                return
        else:
            title, kind = sender_name, "p2p"
            if title == fallback_name(open_id):
                return
        update_meta(thread_id, channel_title=title, channel_kind=kind)

    def _is_duplicate(self, msg_id: str) -> bool:
        if msg_id in self._seen:
            return True
        self._seen[msg_id] = None
        while len(self._seen) > _DEDUP_CACHE_SIZE:
            self._seen.popitem(last=False)
        return False

    async def on_message(self, data: Any) -> None:
        """处理一条飞书入站消息：解析文本后驱动该会话的一轮 agent run。"""
        ch = self.channel
        try:
            event = data.event
            message = event.message
            sender = event.sender

            message_id = message.message_id
            if self._is_duplicate(message_id):
                return
            if sender.sender_type == "bot":
                return  # 忽略机器人自己的消息
            open_id = sender.sender_id.open_id if sender.sender_id else None
            if not open_id:
                return

            chat_id = message.chat_id
            chat_type = message.chat_type
            msg_type = message.message_type

            # 白名单
            if not ch.is_allowed(open_id):
                logger.warning(f"Feishu: 拒绝来自 {open_id} 的消息（不在 allow_from）")
                return

            # 群聊策略：mention 模式下仅 @机器人 才响应
            if (
                chat_type == "group"
                and ch.config.group_policy == "mention"
                and not self._is_bot_mentioned(message)
            ):
                return

            try:
                content_json = json.loads(message.content) if message.content else {}
            except json.JSONDecodeError:
                content_json = {}

            mentions = getattr(message, "mentions", None)
            if msg_type == "text":
                text = resolve_mentions(
                    (content_json.get("text") or "").strip(), mentions
                )
            elif msg_type == "post":
                text = resolve_mentions(extract_post_text(content_json), mentions)
            else:
                text = ""  # image / file 等：正文为空，靠媒体承载

            # 媒体源 = 当前消息 +（若是回复）被回复的父消息。从每个源抽图片与文件。
            sources = [(message_id, msg_type, content_json)]
            parent_id = getattr(message, "parent_id", None)
            if parent_id:
                parent = await asyncio.get_running_loop().run_in_executor(
                    None, self._fetch_parent_sync, parent_id
                )
                if parent:
                    p_id, p_type, p_content = parent
                    try:
                        p_json = json.loads(p_content) if p_content else {}
                    except json.JSONDecodeError:
                        p_json = {}
                    sources.append((p_id, p_type, p_json))

            # 只收集媒体引用，先不下载（下载放到持锁后，避免 TOCTOU 误判忙闲）
            image_refs: list[tuple[str, str]] = []  # (owner_message_id, image_key)
            file_refs: list[tuple[str, str, str]] = []  # (owner_message_id, key, name)
            for mid, mtype, mjson in sources:
                image_refs.extend((mid, ik) for ik in image_keys_of(mtype, mjson))
                fr = file_ref_of(mtype, mjson)
                if fr:
                    file_refs.append((mid, fr[0], fr[1]))

            if not text and not image_refs and not file_refs:
                return

            # 解析发送者显示名：群聊走群成员源、私聊走通讯录源；失败/取不到退兜底名。
            # 恒有名字 → _render 的 <sender> 标签每条必带（模型的消息边界依赖它）。
            try:
                name_map = await ch.directory.resolve_senders_in_chat(
                    chat_id if chat_type == "group" else None, [open_id]
                )
                sender_name = name_map.get(open_id) or fallback_name(open_id)
            except Exception:
                logger.warning(f"Feishu: 解析发送者姓名失败 {open_id}", exc_info=True)
                sender_name = fallback_name(open_id)

            thread_id = feishu_thread_id(chat_id)
            await self._sync_session_title(
                thread_id, chat_id, chat_type, sender_name, open_id
            )
            bridge = await ch.bridge_pool.get(thread_id)
            lock = ch.bridge_pool.lock(thread_id)
            pending = _Pending(
                text,
                image_refs,
                file_refs,
                reply_to=message_id,
                sender_name=sender_name,
                ts=int(getattr(message, "create_time", 0) or 0),
            )

            # 忙判与上锁相邻、其间无 await：事件循环上原子。忙时入队（上限 _MAX_QUEUE，
            # 满则丢弃并提示），由当前持锁者跑完后合并处理；空闲则当场上锁处理。
            if lock.locked():
                queue = self._queues.setdefault(thread_id, [])
                if len(queue) >= _MAX_QUEUE:
                    await ch.send_markdown(
                        chat_id,
                        "消息有点多，这条先跳过，等我回复后再发。",
                        reply_to=message_id,
                    )
                    return
                queue.append(pending)
                return

            async with lock:
                await self._drain(ch, bridge, chat_id, thread_id, pending)
        except Exception as e:
            logger.error(f"Feishu 消息处理失败: {e}", exc_info=True)

    async def _drain(
        self, ch: FeishuChannel, bridge, chat_id: str, thread_id: str, first: _Pending
    ) -> None:
        """处理 first + 期间排队的全部消息：每轮把积压的合并成一次 agent run，直到队空。

        调用方已持本会话运行锁；期间到达的新消息因锁被占走入队，由本循环兜底取走，
        故起手与每轮跑完都重新 pop 队列直至为空。
        """
        batch = [first]
        while batch:
            await self._run_batch(ch, bridge, chat_id, thread_id, batch)
            batch = self._queues.pop(thread_id, [])

    async def _run_batch(
        self,
        ch: FeishuChannel,
        bridge,
        chat_id: str,
        thread_id: str,
        batch: list[_Pending],
    ) -> None:
        """把一批（≥1 条）积压消息合并成一次 agent run。媒体在此下载（调用方已持锁）。"""
        merged_text = merge_messages(batch)
        image_refs = [r for m in batch for r in m.image_refs]
        file_refs = [r for m in batch for r in m.file_refs]
        reply_to = batch[-1].reply_to  # 回复批次里最近一条

        # 图片/文件各自独立、相互无依赖 → 并发下载（gather 保序），多媒体不再 N 倍延迟
        image_blocks: list[dict] = []
        if image_refs:
            results = await asyncio.gather(
                *(self._image_block(mid, ik) for mid, ik in image_refs)
            )
            image_blocks = [b for b in results if b]
        if file_refs:
            target = inbound_dir(thread_id)
            bridge.add_folder(str(target))  # 授权该目录给本会话权限引擎
            results = await asyncio.gather(
                *(
                    self._download_file(mid, fk, fname, target)
                    for mid, fk, fname in file_refs
                )
            )
            merged_text = attach_files_to_text(merged_text, [p for p in results if p])

        await run_turn(
            ch,
            bridge,
            chat_id=chat_id,
            thread_id=thread_id,
            reply_to=reply_to,
            content=build_content(merged_text, image_blocks),
            tool_mode=ch.config.tool_mode,
            # 渲染数据与模型文本分离：每条原始消息的 {sender, ts, text} 结构化存进
            # additional_kwargs，desktop 气泡只读它、不反解析正文——正文里的
            # <sender> 标签纯给模型看，字面标签无法伪造气泡、也无对齐问题。
            # 媒体-only 消息同样给占位文本，避免渲染出只有人名没有内容的悬空气泡
            message_meta={
                "items": [
                    {"sender": m.sender_name, "ts": m.ts, "text": _body(m)}
                    for m in batch
                ]
            },
        )
        # 本轮已入 checkpoint：通知所有 desktop 连接刷新会话列表 / 旁观视图
        hub.on_channel_activity(thread_id, "feishu")

    def _is_bot_mentioned(self, message: Any) -> bool:
        """检查群消息是否 @ 了本机器人（@_all 或精确匹配 bot open_id）。

        不做"无 user_id 即机器人"的启发式——真人 open_id 同样 ou_ 开头、user_id 也常因
        缺权限为空，会把 @某真人误判成 @机器人。bot_open_id 取不到时只认 @_all（宁可漏判
        不误判）；故群 @ 识别依赖 start() 成功拉到 bot_open_id。
        """
        if "@_all" in (message.content or ""):
            return True
        bot_open_id = self.channel.bot_open_id
        if not bot_open_id:
            return False
        for mention in getattr(message, "mentions", None) or []:
            mid = getattr(mention, "id", None)
            if mid and (getattr(mid, "open_id", None) or "") == bot_open_id:
                return True
        return False

    # ── 媒体（图片）下载 → 原始 base64 image block（不在此压缩）──
    async def _image_block(self, message_id: str, image_key: str) -> dict | None:
        """下载飞书图片 → 原始 base64 Anthropic image block（不在此压缩）。

        图片经 stream_response 的 persist_image_blocks 统一存盘并转成 <attached-file> 路径
        引用，模型用 read/vision 按需读取（压缩在读取端由 media.py 完成一次）；故此处只下载不
        压缩，避免与读取端重复压缩。裸 base64 不会直发模型（persist 会先剥离），无 API 400 风险。
        """
        import base64

        from lumi.agents.tools.providers.filesystem.media import detect_image_format

        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            None, self._download_resource_sync, message_id, image_key, "image"
        )
        if not data:
            return None
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": detect_image_format(data),
                "data": base64.b64encode(data).decode("ascii"),
            },
        }

    async def _download_file(
        self, message_id: str, file_key: str, file_name: str, target: Path
    ) -> str | None:
        """下载飞书文件资源到 target 目录，返回落盘绝对路径。"""
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            None, self._download_resource_sync, message_id, file_key, "file"
        )
        if not data:
            return None
        path = target / safe_filename(file_key, file_name)
        try:
            await loop.run_in_executor(None, path.write_bytes, data)
        except Exception as e:
            logger.error(f"Feishu 文件写盘失败 {path}: {e}", exc_info=True)
            return None
        return str(path)

    def _download_resource_sync(
        self, message_id: str, file_key: str, rtype: str
    ) -> bytes | None:
        """同步：GetMessageResource 下载图片/文件字节。"""
        from lark_oapi.api.im.v1 import GetMessageResourceRequest

        from lumi.gateway.channels.feishu.lark_call import lark_call

        request = (
            GetMessageResourceRequest.builder()
            .message_id(message_id)
            .file_key(file_key)
            .type(rtype)
            .build()
        )
        resp = lark_call(
            f"资源下载 {rtype}",
            lambda: self.channel.client.im.v1.message_resource.get(request),
            level="error",
        )
        if resp is None:
            return None
        f = resp.file
        return f.read() if hasattr(f, "read") else f

    def _fetch_parent_sync(self, parent_id: str) -> tuple[str, str, str] | None:
        """同步：取父消息，返回 (message_id, msg_type, content_json_str)；失败 None。"""
        from lark_oapi.api.im.v1 import GetMessageRequest

        from lumi.gateway.channels.feishu.lark_call import lark_call

        request = GetMessageRequest.builder().message_id(parent_id).build()
        resp = lark_call(
            f"获取父消息 {parent_id}",
            lambda: self.channel.client.im.v1.message.get(request),
        )
        if resp is None:
            return None
        items = getattr(resp.data, "items", None) or []
        if not items:
            return None
        item = items[0]
        body = getattr(item, "body", None)
        content = (getattr(body, "content", "") if body else "") or ""
        return (
            getattr(item, "message_id", parent_id),
            getattr(item, "msg_type", "") or "",
            content,
        )
