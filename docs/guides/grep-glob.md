# Grep & Glob 工具使用指南

Lumi 内置 `grep` 和 `glob` 两个文件搜索工具，分别用于文件内容搜索和文件路径匹配。

---

## Grep 工具

基于 ripgrep 的搜索工具，支持正则表达式、多种输出模式、上下文行和分页。ripgrep 不可用时自动降级到纯 Python 实现。

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `pattern` | `str` | （必填） | 正则表达式搜索模式 |
| `path` | `str \| None` | `None` | 搜索目录，默认为当前工作目录 |
| `glob` | `str \| None` | `None` | 文件过滤 glob 模式（如 `*.py`） |
| `type` | `str \| None` | `None` | 按文件类型搜索（如 `py`、`js`） |
| `output_mode` | `str` | `"files_with_matches"` | 输出模式（见下文） |
| `case_insensitive` | `bool` | `False` | 大小写不敏感搜索 |
| `multiline` | `bool` | `False` | 多行匹配模式 |
| `after_context` | `int \| None` | `None` | 匹配行之后显示的行数 |
| `before_context` | `int \| None` | `None` | 匹配行之前显示的行数 |
| `context` | `int \| None` | `None` | 匹配行前后显示的行数 |
| `offset` | `int` | `0` | 跳过前 N 条结果（分页偏移） |
| `head_limit` | `int` | `0` | 限制返回结果数 |

### 输出模式

| 模式 | 说明 |
|---|---|
| `content` | 显示匹配行内容，支持上下文行和行号 |
| `files_with_matches` | 仅显示包含匹配的文件路径（默认） |
| `count` | 显示每个文件的匹配计数 |

---

## Glob 工具

基于 `wcmatch` 的文件路径匹配工具。

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `pattern` | `str` | （必填） | glob 匹配模式 |
| `path` | `str` | `"."` | 搜索起始目录 |

### Glob 语法

| 模式 | 说明 | 示例 |
|---|---|---|
| `*` | 匹配单层目录中的任意字符 | `*.py` |
| `**` | 递归匹配零或多层目录 | `**/*.py` |
| `?` | 匹配单个字符 | `test_?.py` |
| `{a,b}` | 花括号展开 | `*.{ts,tsx}` |
| `[abc]` | 字符集 | `[Rr]eadme*` |

返回按修改时间倒序排列的匹配文件列表，包含路径、大小和修改时间。
