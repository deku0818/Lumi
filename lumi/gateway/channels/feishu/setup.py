"""机器人接入体检：权限、事件订阅、版本发布。

三者任缺其一，机器人都是「连上了但不回消息」，且开放平台不会有任何报错。所幸
``应用版本信息`` 接口把三者一次性吐出来（scopes / event_infos / status），故整条
链路可以自动判定，无需让用户自陈「我配好了」。

代价是这个接口自己也要授权（SETUP_SCOPES，未开通回 99991672）。故体检有一层引导：
先开通它，之后一切自动——「一键开通」链接把它与机器人权限一并预填，只需点一次。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict

from lumi.gateway.channels.feishu.checks import Check, blocked_tail
from lumi.gateway.channels.feishu.lark_call import NETWORK_ERROR, lark_call_classified
from lumi.gateway.channels.feishu.scopes import (
    ALL_SCOPES,
    BOT_SCOPES,
    MESSAGE_EVENT,
    OPTIONAL_SCOPES,
    SETUP_SCOPES,
    auth_url,
    event_url,
)

# 应用无该接口权限，与「凭证无效」是两回事（见 diagnose 的分支注释）
_PERMISSION_DENIED = 99991672
# 拿到响应但没有版本条目，与上面两者也不同：凭证与权限都没问题，就是还没建过版本
_NO_VERSION = -2

_STEPS: tuple[tuple[str, str], ...] = (
    ("credentials", "应用凭证"),
    ("scopes", "机器人权限"),
    ("events", "事件订阅"),
    ("version", "版本发布"),
)

# 版本审核状态，取值见开放平台「获取应用版本列表」
_PUBLISHED = 1
_STATUS_TEXT = {2: "审核被拒绝", 3: "审核中", 4: "尚未提交审核"}


def _fetch_version(app_id: str, app_secret: str) -> tuple[dict | None, str, int]:
    """取最新一个应用版本，失败时返回 ``(None, 原因, 错误码)``。

    错误码要带出来：缺 SETUP_SCOPES（99991672）、请求未送达（NETWORK_ERROR）、还没
    建过版本（_NO_VERSION）与凭证本身无效是四种不同的故障，前三者凭证要么是好的、
    要么根本没被验证，一律报「凭证无效」会把用户支去重抄 App Secret 白忙一场。
    """
    import lark_oapi as lark

    client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
    request = (
        lark.BaseRequest.builder()
        .http_method(lark.HttpMethod.GET)
        .uri("/open-apis/application/v6/applications/me/app_versions")
        .queries([("page_size", "1"), ("lang", "zh_cn")])
        .token_types({lark.AccessTokenType.TENANT})
        .build()
    )
    response, code, reason = lark_call_classified(
        "application/v6 app_versions", lambda: client.request(request)
    )
    if response is None:
        return None, reason, code
    # 走到这里 lark 已判定响应成功，其 body 必是合法 JSON
    items = (json.loads(response.raw.content).get("data") or {}).get("items") or []
    if not items:
        return None, "应用还没有任何版本，需先在开放平台创建版本并发布", _NO_VERSION
    return items[0], "", 0


def _fail(name: str, why: str, **kw) -> list[dict]:
    """第①项失败即短路：后三项无从判起，统一标记为「需先…」。"""
    return blocked_tail(
        [Check(key="credentials", name=name, tone="error", **kw)], _STEPS, why
    )


def diagnose(app_id: str, app_secret: str) -> list[dict]:
    """逐项体检机器人接入，返回可直接下发给 desktop 的 dict 列表。

    同步实现（网络调用），调用方需丢线程池。
    """
    # 两者都支持 ${ENV_VAR} 引用（见 FeishuChannelConfig）：不展开会拿空凭证请求，
    # 也会拼出 https://open.feishu.cn/app/${FEISHU_APP_ID}/auth 这种点不开的链接
    app_id = os.path.expandvars(app_id)
    app_secret = os.path.expandvars(app_secret)

    if not app_id or not app_secret:
        return _fail(
            "缺少 App ID 或 App Secret",
            "需先填写凭证",
            detail="在开放平台「凭证与基础信息」页复制",
            fix_url="https://open.feishu.cn/app",
        )

    # ① 凭证：拉版本信息本身就验证了 app_id / app_secret，后三项也复用这份数据。
    # 四种失败各有各的下一步动作，故分开报——都甩「凭证无效」会把用户支去重抄 Secret
    version, reason, code = _fetch_version(app_id, app_secret)
    if code == NETWORK_ERROR:
        # 请求没到开放平台，凭证根本没被验证过，给 fix_url 是误导
        return _fail(
            "无法连接飞书开放平台",
            "需先恢复网络",
            detail=reason,
            fix_note="检查网络 / 代理后重试；凭证是否正确尚未验证",
        )
    if code == _PERMISSION_DENIED:
        # 凭证是好的，只是没授权体检自己读版本信息。链接连同机器人权限一并开通，
        # 免得用户开完这一个回来发现又缺别的——一次点击把全部权限申请完
        return _fail(
            "尚未开通体检所需权限",
            "需先开通体检权限",
            detail="凭证有效，但读取应用版本信息需要 " + "、".join(SETUP_SCOPES),
            fix_url=auth_url(app_id, ALL_SCOPES),
            fix_note="链接已预填机器人所需的全部权限，开通后发布版本，再回来重新检查",
        )
    if code == _NO_VERSION:
        return _fail(
            "应用还没有任何版本",
            "需先创建并发布版本",
            detail=reason,
            fix_url=f"https://open.feishu.cn/app/{app_id}/publish",
        )
    if version is None:
        return _fail(
            "凭证无效或应用不可用",
            "需先修正凭证",
            detail=reason,
            fix_url="https://open.feishu.cn/app",
        )

    checks = [
        Check(
            key="credentials",
            name="应用凭证有效",
            detail=str(version.get("app_name") or ""),
        )
    ]

    # ② 权限：缺必需项即失败；可选项只影响显示名，降级提示不拦路。
    # 读的是最新一个版本——用户改了权限但没发布时，这里看到的是草稿里的新权限（显示
    # 已开通，实则未生效）。不额外区分：此时第④项必然报「未发布」，两条合起来仍指向
    # 同一个动作（去发布），而单独为此多拉一次已发布版本并不能给出更好的指引。
    granted = {s.get("scope") for s in (version.get("scopes") or [])}
    missing = [s for s in BOT_SCOPES if s not in granted]
    # 按丢失的功能报，而不是甩一串 scope 名——用户要判断的是「值不值得去补」
    lost = [what for scope, what in OPTIONAL_SCOPES if scope not in granted]
    if missing:
        # 可选项也一并列出：链接本就一次开全，藏着它会让用户开完必需项、重新检查，
        # 才第一次看到还有降级要补，白跑一轮开放平台
        checks.append(
            Check(
                key="scopes",
                name="缺少机器人权限",
                tone="error",
                detail="未开通："
                + "、".join(missing)
                + (f"；另缺可选权限，将失去：{'、'.join(lost)}" if lost else ""),
                fix_url=auth_url(app_id, ALL_SCOPES),
                fix_note="链接已预填全部权限（含可选），开通后需发布版本才生效",
            )
        )
    else:
        checks.append(
            Check(
                key="scopes",
                name="机器人权限已开通",
                tone="warn" if lost else "ok",
                detail="收发消息、下载资源"
                + ("；缺可选权限，暂不可用：" if lost else ""),
                emphasis="、".join(lost),
                # 可选项不拦路但仍给链接：想补的人不该再去翻文档找 scope 名
                fix_url=auth_url(app_id, ALL_SCOPES) if lost else "",
            )
        )

    # ③ 事件订阅：没有它长连接照样能建立，但一条消息都收不到
    events = {e.get("event_type") for e in (version.get("event_infos") or [])}
    if MESSAGE_EVENT not in events:
        checks.append(
            Check(
                key="events",
                name="未订阅接收消息事件",
                tone="error",
                detail=f"缺少 {MESSAGE_EVENT}，机器人收不到任何消息",
                fix_url=event_url(app_id),
                fix_note="订阅方式选「使用长连接接收事件」，添加事件后需发布版本",
            )
        )
    else:
        checks.append(Check(key="events", name="事件订阅已配置", detail=MESSAGE_EVENT))

    # ④ 发布：权限与事件的改动都只在发布后生效，漏这步的表现与没配一模一样
    status = version.get("status")
    if status != _PUBLISHED:
        checks.append(
            Check(
                key="version",
                name="最新版本未发布",
                tone="error",
                detail=f"v{version.get('version') or '?'} {_STATUS_TEXT.get(status, '状态异常')}，"
                "此前的权限与事件改动尚未生效",
                fix_url=f"https://open.feishu.cn/app/{app_id}/publish",
                fix_note="在开放平台创建版本并提交，由管理员审核通过后生效",
            )
        )
    else:
        checks.append(
            Check(
                key="version",
                name="版本已发布",
                detail=f"v{version.get('version') or ''}",
            )
        )
    return [asdict(c) for c in checks]
