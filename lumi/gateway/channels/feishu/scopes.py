"""机器人接入所需的权限与事件，以及开放平台直达链接。

清单是诊断与「一键开通」链接的共同来源——两者错开就会出现「助手说缺权限、点过去
却没勾上」。

事件常量无法与 channel.py 共用一份定义：lark SDK 只认 register_p2_xxx 方法名，吃不
进字符串。故改由 test_event_constants_match_registered_handlers 锁住两边一致——改了
注册点而没改这里，测试即红。没有这层锁，体检会报「事件订阅已配置」而机器人收不到
任何消息，正是它本该拦住的故障。
"""

from __future__ import annotations

# 缺任一都会让机器人整体不可用
BOT_SCOPES: tuple[str, ...] = (
    "im:message",  # 获取与发送单聊、群组消息
    "im:message:send_as_bot",  # 以应用身份发消息
    "im:message.p2p_msg:readonly",  # 接收单聊消息
    "im:message.group_at_msg:readonly",  # 接收群中 @ 机器人的消息
    "im:resource",  # 下载消息里的图片与文件
)

# 缺了仍能收发，只是各自丢一项体验。带上「缺了会怎样」——一句笼统的降级提示没法让
# 用户判断该不该去补，而这几项的影响彼此完全不同（显示名 vs 打字机效果）。
OPTIONAL_SCOPES: tuple[tuple[str, str], ...] = (
    ("contact:user.base:readonly", "发送者姓名"),
    ("im:chat:read", "群名与群信息"),
    ("im:chat.members:read", "群成员名单"),
    # CardKit 全程失败时 streaming.py 会降级成普通 markdown 卡（_fallback_send），
    # 回复不会丢，只是失去逐字上屏
    ("cardkit:card:write", "打字机流式卡片"),
)

# 妙记：读内容 + 订阅事件所需，缺任一都会静默失效。必须开在「用户身份权限」tab 下
# ——lark-cli 以 --as user 取数，tenant 侧开通不会进 user_access_token
MINUTES_SCOPES: tuple[str, ...] = (
    "minutes:minutes.basic:read",
    "minutes:minutes.transcript:export",
)

# 接入体检自身所需：查本应用的版本信息（scopes / event_infos / status）也要授权，
# 未开通时接口回 99991672。取只读版而非 self_manage——体检只读不写，够用即可。
# 它与机器人无关，但必须一并塞进「一键开通」链接：否则用户开完权限回来，体检仍是红的。
SETUP_SCOPES: tuple[str, ...] = ("application:application.app_version:readonly",)

# 一键开通链接用的全集
ALL_SCOPES: tuple[str, ...] = (
    SETUP_SCOPES + BOT_SCOPES + tuple(s for s, _ in OPTIONAL_SCOPES)
)

# 与 FeishuChannel._build_event_handler 的注册项一一对应（由测试锁住，见模块 docstring）
MESSAGE_EVENT = "im.message.receive_v1"
MINUTE_EVENT = "minutes.minute.generated_v1"


def auth_url(app_id: str, scopes: tuple[str, ...], token_type: str = "tenant") -> str:
    """开放平台权限开通页直达链接，权限已预勾选。

    token_type 决定页面停在哪个 tab：机器人走应用身份（tenant），妙记走用户身份
    （user，lark-cli 以 --as user 取数，tenant 侧开通不会进 user_access_token）。
    非文档化路径但开放平台长期在用；点不开时仍可回落到权限管理页手动批量导入。
    """
    return (
        f"https://open.feishu.cn/app/{app_id}/auth"
        f"?q={','.join(scopes)}&op_from=openapi&token_type={token_type}"
    )


def event_url(app_id: str) -> str:
    """开放平台「事件与回调」配置页。"""
    return f"https://open.feishu.cn/app/{app_id}/event"
