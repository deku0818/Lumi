"""bash 命令的保守切分：只读判定、权限规则匹配、工作区边界与写保护检查共用的唯一口径。

不实现完整 bash 语法，只保证在「会执行哪些子命令、写哪些文件」上不比 bash 乐观：
引号 / 转义 / 注释 / heredoc 正文按 bash 规则处理；命令替换、进程替换与子 shell 里的
命令作为独立子命令拆出。值在执行时才确定的词（变量、命令替换、``$'…'``）含
``DYNAMIC`` 标记，调用方据此按「未知」保守处理。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 词中值在执行时才确定的部分（变量 / 命令替换 / ANSI-C 引用）以此占位
DYNAMIC = "\0"


@dataclass(frozen=True)
class Segment:
    """一条子命令。"""

    text: str  # 原文（规则匹配用）
    words: tuple[str, ...]  # 去引号后的词，不含重定向部分
    writes: tuple[str, ...]  # 写重定向目标（不含 /dev/null 与 fd 复制）


# 按最长优先排列。重定向先于分隔符判定（&> 不是后台符）
_REDIRECTS: dict[str, str] = {
    "&>>": "write",
    "&>": "write",
    "<<<": "data",
    "<<-": "heredoc",
    "<<": "heredoc",
    ">>": "write",
    ">|": "write",
    ">&": "dup",
    "<>": "write",
    "<&": "data",
    ">": "write",
    "<": "data",
}
_SEPARATORS: tuple[str, ...] = (
    ";;&",
    ";;",
    ";&",
    "&&",
    "||",
    "|&",
    "|",
    ";",
    "&",
    "\n",
    "(",
    ")",
)
# 子命令开头的 shell 语法关键字：不改变接下来执行哪条命令（then rm ≡ rm），切掉后
# 规则才能匹配到真正的命令名。变量赋值前缀（X=1 cmd）会改变命令行为，保留。
_KEYWORDS: frozenset[str] = frozenset(
    {
        "if",
        "then",
        "elif",
        "else",
        "fi",
        "do",
        "done",
        "while",
        "until",
        "!",
        "{",
        "}",
        "time",
    }
)


def parse_command(command: str) -> tuple[Segment, ...]:
    """切分为子命令（含命令替换 / 进程替换 / 子 shell 内的命令）。"""
    lexer = _Lexer(command)
    lexer.run(nested=False)
    return tuple(lexer.segments)


def has_background_operator(command: str) -> bool:
    """命令中（含替换 / 子 shell 内）是否有后台符 ``&``——其进程会脱离追踪。"""
    lexer = _Lexer(command)
    lexer.run(nested=False)
    return lexer.background


@dataclass
class _Builder:
    """正在累积的一条子命令。"""

    start: int
    words: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    starts: list[int] = field(default_factory=list)  # words 各词在原文中的起点
    word: list[str] = field(default_factory=list)
    word_start: int = 0
    quoted: bool = False  # 当前词含引号（空串 "" 也算一个词）
    pending: str = ""  # 当前词是哪种重定向的操作数

    def add(self, text: str) -> None:
        self.word.append(text)

    def at_word_start(self) -> bool:
        return not self.word and not self.quoted

    def end_word(self, heredocs: list[tuple[str, bool]]) -> None:
        if self.at_word_start():
            return
        word, kind = "".join(self.word), self.pending
        if kind == "write" or (kind == "dup" and not (word.isdigit() or word == "-")):
            if word != "/dev/null":
                self.writes.append(word)
        elif kind == "heredoc":
            heredocs.append((word, self.quoted))
        elif not kind:
            self.words.append(word)
            self.starts.append(self.word_start)
        self.word, self.quoted, self.pending = [], False, ""


class _Lexer:
    def __init__(self, source: str) -> None:
        self.s = source
        self.i = 0
        self.segments: list[Segment] = []
        self.background = False  # 遇到过后台符 &

    def run(self, nested: bool) -> None:
        """从当前位置切分到串尾；``nested`` 时到未配对的 ``)`` 为止（命令 / 进程替换）。"""
        s = self.s
        seg = _Builder(self.i)
        heredocs: list[tuple[str, bool]] = []
        depth = 0
        while self.i < len(s):
            c = s[self.i]
            if seg.at_word_start():
                seg.word_start = self.i
            if c in " \t":
                seg.end_word(heredocs)
                self.i += 1
            elif c == "\\":
                if s[self.i + 1 : self.i + 2] != "\n":  # 反斜杠续行不产生字符
                    seg.add(s[self.i + 1 : self.i + 2])
                self.i += 2
            elif c == "'":
                end = s.find("'", self.i + 1)
                end = len(s) if end < 0 else end
                seg.add(s[self.i + 1 : end])
                seg.quoted = True
                self.i = end + 1
            elif c == '"':
                self.i += 1
                self._expansions(seg, stop='"', limit=len(s))
                seg.quoted = True
            elif c == "`":
                self._backtick(seg)
            elif c == "$":
                self._dollar(seg)
            elif c == "#" and seg.at_word_start():
                end = s.find("\n", self.i)
                self.i = len(s) if end < 0 else end
            elif s.startswith(("<(", ">("), self.i):
                self.i += 2
                self.run(nested=True)
                seg.add(DYNAMIC)
            elif op := next((r for r in _REDIRECTS if s.startswith(r, self.i)), ""):
                if "".join(seg.word).isdigit() and not seg.quoted:
                    seg.word = []  # 2>file 的 2 是 fd 号，不是参数
                seg.end_word(heredocs)
                seg.pending = _REDIRECTS[op]
                self.i += len(op)
            elif op := next((p for p in _SEPARATORS if s.startswith(p, self.i)), ""):
                if op == ")" and nested and depth == 0:
                    self.i += 1
                    break
                depth += {"(": 1, ")": -1}.get(op, 0)
                self.background |= op == "&"
                seg = self._flush(seg, heredocs, self.i)
                self.i += len(op)
                if op == "\n" and heredocs:
                    self._heredoc_bodies(heredocs)
                    heredocs = []
                seg.start = self.i
            else:
                seg.add(c)
                self.i += 1
        end = self.i - 1 if nested and s[self.i - 1 : self.i] == ")" else self.i
        self._flush(seg, heredocs, end)

    def _flush(
        self, seg: _Builder, heredocs: list[tuple[str, bool]], end: int
    ) -> _Builder:
        seg.end_word(heredocs)
        k = 0
        while k < len(seg.words) and seg.words[k] in _KEYWORDS:
            k += 1
        if k < len(seg.words) or seg.writes:
            start = seg.starts[k] if k < len(seg.words) else seg.start
            text = self.s[start:end].strip()
            self.segments.append(Segment(text, tuple(seg.words[k:]), tuple(seg.writes)))
        return _Builder(end)

    def _expansions(self, seg: _Builder, stop: str, limit: int) -> None:
        """双引号式上下文：只认转义、``$`` 展开与反引号，到 ``stop`` 或 ``limit`` 为止。"""
        s = self.s
        while self.i < limit:
            c = s[self.i]
            if c == stop:
                self.i += 1
                return
            if c == "\\":
                seg.add(s[self.i + 1 : self.i + 2])
                self.i += 2
            elif c == "`":
                self._backtick(seg)
            elif c == "$":
                self._dollar(seg)
            else:
                seg.add(c)
                self.i += 1

    def _dollar(self, seg: _Builder) -> None:
        s = self.s
        nxt = s[self.i + 1 : self.i + 2]
        if s.startswith("$((", self.i):  # 算术：& 是位与、<< 是位移，括号配平越过
            depth, self.i = 2, self.i + 3
            while self.i < len(s) and depth:
                depth += {"(": 1, ")": -1}.get(s[self.i], 0)
                self.i += 1
        elif nxt == "(":  # 命令替换
            self.i += 2
            self.run(nested=True)
        elif nxt == "{":
            self.i += 2
            self._expansions(seg, stop="}", limit=len(s))
        elif nxt == "'":  # ANSI-C 引用：\ 转义任意字符
            j = self.i + 2
            while j < len(s) and s[j] != "'":
                j += 2 if s[j] == "\\" else 1
            self.i = j + 1
        elif nxt == '"':  # $"…" 同双引号
            self.i += 2
            self._expansions(seg, stop='"', limit=len(s))
            seg.quoted = True
            return
        elif nxt and (nxt.isalnum() or nxt in "_@*#?$!-"):
            j = self.i + 2
            if nxt.isalpha() or nxt == "_":
                while j < len(s) and (s[j].isalnum() or s[j] == "_"):
                    j += 1
            self.i = j
        else:
            seg.add("$")
            self.i += 1
            return
        seg.add(DYNAMIC)

    def _backtick(self, seg: _Builder) -> None:
        s = self.s
        j = self.i + 1
        while j < len(s) and s[j] != "`":
            j += 2 if s[j] == "\\" else 1
        inner = _Lexer(s[self.i + 1 : j].replace("\\`", "`"))
        inner.run(nested=False)
        self.segments.extend(inner.segments)
        self.i = j + 1
        seg.add(DYNAMIC)

    def _heredoc_bodies(self, heredocs: list[tuple[str, bool]]) -> None:
        """越过各段 heredoc 正文；定界词未加引号的正文照常展开，其中的命令替换照拆。"""
        s = self.s
        for delimiter, quoted in heredocs:
            while self.i < len(s):
                end = s.find("\n", self.i)
                end = len(s) if end < 0 else end
                if s[self.i : end].strip() == delimiter:
                    self.i = end + 1
                    break
                if not quoted:
                    self._expansions(_Builder(self.i), stop="\n", limit=end)
                self.i = end + 1
