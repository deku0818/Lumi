"""飞书"成员 / 群信息"获取的唯一出口：统一 SDK 调用 + 两层缓存 + 启动预热。

对外只暴露解析方法，底层 SDK 调用统一经 ``lark_call``（分页、错误日志一致）。

``open_id → 显示名`` 有两个数据源，能力不同：

- **群成员接口**（``im.v1.chat_members.get``）：返回群内显示名，**不受**通讯录
  可见范围限制 → 群场景首选，且天然覆盖预热后才入群的新人。
- **通讯录接口**（``contact.v3.users.batch``）：受可见范围限制 → 私聊等无群上下文
  时的退路。

两个数据源共享同一个 ``open_id → 名`` 缓存：飞书群成员名即用户显示名，与通讯录名
一致，无需按来源分层。群场景未命中只刷群成员、不回退通讯录——群成员接口已覆盖全员，
群里却查不到的人（可见范围外）通讯录通常也查不到，回退收益低。

缓存命中即不调 API；解析不到用兜底名（``用户_xxxxxx`` / ``群_xxxxxx``）且不写缓存，
保留下次重试机会。进程内缓存，重启即清空（启动预热重建）。

需要应用权限：``im:chat``（列群 / 群成员 / 群信息）、``contact:user.base:readonly``
（通讯录批量）。
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

from lumi.gateway.channels.feishu.caching import CachingDirectory
from lumi.gateway.channels.feishu.lark_call import lark_call
from lumi.utils.logger import logger

USER_BATCH_SIZE = 50  # contact.users.batch 单次上限
PAGE_SIZE = 100  # 列群 / 群成员分页大小
# 群成员刷新冷却：成功后整群已写缓存，60s 内无需重刷
MEMBER_REFRESH_COOLDOWN = 60.0
# 群成员刷新拿到空结果（瞬时抖动 / 无权限）后的退避：首次很短，让瞬时失败快速恢复；
# 持续失败指数退避到上限，避免权限不足的群每条消息刷整群刷屏
MEMBER_BACKOFF_MIN = 5.0
MEMBER_BACKOFF_MAX = 300.0

T = TypeVar("T")


def fallback_name(open_id: str | None) -> str:
    """open_id 解析不到时的占位名。"""
    if not open_id:
        return "用户_unknown"
    return f"用户_{open_id[-6:]}"


def fallback_chat_name(chat_id: str | None) -> str:
    """chat_id 解析不到时的占位群名。"""
    if not chat_id:
        return "群_unknown"
    return f"群_{chat_id[-6:]}"


class FeishuDirectory:
    """per-channel 的成员 / 群信息门面；HTTP client 在 channel start 后注入。"""

    def __init__(self) -> None:
        self._client: Any = None
        self._users: CachingDirectory[str, str] = CachingDirectory()  # open_id→名
        self._chats: CachingDirectory[str, str] = CachingDirectory()  # chat_id→群名
        self._member_cooldown: dict[str, float] = {}  # chat_id → 下次可刷成员的时刻
        self._member_backoff: dict[str, float] = {}  # chat_id → 当前失败退避秒数
        self._refresh_lock = threading.Lock()

    def set_client(self, client: Any) -> None:
        self._client = client

    # ------------------------------------------------------------------
    # 注入（预热 / 测试）
    # ------------------------------------------------------------------

    def prime_user(self, open_id: str, name: str) -> None:
        self._users.prime(open_id, name)

    # ------------------------------------------------------------------
    # 对外解析
    # ------------------------------------------------------------------

    async def resolve_chat_name(self, chat_id: str) -> str:
        """群名；缓存未命中则调一次 ``im.chat.get``。"""
        if not chat_id:
            return fallback_chat_name(chat_id)
        out = await self._chats.resolve(
            [chat_id], self._fetch_chat_names, fallback_chat_name
        )
        return out[chat_id]

    async def resolve_users(self, open_ids: list[str]) -> dict[str, str]:
        """通讯录源解析 open_id → 名（私聊 / 无群上下文场景）。"""
        return await self._users.resolve(open_ids, self._fetch_users, fallback_name)

    async def resolve_senders_in_chat(
        self, chat_id: str | None, open_ids: list[str]
    ) -> dict[str, str]:
        """群场景解析：未命中者用群成员源补全（不受可见范围、覆盖新人）。

        没有 chat_id（私聊等）时退回通讯录源。
        """
        if not chat_id:
            return await self.resolve_users(open_ids)
        return await self._users.resolve(
            open_ids,
            lambda ids: self._fetch_members_for(chat_id, ids),
            fallback_name,
        )

    # ------------------------------------------------------------------
    # 启动预热
    # ------------------------------------------------------------------

    async def warmup(self) -> None:
        """拉 bot 所在所有群 + 群成员灌入缓存，大幅减少运行时兜底名。

        在 executor 里串行拉取（启动多花几秒）；任何异常只记 warning、不阻断启动。
        """
        if self._client is None:
            return
        try:
            chats, members = await asyncio.get_running_loop().run_in_executor(
                None, self._warmup_sync
            )
        except Exception:
            logger.warning("Feishu 预热失败，跳过（不阻断启动）", exc_info=True)
            return
        self._chats.prime_many(chats)
        self._users.prime_many(members)
        logger.info(f"Feishu 预热完成：群 {len(chats)} 个、成员 {len(members)} 人")

    def _warmup_sync(self) -> tuple[dict[str, str], dict[str, str]]:
        chats = self._fetch_chats()  # [(chat_id, 群名|None)]，含无名群
        chat_names = {cid: name for cid, name in chats if name}
        members: dict[str, str] = {}
        for cid, _ in chats:  # 无名群成员同样预热
            members.update(self._fetch_chat_members(cid))  # dict 天然去重
        return chat_names, members

    # ------------------------------------------------------------------
    # 底层 SDK 读取（同步，统一经 lark_call）
    # ------------------------------------------------------------------

    def _paginate(
        self,
        page_call: Callable[[str | None], Any],
        extract: Callable[[Any], T | None],
    ) -> list[T]:
        """通用分页：``page_call(page_token)`` 返回已 ``lark_call`` 包裹的 resp（失败为
        None），``extract(item)`` 把单条 item 转成结果或 None。汇总所有页的非 None 项。
        """
        out: list[T] = []
        page_token: str | None = None
        while True:
            resp = page_call(page_token)
            if resp is None:
                break
            for it in getattr(resp.data, "items", None) or []:
                v = extract(it)
                if v is not None:
                    out.append(v)
            if not getattr(resp.data, "has_more", False):
                break
            page_token = getattr(resp.data, "page_token", None)
        return out

    def _fetch_users(self, open_ids: list[str]) -> dict[str, str]:
        """通讯录批量：``contact.v3.users.batch``，按 50 分片。"""
        if self._client is None:
            return {}
        from lark_oapi.api.contact.v3 import BatchUserRequest

        out: dict[str, str] = {}
        for start in range(0, len(open_ids), USER_BATCH_SIZE):
            chunk = open_ids[start : start + USER_BATCH_SIZE]
            req = (
                BatchUserRequest.builder()
                .user_ids(chunk)
                .user_id_type("open_id")
                .build()
            )
            resp = lark_call(
                "contact.users.batch",
                lambda r=req: self._client.contact.v3.user.batch(r),
            )
            if resp is None:
                continue
            for item in getattr(resp.data, "items", None) or []:
                oid = getattr(item, "open_id", None)
                name = getattr(item, "name", None)
                if oid and name:
                    out[oid] = name
        return out

    def _fetch_chat_names(self, chat_ids: list[str]) -> dict[str, str]:
        """逐个 ``im.chat.get`` 取群名（无批量接口）。"""
        if self._client is None:
            return {}
        from lark_oapi.api.im.v1 import GetChatRequest

        out: dict[str, str] = {}
        for cid in chat_ids:
            req = GetChatRequest.builder().chat_id(cid).build()
            resp = lark_call(
                "im.chat.get", lambda r=req: self._client.im.v1.chat.get(r)
            )
            if resp is None:
                continue
            name = getattr(resp.data, "name", None)
            if name:
                out[cid] = name
        return out

    def _fetch_chats(self) -> list[tuple[str, str | None]]:
        """列出 bot 所在的所有群（分页，含无名群）：``im.v1.chat.list``。

        返回 ``(chat_id, 群名|None)``——无名群也保留 chat_id，供预热拉其成员。
        """
        if self._client is None:
            return []
        from lark_oapi.api.im.v1 import ListChatRequest

        def page_call(token: str | None) -> Any:
            builder = ListChatRequest.builder().page_size(PAGE_SIZE)
            if token:
                builder = builder.page_token(token)
            req = builder.build()
            return lark_call(
                "im.chat.list", lambda r=req: self._client.im.v1.chat.list(r)
            )

        def extract(it: Any) -> tuple[str, str | None] | None:
            cid = getattr(it, "chat_id", None)
            return (cid, getattr(it, "name", None)) if cid else None

        return self._paginate(page_call, extract)

    def _fetch_chat_members(self, chat_id: str) -> dict[str, str]:
        """列出群成员（分页）：``im.v1.chat_members.get``，open_id → 群内显示名。"""
        if self._client is None:
            return {}
        from lark_oapi.api.im.v1 import GetChatMembersRequest

        def page_call(token: str | None) -> Any:
            builder = (
                GetChatMembersRequest.builder()
                .chat_id(chat_id)
                .member_id_type("open_id")
                .page_size(PAGE_SIZE)
            )
            if token:
                builder = builder.page_token(token)
            req = builder.build()
            return lark_call(
                "im.chat_members.get",
                lambda r=req: self._client.im.v1.chat_members.get(r),
            )

        def extract(it: Any) -> tuple[str, str] | None:
            oid = getattr(it, "member_id", None)
            name = getattr(it, "name", None)
            return (oid, name) if oid and name else None

        return dict(self._paginate(page_call, extract))

    def _fetch_members_for(self, chat_id: str, open_ids: list[str]) -> dict[str, str]:
        """群成员源补全：刷一次该群成员，整群结果交给 ``resolve`` 写缓存。

        带 per-chat 冷却防狂刷：成功后冷却 60s（整群已缓存）；拿到空结果（瞬时抖动 /
        无权限）按指数退避，瞬时失败几秒后即可重试，持续失败渐增到上限。
        ``open_ids`` 不再用于过滤——返回整群让缓存惠及后续其他发言人。
        """
        now = time.monotonic()
        with self._refresh_lock:
            if now < self._member_cooldown.get(chat_id, 0.0):
                return {}
        members = self._fetch_chat_members(chat_id)
        with self._refresh_lock:
            if members:
                self._member_backoff.pop(chat_id, None)
                self._member_cooldown[chat_id] = now + MEMBER_REFRESH_COOLDOWN
            else:
                prev = self._member_backoff.get(chat_id, 0.0)
                backoff = (
                    min(prev * 2, MEMBER_BACKOFF_MAX) if prev else MEMBER_BACKOFF_MIN
                )
                self._member_backoff[chat_id] = backoff
                self._member_cooldown[chat_id] = now + backoff
        return members
