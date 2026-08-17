"""直连（/direct）核心：stream-json 解析纯函数 + 绑定 sidecar 语义。"""

from __future__ import annotations

import json

import lumi.gateway.channels.relay as relay
from lumi.gateway.channels.feishu.relay_turn import _source_note, _tool_key
from lumi.gateway.channels.relay import RelayEvent, parse_stream_line


def _line(data: dict) -> str:
    return json.dumps(data)


# ── parse_stream_line ──


def test_init_carries_session_id():
    events = parse_stream_line(
        _line({"type": "system", "subtype": "init", "session_id": "sid-1"}), {}
    )
    assert events == [RelayEvent("init", session_id="sid-1")]


def test_text_delta():
    events = parse_stream_line(
        _line(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "你好"},
                },
            }
        ),
        {},
    )
    assert events == [RelayEvent("delta", text="你好")]


def test_non_text_delta_ignored():
    # thinking / input_json 增量不外显
    events = parse_stream_line(
        _line(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "input_json_delta", "partial_json": "{"},
                },
            }
        ),
        {},
    )
    assert events == []


def test_tool_use_and_result_pairing():
    names: dict[str, str] = {}
    start = parse_stream_line(
        _line(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "看一下"},
                        {"type": "tool_use", "id": "toolu_1", "name": "Bash"},
                    ]
                },
            }
        ),
        names,
    )
    assert start == [RelayEvent("tool_start", name="Bash")]
    assert names == {"toolu_1": "Bash"}
    end = parse_stream_line(
        _line(
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "toolu_1"}]
                },
            }
        ),
        names,
    )
    assert end == [RelayEvent("tool_end", name="Bash")]
    assert names == {}  # 配对后清账，不泄漏


def test_orphan_tool_result_ignored():
    assert (
        parse_stream_line(
            _line(
                {
                    "type": "user",
                    "message": {
                        "content": [{"type": "tool_result", "tool_use_id": "toolu_x"}]
                    },
                }
            ),
            {},
        )
        == []
    )


def test_subagent_internal_events_hidden():
    # 子代理内部活动（parent_tool_use_id 非空）不外显，与 Lumi 本体规则一致
    events = parse_stream_line(
        _line(
            {
                "type": "stream_event",
                "parent_tool_use_id": "toolu_parent",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "内部"},
                },
            }
        ),
        {},
    )
    assert events == []


def test_result_success_and_error():
    ok = parse_stream_line(
        _line(
            {
                "type": "result",
                "subtype": "success",
                "result": "done",
                "session_id": "sid-2",
                "is_error": False,
            }
        ),
        {},
    )
    assert ok == [RelayEvent("result", text="done", session_id="sid-2")]
    err = parse_stream_line(
        _line({"type": "result", "is_error": True, "result": "boom"}), {}
    )
    assert err[0].is_error and err[0].text == "boom"


def test_garbage_lines_ignored():
    assert parse_stream_line("not json", {}) == []
    assert parse_stream_line('"just a string"', {}) == []
    assert parse_stream_line(_line({"type": "unknown_kind"}), {}) == []


# ── 绑定 sidecar ──


def test_binding_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(relay, "GLOBAL_CONFIG_DIR", tmp_path)
    tid = "feishu-chat-1"
    assert not relay.is_active(tid)

    relay.update_binding(tid, active=True, agent="claude", cwd="/proj")
    assert relay.is_active(tid)

    # 每轮 resume 派生新 sid：落盘恒追最新
    relay.update_binding(tid, session_id="sid-a")
    relay.update_binding(tid, session_id="sid-b")
    assert relay.binding_of(tid)["session_id"] == "sid-b"

    # 退出直连：active 键清掉，session_id 保留供续接
    relay.update_binding(tid, active=False)
    assert not relay.is_active(tid)
    assert relay.binding_of(tid)["session_id"] == "sid-b"

    # /clear：清 sid，绑定其余字段不动
    relay.update_binding(tid, session_id="")
    assert "session_id" not in relay.binding_of(tid)
    assert relay.binding_of(tid)["cwd"] == "/proj"


def test_binding_corrupt_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(relay, "GLOBAL_CONFIG_DIR", tmp_path)
    path = tmp_path / "channels" / "relay.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    assert relay.load_all() == {}
    assert not relay.is_active("any")


# ── 飞书侧映射 ──


def test_tool_key_aliases():
    assert _tool_key("Bash") == "bash"
    assert _tool_key("TodoWrite") == "todos"
    assert _tool_key("Task") == "agent"
    assert _tool_key("NotebookEdit") == "edit"
    assert _tool_key("WebFetch") == "webfetch"  # 查不到落表默认动作，键原样


def test_split_dir_arg():
    from lumi.gateway.channels.relay import split_dir_arg

    # 无前缀：路径 None，整段恒为任务
    assert split_dir_arg("修一下构建报错") == (None, "修一下构建报错")
    # 任务中途出现 --dir 不误判（只认开头）
    assert split_dir_arg("看下 --dir xxx 是什么") == (None, "看下 --dir xxx 是什么")
    # --dir 吃到行尾，任务从次行开始
    assert split_dir_arg("--dir ~/Cocoon/axi\n修一下报错") == (
        "~/Cocoon/axi",
        "修一下报错",
    )
    # 等号形式同样认（CLI 两种惯用形）
    assert split_dir_arg("--dir=~/Cocoon/axi") == ("~/Cocoon/axi", "")
    # 路径含空格免费支持
    assert split_dir_arg("--dir ~/My Projects/demo") == ("~/My Projects/demo", "")
    # 只有 --dir 无路径 → 路径为空串（与 None 区分：调用方据此响亮报错）
    assert split_dir_arg("--dir") == ("", "")
    # "--dirxxx" 不是本旗标：路径 None，整段仍是任务
    assert split_dir_arg("--dirty 是什么意思") == (None, "--dirty 是什么意思")
    assert split_dir_arg("") == (None, "")


def test_source_note_full_sid():
    # 完整 sid 可整段复制去 resume：不许截断
    assert "f81a9c2e-full-uuid" in _source_note("f81a9c2e-full-uuid")
    assert "—" in _source_note("")


# ── 路由在入队时定格 ──


async def test_drain_segments_by_relay_flag(monkeypatch):
    """排队期间切了 /direct：发给 Lumi 的不能被 cc 执行，反之亦然——按入队时的标记分段。"""
    from lumi.gateway.channels.config import FeishuChannelConfig
    from lumi.gateway.channels.feishu import inbound as inb
    from lumi.gateway.channels.feishu.channel import FeishuChannel

    ch = FeishuChannel(FeishuChannelConfig())
    fi = ch.inbound
    runs: list[tuple[str, list[str]]] = []

    async def fake_batch(ch_, bridge, chat_id, thread_id, batch):
        runs.append(("relay" if batch[0].relay else "lumi", [m.text for m in batch]))

    monkeypatch.setattr(fi, "_run_batch", fake_batch)
    batch = [
        inb._Pending("给 Lumi 1", relay=False),
        inb._Pending("给 Lumi 2", relay=False),
        inb._Pending("给 cc", relay=True),
        inb._Pending("又给 Lumi", relay=False),
    ]
    await fi._drain(ch, None, "chat", "t", batch)
    # 按到达顺序切成连续段，不跨段重排
    assert runs == [
        ("lumi", ["给 Lumi 1", "给 Lumi 2"]),
        ("relay", ["给 cc"]),
        ("lumi", ["又给 Lumi"]),
    ]


def test_relay_precheck_root_without_sandbox(monkeypatch):
    monkeypatch.setattr(relay.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(relay.os, "geteuid", lambda: 0)
    monkeypatch.delenv("IS_SANDBOX", raising=False)
    assert "root" in relay.relay_precheck()
    monkeypatch.setenv("IS_SANDBOX", "1")
    assert relay.relay_precheck() == ""
    monkeypatch.setattr(relay.shutil, "which", lambda _: None)
    assert "未安装" in relay.relay_precheck()
