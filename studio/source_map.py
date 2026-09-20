"""
把检测器给的代码文本映射回源文件里的真实位置。

检测器输出的 `testMethodRawCode` / `testMockLines` 都是**规范化视图**，不是文件里
的原文：方法整体被去掉了类级别缩进（`@Test` 顶格、方法体 4 空格，而文件里分别是
4 和 8），换行统一成 CRLF（文件是 LF），跨物理行的语句被压成一行，有时还连带前面
的注释。实测 Dubbo 3.3.6：94 个测试方法里 **0 个**能在对应文件中逐字节找到。

这件事直接决定编辑协议能不能用。模型被要求"逐字节复制 oldString"，如果我们喂给它
的是这份规范化文本，它复制出来的缩进就是错的，每一条编辑都会以 `oldString not
found` 失败。现有的单次调用路径没踩到这个坑，是因为 `_strip_duplicate_source()`
恰好把 `testMethodRawCode` 剥掉了、只发送 `sourceFiles[].content` 里的真实文件内容
——那是运气，不是设计。

所以：检测器的文本只当"要找什么"的线索，真正进入 payload 和 oldString 的文本一律
从源文件现取。

Maps the detector's code text back to its real position in the source file.

The detector's `testMethodRawCode` / `testMockLines` are **normalized views**, not
the file's own text: a method is stripped of its class-level indentation (`@Test`
at column 0 and the body at 4, where the file has 4 and 8), newlines are
normalized to CRLF (the files are LF), a statement spanning several physical lines
is collapsed onto one, and a preceding comment is sometimes folded in. Measured on
Dubbo 3.3.6: **none** of the 94 test methods can be found byte-for-byte in their
own file.

That decides whether the edit protocol works at all. The model is told to copy
`oldString` byte-for-byte; hand it the normalized text and it copies the wrong
indentation, so every edit fails with `oldString not found`. The existing
single-call path never hit this because `_strip_duplicate_source()` happens to drop
`testMethodRawCode` and send only the real file content from `sourceFiles[]` — luck,
not design.

So: the detector's text is only ever a hint about *what* to find. Whatever reaches
a payload or an `oldString` is taken fresh from the source file.
"""

from __future__ import annotations

import re

_WHITESPACE = " \t\r\n\f\v"


def _normalize(text: str) -> tuple[str, list[int]]:
    """
    彻底去掉空白，并记下每个保留字符在原文中的下标，以便把匹配结果映射回真实偏移量。

    必须是"删除"而不是"折叠成一个空格"：检测器把跨行语句拼回一行时是直接去掉空白的
    （文件里的 `)\\n        .received(` 在它那里是 `).received(`），折叠成空格会得到
    `) .received(`，两边永远对不上。实测这一个选择把方法级定位从 151/442 提到 442/442。
    Whitespace is *removed*, not collapsed to a single space: the detector joins a
    wrapped statement by deleting the whitespace outright (the file's
    `)\\n        .received(` becomes `).received(`), so collapsing would yield
    `) .received(` and never match. Measured, this one choice takes method-level
    location from 151/442 to 442/442.
    """
    out: list[str] = []
    index: list[int] = []
    for position, char in enumerate(text):
        if char in _WHITESPACE:
            continue
        out.append(char)
        index.append(position)
    return "".join(out), index


def locate(file_content: str, snippet: str, near_line: int | None = None) -> tuple[int, int] | None:
    """
    在 `file_content` 里找 `snippet`，忽略缩进与换行差异，返回真实的 (start, end) 偏移量。

    `near_line`（1 基）用于消歧：同一段代码在文件里出现多次时，取离该行最近的一处。
    没有匹配返回 None——调用方应当据此放弃，而不是退回到模糊猜测。
    Finds `snippet` inside `file_content`, ignoring indentation and newline
    differences, and returns real (start, end) offsets. `near_line` (1-based)
    disambiguates when the same code appears more than once: the match closest to
    that line wins. Returns None when there is no match — callers should bail
    rather than fall back to a fuzzy guess.
    """
    normalized_file, index = _normalize(file_content)

    starts: list[int] = []
    for candidate in _snippet_variants(snippet):
        normalized_snippet, _ = _normalize(candidate)
        if not normalized_snippet:
            continue
        cursor = normalized_file.find(normalized_snippet)
        while cursor != -1:
            starts.append(cursor)
            cursor = normalized_file.find(normalized_snippet, cursor + 1)
        if starts:
            break
    if not starts:
        return None

    if len(starts) > 1 and near_line is not None:
        target = _offset_of_line(file_content, near_line)
        starts.sort(key=lambda position: abs(index[position] - target))
    elif len(starts) > 1:
        return None

    start = index[starts[0]]
    end = index[starts[0] + len(normalized_snippet) - 1] + 1
    return start, end


def _snippet_variants(snippet: str) -> list[str]:
    """
    检测器合成语句文本时有两种改写，靠空白归一化还不回去，按顺序退让重试：

    1. 行尾注释被挪到语句前面。文件里是 `verify(...); // 1st in subscribe`，检测器给
       `// 1st in subscribe\\nverify(...);`——顺序反了，去空白也对不上。
    2. lambda 的表达式体被补成语句。文件里是 `() -> helper.get(a, b))`，检测器给
       `helper.get(a, b);`——多了一个原文没有的分号。

    Two rewrites the detector applies when synthesising statement text cannot be undone
    by whitespace normalisation, so they are retried in order: a trailing comment moved
    in front of its statement, and a lambda's expression body completed into a statement
    with a semicolon the file does not contain.
    """
    variants = [snippet]
    without_comments = re.sub(r"(?m)^\s*//[^\n]*\n", "", snippet).strip()
    if without_comments and without_comments != snippet:
        variants.append(without_comments)
    for candidate in list(variants):
        trimmed = candidate.rstrip().rstrip(";").rstrip()
        if trimmed and trimmed != candidate:
            variants.append(trimmed)
    return variants


def _offset_of_line(text: str, line_number: int) -> int:
    offset = 0
    for _ in range(max(0, line_number - 1)):
        next_break = text.find("\n", offset)
        if next_break == -1:
            return offset
        offset = next_break + 1
    return offset


def line_of_offset(text: str, offset: int) -> int:
    """1 基行号，用于把定位结果报告回检测器的坐标系。
    1-based line number, for reporting a located span back in detector coordinates."""
    return text.count("\n", 0, offset) + 1


def enclosing_method(file_content: str, offset: int, require_unique: bool = True) -> tuple[int, int] | None:
    """
    从 `offset` 所在位置向外扩到完整的测试方法（含 `@Test` 等注解与前面的 Javadoc）。

    向前找到方法签名行，再用花括号配对找到方法结束；配对时跳过字符串、字符字面量与
    注释，避免它们里面的花括号把计数带偏。

    `require_unique` 为真时，取出的文本必须在文件里只出现一次——否则它当不了 oldString。
    不唯一就继续往外扩一层：实测中这来自匿名内部类的覆写方法（`NacosNamingServiceWrapperTest`
    里三个测试方法各自 new 了一个一模一样的匿名类），内层方法本身是合法签名、但文本重复，
    再往外扩到真正的测试方法就唯一了。
    Expands from `offset` to the enclosing test method, including its annotations and any
    preceding Javadoc. Walks back to the signature line, then brace-matches to the end,
    skipping strings, char literals and comments so braces inside them do not throw the
    count off.

    With `require_unique`, the extracted text must occur exactly once in the file —
    otherwise it cannot serve as an `oldString`. When it does not, expansion continues
    one level further out: in practice this comes from an anonymous class's overridden
    method (`NacosNamingServiceWrapperTest` news up three identical anonymous classes),
    where the inner method is a valid signature but its text repeats, and the enclosing
    test method is unique.
    """
    search_from = offset
    while True:
        brace = _enclosing_method_brace(file_content, search_from)
        if brace is None:
            return None
        end = _match_brace(file_content, brace)
        if end is None:
            return None

        start = file_content.rfind("\n", 0, brace) + 1
        while start > 0:
            previous_start = file_content.rfind("\n", 0, start - 1) + 1
            stripped = file_content[previous_start:start].strip()
            if stripped.startswith(("@", "*", "/*")):
                start = previous_start
                continue
            break

        line_end = file_content.find("\n", end)
        span = (start, len(file_content) if line_end == -1 else line_end + 1)
        if not require_unique or file_content.count(file_content[span[0]:span[1]]) == 1:
            return span
        search_from = brace


# 方法签名：可选的 throws 子句，结尾是 `名字(参数)`。lambda 的 `-> {`、控制语句的
# `if (...) {` 都不符合，这正是要排除的两类——它们是把 lambda 体误当成测试方法的来源。
# A method signature: an optional throws clause after `name(params)`. A lambda's
# `-> {` and a control statement's `if (...) {` both fail it, which is the point —
# they are what made a lambda body look like a test method.
_METHOD_SIGNATURE = re.compile(r"\b\w+\s*\([^;]*\)\s*(?:throws\s+[\w\s,.]+?)?\s*$", re.DOTALL)
_CONTROL_KEYWORDS = ("if", "for", "while", "switch", "catch", "try", "synchronized", "else", "do", "finally")


def _strip_comments(text: str) -> str:
    """去掉注释再判断签名。注释里出现 `->` 是真实存在的情况（Dubbo 有一条中文注释写着
    `instance listener -> service listener`），会让 lambda 排除规则误伤真正的方法。
    Comments are removed before the signature test. A `->` inside a comment really
    happens (Dubbo has one reading `instance listener -> service listener`) and would
    otherwise make the lambda exclusion reject a genuine method."""
    return re.sub(r"//[^\n]*|/\*.*?\*/", " ", text, flags=re.DOTALL)


def _is_method_header(header: str) -> bool:
    stripped = _strip_comments(header).strip()
    if not stripped or "->" in stripped:
        return False
    first = stripped.split("(")[0].strip().split()
    if first and first[-1] in _CONTROL_KEYWORDS:
        return False
    if stripped.startswith(("new ", "return ", "=")) or stripped.endswith("="):
        return False
    return _METHOD_SIGNATURE.search(stripped) is not None


def _enclosing_method_brace(text: str, offset: int) -> int | None:
    """由内向外找包含 `offset` 的第一个花括号块，其开头是真正的方法签名。
    Walks outward for the first brace block containing `offset` whose header is a
    genuine method signature."""
    search_from = offset
    while True:
        brace = text.rfind("{", 0, search_from)
        if brace == -1:
            return None
        end = _match_brace(text, brace)
        if end is not None and end > offset:
            header_start = max(text.rfind(char, 0, brace) for char in ";{}") + 1
            if _is_method_header(text[header_start:brace]):
                return brace
        search_from = brace


def _match_brace(text: str, open_index: int) -> int | None:
    depth = 0
    position = open_index
    length = len(text)
    while position < length:
        char = text[position]
        if char == "/" and position + 1 < length:
            following = text[position + 1]
            if following == "/":
                position = text.find("\n", position)
                if position == -1:
                    return None
                continue
            if following == "*":
                closing = text.find("*/", position + 2)
                if closing == -1:
                    return None
                position = closing + 2
                continue
        elif char in "\"'":
            position = _skip_literal(text, position)
            if position is None:
                return None
            continue
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return position + 1
        position += 1
    return None


def _skip_literal(text: str, position: int) -> int | None:
    quote = text[position]
    if quote == '"' and text.startswith('"""', position):
        closing = text.find('"""', position + 3)
        return None if closing == -1 else closing + 3
    position += 1
    while position < len(text):
        if text[position] == "\\":
            position += 2
            continue
        if text[position] == quote:
            return position + 1
        if text[position] == "\n":
            return None
        position += 1
    return None
