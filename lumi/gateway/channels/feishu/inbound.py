"""飞书入站消息处理：解析事件 → 派生 thread → 进收件箱 → 空闲开轮 / 忙时在工具边界插话。

支持纯文本 / post 富文本 / 图片（下载为原始 base64 块，经 stream_response 的
persist_image_blocks 统一存盘取路径，与文件附件一并由 bridge 拼 <attached-file>
标签块注入，模型用 read/vision 读）/ 文件（下载到 /tmp/lumi 经 attachments 参数传入）；
回复某条消息时一并带上被回复消息里的图片/文件，其正文以「> 」引用块只进模型侧（气泡
不显示）。每条消息经身份目录解析发送者显示名
（``channel.directory``），正文加 ``<sender>姓名</sender>`` 标签（模型分清群聊里谁说的），
并把 {sender, ts, text} 结构化写进 additional_kwargs 供 desktop 气泡渲染。
content 解析与会话派生的纯函数在 ``parse``。
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lumi.agents.core.inbox import InboxFullError
from lumi.agents.runtime.bg_process import cancel_thread_bg_tasks
from lumi.agents.runtime.bg_tasks import compose_notification_hint, get_task_registry
from lumi.gateway.bridge.core import available_commands
from lumi.gateway.broadcast import hub
from lumi.gateway.channels.commands import (
    CHANNEL_COMMAND_NAMES,
    RUNTIME_COMMANDS,
    SYSTEM_COMMANDS,
    is_model_arg,
    parse_slash_command,
)
from lumi.gateway.channels.config import resolve_ref
from lumi.gateway.channels.feishu.directory import (
    fallback_bot_name,
    fallback_chat_name,
    fallback_name,
)
from lumi.gateway.channels.feishu.minutes import transcript_hint
from lumi.gateway.channels.feishu.outbound import run_turn
from lumi.gateway.channels.feishu.parse import (
    build_content,
    channel_env,
    feishu_p2p_thread_id,
    feishu_thread_id,
    file_ref_of,
    help_markdown,
    image_keys_of,
    message_text,
    parse_content,
    safe_filename,
    session_key_of,
)
from lumi.models import provider_store
from lumi.models.manager import allowed_levels
from lumi.sessions import session_model
from lumi.sessions.session_meta import delete_meta, update_meta
from lumi.sessions.thread_runs import (
    ThreadDeletingError,
    ThreadRunLock,
    preserve_poller,
    thread_runs,
)
from lumi.utils.constants import NOTIFICATION_POLL_INTERVAL, SENDER_TAG
from lumi.utils.logger import logger
from lumi.utils.paths import lumi_tmp_dir

if TYPE_CHECKING:
    from lumi.gateway.channels.feishu.channel import FeishuChannel

# 入站去重 LRU 上限（飞书可能重复推送同一 message_id）
_DEDUP_CACHE_SIZE = 1000

# 群名解析失败后的重试冷却（秒）：无名群/无权限的群不必每条消息都打一次 im.chat.get
_TITLE_RETRY_COOLDOWN = 300.0


@dataclass
class _Pending:
    """一条解析好的入站消息（媒体只记引用，进收件箱前才下载）。"""

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
    quote: str = ""  # 被回复消息的正文：只进模型侧（引用块），气泡不显示


@dataclass(frozen=True)
class _MinuteEvent:
    """待处理的一条妙记生成事件（open_id = 订阅者，即推送对象）。"""

    token: str
    open_id: str
    run_lock: ThreadRunLock | None = field(default=None, compare=False, repr=False)
    revision: int = field(default=0, compare=False, repr=False)

    @property
    def expired(self) -> bool:
        return self.run_lock is not None and (
            self.run_lock.deleting or self.run_lock.revision != self.revision
        )


def _provider_models() -> list[tuple[str, str, str]]:
    """已配置的全部模型：(profile_id, profile_name, model) 列表（一次读盘）。"""
    profiles, _ = provider_store.load()
    return [(p.id, p.name, m) for p in profiles for m in p.models]


def _model_listing(entries: list[tuple[str, str, str]]) -> list[str]:
    """/model 卡片的可选模型段：灰字组标题 + 每行 `模型` — 连接名。"""
    return [
        "<font color='grey'>可选模型</font>",
        *(f"`{m}` — {name}" for _, name, m in entries),
    ]


def inbound_dir(thread_id: str) -> Path:
    """本会话飞书入站文件落地目录（/tmp/lumi/feishu/<thread>/），下载文件存这里。"""
    return lumi_tmp_dir("feishu", thread_id)


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


def _quoted(m: _Pending, body: str) -> str:
    """被回复消息的正文以「> 」引用块前置到 body——只用于模型侧文本，气泡 items 不走这里。"""
    if not m.quote:
        return body
    return "\n".join(f"> {line}" for line in m.quote.splitlines()) + f"\n{body}"


def _render(m: _Pending) -> str:
    """单条消息渲染：有发送者则加 <sender> 标签行；媒体-only 用占位保住存在感。

    标签是渠道无关约定（见 constants.SENDER_TAG）：纯给模型看（分清群聊里谁说的）；
    desktop 气泡从 additional_kwargs 的结构化 items 渲染，不解析此文本。
    """
    body = _quoted(m, _body(m))
    if m.sender_name:
        return f"<{SENDER_TAG}>{m.sender_name}</{SENDER_TAG}>\n{body}"
    return body


def _message_meta(m: _Pending) -> dict:
    """显示声明：{sender, ts, text} 结构化存进 additional_kwargs，desktop 气泡只读它、
    不反解析正文——正文里的 <sender> 标签纯给模型看，字面标签无法伪造气泡。媒体-only
    消息同样给占位文本，避免渲染出只有人名没有内容的悬空气泡。"""
    return {"items": [{"sender": m.sender_name, "ts": m.ts, "text": _body(m)}]}


class FeishuInbound:
    """飞书入站事件处理 collaborator。"""

    def __init__(self, channel: FeishuChannel) -> None:
        self.channel = channel
        self._seen: OrderedDict[str, None] = OrderedDict()
        # thread_id → 正在（等锁 / 持锁）跑空本会话收件箱的 task：已有它在，新到的消息
        # 进收件箱即可，由它注入当前轮或轮后另起一轮，不必再派一个等锁者
        self._draining: dict[str, asyncio.Task] = {}
        # chat_id → 群名解析失败（无名群/无权限，缓存不收兜底名）后的下次重试时刻，
        # 免得这类群每条消息都白打一次 im.chat.get
        self._title_retry_at: dict[str, float] = {}

    async def _resolve_title(
        self, chat_id: str, chat_type: str | None, sender_name: str, open_id: str
    ) -> str | None:
        """本会话的显示名：群名 / 私聊对方姓名；解析不到（或在退避冷却里）返回 None。

        每条消息只解析一次，sidecar 与 <env> 块共用这一个结果——两处各解析一次会绕开
        下面的退避冷却，把无名群刷成每消息一次 API。兜底名（群_xxx / 用户_xxx）一律
        当作"没解析到"：写盘会让 API 抖动覆盖真名，喂模型会被当真名复述给用户。
        """
        if chat_type != "p2p":
            if time.monotonic() < self._title_retry_at.get(chat_id, 0.0):
                return None
            title = await self.channel.directory.resolve_chat_name(chat_id)
            if title == fallback_chat_name(chat_id):
                self._title_retry_at[chat_id] = time.monotonic() + _TITLE_RETRY_COOLDOWN
                return None
            return title
        if sender_name in (fallback_name(open_id), fallback_bot_name(open_id)):
            return None
        return sender_name

    def _sync_session_title(
        self, thread_id: str, chat_type: str | None, title: str | None
    ) -> None:
        """把会话显示名写进 session sidecar，供 desktop 会话列表显示。

        channel_title 与手动重命名的 title 分开存：手动名永久优先，群改名自动跟随。
        update_meta 内置变更检测（无变化不写盘），故可每消息无脑调用；desktop
        「清空会话」删掉 sidecar 后，下条消息也能如实重写。
        """
        if title:
            update_meta(
                thread_id,
                channel_title=title,
                channel_kind="p2p" if chat_type == "p2p" else "group",
            )

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
            open_id = sender.sender_id.open_id if sender.sender_id else None
            if not open_id:
                return
            # 只挡自己（而非按 sender_type == "bot" 一刀切）：别的机器人 @ 本机器人是
            # 正当来源（需开 im:message.group_at_msg.include_bot:readonly），按 bot 全拒
            # 会让这条路整个哑掉。飞书声明自己发的消息不回推，这里仍留一道——真回推了
            # 就是无限自问自答。
            if open_id == ch.bot_open_id:
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

            content_json = parse_content(message.content)
            # image / file 等正文为空，靠媒体承载
            text = message_text(
                msg_type, content_json, getattr(message, "mentions", None)
            )

            # 渠道系统命令（/stop /clear /help）：渠道层即时执行，不进 agent、不排队
            # ——/stop 恰是忙时才有意义，进队列等锁就荒谬了。
            session_key = session_key_of(chat_type, chat_id, open_id)
            thread_id = feishu_thread_id(session_key, ch.config.thread_prefix)
            parsed = parse_slash_command(text) if text else None
            if parsed and parsed[0] in SYSTEM_COMMANDS:
                await self._run_system_command(
                    parsed[0], chat_id, thread_id, message_id
                )
                return
            # /model、/effort：本会话热切换。值不像切换操作的（中文/多词自然语言）
            # 不拦，按「未知命令照常喂模型」的既有契约走
            if parsed and parsed[0] in RUNTIME_COMMANDS and is_model_arg(parsed[1]):
                runtime_cmd = (
                    self._cmd_session_model
                    if parsed[0] == "model"
                    else self._cmd_session_effort
                )
                await runtime_cmd(parsed[1], chat_id, thread_id, message_id)
                return

            # 媒体源 = 当前消息 +（若是回复）被回复的父消息。从每个源抽图片与文件。
            sources = [(message_id, msg_type, content_json)]
            quote = ""
            parent_id = getattr(message, "parent_id", None)
            if parent_id:
                parent = await asyncio.get_running_loop().run_in_executor(
                    None, self._fetch_parent_sync, parent_id
                )
                if parent:
                    p_type = parent.msg_type or ""
                    p_json = parse_content(parent.body.content if parent.body else "")
                    sources.append((parent.message_id or parent_id, p_type, p_json))
                    # 父消息正文作引用只进模型侧：群聊 mention 模式下它通常不在会话
                    # 历史里（「回复并 @机器人 总结」）。本机器人自己发的已在历史，跳过
                    if not (
                        parent.sender
                        and parent.sender.id == resolve_ref(ch.config.app_id)
                    ):
                        quote = message_text(p_type, p_json, parent.mentions)

            # 只收集媒体引用，先不下载（下载放到持锁后，避免 TOCTOU 误判忙闲）
            image_refs: list[tuple[str, str]] = []  # (owner_message_id, image_key)
            file_refs: list[tuple[str, str, str]] = []  # (owner_message_id, key, name)
            for mid, mtype, mjson in sources:
                image_refs.extend((mid, ik) for ik in image_keys_of(mtype, mjson))
                fr = file_ref_of(mtype, mjson)
                if fr:
                    file_refs.append((mid, fr[0], fr[1]))

            if not text and not image_refs and not file_refs:
                # 唯一会静默吞掉整条消息的分支，留一行日志：不解析的消息类型（表情包、
                # 无 user_dsl 的卡片…）从这里消失，没这行只能从"机器人不理我"倒查
                logger.info(f"Feishu: 跳过无正文消息 msg_type={msg_type}")
                return

            # 解析发送者显示名：真人走群成员源/通讯录源，bot 走消息反查应用名
            # （群成员/通讯录都不覆盖 bot）；失败/取不到退兜底名。
            # 恒有名字 → _render 的 <sender> 标签每条必带（模型的消息边界依赖它）。
            is_bot = getattr(sender, "sender_type", "user") != "user"
            try:
                if is_bot:
                    sender_name = await ch.directory.resolve_bot_sender(
                        open_id, message_id
                    )
                else:
                    name_map = await ch.directory.resolve_senders_in_chat(
                        chat_id if chat_type == "group" else None, [open_id]
                    )
                    sender_name = name_map.get(open_id) or fallback_name(open_id)
            except Exception:
                logger.warning(f"Feishu: 解析发送者姓名失败 {open_id}", exc_info=True)
                sender_name = (
                    fallback_bot_name(open_id) if is_bot else fallback_name(open_id)
                )

            title = await self._resolve_title(chat_id, chat_type, sender_name, open_id)
            self._sync_session_title(thread_id, chat_type, title)
            await self._admit(
                chat_id,
                thread_id,
                _Pending(
                    text,
                    image_refs,
                    file_refs,
                    reply_to=message_id,
                    sender_name=sender_name,
                    ts=int(getattr(message, "create_time", 0) or 0),
                    quote=quote,
                ),
                env=channel_env(chat_type, chat_id, open_id, title),
            )
        except Exception as e:
            logger.error(f"Feishu 消息处理失败: {e}", exc_info=True)

    async def _admit(
        self,
        chat_id: str,
        thread_id: str,
        pending: _Pending,
        env: str = "",
    ) -> None:
        """把一条消息交给本会话，与 desktop 同一套插话规则（见 agents/core/inbox.py）：
        进收件箱；用户轮在跑则由图在下一个工具边界注入，否则（空闲 / 系统轮持锁）由
        跑空收件箱的 task 拿锁后另起一轮。上限满则丢弃并提示。

        跑空者（``_draining``）至多一个：首个发现没有跑空者的消息由自己的 task 等锁接手
        ——持锁的不一定是用户轮（妙记轮 / 每日整理 / 删会话 / 通知轮），靠持锁者兜底会让
        消息搁浅到下一条消息才被捎带。技能命令插不进别的轮：忙时提示，空闲当场跑。
        """
        pool = self.channel.bridge_pool
        # 映射记在池上：热重载保留池但重建 inbound，通知 poller 靠它回投。
        # 存真实 chat_id（私聊的 thread key 是 open_id，但投递走 chat_id 更直接）
        pool.chat_ids[thread_id] = chat_id
        lock = thread_runs.lock_for(thread_id)
        revision = lock.revision
        bridge = await pool.get(thread_id)
        if lock.deleting or lock.revision != revision:
            return  # 建桥期间已清空会话，旧消息不再开轮。
        if env:
            # 每条消息无脑覆盖（同 chat 恒等值，群改名即刷新）
            bridge.set_env_extra(env)
        command = self._skill_command(bridge, pending)
        if command is not None:
            if lock.locked():
                await self._send_busy_hint(chat_id, pending.reply_to)
                return
            await self._locked_run(bridge, chat_id, thread_id, (command, pending))
            return
        try:
            await self._interject(bridge, thread_id, pending)
        except InboxFullError:
            await self.channel.send_markdown(
                chat_id,
                "消息有点多，等我回复后再发。",
                reply_to=pending.reply_to,
                title="⏳ 这条先跳过",
                template="orange",
            )
            return
        if lock.deleting or lock.revision != revision:
            # 下载期间会话被清空：收件箱已随删除清场，这条不能留给新会话
            bridge.withdraw_interjection(pending.reply_to)
            return
        # 进箱与跑空者判定之间无 await；正在取消的旧任务不会再取箱，新消息另派等锁者
        draining = self._draining.get(thread_id)
        if draining is not None and not draining.cancelling():
            return
        await self._locked_run(bridge, chat_id, thread_id)

    def _skill_command(self, bridge, m: _Pending) -> tuple[str, str] | None:
        """纯文本且命中已知技能命令时返回 (name, extra)；未知的 /xxx 照常当普通消息。

        渠道命令遮蔽同名技能：漏到这里的形态（如自然语言 /model）也不落入同名技能，
        遮蔽到底，按普通文本喂模型。
        """
        if m.image_refs or m.file_refs:
            return None
        parsed = parse_slash_command(m.text)
        if (
            parsed
            and parsed[0] not in CHANNEL_COMMAND_NAMES
            and any(c["name"] == parsed[0] for c in bridge.list_commands())
        ):
            return parsed
        return None

    async def _interject(self, bridge, thread_id: str, m: _Pending) -> None:
        """下载媒体、按普通用户消息构造后放进收件箱。消息 id 直接用飞书 message_id：
        注入后的回复卡片据此锚到这条消息上。"""
        # 图片/文件各自独立、相互无依赖 → 并发下载（gather 保序），多媒体不再 N 倍延迟
        image_blocks: list[dict] = []
        if m.image_refs:
            results = await asyncio.gather(
                *(self._image_block(mid, ik) for mid, ik in m.image_refs)
            )
            image_blocks = [b for b in results if b]
        file_paths: list[str] = []
        if m.file_refs:
            target = inbound_dir(thread_id)
            results = await asyncio.gather(
                *(
                    self._download_file(mid, fk, fname, target)
                    for mid, fk, fname in m.file_refs
                )
            )
            file_paths = [p for p in results if p]
        await bridge.interject(
            build_content(_render(m), image_blocks),
            attachments=file_paths,
            message_id=m.reply_to,
            message_meta=_message_meta(m),
        )

    async def _locked_run(
        self,
        bridge,
        chat_id: str,
        thread_id: str,
        command: tuple[tuple[str, str], _Pending] | None = None,
    ) -> None:
        """持锁跑空本会话收件箱（可选先跑一条技能命令轮），登记 run_tasks 供 /stop 取消。

        所有"拿锁跑用户轮"的入口统一走这里，登记不可能被漏掉（每条消息经
        run_coroutine_threadsafe 独立成 task，cancel 只杀本轮，不伤接收循环）。
        每轮跑完都重查收件箱：没赶上工具边界的插话在此另起一轮。末次判空到登记注销
        之间无 await，期间进箱的消息必然看到跑空者仍在、或看到它已退出而自己接手。
        调用方判完锁空闲后到本调用的 acquire 之间不得有 await（保持忙判原子）。
        """
        pool = self.channel.bridge_pool
        me = asyncio.current_task()
        self._draining[thread_id] = me
        try:
            async with pool.lock(thread_id):
                if pool.closed:
                    return
                pool.run_tasks[thread_id] = me
                try:
                    if command is not None:
                        cmd, m = command
                        await run_turn(
                            self.channel,
                            bridge,
                            chat_id=chat_id,
                            reply_to=m.reply_to,
                            command=cmd,
                            message_meta=_message_meta(m),
                        )
                        hub.on_channel_activity(thread_id, "feishu")
                    while bridge.pending_interjections():
                        await run_turn(
                            self.channel, bridge, chat_id=chat_id, interjections=True
                        )
                        # 本轮已入 checkpoint：通知 desktop 连接刷新会话列表 / 旁观视图
                        hub.on_channel_activity(thread_id, "feishu")
                finally:
                    pool.run_tasks.pop(thread_id, None)
        except (asyncio.CancelledError, ThreadDeletingError):
            if thread_runs.is_deleting(thread_id):
                bridge.take_interjections()
            raise
        finally:
            if self._draining.get(thread_id) is me:
                del self._draining[thread_id]

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

    async def _cmd_session_model(
        self, value: str, chat_id: str, thread_id: str, message_id: str
    ) -> None:
        """普通模式的 /model：裸敲看本会话生效模型 + 可选列表，带完整模型名只切本会话。

        写的是会话级覆盖（sidecar 落盘），下一轮开跑前经 ``align_session_model`` 对齐
        ——只动本会话，别的群 / desktop 都不受影响。``/model default`` 落回新会话默认。
        """
        ch = self.channel
        if not value:
            current = session_model.resolve(thread_id)
            await ch.send_markdown(
                chat_id,
                "\n".join(
                    [
                        f"当前：`{current.model}`",
                        "",
                        *_model_listing(_provider_models()),
                    ]
                ),
                reply_to=message_id,
                title="🧠 会话模型",
                note="/model 完整模型名 切换（仅本会话）· /model default 恢复默认",
            )
            return
        if value == "default":
            session_model.set_model(thread_id, "", "")
            await ch.send_markdown(
                chat_id,
                f"模型：`{session_model.resolve(thread_id).model}`",
                reply_to=message_id,
                title="✅ 已恢复默认",
                template="green",
                note="下一条消息生效 · 仅本会话",
            )
            return
        entries = _provider_models()
        q = value.lower()
        matches = [e for e in entries if e[2].lower() == q]
        if not matches:
            await ch.send_markdown(
                chat_id,
                "\n".join([f"没有叫 `{value}` 的模型。", "", *_model_listing(entries)]),
                reply_to=message_id,
                title="❌ 未找到模型",
                template="red",
            )
            return
        # 同名模型可能挂在多个连接下：连接偏好交给 resolve()（指定 profile 精确
        # 命中 > active 优先按名反查），不在这里重复实现它的契约
        model = matches[0][2]
        pid = provider_store.resolve(model).provider
        pname = next((n for i, n, _ in matches if i == pid), matches[0][1])
        session_model.set_model(thread_id, model, pid)
        await ch.send_markdown(
            chat_id,
            f"模型：`{model}`（{pname}）",
            reply_to=message_id,
            title="✅ 已切换",
            template="green",
            note="下一条消息生效 · 仅本会话",
        )

    async def _cmd_session_effort(
        self, value: str, chat_id: str, thread_id: str, message_id: str
    ) -> None:
        """普通模式的 /effort：裸敲看本会话档位 + 该模型可选档位，带值只改本会话。

        档位依附模型（``/model`` 换模型即清），故可选项按本会话当前模型现算——列出
        端点不认的档位只会换来一次 400。auto 恒可选（= 清除会话档位、跟随模型默认），
        但 ``allowed_levels`` 对多数形态本就以 auto 打头，去重保序免得列出两个 auto。
        """
        ch = self.channel
        current = session_model.resolve(thread_id)
        levels = list(dict.fromkeys(["auto", *allowed_levels(current.model)]))
        options = "可选：" + " ".join(f"`{lv}`" for lv in levels)
        if not value:
            # 未设会话级档位时生效的是该模型在 profile 里的档位（desktop 选择器写的），
            # 不是 auto——报 auto 会让「明明设过 high」看起来没生效
            effective = (
                current.effort
                or provider_store.resolve(current.model, current.provider).effort
            )
            await ch.send_markdown(
                chat_id,
                "\n".join(
                    [
                        f"当前：`{effective}`（模型 `{current.model}`）",
                        "",
                        options,
                    ]
                ),
                reply_to=message_id,
                title="🧠 思考档位",
                note="/effort 档位 切换（仅本会话）· auto = 跟随模型默认",
            )
            return
        if value not in levels:
            await ch.send_markdown(
                chat_id,
                f"`{current.model}` 没有 `{value}` 这个档位。\n\n{options}",
                reply_to=message_id,
                title="❌ 档位无效",
                template="red",
            )
            return
        session_model.set_effort(thread_id, value)
        await ch.send_markdown(
            chat_id,
            f"档位：`{value}`",
            reply_to=message_id,
            title="✅ 已切换",
            template="green",
            note="下一条消息生效 · 仅本会话",
        )

    async def _cmd_stop(self, chat_id: str, thread_id: str, message_id: str) -> None:
        """停止当前轮 + 本会话全部后台任务（IM 没有 desktop 的任务抽屉，这是唯一手段）。

        停到轮才清收件箱（未注入的插话，与 desktop 同：不会被执行，并如实告知）——没停
        到任何东西时不能丢用户的消息。通知 poller 持锁跑的轮不登记 run_tasks（cancel
        会杀掉整个轮询），不可中断，如实告知而非误报"没有任务"。被停的后台任务仍走
        既有收尾链（FAILED + 通知入队）。
        """
        ch = self.channel
        pool = ch.bridge_pool
        task = pool.run_tasks.get(thread_id)
        run_stopped = task is not None and not task.done()
        dropped = 0
        if run_stopped:
            bridge = pool.peek(thread_id)
            if bridge is not None:
                # 先清收件箱再取消：只停当前轮的话，未注入的插话会立刻另起一轮
                dropped = len(bridge.take_interjections())
                bridge.reject_pending()  # 停轮惯用法：挂起审批先拒绝收尾，再硬取消
            if not task.cancelling():
                # 已在取消收尾中的轮不重复 cancel：第二发会把 run_turn 从收尾中
                # 打出去提前放锁，下一轮与游离的写回并发（同 desktop 守卫）
                task.cancel()
        bg_stopped = await cancel_thread_bg_tasks(thread_id)
        if not run_stopped and not bg_stopped:
            lock = pool.try_lock(thread_id)
            if lock is not None and lock.locked():
                await ch.send_markdown(
                    chat_id,
                    "正在处理后台任务通知，这一轮无法中断，请稍候。",
                    reply_to=message_id,
                    title="⏳ 暂时无法中断",
                    template="orange",
                )
            else:
                await ch.send_markdown(
                    chat_id,
                    "当前没有正在执行的任务。",
                    reply_to=message_id,
                    title="💤 当前空闲",
                    template="blue",
                )
            return
        parts = []
        if run_stopped:
            parts.append("已停止当前任务")
        if bg_stopped:
            parts.append(f"已停止 {bg_stopped} 个后台任务")
        if dropped:
            parts.append(f"另有 {dropped} 条未处理的消息已丢弃")
        await ch.send_markdown(
            chat_id,
            "，".join(parts) + "。",
            reply_to=message_id,
            title="⏹ 已停止",
            template="green",
        )

    async def _cmd_clear(self, chat_id: str, thread_id: str, message_id: str) -> None:
        """清空会话（与 desktop「清空会话」同路径：delete_thread + delete_meta + 广播）。"""
        ch = self.channel
        pool = ch.bridge_pool
        bridge = await pool.get(thread_id)
        lock = pool.lock(thread_id)
        if lock.locked():
            await self._send_busy_hint(chat_id, message_id)
            return
        async with lock:
            await bridge.delete_thread(thread_id)
        # 连会话级模型覆盖一并清（同存一条 meta）：thread_id 确定性派生，
        # 不清则「全新对话」下一条消息又静默套回旧覆盖
        delete_meta(thread_id)
        hub.on_channel_activity(thread_id, "feishu")
        await ch.send_markdown(
            chat_id,
            "下一条消息将开启全新对话。",
            reply_to=message_id,
            title="✅ 会话已清空",
            template="green",
        )

    async def _send_busy_hint(self, chat_id: str, message_id: str) -> None:
        """撞上在跑的轮的操作（/clear）：提示先停。"""
        await self.channel.send_markdown(
            chat_id,
            "正在执行任务，请先发送 /stop 再试。",
            reply_to=message_id,
            title="⏳ 请先停止任务",
            template="orange",
        )

    async def _cmd_help(self, chat_id: str, thread_id: str, message_id: str) -> None:
        """渠道直答命令列表卡片，不跑 agent、不为此建桥（建桥重且常驻）。

        渠道桥记忆恒开且 thread 恒为渠道前缀，available_commands(memory_enabled=True,
        channel=True) 与 bridge.list_commands() 恒等价，无需按有桥无桥分化。
        """
        await self.channel.send_markdown(
            chat_id,
            help_markdown(
                available_commands(
                    memory_enabled=True,
                    channel=True,
                    workspace=self.channel.config.workspace,
                )
            ),
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
            thread_id = feishu_p2p_thread_id(open_id, self.channel.config.thread_prefix)
            lock = thread_runs.lock_for(thread_id)
            if not lock.deleting:
                self.channel.bridge_pool.minute_events.append(
                    _MinuteEvent(token, open_id, lock, lock.revision)
                )

    async def _drain_minute_events(self) -> None:
        """认领待处理的妙记事件（与后台通知共用轮询节拍）。

        会话忙则跳过留到下个 tick，与通知轮同一套空闲加锁策略。单条失败只记日志，
        不影响其余事件。
        """
        pool = self.channel.bridge_pool
        for item in list(pool.minute_events):
            if item.expired:
                pool.minute_events.remove(item)
                continue
            token = item.token
            thread_id = feishu_p2p_thread_id(
                item.open_id, self.channel.config.thread_prefix
            )
            # 妙记会话常是全新 thread（用户未必私聊过 bot），需先建桥；而建桥是
            # await 点，必须在取锁判忙**之前**做完：否则 try_lock 与 acquire 之间
            # 夹着 await，锁会被入站消息抢走，而 async with 是阻塞等待（非跳过），
            # 会把整个 notification_loop 卡到那一轮跑完。
            bridge = pool.peek(thread_id)
            if bridge is None:
                try:
                    bridge = await pool.get(thread_id)
                except Exception:
                    # 建桥失败出队不重试：持久故障不会自愈，留队只会每个 tick 刷一条堆栈
                    pool.minute_events.remove(item)
                    logger.error(f"妙记会话建桥失败 token={token}", exc_info=True)
                    continue
            if item.expired:
                pool.minute_events.remove(item)
                continue
            # 自此到 async with 之间无 await 点，锁不会被抢（与通知轮同一范式）
            lock = pool.try_lock(thread_id)
            if lock is None or lock.locked():
                continue  # 在跑的轮次持锁，下个 tick 再认领
            pool.minute_events.remove(item)
            async with preserve_poller(lock):
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
                reply_to="",
                content=content,
                synthetic=True,
            )
        except asyncio.CancelledError:
            if not thread_runs.is_deleting(thread_id):
                on_cancel()  # 停机/重载可重投，主动删除则丢弃旧通知。
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
            lambda: self.channel.bridge_pool.minute_events.append(event),
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
                async with preserve_poller(lock):
                    try:
                        bridge = await pool.get(thread_id)
                        # 持锁期间到达的消息留在收件箱，由跑空者等锁接手（见 _admit）
                        await self._run_notification_turn(bridge, thread_id, chat_id)
                    except Exception:
                        # 普通异常仅跳过本轮；删除取消由 preserve_poller 吸收，停机取消传播。
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
        """下载飞书文件资源到 target 目录，返回落盘绝对路径；失败 None。"""
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
            logger.error(f"Feishu 资源写盘失败 {path}: {e}", exc_info=True)
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

    def _fetch_parent_sync(self, parent_id: str) -> Any | None:
        """同步：取父消息（lark ``Message``：msg_type / body / mentions / sender）；失败 None。"""
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
        return items[0] if items else None
