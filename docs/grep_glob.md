# Grep & Glob 工具

Lumi 内置了 `grep` 和 `glob` 两个文件搜索工具，分别用于文件内容搜索和文件路径匹配。`grep` 底层优先使用 ripgrep（`rg`），不可用时自动降级到纯 Python 实现。

---

## Grep 工具

基于 ripgrep 的强力搜索工具，支持正则表达式、多种输出模式、上下文行和分页。

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `pattern` | `str` | （必填） | 正则表达式搜索模式 |
| `path` | `str \| None` | `None` | 搜索目录，默认为当前工作目录 |
| `glob` | `str \| None` | `None` | 文件过滤 glob 模式（如 `*.py`、`*.{ts,tsx}`），映射到 `rg --glob` |
| `type` | `str \| None` | `None` | 按文件类型搜索（`rg --type`），如 `py`、`js`、`rust` |
| `output_mode` | `str` | `"files_with_matches"` | 输出模式：`content`、`files_with_matches`、`count` |
| `case_insensitive` | `bool` | `False` | 大小写不敏感搜索（`rg -i`） |
| `multiline` | `bool` | `False` | 多行匹配模式，`.` 可匹配换行符（`rg -U --multiline-dotall`） |
| `after_context` | `int \| None` | `None` | 匹配行之后显示的行数（`rg -A`） |
| `before_context` | `int \| None` | `None` | 匹配行之前显示的行数（`rg -B`） |
| `context` | `int \| None` | `None` | 匹配行前后显示的行数（`rg -C`） |
| `line_number` | `bool` | `True` | 输出中显示行号 |
| `offset` | `int` | `0` | 跳过前 N 条结果（分页偏移） |
| `head_limit` | `int` | `0` | 限制返回结果数，`0` 表示不限制 |

### 输出模式

| 模式 | 说明 | 返回格式 |
|---|---|---|
| `content` | 显示匹配行内容，支持上下文行和行号 | 分页结果，含 `total`、`offset`、`truncated` |
| `files_with_matches` | 仅显示包含匹配的文件路径（默认） | 文件路径列表 |
| `count` | 显示每个文件的匹配计数 | 文件路径 + 匹配数 |

### 使用示例

```python
# 搜索所有 Python 文件中的函数定义
await grep.ainvoke({"pattern": "def \\w+", "type": "py", "output_mode": "content"})

# 大小写不敏感搜索，带上下文
await grep.ainvoke({
    "pattern": "error",
    "case_insensitive": True,
    "context": 2,
    "output_mode": "content",
})

# 仅列出包含 TODO 的文件
await grep.ainvoke({"pattern": "TODO", "output_mode": "files_with_matches"})

# 统计每个文件的匹配数
await grep.ainvoke({"pattern": "import", "glob": "*.py", "output_mode": "count"})

# 分页：跳过前 100 条，取 50 条
await grep.ainvoke({
    "pattern": "log",
    "output_mode": "content",
    "offset": 100,
    "head_limit": 50,
})
```

---

## Glob 工具

基于 `wcmatch` 的文件路径匹配工具，支持递归搜索和扩展 glob 语法。

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `pattern` | `str` | （必填） | glob 匹配模式 |
| `path` | `str` | `"."` | 搜索起始目录 |

### 支持的 glob 语法

| 模式 | 说明 | 示例 |
|---|---|---|
| `*` | 匹配单层目录中的任意字符 | `*.py` |
| `**` | 递归匹配零或多层目录 | `**/*.py` |
| `?` | 匹配单个字符 | `test_?.py` |
| `{a,b}` | 花括号展开 | `*.{ts,tsx}` |
| `[abc]` | 字符集 | `[Rr]eadme*` |

### 输出格式

返回匹配文件列表，每个文件包含路径、大小和修改时间，按修改时间倒序排列：

```
找到 3 个文件:
  src/main.py (2.1KB, 2026-03-05)
  src/utils.py (1.3KB, 2026-03-04)
  tests/test_main.py (0.8KB, 2026-03-03)
```

### 使用示例

```python
# 查找所有 Python 文件
await glob.ainvoke({"pattern": "**/*.py"})

# 查找特定目录下的配置文件
await glob.ainvoke({"pattern": "*.{yaml,yml,json}", "path": "config"})
```

---

## 实现细节

- **ripgrep 优先**：`grep` 优先调用系统 ripgrep，不可用时自动降级到纯 Python 正则搜索
- **大小写处理**：默认大小写敏感（显式传 `--case-sensitive`），避免 ripgrep 的 smart-case 行为
- **权限集成**：搜索路径受工作区边界保护，超出授权目录的搜索会被拦截
- **分页机制**：`content` 模式下，后端返回全量匹配后在应用层做 `offset` + `head_limit` 截断，默认上限 1000 条
