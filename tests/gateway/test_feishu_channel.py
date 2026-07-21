"""飞书 channel 纯函数 / 数据契约测试（无 lark SDK、无网络、无 bridge）。

覆盖：thread 派生、post 文本/图片抽取、多模态 content、文件附件路径安全、@mention 清理、
白名单语义。飞书已禁用 ask 工具，无 ask 卡片相关测试。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from lumi.gateway.channels.config import FeishuChannelConfig
from lumi.gateway.channels.feishu import inbound as inb
from lumi.gateway.channels.feishu.channel import FeishuChannel
from lumi.gateway.channels.feishu.inbound import (
    build_content,
    extract_post_images,
    extract_post_text,
    feishu_thread_id,
    file_ref_of,
    image_keys_of,
    resolve_mentions,
    safe_filename,
)


# ── thread 派生 ──
def test_feishu_thread_id_is_dns1035():
    assert feishu_thread_id("oc_ABC123") == "feishu-oc-abc123"
    # 全非法字符也能得到合规 id（含 feishu 前缀）
    tid = feishu_thread_id("oc_用户@#$")
    assert tid.startswith("feishu-oc")
    assert all(c.islower() or c.isdigit() or c == "-" for c in tid)


# ── post 富文本文本抽取 ──
def test_extract_post_text_localized():
    content = {
        "zh_cn": {
            "title": "标题",
            "content": [
                [{"tag": "text", "text": "你好"}, {"tag": "a", "text": "链接"}]
            ],
        }
    }
    assert extract_post_text(content) == "标题 你好 链接"


def test_extract_post_text_wrapped_and_at():
    content = {"post": {"en_us": {"content": [[{"tag": "at", "user_name": "Bob"}]]}}}
    assert extract_post_text(content) == "@Bob"


def test_extract_post_text_empty():
    assert extract_post_text({}) == ""
    assert extract_post_text({"zh_cn": {}}) == ""


# ── 媒体（图片）多模态 ──
def test_extract_post_images():
    content = {
        "zh_cn": {
            "content": [
                [{"tag": "img", "image_key": "img_k1"}, {"tag": "text", "text": "x"}]
            ]
        }
    }
    assert extract_post_images(content) == ["img_k1"]
    assert extract_post_images({}) == []


def test_image_keys_of():
    assert image_keys_of("image", {"image_key": "img_k2"}) == ["img_k2"]
    assert image_keys_of("image", {}) == []
    assert image_keys_of("text", {"text": "x"}) == []


def test_build_content_text_only_returns_str():
    assert build_content("你好", []) == "你好"


def test_build_content_with_image_returns_blocks():
    img = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAA"},
    }
    blocks = build_content("这是什么图片", [img])
    assert blocks == [{"type": "text", "text": "这是什么图片"}, img]
    # 纯图无文本：只含图片块
    assert build_content("", [img]) == [img]


# ── 文件附件 ──
def test_file_ref_of():
    assert file_ref_of("file", {"file_key": "fk_1", "file_name": "a.pdf"}) == (
        "fk_1",
        "a.pdf",
    )
    assert file_ref_of("file", {"file_key": "fk_1"}) == ("fk_1", "")
    assert file_ref_of("file", {}) is None  # 无 file_key
    assert file_ref_of("text", {"file_key": "x"}) is None


def test_safe_filename_prevents_traversal():
    # 路径穿越被消解，原名清洗，带 key 前缀
    out = safe_filename("file_v3abcdef0000", "../../etc/passwd")
    assert "/" not in out and ".." not in out
    assert out.startswith("file_v3abcd")
    # 无名 → .bin
    assert safe_filename("file_keyabc123", "").endswith(".bin")


# ── @mention 占位符清理 ──
class _M:
    def __init__(self, key, name):
        self.key = key
        self.name = name


def test_resolve_mentions():
    text = "@_user_1 帮我看下"
    assert resolve_mentions(text, [_M("@_user_1", "张三")]) == "@张三 帮我看下"


def test_resolve_mentions_noop():
    assert resolve_mentions("无提及", None) == "无提及"
    assert resolve_mentions("", [_M("@_user_1", "x")]) == ""


# ── 白名单语义 ──
def test_is_allowed_default_star_allows_all():
    ch = FeishuChannel(FeishuChannelConfig())  # 默认 ["*"]
    assert ch.is_allowed("ou_anyone") is True


def test_is_allowed_empty_denies_all():
    ch = FeishuChannel(FeishuChannelConfig(allow_from=[]))
    assert ch.is_allowed("ou_anyone") is False


def test_is_allowed_explicit_list():
    ch = FeishuChannel(FeishuChannelConfig(allow_from=["ou_a"]))
    assert ch.is_allowed("ou_a") is True
    assert ch.is_allowed("ou_b") is False


# ── 连接状态灯 ──
def test_status_states():
    ch = FeishuChannel(FeishuChannelConfig())
    assert ch.status()["state"] == "stopped"  # 未启动
    ch._error = "缺少 app_id / app_secret"
    assert ch.status() == {"state": "error", "detail": "缺少 app_id / app_secret"}
    ch._error = None
    ch._running = True
    assert ch.status()["state"] == "connecting"  # 运行但 WS 未连

    class _WS:
        _conn = object()

    ch._ws_client = _WS()
    ch._bot_open_id = "ou_bot"
    assert ch.status()["state"] == "connected"
    ch._ws_client._conn = None  # 掉线：lark 置 _conn=None
    assert ch.status()["state"] == "connecting"  # 不再假绿


# ── 群 @机器人 识别（不做 ou_ 启发式误判）──
class _Mid:
    def __init__(self, open_id):
        self.open_id = open_id


class _Mention:
    def __init__(self, open_id):
        self.id = _Mid(open_id)


class _Msg:
    def __init__(self, content, mentions):
        self.content = content
        self.mentions = mentions


def test_bot_mentioned_exact_match():
    ch = FeishuChannel(FeishuChannelConfig())
    ch._bot_open_id = "ou_bot"
    fi = ch.inbound
    assert fi._is_bot_mentioned(_Msg("@bot 你好", [_Mention("ou_bot")])) is True
    # @真人（非机器人）不再被误判为 @机器人
    assert fi._is_bot_mentioned(_Msg("@张三", [_Mention("ou_real_person")])) is False


def test_bot_mentioned_all():
    ch = FeishuChannel(FeishuChannelConfig())
    ch._bot_open_id = None  # 取不到 bot_open_id 时只认 @_all
    fi = ch.inbound
    assert fi._is_bot_mentioned(_Msg("@_all 通知", [])) is True
    assert fi._is_bot_mentioned(_Msg("@张三", [_Mention("ou_x")])) is False


# ── 忙时排队 + 合并 ──
async def test_run_batch_merges_text_and_replies_to_latest(monkeypatch):
    captured = {}
    monkeypatch.setattr(inb, "run_turn", _capture_run_turn(captured))
    fi = FeishuChannel(FeishuChannelConfig()).inbound
    batch = [
        inb._Pending("第一条", reply_to="m1"),
        inb._Pending("第二条", reply_to="m2"),
    ]
    await fi._run_batch(fi.channel, None, "oc_x", "t", batch)
    content = captured["content"]
    assert (
        "<system-reminder>" in content and "第一条" in content and "第二条" in content
    )
    assert captured["reply_to"] == "m2"  # 回复批次里最近一条


async def test_run_batch_merges_media(monkeypatch):
    captured = {}
    monkeypatch.setattr(inb, "run_turn", _capture_run_turn(captured))
    fi = FeishuChannel(FeishuChannelConfig()).inbound

    async def fake_img(mid, ik):
        return {"type": "image", "source": {"key": ik}}

    monkeypatch.setattr(fi, "_image_block", fake_img)
    batch = [
        inb._Pending("看图", image_refs=[("m1", "ik1")], reply_to="m1", ts=1000),
        inb._Pending("", image_refs=[("m2", "ik2")], reply_to="m2", ts=2000),
    ]
    await fi._run_batch(fi.channel, None, "oc", "t", batch)
    content = captured["content"]
    assert content[0]["type"] == "text"
    assert "看图" in content[0]["text"]
    assert sum(1 for b in content if b.get("type") == "image") == 2
    # 渲染数据结构化透传：每条原始消息的 sender/ts/text（desktop 气泡只读它）；
    # 媒体-only 消息给占位文本，避免渲染出只有人名没有内容的悬空气泡
    assert captured["message_meta"] == {
        "items": [
            {"sender": "", "ts": 1000, "text": "看图"},
            {"sender": "", "ts": 2000, "text": "［图片］"},
        ]
    }


# ── 渠道系统命令 ──
def _sent_collector(ch, monkeypatch):
    sent = []

    async def fake_send(chat_id, text, reply_to=None, title=""):
        sent.append((text, title))
        return "mid"

    monkeypatch.setattr(ch, "send_markdown", fake_send)
    return sent


async def test_stop_cancels_run_and_clears_queue(monkeypatch):
    ch = FeishuChannel(FeishuChannelConfig())
    fi = ch.inbound
    sent = _sent_collector(ch, monkeypatch)

    async def hang():
        await asyncio.sleep(30)

    task = asyncio.create_task(hang())
    await asyncio.sleep(0)
    ch.bridge_pool.run_tasks["t"] = task
    fi._queues["t"] = [inb._Pending("排队中")]
    await fi._run_system_command("stop", "oc", "t", "m1")
    assert "t" not in fi._queues  # 积压一并清空，否则马上又触发新一轮
    assert "已停止" in sent[0][0]
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_stop_idle_replies_nothing_running(monkeypatch):
    ch = FeishuChannel(FeishuChannelConfig())
    sent = _sent_collector(ch, monkeypatch)
    await ch.inbound._run_system_command("stop", "oc", "t", "m1")
    assert "没有正在执行" in sent[0][0]


async def test_stop_idle_still_stops_bg_tasks(monkeypatch):
    # 空闲但有后台任务在跑：/stop 也要能停（IM 没有任务抽屉，这是唯一手段）
    ch = FeishuChannel(FeishuChannelConfig())
    fi = ch.inbound
    sent = _sent_collector(ch, monkeypatch)

    async def fake_cancel(thread_id):
        return 2

    monkeypatch.setattr(inb, "cancel_thread_bg_tasks", fake_cancel)
    await fi._run_system_command("stop", "oc", "t", "m1")
    assert "已停止 2 个后台任务" in sent[0][0]
    assert "当前任务" not in sent[0][0]  # 没有在跑的轮，不虚报


async def test_stop_reports_run_and_bg_together(monkeypatch):
    ch = FeishuChannel(FeishuChannelConfig())
    fi = ch.inbound
    sent = _sent_collector(ch, monkeypatch)

    async def hang():
        await asyncio.sleep(30)

    task = asyncio.create_task(hang())
    await asyncio.sleep(0)
    ch.bridge_pool.run_tasks["t"] = task

    async def fake_cancel(thread_id):
        return 1

    monkeypatch.setattr(inb, "cancel_thread_bg_tasks", fake_cancel)
    await fi._run_system_command("stop", "oc", "t", "m1")
    assert "已停止当前任务" in sent[0][0] and "1 个后台任务" in sent[0][0]
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_stop_during_notification_turn_keeps_queue(monkeypatch):
    # 通知 poller 持锁的轮不可取消：如实告知、不误报"没有任务"、不丢排队消息
    ch = FeishuChannel(FeishuChannelConfig())
    fi = ch.inbound
    sent = _sent_collector(ch, monkeypatch)
    lock = asyncio.Lock()
    await lock.acquire()
    ch.bridge_pool._locks["t"] = lock  # 锁被占但 run_tasks 无条目 = poller 轮
    fi._queues["t"] = [inb._Pending("排队中")]
    await fi._run_system_command("stop", "oc", "t", "m1")
    assert "无法中断" in sent[0][0]
    assert fi._queues["t"]  # 没停到任何东西，排队消息不能被丢


class _ClearBridge:
    def __init__(self):
        self.deleted = []

    async def delete_thread(self, tid):
        self.deleted.append(tid)


def _patch_pool_get(ch, monkeypatch, bridge):
    async def fake_get(tid):
        ch.bridge_pool._locks.setdefault(tid, asyncio.Lock())
        return bridge

    monkeypatch.setattr(ch.bridge_pool, "get", fake_get)


async def test_clear_deletes_thread_and_meta(monkeypatch):
    ch = FeishuChannel(FeishuChannelConfig())
    sent = _sent_collector(ch, monkeypatch)
    bridge = _ClearBridge()
    _patch_pool_get(ch, monkeypatch, bridge)
    meta_deleted = []
    monkeypatch.setattr(inb, "delete_meta", meta_deleted.append)
    await ch.inbound._run_system_command("clear", "oc", "t", "m1")
    assert bridge.deleted == ["t"] and meta_deleted == ["t"]
    assert "已清空" in sent[0][0]


async def test_clear_busy_prompts_stop_first(monkeypatch):
    ch = FeishuChannel(FeishuChannelConfig())
    sent = _sent_collector(ch, monkeypatch)
    bridge = _ClearBridge()
    _patch_pool_get(ch, monkeypatch, bridge)
    lock = asyncio.Lock()
    await lock.acquire()
    ch.bridge_pool._locks["t"] = lock
    await ch.inbound._run_system_command("clear", "oc", "t", "m1")
    assert bridge.deleted == []
    assert "/stop" in sent[0][0]


def test_help_markdown_groups_and_empty_skills():
    out = inb.help_markdown(
        [{"name": "commit", "description": "提交", "type": "skill"}]
    )
    assert "技能命令" in out and "`/commit` 提交" in out
    # 分割线前后必须有空行：紧贴上一行的 --- 会把整段变成 setext 大字标题
    assert "\n\n---\n\n" in out
    # 无 skill：跳过技能组，无悬空分割线
    out2 = inb.help_markdown([])
    assert "技能命令" not in out2 and "---" not in out2 and "`/stop`" in out2


def test_help_markdown_system_commands_not_under_skills():
    # system 类命令（dream/compact 等）归「会话控制」，不混进「技能命令」
    out = inb.help_markdown(
        [
            {"name": "commit", "description": "提交", "type": "skill"},
            {"name": "dream", "description": "整理记忆", "type": "system"},
            {"name": "compact", "description": "压缩历史", "type": "system"},
        ]
    )
    skills_part, control_part = out.split("会话控制", 1)
    assert "`/commit`" in skills_part
    assert "`/dream`" not in skills_part and "`/compact`" not in skills_part
    assert "`/dream`" in control_part and "`/compact`" in control_part
    assert "`/stop`" in control_part  # 与渠道系统命令同组


def test_help_line_truncates_long_and_multiline_description():
    assert inb._help_line("x", "第一行\n第二行") == "`/x` 第一行"
    long = inb._help_line("y", "很" * 80)
    assert long.endswith("…") and len(long) < 80


def test_available_commands_split_by_surface():
    # dream 系按载体分流：desktop 短会话只见 /dream，IM 长会话只见 /dream-session；
    # /compact 两端恒有
    from lumi.gateway.bridge.core import available_commands

    desktop = {c["name"] for c in available_commands(True, channel=False)}
    channel = {c["name"] for c in available_commands(True, channel=True)}
    assert "dream" in desktop and "dream-session" not in desktop
    assert "dream-session" in channel and "dream" not in channel
    assert "compact" in desktop and "compact" in channel
    # 无记忆会话两端都无 dream 系，/compact 仍在
    plain = {c["name"] for c in available_commands(False)}
    assert "dream" not in plain and "dream-session" not in plain
    assert "compact" in plain


async def test_help_lists_skill_and_system_commands(monkeypatch):
    # /help 不跑 agent、不为此建桥：恒走 available_commands（渠道桥记忆恒开，口径等价）
    ch = FeishuChannel(FeishuChannelConfig())
    sent = _sent_collector(ch, monkeypatch)
    monkeypatch.setattr(
        inb,
        "available_commands",
        lambda memory_enabled, channel=False: [
            {"name": "commit", "description": "", "type": "skill"}
        ],
    )
    await ch.inbound._run_system_command("help", "oc", "t", "m1")
    text, title = sent[0]
    assert "/commit" in text and "/stop" in text and "/help" in text
    assert "Lumi" in title  # /help 带彩色 header 卡片
    assert ch.bridge_pool._bridges == {}  # 没有因 /help 建桥


async def test_clear_drains_messages_queued_during_clear(monkeypatch):
    # /clear 持锁窗口内入队的消息：清空完成后当场接手，不搁浅到下条消息
    captured = {}
    monkeypatch.setattr(inb, "run_turn", _capture_run_turn(captured))
    ch = FeishuChannel(FeishuChannelConfig())
    fi = ch.inbound
    _sent_collector(ch, monkeypatch)
    bridge = _ClearBridge()
    _patch_pool_get(ch, monkeypatch, bridge)
    monkeypatch.setattr(inb, "delete_meta", lambda tid: None)
    fi._queues["t"] = [inb._Pending("清空期间到达", reply_to="m2")]
    await fi._run_system_command("clear", "oc", "t", "m1")
    assert "t" not in fi._queues
    assert "清空期间到达" in captured["content"]


# ── 斜杠命令路由 ──
class _CmdBridge:
    def list_commands(self):
        return [{"name": "commit", "description": "", "type": "skill"}]


def _capture_run_turn(captured):
    """run_turn 的统一 fake：记录全部关键字实参（各测试按需断言）。"""

    async def fake_run_turn(ch, bridge, **kwargs):
        captured.update(kwargs)

    return fake_run_turn


async def test_run_batch_known_slash_command_routes_to_command(monkeypatch):
    captured = {}
    monkeypatch.setattr(inb, "run_turn", _capture_run_turn(captured))
    fi = FeishuChannel(FeishuChannelConfig()).inbound
    batch = [inb._Pending("/commit fix bug", reply_to="m1", sender_name="李雷")]
    await fi._run_batch(fi.channel, _CmdBridge(), "oc", "t", batch)
    assert captured["command"] == ("commit", "fix bug")


async def test_run_batch_unknown_slash_is_plain_text(monkeypatch):
    captured = {}
    monkeypatch.setattr(inb, "run_turn", _capture_run_turn(captured))
    fi = FeishuChannel(FeishuChannelConfig()).inbound
    batch = [inb._Pending("/nope 你好", reply_to="m1")]
    await fi._run_batch(fi.channel, _CmdBridge(), "oc", "t", batch)
    assert captured["command"] is None
    assert "/nope 你好" in captured["content"]


async def test_run_batch_merged_batch_command_not_recognized(monkeypatch):
    # 混批（≥2 条）里的命令当普通文本，不触发命令路由
    captured = {}
    monkeypatch.setattr(inb, "run_turn", _capture_run_turn(captured))
    fi = FeishuChannel(FeishuChannelConfig()).inbound
    batch = [
        inb._Pending("先看这个", reply_to="m1"),
        inb._Pending("/commit", reply_to="m2"),
    ]
    await fi._run_batch(fi.channel, _CmdBridge(), "oc", "t", batch)
    assert captured["command"] is None


def test_merge_messages_single_is_plain_text():
    # 单条无发送者：原样返回，不加 reminder / 标签
    assert inb.merge_messages([inb._Pending("帮我看下这个")]) == "帮我看下这个"


def test_render_single_with_sender_tag():
    out = inb.merge_messages([inb._Pending("帮我看下这个", sender_name="李雷")])
    assert out == "<sender>李雷</sender>\n帮我看下这个"


def test_merge_messages_multi_has_reminder_and_sender_tags():
    out = inb.merge_messages(
        [
            inb._Pending("等等，先别删那个文件", sender_name="李雷"),
            inb._Pending("改成用 SQLite", sender_name="韩梅梅"),
            inb._Pending("", image_refs=[("m3", "ik")], sender_name="李雷"),
        ]
    )
    assert "<system-reminder>" in out
    assert "连发的 3 条消息" in out
    assert "<sender>李雷</sender>\n等等，先别删那个文件" in out
    assert "<sender>韩梅梅</sender>\n改成用 SQLite" in out
    assert "<sender>李雷</sender>\n［图片］" in out  # 媒体-only 消息占位保序


def test_merge_messages_media_placeholders():
    out = inb.merge_messages(
        [
            inb._Pending("看下这两个"),
            inb._Pending("", file_refs=[("m", "fk", "报告.pdf")]),
            inb._Pending("", image_refs=[("a", "1"), ("b", "2")]),
        ]
    )
    assert "看下这两个" in out
    assert "［文件：报告.pdf］" in out
    assert "［图片×2］" in out


async def test_drain_processes_first_then_merges_queued(monkeypatch):
    fi = FeishuChannel(FeishuChannelConfig()).inbound
    calls = []

    async def fake_run_batch(ch, bridge, chat_id, thread_id, batch):
        calls.append([m.text for m in batch])
        if len(calls) == 1:  # 模拟第一轮处理期间又来两条
            fi._queues["t"] = [
                inb._Pending("追加1", reply_to="m2"),
                inb._Pending("追加2", reply_to="m3"),
            ]

    monkeypatch.setattr(fi, "_run_batch", fake_run_batch)
    await fi._drain(fi.channel, None, "oc", "t", [inb._Pending("首条", reply_to="m1")])
    # 第一轮单独跑首条；第二轮把期间积压的两条合并成一轮
    assert calls == [["首条"], ["追加1", "追加2"]]
    assert "t" not in fi._queues  # 队列已清空


# ── 渠道运行时配置：ChannelRuntimeConfig 基类 + effort 覆盖机制 ──────────


def test_channel_runtime_config_inherited():
    """FeishuChannelConfig 继承 ChannelRuntimeConfig：model/effort/tool_mode/workspace
    四项运行时字段都在，默认值正确（新渠道继承同一组即免费获得）。"""
    from lumi.gateway.channels.config import ChannelRuntimeConfig

    assert issubclass(FeishuChannelConfig, ChannelRuntimeConfig)
    base = ChannelRuntimeConfig()
    assert (base.model, base.effort, base.tool_mode, base.workspace) == (
        "",
        "auto",
        "auto",
        "",
    )

    cfg = FeishuChannelConfig(model="claude-opus-4-8", effort="high")
    assert cfg.model == "claude-opus-4-8" and cfg.effort == "high"
    # 序列化含全部运行时字段（前端 config.model_dump 消费）
    keys = cfg.model_dump().keys()
    assert {"model", "effort", "tool_mode", "workspace"} <= set(keys)


def test_drain_ultra_note_prefers_channel_override(monkeypatch):
    """drain_ultra_note：渠道 context.effort 覆盖优先于全局 resolve()。

    - override='ultra' → 触发 workflow 编排提醒（即便全局非 ultra）
    - override='low' 但全局 ultra → 不触发（渠道档位说了算）
    - override=None（desktop 会话）→ 回退全局 resolve().effort
    """
    from types import SimpleNamespace

    from lumi.gateway.bridge.folders import FolderManager

    # 全局 profile 为 ultra，用来验证 override 能盖过它
    monkeypatch.setattr(
        "lumi.models.provider_store.resolve",
        lambda name=None: SimpleNamespace(effort="ultra"),
    )

    def fm(effort):
        bridge = SimpleNamespace(
            _context=SimpleNamespace(effort=effort), _notified_ultra=False
        )
        return FolderManager(bridge), bridge

    # override='ultra' → 开启提醒
    m, b = fm("ultra")
    assert "已开启" in m.drain_ultra_note() and b._notified_ultra is True

    # override='low' 压过全局 ultra → 不进 ultra 态（无提醒、状态保持 False）
    m, b = fm("low")
    assert m.drain_ultra_note() == "" and b._notified_ultra is False

    # override=None → 回退全局 resolve()（ultra）→ 开启提醒
    m, b = fm(None)
    assert "已开启" in m.drain_ultra_note() and b._notified_ultra is True


# ── 妙记生成事件 ──
class _Sub:
    def __init__(self, open_id: str):
        self.open_id = open_id


class _MinuteData:
    """模拟 lark SDK 的 P2MinutesMinuteGeneratedV1 结构。"""

    def __init__(self, token: str, open_ids: list[str], event_id: str = "evt-1"):
        self.header = SimpleNamespace(event_id=event_id)
        self.event = SimpleNamespace(
            minute_token=token,
            minute_source=None,
            subscriber_ids=[_Sub(o) for o in open_ids],
        )


async def test_minute_event_enqueues_token_and_subscriber():
    fi = FeishuChannel(FeishuChannelConfig()).inbound
    await fi.on_minute_generated(_MinuteData("obcnTOK", ["ou_me"]))
    assert fi._minute_events == [inb._MinuteEvent("obcnTOK", "ou_me")]


async def test_minute_event_deduped_by_event_id():
    fi = FeishuChannel(FeishuChannelConfig()).inbound
    await fi.on_minute_generated(_MinuteData("obcnTOK", ["ou_me"], event_id="e1"))
    await fi.on_minute_generated(_MinuteData("obcnTOK", ["ou_me"], event_id="e1"))
    assert len(fi._minute_events) == 1  # 飞书重推同一事件只处理一次


async def test_minute_event_without_subscribers_skipped():
    """payload 无 owner 字段，subscriber_ids 为空则无法定位推送对象，跳过而非猜。"""
    fi = FeishuChannel(FeishuChannelConfig()).inbound
    await fi.on_minute_generated(_MinuteData("obcnTOK", []))
    assert fi._minute_events == []


async def test_minute_event_without_token_skipped():
    fi = FeishuChannel(FeishuChannelConfig()).inbound
    await fi.on_minute_generated(_MinuteData("", ["ou_me"]))
    assert fi._minute_events == []


async def test_minute_turn_targets_open_id_and_injects_token(monkeypatch):
    """私聊直投 open_id（send_markdown 按前缀选 receive_id_type），提示带 token。"""
    captured = {}
    monkeypatch.setattr(inb, "run_turn", _capture_run_turn(captured))
    ch = FeishuChannel(FeishuChannelConfig())
    fi = ch.inbound
    _sent_collector(ch, monkeypatch)

    await fi._run_minute_turn(
        None, "feishu-ou-me", "ou_me", inb._MinuteEvent("obcnTOK", "ou_me")
    )

    assert captured["chat_id"] == "ou_me"
    assert captured["thread_id"] == "feishu-ou-me"
    assert "obcnTOK" in captured["content"]
    assert captured["synthetic"] is True  # 合成轮：用户侧不显示注入文本

    content = captured["content"]
    assert content.startswith("<system-reminder>")  # 与 hook 注入同一约定
    # 直接给出取数命令：省掉 list skill → 读 skill → 试参数的探索开销
    assert "lark-cli minutes +detail" in content
    # 先 cd 到临时区：工具默认写 ./minutes/ 会把会议记录留在工作区，而 --output-dir
    # 只收当前目录内的相对路径（传绝对路径报 invalid_argument），故只能靠 cd
    assert f"cd {inb.lumi_tmp_dir()} &&" in content
    assert "--output-dir" not in content


async def test_minute_turn_sends_no_anchor_message(monkeypatch):
    """不发"正在整理…"占位消息：流式卡片经 Create API 直投，首条即内容本身。"""
    captured = {}
    monkeypatch.setattr(inb, "run_turn", _capture_run_turn(captured))
    ch = FeishuChannel(FeishuChannelConfig())
    fi = ch.inbound
    sent = _sent_collector(ch, monkeypatch)

    await fi._run_minute_turn(
        None, "feishu-ou-me", "ou_me", inb._MinuteEvent("obcnTOK", "ou_me")
    )

    assert sent == []  # 一条占位消息都没发
    assert captured["reply_to"] == ""  # 无锚点，交由 streaming 走 Create 直投


async def test_drain_minute_events_skips_busy_session(monkeypatch):
    """已建桥的会话正在跑时不抢锁，事件留队下个 tick 再认领。"""
    ch = FeishuChannel(FeishuChannelConfig())
    fi = ch.inbound
    fi._minute_events = [inb._MinuteEvent("obcnTOK", "ou_me")]
    ran = []

    async def spy(*a):
        ran.append(a)

    monkeypatch.setattr(fi, "_run_minute_turn", spy)

    # 模拟"已建桥"：bridge 与锁在 pool.get 里一并创建
    tid = feishu_thread_id("ou_me")
    ch.bridge_pool._bridges[tid] = object()
    ch.bridge_pool._locks[tid] = asyncio.Lock()
    async with ch.bridge_pool._locks[tid]:  # 该会话正在跑一轮
        await fi._drain_minute_events()

    assert ran == [] and fi._minute_events == [inb._MinuteEvent("obcnTOK", "ou_me")]


async def test_drain_minute_events_bridges_unseen_session(monkeypatch):
    """未建桥的全新会话必须建桥并跑，不能因「无锁」被跳过。

    回归：锁随建桥创建，妙记会话常是用户从未私聊过的全新 thread，
    早期实现对 try_lock 返回 None 直接 continue，导致首个妙记事件永远不被处理。
    """
    ch = FeishuChannel(FeishuChannelConfig())
    fi = ch.inbound
    fi._minute_events = [inb._MinuteEvent("obcnTOK", "ou_new")]
    ran = []

    async def spy(bridge, thread_id, target, event):
        ran.append((thread_id, target, event.token))

    monkeypatch.setattr(fi, "_run_minute_turn", spy)
    _patch_pool_get(ch, monkeypatch, None)

    await fi._drain_minute_events()

    tid = feishu_thread_id("ou_new")
    assert ran == [(tid, "ou_new", "obcnTOK")]
    assert fi._minute_events == []
    assert ch.bridge_pool.chat_ids[tid] == "ou_new"  # 回填供后台通知认领


def test_session_key_only_exact_p2p_uses_open_id():
    """仅精确 "p2p" 用 open_id；群与任何未知 chat_type 一律 chat_id（宁可不裂）。"""
    assert inb.session_key_of("p2p", "oc_dm", "ou_me") == "ou_me"
    assert inb.session_key_of("group", "oc_team", "ou_me") == "oc_team"
    assert inb.session_key_of(None, "oc_team", "ou_me") == "oc_team"
    assert inb.session_key_of("topic", "oc_team", "ou_me") == "oc_team"


def _inbound_event(chat_type: str, chat_id: str, open_id: str):
    """一条最简文本入站消息事件（字段名与 lark EventMessage 对齐）。

    message_id 随发送者变：同一测试里连发两条时不能被去重 LRU 吃掉。
    """
    return SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                message_id=f"om_{open_id}",
                chat_id=chat_id,
                chat_type=chat_type,
                message_type="text",
                content=json.dumps({"text": "你好"}),
                mentions=None,
                parent_id=None,
                create_time=1000,
            ),
            sender=SimpleNamespace(sender_type="user", sender_id=_Mid(open_id)),
        )
    )


async def _inbound_thread_of(ch, monkeypatch, chat_type, chat_id, open_id) -> str:
    """跑一遍真实 on_message，返回它派生出的 thread_id。

    只挡住网络（发送者姓名）、落盘（sidecar）与真正建桥/跑轮，派生逻辑本身不挡
    ——测试的全部意义就在于验证那一段。已打桩 bridge_pool.get，调用方无需重复。
    """
    fi = ch.inbound
    captured = []

    async def fake_resolve(chat, ids):
        return {open_id: "张三"}

    async def fake_sync(*a, **k):
        return None

    async def fake_drain(bridge, cid, thread_id, batch):
        captured.append(thread_id)

    monkeypatch.setattr(ch.directory, "resolve_senders_in_chat", fake_resolve)
    monkeypatch.setattr(fi, "_sync_session_title", fake_sync)
    monkeypatch.setattr(fi, "_locked_drain", fake_drain)
    _patch_pool_get(ch, monkeypatch, None)

    await fi.on_message(_inbound_event(chat_type, chat_id, open_id))

    assert captured, "on_message 未跑到派生 thread 这一步（异常被 try/except 吞了）"
    return captured[0]


async def test_inbound_p2p_thread_keyed_by_open_id(monkeypatch):
    """入站私聊必须按 open_id 派生 thread —— 与妙记推送同源的前提。"""
    ch = FeishuChannel(FeishuChannelConfig())
    thread = await _inbound_thread_of(ch, monkeypatch, "p2p", "oc_dm", "ou_me")
    assert thread == feishu_thread_id("ou_me")
    # 投递地址仍回填真实 chat_id（thread key 是 open_id，两者刻意不同）
    assert ch.bridge_pool.chat_ids[thread] == "oc_dm"


async def test_inbound_group_thread_keyed_by_chat_id(monkeypatch):
    """群聊仍按 chat_id 派生：同群不同发言人必须落在同一条 thread。"""
    ch = FeishuChannel(FeishuChannelConfig(group_policy="open"))
    a = await _inbound_thread_of(ch, monkeypatch, "group", "oc_team", "ou_a")
    b = await _inbound_thread_of(ch, monkeypatch, "group", "oc_team", "ou_b")
    assert a == b == feishu_thread_id("oc_team")


async def test_minute_push_lands_on_the_inbound_p2p_thread(monkeypatch):
    """回归：妙记推送与入站私聊必须落在同一条 thread 上。

    入站侧的 thread 由真实 on_message 跑出来（不是用被测函数自己算的），否则
    这条断言会退化成同义反复——入站改回按 chat_id 派生也照样通过。
    """
    ch = FeishuChannel(FeishuChannelConfig())
    inbound_thread = await _inbound_thread_of(ch, monkeypatch, "p2p", "oc_dm", "ou_me")

    ran = []

    async def spy(bridge, thread_id, target, event):
        ran.append((thread_id, target))

    fi = ch.inbound
    fi._minute_events = [inb._MinuteEvent("obcnTOK", "ou_me")]
    monkeypatch.setattr(fi, "_run_minute_turn", spy)  # 桥已由 _inbound_thread_of 打桩

    await fi._drain_minute_events()

    # 落在入站那条 thread 上，且投递走入站回填的真实 chat_id（而非 open_id 直投）
    assert ran == [(inbound_thread, "oc_dm")]


# ── 妙记订阅自愈 + 链路诊断 ──
def _fake_run(stdout: str = "", stderr: str = "", exc: Exception | None = None):
    def run(cmd, capture_output=False, text=False, timeout=None):
        if exc is not None:
            raise exc
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=0)

    return run


def _patch_subprocess(monkeypatch, run):
    import subprocess

    monkeypatch.setattr(subprocess, "run", run)


def test_ensure_subscription_ok(monkeypatch):
    from lumi.gateway.channels.feishu.minutes import ensure_subscription

    _patch_subprocess(monkeypatch, _fake_run(stdout='{"ok": true, "data": {}}'))
    assert ensure_subscription() == ""  # 空串 = 成功


def test_ensure_subscription_reports_api_error(monkeypatch):
    from lumi.gateway.channels.feishu.minutes import ensure_subscription

    _patch_subprocess(
        monkeypatch,
        _fake_run(stdout='{"ok": false, "error": {"message": "token expired"}}'),
    )
    assert "token expired" in ensure_subscription()


def test_ensure_subscription_handles_missing_binary(monkeypatch):
    """lark-cli 不在 PATH 时给出可辨认的原因，而非抛异常打断 channel 启动。"""
    from lumi.gateway.channels.feishu.minutes import ensure_subscription

    _patch_subprocess(monkeypatch, _fake_run(exc=FileNotFoundError()))
    assert "lark-cli" in ensure_subscription()


def test_ensure_subscription_handles_non_json(monkeypatch):
    """未登录等情况下 lark-cli 可能输出非 JSON，不能让解析异常冒泡。"""
    from lumi.gateway.channels.feishu.minutes import ensure_subscription

    _patch_subprocess(monkeypatch, _fake_run(stdout="panic: not logged in"))
    assert "not logged in" in ensure_subscription()


def test_ensure_subscription_handles_timeout(monkeypatch):
    from lumi.gateway.channels.feishu.minutes import ensure_subscription

    _patch_subprocess(monkeypatch, _fake_run(exc=TimeoutError("timed out")))
    assert "timed out" in ensure_subscription()


def _patch_which(monkeypatch, found: bool):
    import shutil

    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/local/bin/lark-cli" if found else None
    )


def test_diagnose_reports_missing_cli_and_blocks_rest(monkeypatch):
    """lark-cli 缺失时给安装命令，后续三项标记为「需先安装」而不重复探测。"""
    from lumi.gateway.channels.feishu.minutes import diagnose

    _patch_which(monkeypatch, False)
    checks = diagnose("cli_x")
    assert [c["key"] for c in checks] == ["cli", "auth", "scope", "subscription"]
    assert all(c["tone"] == "error" for c in checks)
    assert "npm i -g @larksuite/cli" in checks[0]["fix_cmd"]


def test_diagnose_reports_unauthorized(monkeypatch):
    """未登录：给 auth login 命令，权限/订阅不再探测（必然失败）。"""
    from lumi.gateway.channels.feishu.minutes import diagnose

    _patch_which(monkeypatch, True)
    # 未授权时 CLI 的真实形态：available=false，且不带 tokenStatus 字段
    _patch_subprocess(
        monkeypatch,
        _fake_run(
            stdout='{"identities": {"user": {"status": "missing", "available": false}}}'
        ),
    )
    checks = diagnose("cli_x")
    assert checks[0]["tone"] == "ok" and checks[1]["tone"] == "error"
    assert "auth login" in checks[1]["fix_cmd"]


def test_diagnose_separates_cli_failure_from_unauthorized(monkeypatch):
    """CLI 跑不通 ≠ 未授权：混为一谈会把用户支去扫码，而扫码解决不了 CLI 故障。"""
    from lumi.gateway.channels.feishu.minutes import diagnose

    _patch_which(monkeypatch, True)
    _patch_subprocess(monkeypatch, _fake_run(stdout="Segmentation fault"))
    checks = diagnose("cli_x")
    auth = next(c for c in checks if c["key"] == "auth")
    assert auth["tone"] == "error"
    assert "auth login" not in auth["fix_cmd"]  # 不引导扫码
    assert "Segmentation fault" in auth["detail"]  # 真实原因带到 UI，而非泛泛一句


def test_diagnose_accepts_needs_refresh(monkeypatch):
    """needs_refresh 是可用状态（下次 user API 调用自动刷新），不得误报未授权。

    access_token 约 2 小时到期即转此状态，认死 tokenStatus == "valid" 会让每次闲置
    超时后的诊断都谎报「授权已失效」，把用户支去重新扫码。
    """
    from lumi.gateway.channels.feishu.minutes import diagnose
    from lumi.gateway.channels.feishu.scopes import MINUTES_SCOPES

    _patch_which(monkeypatch, True)
    _patch_subprocess(
        monkeypatch,
        _fake_run(
            stdout=json.dumps(
                {
                    "identities": {
                        "user": {
                            "status": "needs_refresh",
                            "available": True,
                            "tokenStatus": "needs_refresh",
                            "userName": "鄢楚威",
                            "scope": " ".join(MINUTES_SCOPES),
                        }
                    }
                }
            )
        ),
    )
    checks = diagnose("cli_x")
    auth = next(c for c in checks if c["key"] == "auth")
    assert auth["tone"] == "ok"
    # 未被 _with_blocked_tail 截断：后续项是真探测出来的
    assert next(c for c in checks if c["key"] == "scope")["tone"] == "ok"


def test_diagnose_reports_missing_scope_with_link(monkeypatch):
    """权限缺失：列出缺哪个 scope，并给开放平台直达链接。"""
    from lumi.gateway.channels.feishu.minutes import diagnose

    _patch_which(monkeypatch, True)
    _patch_subprocess(
        monkeypatch,
        _fake_run(
            stdout=json.dumps(
                {
                    "identities": {
                        "user": {
                            "available": True,
                            "userName": "鄢楚威",
                            "scope": "minutes:minutes.basic:read",  # 缺 transcript:export
                        }
                    }
                }
            )
        ),
    )
    checks = diagnose("cli_x")
    scope = next(c for c in checks if c["key"] == "scope")
    assert scope["tone"] == "error"
    assert "transcript:export" in scope["detail"]
    assert "cli_x" in scope["fix_url"]  # 链接指向该 app 的权限页


def test_diagnose_all_green(monkeypatch):
    from lumi.gateway.channels.feishu.minutes import diagnose
    from lumi.gateway.channels.feishu.scopes import MINUTES_SCOPES

    _patch_which(monkeypatch, True)
    calls = []

    def run(cmd, capture_output=False, text=False, timeout=None):
        calls.append(cmd)
        if "auth" in cmd:
            body = {
                "identities": {
                    "user": {
                        "available": True,
                        "userName": "鄢楚威",
                        "scope": " ".join(MINUTES_SCOPES),
                    }
                }
            }
        else:  # 订阅调用
            body = {"ok": True}
        return SimpleNamespace(stdout=json.dumps(body), stderr="", returncode=0)

    _patch_subprocess(monkeypatch, run)
    checks = diagnose("cli_x")
    assert all(c["tone"] == "ok" for c in checks)
    # 诊断即修复：全绿路径必然调过一次订阅接口
    assert any("subscription" in " ".join(c) for c in calls)


# ── 流式卡片投递：有锚点走 Reply，无锚点走 Create ──
def test_streaming_card_delivery_picks_api_by_anchor(monkeypatch):
    """有 reply 锚点走 Reply API，无锚点走 Create API 直投。

    后者是去掉"正在整理…"占位消息的前提：早期实现因流式卡只能 reply，
    通知轮/纪要轮不得不先发一条占位消息当锚点，那条纯噪音。
    """
    from lumi.gateway.channels.feishu import lark_call as lc_mod

    ch = FeishuChannel(FeishuChannelConfig())
    calls = {"reply": [], "create": []}
    monkeypatch.setattr(
        ch, "reply_message_sync", lambda mid, t, c: calls["reply"].append(mid) or "r"
    )
    monkeypatch.setattr(
        ch,
        "send_message_sync",
        lambda rid, t, c: calls["create"].append(rid) or "c",
    )
    # 建卡走 cardkit：桩掉 lark_call（streaming 内是局部 import，patch 源模块即可），
    # 返回带 card_id 的响应；传入的 lambda 不会被求值，故无需真实 client
    fake_resp = SimpleNamespace(data=SimpleNamespace(card_id="card_1"))
    monkeypatch.setattr(lc_mod, "lark_call", lambda op, fn, level="warning": fake_resp)

    # 无锚点 → Create 直投（receive_id_type 由 send_message_sync 按前缀自行判定）
    assert ch.streaming._create_streaming_card_sync("ou_me", None) == "card_1"
    assert calls["create"] == ["ou_me"]
    assert calls["reply"] == []

    # 有锚点 → Reply，不碰 Create
    assert ch.streaming._create_streaming_card_sync("oc_room", "m1") == "card_1"
    assert calls["reply"] == ["m1"]
    assert len(calls["create"]) == 1


def test_stream_buf_carries_chat_id():
    """buf 必须带 chat_id：_rebuild_card 只拿得到 buf，无锚点重建全靠它。"""
    ch = FeishuChannel(FeishuChannelConfig())
    buf = ch.streaming._new_buf("oc_room")
    assert buf.chat_id == "oc_room"


async def test_drain_minute_events_bridges_before_lock_check(monkeypatch):
    """回归：建桥必须早于「取锁判忙」。

    建桥是 await 点。若把它夹在 try_lock 与 async with 之间，锁会被入站消息抢走，
    而 async with 是阻塞等待（非跳过），整个 notification_loop 会卡到那一轮跑完，
    连带后台任务通知一起停摆。
    """
    ch = FeishuChannel(FeishuChannelConfig())
    fi = ch.inbound
    fi._minute_events = [inb._MinuteEvent("obcnTOK", "ou_new")]
    order: list[str] = []
    pool = ch.bridge_pool

    def spy_peek(tid):
        order.append("peek")
        return pool._bridges.get(tid)

    def spy_try_lock(tid):
        order.append("try_lock")
        return pool._locks.get(tid)

    async def spy_get(tid):
        order.append("get")
        pool._locks.setdefault(tid, asyncio.Lock())
        return object()

    monkeypatch.setattr(pool, "peek", spy_peek)
    monkeypatch.setattr(pool, "try_lock", spy_try_lock)
    monkeypatch.setattr(pool, "get", spy_get)

    async def noop(*a):
        pass

    monkeypatch.setattr(fi, "_run_minute_turn", noop)

    await fi._drain_minute_events()

    # 先探「是否已建桥」→ 建桥 → 再取锁判忙（try_lock 必须晚于 get）
    assert order[:2] == ["peek", "get"]
    assert "try_lock" in order[2:], f"建桥后必须重新判忙，实际顺序: {order}"
    # 且 get 只调一次——第一次的 bridge 直接复用，不再重复建
    assert order.count("get") == 1


async def test_drain_minute_events_keeps_event_when_bridging_fails(monkeypatch):
    """建桥失败时事件留在队列等下轮，不能已出队又丢掉。"""
    ch = FeishuChannel(FeishuChannelConfig())
    fi = ch.inbound
    fi._minute_events = [inb._MinuteEvent("obcnTOK", "ou_new")]

    async def boom(tid):
        raise RuntimeError("initialize failed")

    monkeypatch.setattr(ch.bridge_pool, "get", boom)
    await fi._drain_minute_events()
    assert fi._minute_events == [inb._MinuteEvent("obcnTOK", "ou_new")]


def test_diagnose_expands_env_var_app_id(monkeypatch):
    """app_id 支持 ${ENV} 引用；不展开会拼出点不开的修复链接。"""
    from lumi.gateway.channels.feishu.minutes import diagnose

    monkeypatch.setenv("DEMO_FEISHU_APP", "cli_real123")
    _patch_which(monkeypatch, True)
    # 未授权分支不含链接，故用缺 scope 分支验证 URL
    _patch_subprocess(
        monkeypatch,
        _fake_run(
            stdout=json.dumps(
                {"identities": {"user": {"available": True, "scope": ""}}}
            )
        ),
    )
    checks = diagnose("${DEMO_FEISHU_APP}")
    scope = next(c for c in checks if c["key"] == "scope")
    assert "cli_real123" in scope["fix_url"]
    assert "${" not in scope["fix_url"]


# ── 机器人接入体检（feishu/setup.py）──
# 四项全部从「应用版本信息」一次读出，故测试只需替换 _fetch_version 的返回。
# 样本形状取自真实接口响应：events 是中文显示名（不可用于比对），event_type 在
# event_infos 里；scopes 为对象数组而非字符串数组。


def _version_sample(**overrides) -> dict:
    from lumi.gateway.channels.feishu.scopes import (
        BOT_SCOPES,
        MESSAGE_EVENT,
        OPTIONAL_SCOPES,
    )

    sample = {
        "app_name": "楚威的助手",
        "version": "1.0.7",
        "status": 1,
        "scopes": [
            {"scope": s, "level": 1}
            for s in BOT_SCOPES + tuple(x for x, _ in OPTIONAL_SCOPES)
        ],
        "events": ["接收消息"],
        "event_infos": [{"event_name": "接收消息", "event_type": MESSAGE_EVENT}],
    }
    sample.update(overrides)
    return sample


def _patch_version(monkeypatch, version: dict | None, reason: str = "", code: int = 0):
    from lumi.gateway.channels.feishu import setup

    monkeypatch.setattr(
        setup, "_fetch_version", lambda app_id, secret: (version, reason, code)
    )


def test_setup_all_green(monkeypatch):
    """权限、事件、版本俱全时四项全通过。"""
    from lumi.gateway.channels.feishu.setup import diagnose

    _patch_version(monkeypatch, _version_sample())
    checks = diagnose("cli_x", "secret")
    assert [c["key"] for c in checks] == ["credentials", "scopes", "events", "version"]
    assert all(c["tone"] == "ok" for c in checks)


def test_setup_missing_scope_gives_prefilled_auth_link(monkeypatch):
    """缺必需权限：修复链接须预填全部权限，且带上可选项一并开通。"""
    from lumi.gateway.channels.feishu.scopes import BOT_SCOPES
    from lumi.gateway.channels.feishu.setup import diagnose

    kept = [{"scope": s} for s in BOT_SCOPES if s != "im:message:send_as_bot"]
    _patch_version(monkeypatch, _version_sample(scopes=kept))
    scopes = next(c for c in diagnose("cli_x", "secret") if c["key"] == "scopes")
    assert scopes["tone"] == "error"
    assert "im:message:send_as_bot" in scopes["detail"]
    assert "im:message:send_as_bot" in scopes["fix_url"]
    assert "token_type=tenant" in scopes["fix_url"]  # 机器人权限在应用身份 tab


def test_setup_optional_scope_missing_is_not_a_failure(monkeypatch):
    """可选权限缺失不该标红——各有降级路径，收发照常，否则用户会去修一个不影响使用的问题。

    但要按「丢了什么功能」报，而非甩一串 scope 名：这几项的影响彼此完全不同
    （显示名 vs 打字机效果），用户得据此判断值不值得去补。
    """
    from lumi.gateway.channels.feishu.scopes import BOT_SCOPES
    from lumi.gateway.channels.feishu.setup import diagnose

    _patch_version(
        monkeypatch, _version_sample(scopes=[{"scope": s} for s in BOT_SCOPES])
    )
    scopes = next(c for c in diagnose("cli_x", "secret") if c["key"] == "scopes")
    assert scopes["tone"] == "warn"
    # 降级掉的功能走 emphasis（前端加粗），不埋在 detail 里让前端切字符串
    assert "发送者姓名" in scopes["emphasis"]
    # cardkit 降级后回复仍在，只是不逐字上屏
    assert "打字机流式卡片" in scopes["emphasis"]
    assert scopes["fix_url"]  # 想补的人不该再去翻文档找 scope 名


def test_setup_detects_missing_event_subscription(monkeypatch):
    """事件没订阅时长连接照样能建立，只是一条消息都收不到——必须单独报出来。"""
    from lumi.gateway.channels.feishu.setup import diagnose

    # 订了别的事件但没订接收消息：中文 events 仍有值，只有 event_type 能判准
    _patch_version(
        monkeypatch,
        _version_sample(
            events=["消息已读"],
            event_infos=[
                {"event_name": "消息已读", "event_type": "im.message.message_read_v1"}
            ],
        ),
    )
    events = next(c for c in diagnose("cli_x", "secret") if c["key"] == "events")
    assert events["tone"] == "error"
    assert "im.message.receive_v1" in events["detail"]
    assert events["fix_url"].endswith("/event")


def test_setup_detects_unpublished_version(monkeypatch):
    """权限与事件改完不发布，表现与没配一模一样，故未发布必须报错而非全绿。"""
    from lumi.gateway.channels.feishu.setup import diagnose

    _patch_version(monkeypatch, _version_sample(status=4))
    checks = {c["key"]: c for c in diagnose("cli_x", "secret")}
    assert (
        checks["scopes"]["tone"] == "ok" and checks["events"]["tone"] == "ok"
    )  # 前两项照常判
    assert checks["version"]["tone"] == "error"
    assert "尚未提交审核" in checks["version"]["detail"]


def test_setup_blocks_rest_when_credentials_fail(monkeypatch):
    """凭证不通时后三项无从判起，统一标记而不是谎报通过。"""
    from lumi.gateway.channels.feishu.setup import diagnose

    _patch_version(monkeypatch, None, "code=10003 invalid param")
    checks = diagnose("cli_x", "bad_secret")
    assert [c["key"] for c in checks] == ["credentials", "scopes", "events", "version"]
    assert all(c["tone"] == "error" for c in checks)
    assert "10003" in checks[0]["detail"]


def test_setup_expands_env_vars_in_credentials(monkeypatch):
    """app_id 支持 ${ENV} 引用，不展开会拼出点不开的修复链接（与妙记诊断同一处坑）。"""
    from lumi.gateway.channels.feishu.scopes import BOT_SCOPES
    from lumi.gateway.channels.feishu.setup import diagnose

    monkeypatch.setenv("DEMO_FEISHU_APP", "cli_real123")
    _patch_version(
        monkeypatch, _version_sample(scopes=[{"scope": s} for s in BOT_SCOPES[:1]])
    )
    scopes = next(
        c for c in diagnose("${DEMO_FEISHU_APP}", "secret") if c["key"] == "scopes"
    )
    assert "cli_real123" in scopes["fix_url"]
    assert "${" not in scopes["fix_url"]


def test_setup_permission_denied_is_not_reported_as_bad_credentials(monkeypatch):
    """缺体检权限（99991672）时凭证其实是好的——报成「凭证无效」会把用户支去重抄 Secret。

    体检接口自身也需授权，这是整套自动判定的前置条件：必须单独识别并给出开通链接，
    且链接要连同机器人权限一并预填，否则用户开完这一个回来仍是红的。
    """
    from lumi.gateway.channels.feishu.scopes import BOT_SCOPES, SETUP_SCOPES
    from lumi.gateway.channels.feishu.setup import diagnose

    _patch_version(monkeypatch, None, "code=99991672 Access denied.", code=99991672)
    checks = diagnose("cli_x", "secret")
    cred = checks[0]
    assert cred["tone"] == "error"
    assert "凭证无效" not in cred["name"]
    assert SETUP_SCOPES[0] in cred["detail"]
    # 一键链接须覆盖体检权限 + 机器人权限，让用户只点一次
    assert SETUP_SCOPES[0] in cred["fix_url"]
    assert all(s in cred["fix_url"] for s in BOT_SCOPES)
    assert all(c["tone"] == "error" for c in checks[1:])


def test_setup_marks_degraded_instead_of_claiming_all_good(monkeypatch):
    """缺可选权限时该项 ok 但须置 warn——否则汇总条报「全部生效」，与详情里的
    「暂不可用」自相矛盾。「能用」和「完好」是两种状态。"""
    from lumi.gateway.channels.feishu.scopes import BOT_SCOPES
    from lumi.gateway.channels.feishu.setup import diagnose

    _patch_version(
        monkeypatch, _version_sample(scopes=[{"scope": s} for s in BOT_SCOPES])
    )
    checks = {c["key"]: c for c in diagnose("cli_x", "secret")}
    assert checks["scopes"]["tone"] == "warn"
    # 其余各项无降级，不该被误标
    assert all(checks[k]["tone"] == "ok" for k in ("credentials", "events", "version"))


def test_setup_no_warn_when_everything_granted(monkeypatch):
    """全部权限齐备时不得置 warn，否则汇总条永远挂着「功能降级」。"""
    from lumi.gateway.channels.feishu.setup import diagnose

    _patch_version(monkeypatch, _version_sample())
    assert all(c["tone"] == "ok" for c in diagnose("cli_x", "secret"))


def test_event_constants_match_registered_handlers():
    """scopes.py 的事件常量必须与 channel.py 实际注册的处理器一致。

    lark SDK 只认 register_p2_xxx 方法名、吃不进字符串，两边无法共用一份定义，故在
    此锁住。若改了注册点而没同步常量：体检拿旧 code 比对 event_infos，会报「事件订阅
    已配置」而机器人一条消息都收不到——正是体检本该拦住的那种静默故障。
    """
    import lark_oapi as lark

    from lumi.gateway.channels.feishu.scopes import MESSAGE_EVENT, MINUTE_EVENT

    handler = FeishuChannel(FeishuChannelConfig())._build_event_handler(lark)
    registered = set(handler._processorMap)
    # SDK 以 p2. 前缀存放 v2 事件处理器
    assert f"p2.{MESSAGE_EVENT}" in registered
    assert f"p2.{MINUTE_EVENT}" in registered


def test_setup_network_failure_is_not_reported_as_bad_credentials():
    """断网时凭证根本没被验证过，报「凭证无效」会把用户支去重抄 Secret——抄多少遍都没用。"""
    from lumi.gateway.channels.feishu import setup
    from lumi.gateway.channels.feishu.setup import diagnose

    original = setup._fetch_version
    try:
        setup._fetch_version = lambda a, s: (None, "Connection timed out", -1)
        checks = diagnose("cli_x", "secret")
    finally:
        setup._fetch_version = original
    cred = checks[0]
    assert cred["tone"] == "error"
    assert "凭证无效" not in cred["name"]
    assert "Connection timed out" in cred["detail"]
    assert not cred["fix_url"]  # 断网时给开放平台链接是误导


def test_setup_lists_optional_gaps_alongside_required_ones(monkeypatch):
    """必需与可选权限同时缺失时要一次报全，否则用户补完必需项才发现还有降级，白跑一轮。"""
    from lumi.gateway.channels.feishu.setup import diagnose

    _patch_version(monkeypatch, _version_sample(scopes=[]))
    scopes = next(c for c in diagnose("cli_x", "secret") if c["key"] == "scopes")
    assert scopes["tone"] == "error"
    assert "im:message:send_as_bot" in scopes["detail"]  # 必需项
    assert "打字机流式卡片" in scopes["detail"]  # 可选项也提前告知
