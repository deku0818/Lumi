"""飞书 CardKit 流式卡片：首帧创建 → 定时器节流覆写 → 终态关闭。

FeishuChannel 把 BridgeEvent 流折叠成 ``send_delta`` 调用喂到这里。节流模型：每个 buf
配一个 :class:`Throttle`（``loop.call_later`` 主动注册定时器，即使上游静默——工具执行 /
网络抖动——也在 ``STREAM_MIN_MS`` 后把累积尾部自动刷出）+ 一个 :class:`UpdateQueue`
（合并在途更新，至多 1 in-flight，字符阈值下的激进 fire 实际 HTTP QPS 仍受单次往返限制，
不打爆飞书限流）。

健壮性两道兜底：
- **超长保护**：``buf.text`` 无上限累积，超过飞书卡片 markdown 上限会整段 update 失败、
  答案全丢；渲染前用 :func:`_render_card_text` 截到尾部窗口。
- **卡片失效恢复**：卡片被撤销 / 已 finish / CardKit content 创建失败时（错误码见
  :func:`_is_card_invalid`），换新 card_id 重建并重发全量文本，而非默默卡死。

state 只含 per-chat 的累积 buf，由主事件循环里的 ``_cleanup_loop`` 定期扫描，驱逐
``STREAM_BUF_TTL`` 秒内无更新的孤儿 buf（客户端中途放弃 / 流异常未带终态）。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from lumi.gateway.channels.feishu.throttle import Throttle
from lumi.gateway.channels.feishu.update_queue import UpdateQueue
from lumi.utils.logger import logger

if TYPE_CHECKING:
    from lumi.gateway.channels.feishu.channel import FeishuChannel

# CardKit 流式卡片里唯一 markdown element 的 id；更新/设置都按 card_id + element_id 寻址
STREAM_ELEMENT_ID = "streaming_md"

# 节流双阈值：距上次 fire 满 STREAM_MIN_MS 毫秒、或新增累计满 STREAM_MIN_CHARS 字符，
# 任一满足即刷新一次。UpdateQueue 合并兜底，实际 HTTP QPS 受单次往返限制。
STREAM_MIN_MS = 250
STREAM_MIN_CHARS = 64

# 单张卡片 markdown content 的渲染上限（字符）。飞书 element content 上限约 3 万，留足
# 余量；超出只渲染尾部窗口 + 顶部省略提示，保证 update 永不因超长失败。
STREAM_MAX_CHARS = 20000
STREAM_TRUNCATE_NOTICE = "_（内容较长，仅显示最新部分）_\n\n"

# 单张流式卡片失效后换新卡重建的次数上限，防重建风暴。
STREAM_MAX_REBUILDS = 2

# 单个 buf 闲置多久视为孤儿（秒）。客户端放弃 streaming 但未触发终态时兜底。
STREAM_BUF_TTL = 300.0

# 清扫协程的扫描周期（秒）
STREAM_CLEANUP_INTERVAL = 60.0

# 忙碌状态行的 spinner 动画帧 + 自驱刷新间隔（秒）。工具执行期间无正文 token，靠这个
# 独立定时器持续轮换帧覆写卡片，营造动效；间隔取 0.8s 压低 HTTP QPS。
TOOL_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
TOOL_ANIM_INTERVAL = 0.8

# 工具名 → 面向非技术用户的动作短语（渲染时前面接"正在"）。不求精确，传达 agent 大致
# 在做什么即可。未列出的工具（含 MCP 动态工具）回退到 _DEFAULT_TOOL_ACTION。
TOOL_FRIENDLY_ACTIONS = {
    "bash": "执行命令",
    "read": "查看文件",
    "write": "撰写文件",
    "edit": "修改文件",
    "ls": "浏览目录",
    "glob": "查找文件",
    "grep": "检索内容",
    "todos": "梳理任务",
    "ask": "请求确认",
    "cron": "安排日程",
    "workflow": "编排任务",
    "agent": "协调助手",
    "skill": "运用技能",
}
_DEFAULT_TOOL_ACTION = "处理任务"

# 流式卡片"目标已失效"——应丢弃旧 card_id、换新卡重建：230002 卡片已撤销 / 230005 /
# 230017 / 230020 卡片已结束或不可用；230099 CardKit "failed to create card content"。
# 注意 230001 是"消息内容格式错"，不在此集合，否则会掩盖真正的 schema bug。
_CARD_INVALID_CODES = {230002, 230005, 230017, 230020, 230099}


def _is_card_invalid(code: int) -> bool:
    """流式卡片是否已失效、应换新卡重建。"""
    return code in _CARD_INVALID_CODES


def _render_card_text(text: str) -> str:
    """渲染到卡片的文本：超过 ``STREAM_MAX_CHARS`` 时只保留尾部窗口 + 省略提示。"""
    if len(text) <= STREAM_MAX_CHARS:
        return text
    return STREAM_TRUNCATE_NOTICE + text[-STREAM_MAX_CHARS:]


def _status_line(buf: FeishuStreamBuf) -> str:
    """无正文输出的忙碌期状态行：执行工具时列工具名，工具间隙/等回复时显示思考中。"""
    if not buf.busy:
        return ""
    frame = TOOL_SPINNER_FRAMES[buf.anim_frame % len(TOOL_SPINNER_FRAMES)]
    if buf.active_tools:
        actions: list[str] = []
        for n in buf.active_tools:
            action = TOOL_FRIENDLY_ACTIONS.get(n, _DEFAULT_TOOL_ACTION)
            if action not in actions:  # 多个同类工具（如读两个文件）合并显示
                actions.append(action)
        return f"{frame} 正在{'、'.join(actions)}…"
    return f"{frame} 思考中…"


def _compose(buf: FeishuStreamBuf, text: str) -> str:
    """渲染到卡片的完整 markdown：正文（含超长截断）+ 忙碌状态行（非持久）。

    状态行不进 ``buf.text``，不会污染最终答案。飞书 content 字段不接受空串
    （code 99992402）：正文与状态行都空时占位单空格。
    """
    body = _render_card_text(text)
    suffix = _status_line(buf)
    if not suffix:
        return body or " "
    return f"{body}\n\n{suffix}" if body else suffix


@dataclass
class FeishuStreamBuf:
    """一次会话的 CardKit 流式卡片累积状态。

    text 始终是要写到卡片的全量 markdown（每次 update 覆盖式写入，非增量）；sequence
    严格单调递增，飞书 OpenAPI 要求后续操作 sequence 大于前次。rendered_len 记录上次成功
    渲染到卡片的 text 长度（-1 = 从未渲染成功）。reply_to_id / rebuilds / epoch 服务于
    卡片失效换卡。throttle / queue 在 buf 首次创建时装配。
    """

    text: str = ""
    chat_id: str = ""  # 无 reply 锚点时用它经 Create API 直投卡片
    card_id: str | None = None
    sequence: int = 0
    last_edit: float = 0.0
    rendered_len: int = -1
    reply_to_id: str | None = None
    rebuilds: int = 0
    epoch: int = 0
    throttle: Throttle | None = field(default=None, repr=False)
    queue: UpdateQueue | None = field(default=None, repr=False)
    active_tools: list[str] = field(default_factory=list)
    busy: bool = False
    anim_frame: int = 0
    anim_timer: asyncio.TimerHandle | None = field(default=None, repr=False)

    @property
    def dirty(self) -> bool:
        """有新内容尚未渲染到卡片（按累积 text 长度判断）。"""
        return len(self.text) != self.rendered_len


class FeishuStreaming:
    """持有每会话的 streaming 状态，供 FeishuChannel.send_delta 调用。"""

    def __init__(self, channel: FeishuChannel) -> None:
        self.channel = channel
        # 每会话一张卡，按 chat_id 做 key
        self.bufs: dict[str, FeishuStreamBuf] = {}
        self._cleanup_task: asyncio.Task[None] | None = None

    def start_cleanup(self) -> None:
        """在当前 running loop 启动孤儿 buf 清扫协程；重复调用幂等。"""
        if self._cleanup_task is not None and not self._cleanup_task.done():
            return
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(), name="feishu-streaming-cleanup"
        )

    async def stop_cleanup(self) -> None:
        """取消清扫协程并等其退出。"""
        task = self._cleanup_task
        self._cleanup_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def _cleanup_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(STREAM_CLEANUP_INTERVAL)
                await self._evict_stale()
        except asyncio.CancelledError:
            return

    async def _evict_stale(self) -> None:
        now = time.monotonic()
        stale: list[str] = [
            chat_id
            for chat_id, buf in self.bufs.items()
            if buf.last_edit > 0.0 and (now - buf.last_edit) > STREAM_BUF_TTL
        ]
        for chat_id in stale:
            buf = self.bufs.pop(chat_id, None)
            if buf is None:
                continue
            if buf.throttle is not None:
                buf.throttle.dispose()
            self._cancel_anim(buf)
            logger.warning(
                f"Feishu 流式 buf 超时驱逐 chat_id={chat_id} "
                f"idle={now - buf.last_edit:.0f}s card_id={buf.card_id}"
            )
            if buf.card_id:
                buf.sequence += 1
                try:
                    await asyncio.get_running_loop().run_in_executor(
                        None, self._close_streaming_mode_sync, buf.card_id, buf.sequence
                    )
                except Exception as e:
                    logger.warning(
                        f"Feishu 流式 buf 驱逐关卡异常 card_id={buf.card_id}: {e}"
                    )

    def _new_buf(self, chat_id: str) -> FeishuStreamBuf:
        """装配一个带节流器 / 合并队列的空 buf（正文与工具状态两条路径共用）。"""
        return FeishuStreamBuf(
            chat_id=chat_id,
            queue=UpdateQueue(),
            throttle=Throttle(
                min_ms=STREAM_MIN_MS,
                min_chars=STREAM_MIN_CHARS,
                on_fire=lambda cid=chat_id: self._on_fire(cid),
            ),
        )

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """流式片段推送：首帧创建 CardKit 卡片，后续交给定时器节流覆写。

        metadata 约定：
        - ``_stream_end=True``：终态。``_aborted=True`` 表示流异常中止，丢弃累积文本但
          仍关掉 streaming_mode；默认则 flush 全量 + 关 streaming_mode。
        - ``_tool_activity={"phase","name"}``：工具开始/结束信号，驱动忙碌状态行。
        - 其它：普通 token delta，追加到 buf.text，交节流器 ``note``。
        """
        if not self.channel.client:
            return
        meta = metadata or {}
        loop = asyncio.get_running_loop()

        if meta.get("_stream_end"):
            await self._flush_end(
                loop,
                chat_id,
                aborted=bool(meta.get("_aborted")),
                reply_to=meta.get("message_id"),
            )
            return

        activity = meta.get("_tool_activity")
        if activity is not None:
            await self._note_tool_activity(chat_id, activity, meta.get("message_id"))
            return

        if not delta:
            return
        buf = self.bufs.get(chat_id)
        if buf is None:
            buf = self._new_buf(chat_id)
            self.bufs[chat_id] = buf
        buf.text += delta

        if not buf.text.strip():
            return

        # 正文开始流出：忙碌状态行（工具/思考中）让位给正文，停 spinner。卡片已存在时
        # 立即重渲染一次抹掉状态行——否则要等 throttle 定时器才刷新，"思考中"会和首段
        # 正文重叠。卡片未建则交由下方首帧渲染。
        if buf.active_tools or buf.busy:
            buf.active_tools.clear()
            buf.busy = False
            self._cancel_anim(buf)
            if buf.card_id is not None:
                self._enqueue_render(buf)

        if buf.card_id is None:
            # message_id 可为空：无锚点时 _ensure_card 会经 Create API 直投 chat_id
            if not await self._ensure_card(buf, meta.get("message_id")):
                # 创建失败：buf.text 继续累积，下一次 delta 会再次尝试创建
                return
            # 首帧立即渲染一次，不等节流——让用户尽快看到第一段文字
            await self._push_update(buf, buf.card_id, buf.text, 1)
            return

        # 卡片已建：交定时器节流，by-design 在静默期也会自动 flush 尾部
        buf.throttle.note(len(delta))

    async def _ensure_card(self, buf: FeishuStreamBuf, message_id: str | None) -> bool:
        """buf 尚无 card 时建一张 streaming 卡。

        有 message_id 则 reply 到它，没有则直投 buf.chat_id（通知 / 妙记纪要轮）。
        """
        if buf.card_id is not None:
            return True
        buf.reply_to_id = message_id
        card_id = await asyncio.get_running_loop().run_in_executor(
            None, self._create_streaming_card_sync, buf.chat_id, message_id
        )
        if not card_id:
            return False
        buf.card_id = card_id
        buf.sequence = 1
        return True

    async def _push_update(
        self, buf: FeishuStreamBuf, card_id: str, text: str, seq: int
    ) -> tuple[bool, int]:
        """一次卡片 content 覆写：渲染（含超长截断）→ executor 调用 → 记账。"""
        loop = asyncio.get_running_loop()
        ok, code = await loop.run_in_executor(
            None, self._stream_update_text_sync, card_id, _compose(buf, text), seq
        )
        buf.last_edit = time.monotonic()
        if ok:
            buf.rendered_len = len(text)
        return ok, code

    def _on_fire(self, chat_id: str) -> None:
        """Throttle 回调（同步）：内容有变化时入队一次覆写更新。"""
        buf = self.bufs.get(chat_id)
        if buf is None or buf.card_id is None or buf.queue is None:
            return
        if not buf.text.strip() or not buf.dirty:
            return
        self._enqueue_render(buf)

    def _enqueue_render(self, buf: FeishuStreamBuf) -> None:
        """快照正文 + 递增 seq，入队一次卡片覆写（``_compose`` 自带工具状态行）。

        update 撞上卡片失效错误码时触发换卡重建；epoch 校验丢弃重建前 enqueue 的旧卡
        stale 更新。正文节流刷新、spinner 动画刷新、工具状态变更刷新共用此路径。
        """
        if buf.card_id is None or buf.queue is None:
            return
        buf.sequence += 1
        seq = buf.sequence
        card_id = buf.card_id
        epoch = buf.epoch
        text = buf.text

        async def _task() -> None:
            if buf.epoch != epoch:
                return  # 卡片已重建，这是旧卡的 stale 更新，丢弃
            ok, code = await self._push_update(buf, card_id, text, seq)
            if not ok and _is_card_invalid(code):
                await self._rebuild_card(buf)

        buf.queue.enqueue(_task)

    async def _note_tool_activity(
        self, chat_id: str, activity: dict[str, str], message_id: str | None
    ) -> None:
        """记录工具开始/结束，驱动状态行 + spinner 动画。"""
        name = activity.get("name")
        phase = activity.get("phase")
        if not name:
            return
        buf = self.bufs.get(chat_id)
        if buf is None:
            if phase != "start":
                return  # end 无对应 buf：孤立 / 迟到事件，忽略
            buf = self._new_buf(chat_id)
            self.bufs[chat_id] = buf
        if phase == "start":
            if name not in buf.active_tools:
                buf.active_tools.append(name)
        else:  # end：仅在确实跟踪着该工具时处理，避免正文已让位后迟到的 end 重启忙碌态
            if name not in buf.active_tools:
                return
            buf.active_tools.remove(name)
        buf.busy = True

        if not await self._ensure_card(buf, message_id):
            return  # 无 reply 锚点或建卡失败，等正文路径建卡后再带上状态行

        self._schedule_anim(chat_id)
        self._enqueue_render(buf)

    def _schedule_anim(self, chat_id: str) -> None:
        """（重新）注册 spinner 动画定时器，到点轮换一帧并续下一帧。"""
        buf = self.bufs.get(chat_id)
        if buf is None:
            return
        self._cancel_anim(buf)
        loop = asyncio.get_running_loop()
        buf.anim_timer = loop.call_later(
            TOOL_ANIM_INTERVAL, lambda: self._on_anim(chat_id)
        )

    @staticmethod
    def _cancel_anim(buf: FeishuStreamBuf) -> None:
        if buf.anim_timer is not None:
            buf.anim_timer.cancel()
            buf.anim_timer = None

    def _on_anim(self, chat_id: str) -> None:
        """动画定时器回调（同步）：换一帧 spinner，入队覆写，续下一帧。"""
        buf = self.bufs.get(chat_id)
        if buf is None:
            return
        buf.anim_timer = None
        if not buf.busy or buf.card_id is None or buf.queue is None:
            return
        buf.anim_frame += 1
        self._enqueue_render(buf)
        self._schedule_anim(chat_id)

    async def _rebuild_card(self, buf: FeishuStreamBuf) -> None:
        """流式卡片失效 → 换新卡并重发全量文本。

        epoch 自增使旧卡的在途 / 后续更新作废；rebuilds 上限防重建风暴。
        """
        # 无需 reply_to_id 也能重建：无锚点（通知 / 纪要轮）时靠 buf.chat_id 直投
        if buf.rebuilds >= STREAM_MAX_REBUILDS:
            return
        buf.rebuilds += 1
        buf.epoch += 1
        logger.warning(
            f"Feishu 流式卡片失效，换新卡重建（第 {buf.rebuilds} 次）: old={buf.card_id}"
        )
        loop = asyncio.get_running_loop()
        new_card = await loop.run_in_executor(
            None, self._create_streaming_card_sync, buf.chat_id, buf.reply_to_id
        )
        if not new_card:
            return
        buf.card_id = new_card
        buf.sequence = 1
        await self._push_update(buf, new_card, buf.text, 1)

    async def _flush_end(
        self,
        loop: asyncio.AbstractEventLoop,
        chat_id: str,
        *,
        aborted: bool,
        reply_to: str | None = None,
    ) -> None:
        buf = self.bufs.pop(chat_id, None)
        if not buf:
            return
        if buf.throttle is not None:
            buf.throttle.dispose()  # 停掉未触发的节流定时器
        self._cancel_anim(buf)
        had_status = buf.busy
        buf.active_tools.clear()
        buf.busy = False
        # 先 drain 掉在途 / pending 的节流更新，确保它们的 seq 都落在终态操作之前
        if buf.queue is not None:
            await buf.queue.drain()
        if not buf.card_id:
            # CardKit 全程不可用（lark 缺 cardkit 模块 / create 持续失败）：非 aborted
            # 时必须降级到普通 markdown 卡，否则整轮已完成的回复对用户完全不可见（静默丢失）。
            if not aborted:
                await self._fallback_send(
                    chat_id, buf.text, reply_to or buf.reply_to_id, "未创建"
                )
            return
        # 非 aborted：仍有未刷尾部，或还挂着忙碌状态行（had_status）时补最后一刷。
        if not aborted and (had_status or (buf.text.strip() and buf.dirty)):
            buf.sequence += 1
            # 纯工具轮（无正文 token）：直接刷 buf.text="" 会经 _compose 回落成单空格，
            # 卡片定格成空白。无正文时落一个完成标记，让用户看到这轮已做完。
            final = buf.text if buf.text.strip() else "✅ 已完成"
            await self._push_update(buf, buf.card_id, final, buf.sequence)
        buf.sequence += 1
        await loop.run_in_executor(
            None, self._close_streaming_mode_sync, buf.card_id, buf.sequence
        )
        # 整段流式从未渲染成功：CardKit 全程失败、非 aborted 时降级到普通 markdown 卡。
        if not aborted and buf.rendered_len < 0:
            await self._fallback_send(
                chat_id, buf.text, buf.reply_to_id, "全程未渲染成功"
            )

    async def _fallback_send(
        self, chat_id: str, text: str, reply_to: str | None, why: str
    ) -> None:
        """CardKit 不可用时把整轮文本降级到普通 markdown 卡。

        普通卡同样有 content 上限，超长会整段失败被吞 → 用 _render_card_text 截到尾部窗口
        （与流式路径一致），保证用户至少看到最新部分而非全丢。
        """
        if not text.strip():
            return
        logger.warning(
            f"Feishu 流式卡片{why}，降级到普通 send: chat_id={chat_id}, len={len(text)}"
        )
        try:
            await self.channel.send_markdown(
                chat_id, _render_card_text(text), reply_to=reply_to
            )
        except Exception as e:
            logger.error(
                f"Feishu 流式卡片降级 send 失败 chat_id={chat_id}: {e}", exc_info=True
            )

    def _create_streaming_card_sync(
        self, chat_id: str, reply_to_id: str | None
    ) -> str | None:
        """创建一张 streaming_mode 卡片并投递，返回 card_id。

        有 reply_to_id 走 Reply API（回复触发本轮的入站消息）；没有则经 Create API
        直投 chat_id——通知轮 / 妙记纪要轮没有可回复的入站消息，早期靠先发一条
        "正在整理…" 占位消息当锚点，那条纯噪音，现已去掉。
        """
        try:
            from lark_oapi.api.cardkit.v1 import (
                CreateCardRequest,
                CreateCardRequestBody,
            )
        except Exception as e:
            logger.warning(f"lark-oapi 缺少 cardkit 模块，流式卡片不可用: {e}")
            return None

        from lumi.gateway.channels.feishu.lark_call import lark_call

        card_json = {
            "schema": "2.0",
            "config": {
                "wide_screen_mode": True,
                "update_multi": True,
                "streaming_mode": True,
            },
            "body": {
                "elements": [
                    {"tag": "markdown", "content": "", "element_id": STREAM_ELEMENT_ID}
                ]
            },
        }
        request = (
            CreateCardRequest.builder()
            .request_body(
                CreateCardRequestBody.builder()
                .type("card_json")
                .data(json.dumps(card_json, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = lark_call(
            "CardKit create",
            lambda: self.channel.client.cardkit.v1.card.create(request),
        )
        if response is None:
            return None
        card_id = getattr(response.data, "card_id", None)
        if not card_id:
            return None
        card_payload = json.dumps({"type": "card", "data": {"card_id": card_id}})
        if reply_to_id:
            sent_mid = self.channel.reply_message_sync(
                reply_to_id, "interactive", card_payload
            )
        else:
            sent_mid = self.channel.send_message_sync(
                chat_id, "interactive", card_payload
            )
        if sent_mid is None:
            logger.warning(
                f"已创建 streaming card {card_id} 但投递失败: "
                f"reply_to={reply_to_id} chat_id={chat_id}"
            )
            return None
        return card_id

    def _stream_update_text_sync(
        self, card_id: str, content: str, sequence: int
    ) -> tuple[bool, int]:
        """覆盖式更新流式卡片 markdown element 的 content（打字机效果）。

        返回 ``(ok, raw_code)``：成功 ``(True, 0)``；失败 ``(False, 飞书错误码)``，
        调用方据 :func:`_is_card_invalid` 决定是否换卡重建。
        """
        from lark_oapi.api.cardkit.v1 import (
            ContentCardElementRequest,
            ContentCardElementRequestBody,
        )

        from lumi.gateway.channels.feishu.lark_call import lark_call_classified

        request = (
            ContentCardElementRequest.builder()
            .card_id(card_id)
            .element_id(STREAM_ELEMENT_ID)
            .request_body(
                ContentCardElementRequestBody.builder()
                .content(content)
                .sequence(sequence)
                .build()
            )
            .build()
        )
        resp, code, _reason = lark_call_classified(
            f"CardKit content 更新 card_id={card_id}",
            lambda: self.channel.client.cardkit.v1.card_element.content(request),
        )
        return resp is not None, code

    def _close_streaming_mode_sync(self, card_id: str, sequence: int) -> bool:
        """关闭卡片 streaming_mode，让会话列表的"生成中"占位消失。"""
        from lark_oapi.api.cardkit.v1 import (
            SettingsCardRequest,
            SettingsCardRequestBody,
        )

        from lumi.gateway.channels.feishu.lark_call import lark_call

        settings_payload = json.dumps(
            {"config": {"streaming_mode": False}}, ensure_ascii=False
        )
        request = (
            SettingsCardRequest.builder()
            .card_id(card_id)
            .request_body(
                SettingsCardRequestBody.builder()
                .settings(settings_payload)
                .sequence(sequence)
                .build()
            )
            .build()
        )
        return (
            lark_call(
                f"CardKit settings 关闭流式 card_id={card_id}",
                lambda: self.channel.client.cardkit.v1.card.settings(request),
            )
            is not None
        )
