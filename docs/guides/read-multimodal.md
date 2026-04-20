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

## 相关配置

与图片相关的配置项见 [config.md - vision_mode](config.md#agents--agent-配置):

- `vision_mode: model`(默认)— 把图片 block 发给模型处理
- `vision_mode: tool` — 图片转占位文本，不占用 vision token
