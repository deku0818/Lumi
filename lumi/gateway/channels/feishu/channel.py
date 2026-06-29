"""飞书 / Lark Channel：lark-oapi WebSocket 长连接接入。

- 不需要公网 webhook；消息经 WS 推到本地，由 SDK 回调分发
- 发送 / patch 消息走 HTTP SDK（同步 API，用线程池包裹避免阻塞事件循环）
- lark WS ``Client.start()`` 阻塞且模块级抓 ``asyncio.get_event_loop()``，故跑在独立
  daemon 线程 + 独立 loop，并 patch 模块级 loop；入站事件经 ``run_coroutine_threadsafe``
  投回主事件循环喂 AgentBridge

每个飞书 chat 派生一个常驻会话 thread（见 :class:`BridgePool`），全程 ``tool_mode``
取配置值（auto / privileged），泄漏的人工审批一律自动拒绝；ask 工具已禁用（不弹询问卡片）。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import threading
from contextlib import suppress
from typing import Any

from lumi.gateway.channels.config import FeishuChannelConfig
from lumi.gateway.channels.feishu.bridge_pool import BridgePool
from lumi.gateway.channels.feishu.directory import FeishuDirectory
from lumi.gateway.channels.feishu.inbound import FeishuInbound
from lumi.gateway.channels.feishu.lark_call import lark_call
from lumi.gateway.channels.feishu.streaming import FeishuStreaming
from lumi.utils.logger import logger

FEISHU_AVAILABLE = importlib.util.find_spec("lark_oapi") is not None


def _markdown_card(content: str) -> str:
    """一次性 markdown 卡片 JSON（非流式，用于错误提示 / 流式降级）。"""
    card = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "update_multi": True},
        "body": {"elements": [{"tag": "markdown", "content": content}]},
    }
    return json.dumps(card, ensure_ascii=False)


class FeishuChannel:
    """飞书（Lark）Channel，基于 WebSocket 长连接。

    飞书开发者后台需：创建应用并开启机器人能力、启用事件订阅并订阅
    ``im.message.receive_v1``、拿 ``app_id`` / ``app_secret`` 填入配置。
    """

    name = "feishu"

    def __init__(
        self, config: FeishuChannelConfig, bridge_pool: BridgePool | None = None
    ) -> None:
        self.config = config
        self._client: Any = None
        self._ws_client: Any = None
        self._ws_thread: threading.Thread | None = None
        self._ws_loop: asyncio.AbstractEventLoop | None = (
            None  # lark WS 专属 loop（停它以中断 start）
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bot_open_id: str | None = None
        self._warmup_task: asyncio.Task | None = None  # 持引用防 GC，stop() 取消
        self._running = False
        self._error: str | None = (
            None  # 启动失败原因（未装 lark / 缺凭证 / 异常）→ UI 状态灯
        )
        # 会话池由 ChannelManager 注入并跨传输重连复用；独立构造（如测试）时自建一个。
        self.bridge_pool = bridge_pool or BridgePool(config.workspace)
        self.inbound = FeishuInbound(self)
        self.streaming = FeishuStreaming(self)
        # 成员/群名解析（open_id → 显示名）：client 在 start() 注入，发送者前缀靠它解析。
        self._directory = FeishuDirectory()

    # ── 暴露给子模块的只读句柄 ──
    @property
    def client(self) -> Any:
        return self._client

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    @property
    def bot_open_id(self) -> str | None:
        return self._bot_open_id

    @property
    def directory(self) -> FeishuDirectory:
        return self._directory

    def status(self) -> dict:
        """运行状态，供 UI 状态灯：error（启动失败）/ connecting / connected / stopped。

        connected 以 lark WS 的实际连接（``_conn`` 非空，断连时 lark 置 None）+ bot_open_id
        为准，故掉线/重连期间会如实回落到 connecting，而非一直假绿。
        """
        if self._error:
            return {"state": "error", "detail": self._error}
        if not self._running:
            return {"state": "stopped", "detail": "未运行"}
        ws = self._ws_client
        connected = ws is not None and getattr(ws, "_conn", None) is not None
        if connected and self._bot_open_id:
            return {"state": "connected", "detail": "已连接"}
        return {"state": "connecting", "detail": "连接中"}

    # ── 生命周期 ──
    async def start(self) -> None:
        """建立 WebSocket 连接并开始监听消息（长运行，直到 stop）。

        启动失败原因（未装 lark / 缺凭证 / 装配异常）记到 ``self._error``，经 status() 让
        UI 状态灯显示"连接失败"而非含糊的"已停止"；自身吞异常不外抛（fire-and-forget 任务）。
        """
        self._error = None
        if not FEISHU_AVAILABLE:
            self._error = "未安装 lark-oapi（请 uv sync --extra feishu）"
            logger.error(self._error)
            return

        app_id = os.path.expandvars(self.config.app_id)
        app_secret = os.path.expandvars(self.config.app_secret)
        if not app_id or not app_secret:
            self._error = "缺少 app_id / app_secret"
            logger.error("Feishu channel %s", self._error)
            return

        try:
            import lark_oapi as lark

            self._running = True
            self._loop = asyncio.get_running_loop()
            self._client = (
                lark.Client.builder()
                .app_id(app_id)
                .app_secret(app_secret)
                .log_level(lark.LogLevel.INFO)
                .build()
            )
            self._directory.set_client(self._client)
            event_handler = self._build_event_handler(lark)
            self._ws_client = lark.ws.Client(
                app_id,
                app_secret,
                event_handler=event_handler,
                log_level=lark.LogLevel.INFO,
            )
            self._ws_thread = threading.Thread(
                target=self._run_ws_in_thread, name="feishu-ws", daemon=True
            )
            self._ws_thread.start()
            # 拉机器人自身 open_id 用于群 @mention 识别（best-effort）
            self._bot_open_id = await self._loop.run_in_executor(
                None, self._fetch_bot_open_id
            )
            if self._bot_open_id:
                logger.info(f"Feishu bot open_id: {self._bot_open_id}")
            self.streaming.start_cleanup()
            # 后台预热成员/群名缓存：best-effort、不阻断启动（失败只记 warning）。
            # 存引用防止被 GC 中途销毁（事件循环只持弱引用），stop() 时取消。
            self._warmup_task = asyncio.create_task(self._directory.warmup())
        except Exception as e:
            self._error = f"启动失败: {e}"
            self._running = False
            logger.error("Feishu channel 启动失败: %s", e, exc_info=True)
            return

        logger.info("Feishu channel 已通过 WebSocket 长连接启动（无需公网）")
        while self._running:
            await asyncio.sleep(1)

    def _build_event_handler(self, lark: Any) -> Any:
        """装配事件分发器：只消费消息接收，其余高频事件静默吸收。"""
        # WS 长连接模式无需 encrypt_key / verification_token，传空串即可。
        builder = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message_sync)
            .register_p1_customized_event("message", lambda _e: None)
        )
        # 已读回执 / 撤回 / 表情回复增删：飞书会推但我们不消费，不注册会被 SDK 反复刷
        # "processor not found" ERROR，统一静默吸收。getattr 守护兼容旧版 SDK。
        for reg in (
            "register_p2_im_message_message_read_v1",
            "register_p2_im_message_recalled_v1",
            "register_p2_im_message_reaction_created_v1",
            "register_p2_im_message_reaction_deleted_v1",
        ):
            noop = getattr(builder, reg, None)
            if callable(noop):
                noop(lambda _e: None)
        return builder.build()

    def _run_ws_in_thread(self) -> None:
        """独立线程跑 WebSocket 客户端；自动重连，异常只记日志不外抛。"""
        import time

        import lark_oapi.ws.client as _lark_ws_client

        ws_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(ws_loop)
        _lark_ws_client.loop = ws_loop  # lark 模块级 loop，否则会抢主事件循环
        self._ws_loop = ws_loop  # 暴露给 stop()：停掉它即可打断阻塞的 start()
        try:
            while self._running:
                try:
                    self._ws_client.start()
                except Exception as e:
                    logger.warning(f"Feishu WebSocket 异常: {e}")
                if self._running:
                    time.sleep(5)
        finally:
            ws_loop.close()

    async def stop(self) -> None:
        """停掉本传输（WS 连接 + daemon 线程）。会话池由 ChannelManager 拥有，此处不关。

        lark.ws.Client 无公开 stop：其 ``start()`` 阻塞在 ``run_until_complete(_select())``、
        且 auto_reconnect 默认开，仅置 _running=False 它永不返回 → 线程/连接每次 reload 泄漏。
        故关掉自动重连 + 从主线程停掉它的专属 loop，打断 start()，daemon 线程随之退出。
        """
        self._running = False
        if self._warmup_task is not None:
            self._warmup_task.cancel()  # 预热未完则中止，避免孤儿任务在将停的 loop 上跑
            self._warmup_task = None
        if self._ws_client is not None:
            with suppress(Exception):
                self._ws_client._auto_reconnect = False  # 断连后不再自动重连
        ws_loop = self._ws_loop
        if ws_loop is not None:
            with suppress(Exception):
                ws_loop.call_soon_threadsafe(ws_loop.stop)
        await self.streaming.stop_cleanup()
        logger.info("Feishu channel 已停止")

    # ── 接收 ──
    def _on_message_sync(self, data: Any) -> None:
        """WS 线程同步回调：把处理逻辑调度回主事件循环。"""
        # 已停止的旧实例（reload 后传输尚未完全退出）丢弃入站，避免孤儿 channel 重复处理
        if not self._running:
            return
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.inbound.on_message(data), self._loop)

    def _fetch_bot_open_id(self) -> str | None:
        """调 /open-apis/bot/v3/info 获取机器人自身 open_id。"""
        import lark_oapi as lark

        request = (
            lark.BaseRequest.builder()
            .http_method(lark.HttpMethod.GET)
            .uri("/open-apis/bot/v3/info")
            .token_types({lark.AccessTokenType.APP})
            .build()
        )
        response = lark_call("bot/v3/info", lambda: self._client.request(request))
        if response is None:
            return None
        try:
            data = json.loads(response.raw.content)
        except Exception as e:
            logger.warning(f"解析 Feishu bot 信息异常: {e}")
            return None
        bot = (data.get("data") or data).get("bot") or data.get("bot") or {}
        return bot.get("open_id")

    # ── 权限 ──
    def is_allowed(self, sender_id: str) -> bool:
        """白名单：``["*"]`` 全允（默认）；``[]`` 全拒；其余仅列表内 open_id。"""
        allow_list = self.config.allow_from  # pydantic 字段，恒为 list
        if not allow_list:
            return False
        if "*" in allow_list:
            return True
        return str(sender_id) in allow_list

    # ── 发送 ──
    async def send_markdown(
        self, chat_id: str, content: str, *, reply_to: str | None = None
    ) -> None:
        """发一条 markdown 卡片消息（错误提示 / 流式降级）。有 reply_to 走 Reply API。"""
        if not self._client or not content.strip():
            return
        body = _markdown_card(content)
        loop = asyncio.get_running_loop()
        if reply_to:
            await loop.run_in_executor(
                None, self.reply_message_sync, reply_to, "interactive", body
            )
        else:
            receive_id_type = "chat_id" if chat_id.startswith("oc_") else "open_id"
            await loop.run_in_executor(
                None,
                self.send_message_sync,
                receive_id_type,
                chat_id,
                "interactive",
                body,
            )

    def reply_message_sync(
        self, parent_message_id: str, msg_type: str, content: str
    ) -> str | None:
        """同步：Reply API 回复一条消息，成功返回新消息 message_id。"""
        from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

        request = (
            ReplyMessageRequest.builder()
            .message_id(parent_message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type(msg_type)
                .content(content)
                .build()
            )
            .build()
        )
        response = lark_call(
            f"reply id={parent_message_id}",
            lambda: self._client.im.v1.message.reply(request),
            level="error",
        )
        data = getattr(response, "data", None) if response is not None else None
        return getattr(data, "message_id", None) if data is not None else None

    def send_message_sync(
        self, receive_id_type: str, receive_id: str, msg_type: str, content: str
    ) -> str | None:
        """同步：Create API 直接发送一条消息，返回 message_id。"""
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        request = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type(msg_type)
                .content(content)
                .build()
            )
            .build()
        )
        response = lark_call(
            f"create receive_id={receive_id}",
            lambda: self._client.im.v1.message.create(request),
            level="error",
        )
        return (
            response.data.message_id if response is not None and response.data else None
        )


async def test_credentials(config: FeishuChannelConfig) -> dict:
    """用给定凭证建临时 client 拉机器人信息，验证连通性。

    返回 {ok, error?, bot_name?}。不影响正在运行的 channel——独立 client、只读调用。
    """
    if not FEISHU_AVAILABLE:
        return {"ok": False, "error": "未安装 lark-oapi，请 uv sync --extra feishu"}
    app_id = os.path.expandvars(config.app_id)
    app_secret = os.path.expandvars(config.app_secret)
    if not app_id or not app_secret:
        return {"ok": False, "error": "需要 app_id 和 app_secret"}

    import lark_oapi as lark

    def _probe() -> dict:
        client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
        request = (
            lark.BaseRequest.builder()
            .http_method(lark.HttpMethod.GET)
            .uri("/open-apis/bot/v3/info")
            .token_types({lark.AccessTokenType.APP})
            .build()
        )
        try:
            resp = client.request(request)
        except Exception as e:
            return {"ok": False, "error": f"请求异常: {e}"}
        if not resp.success():
            return {"ok": False, "error": f"凭证无效: code={resp.code} {resp.msg}"}
        try:
            data = json.loads(resp.raw.content)
        except Exception:
            return {"ok": True, "bot_name": ""}
        bot = (data.get("data") or data).get("bot") or data.get("bot") or {}
        return {"ok": True, "bot_name": bot.get("app_name") or bot.get("open_id") or ""}

    return await asyncio.get_running_loop().run_in_executor(None, _probe)
