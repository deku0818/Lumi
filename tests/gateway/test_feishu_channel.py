"""飞书 channel 纯函数 / 数据契约测试（无 lark SDK、无网络、无 bridge）。

覆盖：thread 派生、post 文本/图片抽取、多模态 content、文件附件路径安全、@mention 清理、
白名单语义。飞书已禁用 ask 工具，无 ask 卡片相关测试。
"""

from __future__ import annotations

import asyncio

import pytest

from lumi.gateway.channels.config import FeishuChannelConfig
from lumi.gateway.channels.feishu import inbound as inb
from lumi.gateway.channels.feishu.channel import FeishuChannel
from lumi.gateway.channels.feishu.inbound import (
    attach_files_to_text,
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


def test_attach_files_to_text():
    out = attach_files_to_text("看下", ["/tmp/lumi/feishu/x/a.pdf"])
    assert out == "看下\n<attached-file>/tmp/lumi/feishu/x/a.pdf</attached-file>"
    # 无文件 → 原样
    assert attach_files_to_text("看下", []) == "看下"
    # 无正文 → 只标签
    assert (
        attach_files_to_text("", ["/tmp/a"]) == "<attached-file>/tmp/a</attached-file>"
    )


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


def test_help_line_truncates_long_and_multiline_description():
    assert inb._help_line("x", "第一行\n第二行") == "`/x` 第一行"
    long = inb._help_line("y", "很" * 80)
    assert long.endswith("…") and len(long) < 80


async def test_help_lists_skill_and_system_commands(monkeypatch):
    # /help 不跑 agent、不为此建桥：恒走 available_commands（渠道桥记忆恒开，口径等价）
    ch = FeishuChannel(FeishuChannelConfig())
    sent = _sent_collector(ch, monkeypatch)
    monkeypatch.setattr(
        inb,
        "available_commands",
        lambda memory_enabled: [{"name": "commit", "description": "", "type": "skill"}],
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
