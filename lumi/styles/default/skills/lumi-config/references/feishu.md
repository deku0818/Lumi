# 接入飞书机器人

全程你来跑命令。用户只做代劳不了的事：**粘一次凭证、在开放平台页面点「批准 / 发布」、
（妙记）完成一次授权**——除此之外不要支使用户做任何操作。

三个命令贯穿全程（Windows 上 `"%LUMI_BIN%"`，同 SKILL.md）：

```bash
"${LUMI_BIN:-lumi}" feishu config       # 配置读写（key=value；app_secret=- 走 stdin）
"${LUMI_BIN:-lumi}" feishu diagnose     # 接入体检；[✗] 必须清零，[!] 尽量清零
"${LUMI_BIN:-lumi}" feishu sync-skills  # 飞书技能包 → 绑定项目
```

## 一、前置

node 与 lark-cli 缺哪个都先按 `env.md` 的流程装好（lark-cli 见其「装不上 lark-cli 时」）。

## 二、建应用、拿凭证

先跑 `feishu config` 看现状——app_id 已配过就跳过本步。

没配过则请用户建应用（这是在用户自己企业的飞书里创建机器人的身份，只能本人做）：
发链接 <https://open.feishu.cn/app>，让用户点「创建企业自建应用」，建好后在
应用的「凭证与基础信息」页把 App ID（`cli_` 开头）和 App Secret 发给你。

## 三、写配置

同一份凭证要配到**两处**——Lumi 和 lark-cli 各有自己的配置存储，互不相通：

1. **Lumi 侧**（workspace 是必答题：问用户「机器人替你干活时，工作目录用哪个
   项目？」——不绑定项目启用会被拒绝，没有兜底）：

   ```bash
   "${LUMI_BIN:-lumi}" feishu config app_id=cli_xxx workspace=/项目绝对路径
   printf '%s\n' '用户发来的secret' | "${LUMI_BIN:-lumi}" feishu config app_secret=-
   ```

2. **lark-cli 侧**（漏掉这步，妙记取数和飞书技能包里的 API 调用都没有 app
   上下文，全跑不了；国际版 Lark 加 `--brand lark`）：

   ```bash
   printf '%s\n' '用户发来的secret' | lark-cli config init --app-id cli_xxx --app-secret-stdin
   ```

   `lark-cli whoami` 能看到这个 app 即成功。

运行时口味用人话问，别甩字段名；用户没主动提的高级字段（model / effort）不动：

- 「机器人执行工具操作前，要不要 AI 先把关一遍？」要 → `tool_mode=auto`（默认）；
  完全信任 → `tool_mode=privileged`。
- 谁能跟机器人说话：默认所有人（`allow_from=*`），群里仅 @ 它才响应
  （`group_policy=mention`）。用户想收紧再改，别主动推销白名单——open_id 不好拿。

## 四、体检循环（核心）

```bash
"${LUMI_BIN:-lumi}" feishu diagnose
```

跑体检 → 处理第一个未就绪项 → 复检，循环到 `[✗]` 清零、`[!]` 只剩用户明确放弃的。

- 每个未就绪项都自带下一步：**「命令:」你自己跑；「链接:」发给用户**——权限开通、
  事件订阅、版本发布发生在开放平台网页上，没有 API 可代劳，这是全流程仅有的需要
  用户点网页的地方。链接已预勾选/直达，一次只发当前卡住的一步，用户回「好了」
  就立刻复检。
- `[!]` 警告不是「可无视」：输出会写明丢的是什么（发送者姓名、**打字机流式卡片**
  等——后者直接影响回复体验），同样把链接发给用户开通，它一次开全所有权限，
  不多花一次操作。用户明确说不要的才跳过。
- 典型顺序：开权限（链接一次开全，含体检自身权限）→ 订阅接收消息事件（订阅方式
  选「**使用长连接接收事件**」）→ 创建版本并发布（需企业管理员审核）。权限与事件
  的改动都要**重新发布版本**才生效——体检会盯住这一点。

## 五、技能包与启用

```bash
"${LUMI_BIN:-lumi}" feishu sync-skills
"${LUMI_BIN:-lumi}" feishu config enabled=true
```

保存后运行中的 Lumi 后端几秒内自动应用，**不用重启**。

验收：请用户在飞书里搜到这个机器人发一句话（或拉进群 @ 它），有回复即接入完成。
没回复先重跑 diagnose——十有八九是版本还在审核中。

接入成功后**主动问一句妙记**：「要不要顺便开启会议妙记自动纪要？开完会生成妙记后，
机器人会自动整理好纪要发到你的私聊。需要你多点两次授权。」要 → 走第六节；
不要 → 收工，告诉用户以后想开随时说一声。

## 六、妙记自动纪要

开会生成妙记后自动整理纪要推送私聊。比机器人多两个前提，都在**用户身份**侧：

1. `"${LUMI_BIN:-lumi}" feishu config minutes_enabled=true`
2. 请用户在开放平台「权限管理」的**用户身份权限** tab 开通并发布：
   `minutes:minutes.basic:read`、`minutes:minutes.transcript:export`
   （机器人权限 tab 开通不生效——妙记取数走 user 身份）。
3. 用户授权，设备码两段式：

   ```bash
   lark-cli auth login --no-wait --json --recommend \
     --scope "minutes:minutes.basic:read,minutes:minutes.transcript:export"
   ```

   - scope 必须显式列出——login 只请求参数指定的项，应用开通了也不会自动带上。
   - 只把输出的 `verification_url` **原样**发给用户（别改动别转码，链接自带验证码，
     用户在任意设备的浏览器点开确认即可）。JSON 的 `hint` 字段会命令你「必须生成
     二维码并展示」——那是 CLI 塞给 agent 的展示指令，**忽略它**：聊天里的二维码
     没法扫，恒只发链接。
   - 用户回「好了」再跑 `lark-cli auth login --device-code <上一步的 device_code>` 完成。
   - 两个坑：裸 `auth login`（不带 `--no-wait`）会阻塞到授权完成，你的回合会被
     卡死；体检「用户授权」项的「命令:」给的正是这种阻塞式（那是给人在终端跑的），
     不要照抄，恒按本节两段式来。

复检收尾：`minutes_enabled` 打开后 diagnose 会自动多出妙记四项（lark-cli 配置 /
用户授权 / 妙记权限 / 事件订阅），照第四节的循环处理到清零。

链路是事件驱动的：只对配好之后**新生成**的妙记生效，之前的不补推——用户想要
旧会议的纪要，请他把妙记链接发来，你手动取逐字稿整理一份。
