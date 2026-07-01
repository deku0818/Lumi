# read 工具 — 多模态文件读取

`read` 工具除了读取文本文件外，还支持**图片**和 **PDF**，会自动将其渲染为图片 block 注入对话上下文，让模型直接"看到"文件内容。

---

## 支持的文件类型

| 类别 | 扩展名 | 行为 |
|---|---|---|
| 文本 | 其他所有扩展名 | 返回带行号的文本，支持 `offset` / `limit` 分段 |
| 图片 | `.png` `.jpg` `.jpeg` `.gif` `.webp` | 走图片压缩管线，作为 `image` block 注入 |
| PDF | `.pdf` | 每页渲染为图片，作为多个 `image` block 注入 |

类型识别基于**扩展名**，但 PDF 会额外校验 `%PDF-` magic bytes，防止伪装文件污染 session。

---

## 参数

```python
read(
    file_path: str,        # 文件路径
    offset: int = 0,       # 文本:起始行号(从 0 开始)
    limit: int = 200,      # 文本:最大读取行数
    pages: str | None = None,  # PDF:页码范围
)
```

### `pages`(仅 PDF)

| 格式 | 含义 |
|---|---|
| `"5"` | 第 5 页 |
| `"1-5"` | 第 1-5 页 |
| `"1,3,5"` | 第 1、3、5 页 |
| `"1-3,7,9-10"` | 混合范围 |

单次调用最多 **20 页**。页码从 1 开始。

---

## PDF 读取策略

| PDF 页数 | 是否必须传 `pages` | 说明 |
|---|---|---|
| ≤ 10 页 | 否 | 不传时整体渲染所有页 |
| 11-20 页 | 是 | 必须传 `pages`，否则报错提示 |
| > 20 页 | 是，且每次 ≤ 20 页 | 需分多次调用 |

PDF 大小上限 **100 MB**，超过会拒绝读取。

---

## 图片压缩管线

图片会经过两阶段压缩：

1. **阶段 1:满足 API 硬约束** — ≤ 5 MB base64、≤ 2000x2000 px
2. **阶段 2:满足 token 预算** — 默认单张图片 25k tokens

压缩策略依次为:保分辨率压缩(PNG palette / JPEG 降质) → resize → 兜底 400x400 JPEG q20。

PDF 每页先以 150 DPI 渲染为 PNG，再各自走图片压缩管线，按页数平均 token 预算。

---

## 返回格式

**文本文件**:返回带行号的字符串（与原行为一致）。

**图片/PDF**:返回 `Command`，update 中包含两条消息：

1. `ToolMessage`:文本摘要（尺寸、媒体类型、字节数 / PDF 页码）
2. `HumanMessage`(meta):`<system-reminder>` 文本 + `image` block(s)

meta 标记使这条消息在 restore / session 列表中不显示，仅用于模型侧上下文。

---

## 错误处理

所有媒体读取错误都带有可执行的 `hint`，例如：

- 空文件 → 提示检查文件完整性
- 非 PDF 文件伪装成 `.pdf` → 提示用对应工具打开
- PDF 超过 10 页未传 `pages` → 提示 `pages='1-10'` 示例
- `pages` 页码越界 → 提示有效范围

错误返回时 `ToolMessage.status="error"`,TUI 会以错误样式渲染。

---

## 无视觉能力主模型：vision 工具

主模型不具备视觉能力时，`read` 直接注入 image block 它是看不懂的。为此提供一个独立的
**`vision` 工具**（`lumi/agents/tools/providers/vision.py`）：

```
vision(file_path, question)
```

- 主模型带着**自己的具体问题**调用（如「这张发票的总金额是多少」），可对同一文件反复追问
- `file_path` 支持**本地路径**与 **http(s) URL**；URL 按内容嗅探（`%PDF-` magic → PDF，否则图片）
- 图片/PDF 复用 `filesystem/media.py` 的压缩管线转 base64，按视觉模型 provider 转格式后单次问答，返回文字
- **仅当 config.yaml 配置了 `vision.model` 时才注册**（`get_vision_tools` 条件加载）；未配则工具不出现

视觉辅助模型在 **config.yaml** 中配置（重启生效）：

```yaml
vision:
  model: "qwen-vl-max"   # 视觉辅助模型名；空 = 不启用 vision 工具
  base_url: ""            # 留空则复用 providers.json 里含该模型的 profile 连接
  api_key: ""             # 留空则复用 providers.json 里含该模型的 profile 连接
```

> 桌面上传的图片本身也会经 `persist_image_blocks` 存到 `~/.lumi/uploads/` 并以
> `<attached-file>` 路径引用交给模型（与普通文件一致）；read/vision 为只读工具、不受工作区
> 边界限制，故能直接读取。
