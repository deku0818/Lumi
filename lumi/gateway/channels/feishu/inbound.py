"""飞书入站消息处理：解析事件 → 派生 thread → 忙时排队合并 → 驱动一次 agent run。

支持纯文本 / post 富文本 / 图片（下载为原始 base64 块，经 stream_response 的
persist_image_blocks 统一存盘取路径，与文件附件一并由 bridge 拼 <attached-file>
标签块注入，模型用 read/vision 读）/ 文件（下载到 /tmp/lumi 经 attachments 参数传入）；回复某条消息时一并带上被回复消息里的图片/文件。每条消息经身份目录解析发送者显示名
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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lumi.agents.runtime.bg_process import cancel_thread_bg_tasks
from lumi.agents.runtime.bg_tasks import compose_notification_hint, get_task_registry
from lumi.gateway.bridge.core import available_commands
from lumi.gateway.broadcast import hub
from lumi.gateway.channels.commands import SYSTEM_COMMANDS, parse_slash_command
from lumi.gateway.channels.feishu.directory import fallback_chat_name, fallback_name
from lumi.gateway.channels.feishu.minutes import transcript_hint
from lumi.gateway.channels.feishu.outbound import run_turn
from lumi.sessions.session_meta import delete_meta, update_meta
from lumi.utils.constants import (
    FEISHU_THREAD_PREFIX,
    NOTIFICATION_POLL_INTERVAL,
    SENDER_TAG,
)
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


@dataclass(frozen=True)
class _MinuteEvent:
    """待处理的一条妙记生成事件（open_id = 订阅者，即推送对象）。"""

    token: str
    open_id: str


def feishu_thread_id(session_key: str) -> str:
    """飞书会话 key → Lumi thread_id（key 由 session_key_of 定）。"""
    return sanitize_thread_id(f"{FEISHU_THREAD_PREFIX}{session_key}")


def feishu_p2p_thread_id(open_id: str) -> str:
    """某人私聊会话的 thread —— 主动推送（妙记）只有 open_id 时的入口。

    与入站私聊同源：都以 open_id 为 key。别退回裸的 ``feishu_thread_id(open_id)``，
    那样传进去的是什么 id 在调用点无从分辨，正是两端不同源裂出两个会话的老路。
    """
    return feishu_thread_id(open_id)


def session_key_of(chat_type: str | None, chat_id: str, open_id: str) -> str:
    """一条入站消息归属的会话 key：私聊按发送者 open_id，其余一律按 chat_id。

    只有精确的 ``"p2p"`` 用 open_id——未知 chat_type（lark 声明为 Optional[str]）
    按 chat_id 保住「一 chat 一 thread」，最坏只是没合并。别和群策略的
    ``chat_type == "group"`` 并成一个谓词：那里未知类型应当响应，方向相反。
    完整取舍见 docs/architecture/feishu.md。
    """
    return open_id if chat_type == "p2p" else chat_id


def _help_line(name: str, description: str) -> str:
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
    skills = [c for c in commands if c.get("type") == "skill"]
    systems = [c for c in commands if c.get("type") != "skill"]
    lines: list[str] = []
    if skills:
        lines.append("<font color='grey'>技能命令</font>")
        lines += [_help_line(c["name"], c["description"]) for c in skills]
        lines += ["", "---", ""]
    lines.append("<font color='grey'>会话控制</font>")
    lines += [_help_line(c["name"], c["description"]) for c in systems]
    lines += [_help_line(n, d) for n, d in SYSTEM_COMMANDS.items()]
    return "\n".join(lines)


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
        # 待处理的妙记事件；由通知轮询在会话空闲时认领
        self._minute_events: list[_MinuteEvent] = []

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

            # 渠道系统命令（/stop /clear /help）：渠道层即时执行，不进 agent、不排队
            # ——/stop 恰是忙时才有意义，进队列等锁就荒谬了。
            session_key = session_key_of(chat_type, chat_id, open_id)
            parsed = parse_slash_command(text) if text else None
            if parsed and parsed[0] in SYSTEM_COMMANDS:
                await self._run_system_command(
                    parsed[0], chat_id, feishu_thread_id(session_key), message_id
                )
                return

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

            thread_id = feishu_thread_id(session_key)
            # 映射记在池上：热重载保留池但重建 inbound，通知 poller 靠它回投。
            # 存真实 chat_id（私聊的 thread key 是 open_id，但投递走 chat_id 更直接）
            ch.bridge_pool.chat_ids[thread_id] = chat_id
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

            await self._locked_drain(bridge, chat_id, thread_id, [pending])
        except Exception as e:
            logger.error(f"Feishu 消息处理失败: {e}", exc_info=True)

    async def _drain(
        self,
        ch: FeishuChannel,
        bridge,
        chat_id: str,
        thread_id: str,
        batch: list[_Pending],
    ) -> None:
        """处理 batch + 期间排队的全部消息：每轮把积压的合并成一次 agent run，直到队空。

        调用方已持本会话运行锁；期间到达的新消息因锁被占走入队，由本循环兜底取走，
        故每轮跑完都重新 pop 队列直至为空。batch 为空时直接空转（通知轮的兜底调用）。
        """
        while batch:
            await self._run_batch(ch, bridge, chat_id, thread_id, batch)
            batch = self._queues.pop(thread_id, [])

    async def _locked_drain(
        self, bridge, chat_id: str, thread_id: str, batch: list[_Pending]
    ) -> None:
        """持锁跑一轮 drain，并登记 run_tasks 供 /stop 取消。

        所有"拿锁跑用户轮"的入口统一走这里，登记不可能被漏掉（每条消息经
        run_coroutine_threadsafe 独立成 task，cancel 只杀本轮，不伤接收循环）。
        调用方判完锁空闲后到本调用的 acquire 之间不得有 await（保持忙判原子）。
        """
        pool = self.channel.bridge_pool
        async with pool.lock(thread_id):
            pool.run_tasks[thread_id] = asyncio.current_task()
            try:
                await self._drain(self.channel, bridge, chat_id, thread_id, batch)
            finally:
                pool.run_tasks.pop(thread_id, None)

    # ── 渠道系统命令 ──

    async def _run_system_command(
        self, name: str, chat_id: str, thread_id: str, message_id: str
    ) -> None:
        """执行渠道系统命令（调用方已确认 name ∈ SYSTEM_COMMANDS）。"""
        if name == "stop":
            await self._cmd_stop(chat_id, thread_id, message_id)
        elif name == "clear":
            await self._cmd_clear(chat_id, thread_id, message_id)
        elif name == "help":
            await self._cmd_help(chat_id, thread_id, message_id)
        else:
            # SYSTEM_COMMANDS 新增条目但忘写 handler：响亮失败，别静默误发 help
            logger.error(f"系统命令 /{name} 无 handler")

    async def _cmd_stop(self, chat_id: str, thread_id: str, message_id: str) -> None:
        """停止当前轮 + 本会话全部后台任务（IM 没有 desktop 的任务抽屉，这是唯一手段）。

        停到轮才清积压队列——没停到任何东西时不能丢用户排队的消息。通知 poller
        持锁跑的轮不登记 run_tasks（cancel 会杀掉整个轮询），不可中断，如实告知
        而非误报"没有任务"。被停的后台任务仍走既有收尾链（FAILED + 通知入队）。
        """
        ch = self.channel
        pool = ch.bridge_pool
        task = pool.run_tasks.get(thread_id)
        run_stopped = task is not None and not task.done()
        if run_stopped:
            # 清积压再取消：只停当前轮的话，积压消息会立刻触发新一轮
            self._queues.pop(thread_id, None)
            bridge = pool.peek(thread_id)
            if bridge is not None:
                bridge.reject_pending()  # 停轮惯用法：挂起审批先拒绝收尾，再硬取消
            task.cancel()
        bg_stopped = await cancel_thread_bg_tasks(thread_id)
        if not run_stopped and not bg_stopped:
            lock = pool.try_lock(thread_id)
            if lock is not None and lock.locked():
                await ch.send_markdown(
                    chat_id,
                    "正在处理后台任务通知，这一轮无法中断，请稍候。",
                    reply_to=message_id,
                )
            else:
                await ch.send_markdown(
                    chat_id, "当前没有正在执行的任务。", reply_to=message_id
                )
            return
        parts = []
        if run_stopped:
            parts.append("已停止当前任务")
        if bg_stopped:
            parts.append(f"已停止 {bg_stopped} 个后台任务")
        await ch.send_markdown(
            chat_id, "⏹ " + "，".join(parts) + "。", reply_to=message_id
        )
        if run_stopped:
            await self._drain_after_cancel(task, thread_id, chat_id)

    async def _drain_after_cancel(
        self, task: asyncio.Task, thread_id: str, chat_id: str
    ) -> None:
        """等被取消的轮释放锁后，接手取消窗口内入队的消息。

        cancel 只是调度 CancelledError，被取消轮的 finally 还要 await 网络收尾，
        期间到达的消息见锁忙入队——被取消的 _drain 不会再 pop 队列，不接手就
        搁浅到下条消息才被捎带。锁已被新轮占用则不管：持锁者的 _drain 会兜底。
        """
        await asyncio.wait({task}, timeout=15)
        pool = self.channel.bridge_pool
        lock = pool.try_lock(thread_id)
        bridge = pool.peek(thread_id)  # 本路径刚 cancel 过该 thread 的轮，桥必已建
        if lock is None or bridge is None or lock.locked():
            return
        if batch := self._queues.pop(thread_id, []):
            await self._locked_drain(bridge, chat_id, thread_id, batch)

    async def _cmd_clear(self, chat_id: str, thread_id: str, message_id: str) -> None:
        """清空会话（与 desktop「清空会话」同路径：delete_thread + delete_meta + 广播）。"""
        ch = self.channel
        pool = ch.bridge_pool
        bridge = await pool.get(thread_id)
        lock = pool.lock(thread_id)
        if lock.locked():
            await ch.send_markdown(
                chat_id, "正在执行任务，请先发送 /stop。", reply_to=message_id
            )
            return
        async with lock:
            await bridge.delete_thread(thread_id)
        delete_meta(thread_id)
        hub.on_channel_activity(thread_id, "feishu")
        await ch.send_markdown(chat_id, "会话已清空。", reply_to=message_id)
        # 清空期间（持锁窗口）入队的消息在此接手，不留到下条消息才被捎带
        if not lock.locked() and (batch := self._queues.pop(thread_id, [])):
            await self._locked_drain(bridge, chat_id, thread_id, batch)

    async def _cmd_help(self, chat_id: str, thread_id: str, message_id: str) -> None:
        """渠道直答命令列表卡片，不跑 agent、不为此建桥（建桥重且常驻）。

        渠道桥记忆恒开且 thread 恒为渠道前缀，available_commands(memory_enabled=True,
        channel=True) 与 bridge.list_commands() 恒等价，无需按有桥无桥分化。
        """
        await self.channel.send_markdown(
            chat_id,
            help_markdown(available_commands(memory_enabled=True, channel=True)),
            reply_to=message_id,
            title="✨ Lumi · 可用命令",
        )

    # ── 妙记生成 ──

    async def on_minute_generated(self, data: Any) -> None:
        """妙记生成事件入队，交通知轮询在会话空闲时跑纪要轮。

        payload 无 owner 字段，推送对象取 ``subscriber_ids``（订阅者即本人）。
        飞书可能重推同一事件，按 event_id 去重（与消息共用 LRU，id 空间不冲突）。
        """
        event = getattr(data, "event", None)
        token = getattr(event, "minute_token", "") if event is not None else ""
        if not token:
            return
        header = getattr(data, "header", None)
        event_id = getattr(header, "event_id", "") if header is not None else ""
        if self._is_duplicate(f"minute:{event_id or token}"):
            return
        open_ids = [
            open_id
            for sub in (getattr(event, "subscriber_ids", None) or [])
            if (open_id := getattr(sub, "open_id", ""))
        ]
        if not open_ids:
            logger.warning(f"妙记事件无 subscriber_ids，无法定位推送对象 token={token}")
            return
        for open_id in open_ids:
            self._minute_events.append(_MinuteEvent(token, open_id))

    async def _drain_minute_events(self) -> None:
        """认领待处理的妙记事件（与后台通知共用轮询节拍）。

        会话忙则跳过留到下个 tick，与通知轮同一套空闲加锁策略。单条失败只记日志，
        不影响其余事件。
        """
        if not self._minute_events:
            return
        pool = self.channel.bridge_pool
        for item in list(self._minute_events):
            token = item.token
            thread_id = feishu_p2p_thread_id(item.open_id)
            # 妙记会话常是全新 thread（用户未必私聊过 bot），需先建桥；而建桥是
            # await 点，必须在取锁判忙**之前**做完：否则 try_lock 与 acquire 之间
            # 夹着 await，锁会被入站消息抢走，而 async with 是阻塞等待（非跳过），
            # 会把整个 notification_loop 卡到那一轮跑完。
            bridge = pool.peek(thread_id)
            if bridge is None:
                try:
                    bridge = await pool.get(thread_id)
                except Exception:
                    # 建桥失败：事件留在队列，下个 tick 重试，不吞
                    logger.error(f"妙记会话建桥失败 token={token}", exc_info=True)
                    continue
            # 自此到 async with 之间无 await 点，锁不会被抢（与通知轮同一范式）
            lock = pool.try_lock(thread_id)
            if lock is None or lock.locked():
                continue  # 在跑的轮次持锁，下个 tick 再认领
            self._minute_events.remove(item)
            async with lock:
                try:
                    # 有真实 chat_id 就用，没有则 open_id 直投并回填（供通知轮认领）
                    target = pool.chat_ids.setdefault(thread_id, item.open_id)
                    await self._run_minute_turn(bridge, thread_id, target, item)
                except Exception:
                    logger.error(f"妙记纪要轮失败 token={token}", exc_info=True)

    async def _run_synthetic_turn(
        self,
        bridge,
        thread_id: str,
        chat_id: str,
        content: str,
        on_cancel: Callable[[], None],
    ) -> None:
        """跑一轮用户不可见的合成轮并把结果推回（调用方已持本会话运行锁）。

        妙记纪要与后台任务通知共用：两者都没有可回复的入站消息，流式卡片经 Create API
        直投 chat_id，用户看到的第一条就是内容本身（不再发"正在整理…"占位消息）。
        被取消（channel 停止 / 重载）时交 on_cancel 回滚待办，等新 poller 认领，不丢结果。
        """
        ch = self.channel
        try:
            await run_turn(
                ch,
                bridge,
                chat_id=chat_id,
                thread_id=thread_id,
                reply_to="",
                content=content,
                tool_mode=ch.config.tool_mode,
                synthetic=True,
            )
        except asyncio.CancelledError:
            on_cancel()
            raise
        # 本轮已入 checkpoint：通知 desktop 旁观刷新
        hub.on_channel_activity(thread_id, "feishu")

    async def _run_minute_turn(
        self, bridge, thread_id: str, target: str, event: _MinuteEvent
    ) -> None:
        """生成纪要并推送到 target（调用方已持本会话运行锁）。

        target 是投递地址（入站回填的真实 chat_id，没有则 open_id 直投），取消时
        把 event 原样塞回队列。
        """
        await self._run_synthetic_turn(
            bridge,
            thread_id,
            target,
            transcript_hint(event.token, str(lumi_tmp_dir())),
            lambda: self._minute_events.append(event),
        )

    # ── 后台任务完成通知 ──

    async def notification_loop(self) -> None:
        """后台任务完成通知 + 妙记生成事件的轮询（生命周期由 channel.start/stop 管理）。

        desktop 的 _notification_loop 对渠道会话刻意不消费（旁观连接不写共享
        thread），飞书会话的通知由本循环认领：会话空闲时持锁注入 meta 轮，让模型
        读取输出文件并把结果经流式卡片推回群里。单个 thread 失败只记日志，
        不杀轮询（否则一次网络/磁盘抖动会永久断掉所有会话的通知）。

        妙记事件复用同一节拍与同一套空闲加锁策略，但走独立队列——两者注入的提示
        与目标会话都不同，混进同一队列会串味（后台任务的措辞会带到纪要轮上）。
        """
        pool = self.channel.bridge_pool
        while True:
            await asyncio.sleep(NOTIFICATION_POLL_INTERVAL)
            try:
                await self._drain_minute_events()
            except Exception:
                logger.error("妙记事件轮询失败", exc_info=True)
            queue = get_task_registry().notification_queue
            if queue.is_empty():
                continue
            for thread_id, chat_id in list(pool.chat_ids.items()):
                if not queue.has_for(thread_id):
                    continue
                lock = pool.try_lock(thread_id)
                if lock is None or lock.locked():
                    continue  # 在跑的轮次持锁，下个 tick 再认领
                async with lock:
                    try:
                        bridge = await pool.get(thread_id)
                        await self._run_notification_turn(bridge, thread_id, chat_id)
                        # 持锁期间排队的入站消息由持锁者兜底取走
                        await self._drain(
                            self.channel,
                            bridge,
                            chat_id,
                            thread_id,
                            self._queues.pop(thread_id, []),
                        )
                    except Exception:
                        # CancelledError 是 BaseException，不会被吞，自然传播停掉轮询
                        logger.error(
                            f"Feishu 通知轮失败 thread={thread_id}", exc_info=True
                        )

    async def _run_notification_turn(
        self, bridge, thread_id: str, chat_id: str
    ) -> None:
        """认领本 thread 的完成通知并跑一轮 meta run（调用方已持本会话运行锁）。"""
        notifications = bridge.drain_notifications(thread_id)
        if not notifications:
            return

        def requeue() -> None:
            queue = get_task_registry().notification_queue
            for xml in notifications:
                queue.enqueue(xml, thread_id)

        await self._run_synthetic_turn(
            bridge,
            thread_id,
            chat_id,
            compose_notification_hint(notifications),
            requeue,
        )

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

        # 斜杠命令：仅单条成批且纯文本时识别（混批/带媒体当普通文本）；语法命中后
        # 再对照已知命令表，未知的 /xxx 照常喂模型。
        command = None
        if len(batch) == 1 and not image_refs and not file_refs:
            parsed = parse_slash_command(batch[0].text)
            if parsed and any(c["name"] == parsed[0] for c in bridge.list_commands()):
                command = parsed

        # 图片/文件各自独立、相互无依赖 → 并发下载（gather 保序），多媒体不再 N 倍延迟
        image_blocks: list[dict] = []
        if image_refs:
            results = await asyncio.gather(
                *(self._image_block(mid, ik) for mid, ik in image_refs)
            )
            image_blocks = [b for b in results if b]
        file_paths: list[str] = []
        if file_refs:
            target = inbound_dir(thread_id)
            bridge.add_folder(str(target))  # 授权该目录给本会话权限引擎
            results = await asyncio.gather(
                *(
                    self._download_file(mid, fk, fname, target)
                    for mid, fk, fname in file_refs
                )
            )
            file_paths = [p for p in results if p]

        await run_turn(
            ch,
            bridge,
            chat_id=chat_id,
            thread_id=thread_id,
            reply_to=reply_to,
            content=build_content(merged_text, image_blocks),
            tool_mode=ch.config.tool_mode,
            command=command,
            attachments=file_paths,
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
