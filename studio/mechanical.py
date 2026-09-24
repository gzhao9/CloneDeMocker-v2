"""
确定性（不调模型）的机械式重构：把测试方法里的局部 mock 换成类级别的共享字段。

这一类改动（论文 Integration 步骤里"没有共享 stub"的那条分支）本质上只有两个
动作——删掉局部创建、把引用改名到字段——不需要任何语义判断，因此由代码来做
比调模型更快、更便宜，也更重要的是**可复现**：同一份输入永远得到同一份输出，
不会因为采样而在两次跑批之间漂移。

代码只在能够证明安全时才动手。任何一个前置条件不成立（变量被重新赋值、名字
冲突、lambda 里重新声明、检测器给的源码与文件内容对不上……）都会返回一个具体
的 bail 原因，交回给 LLM 分支去处理，而不是猜。真正落地之后还有 harness 的编译
和测试兜底，失败会带着诊断进入既有的修复循环。

Deterministic (model-free) mechanical refactoring: replace a test method's local
mock with a class-level shared field.

This class of change (the "no shared stubbing" branch of the paper's Integration
step) consists of exactly two actions — delete the local creation, rename the
references to the field — and needs no semantic judgement, so code does it faster
and cheaper than a model would. More importantly it is reproducible: the same
input always yields the same output, with no sampling drift between batch runs.

The code acts only when it can prove the change is safe. Any unmet precondition
(the variable is reassigned, the name collides, a lambda redeclares it, the
detector's source does not match the file, ...) returns a concrete bail reason and
hands the case to the LLM branch instead of guessing. Whatever does land is still
backed by the harness's compile/test gates, and failures enter the existing repair
loop with diagnostics attached.
"""

from __future__ import annotations

import re

# 字符串/字符字面量、行注释、块注释。掩码时用等长占位符替换，保证所有偏移量不变，
# 因此可以在掩码副本上定位、再把改动应用回原文。
# String/char literals and line/block comments. Masking replaces them with
# equal-length filler so every offset is preserved — matches can be located on the
# masked copy and applied back to the original text.
_MASKABLE = re.compile(
    r'"""(?:\\.|[^\\])*?"""'      # text block (Java 15+)
    r"|\"(?:\\.|[^\"\\\n])*\""    # string literal
    r"|'(?:\\.|[^'\\\n])*'"       # char literal
    r"|//[^\n]*"                  # line comment
    r"|/\*.*?\*/",                # block comment
    re.DOTALL,
)

_MOCK_CALL = r"(?:\w+\.)*(?:mock|spy)\s*\("


def _mask(source: str) -> str:
    """把字面量与注释替换为等长空白，避免在其中误匹配标识符。
    Blanks out literals and comments (length-preserving) so identifier matches
    never land inside them."""
    return _MASKABLE.sub(lambda match: " " * len(match.group(0)), source)


def _identifier_spans(masked: str, name: str) -> list[tuple[int, int]]:
    """标识符位置。排除 `.name` 这种成员访问——那是别的对象的成员，不是这个局部变量。
    Identifier positions, excluding `.name` member access: that belongs to another
    object, not to this local variable."""
    spans = []
    for match in re.finditer(r"\b" + re.escape(name) + r"\b", masked):
        before = masked[:match.start()].rstrip()
        if before.endswith(".") and not before.endswith(".."):
            continue
        spans.append(match.span())
    return spans


def _declares_in_nested_scope(masked: str, name: str) -> bool:
    """lambda 形参或嵌套块里重新声明了同名变量——改名会跨越作用域边界。
    A lambda parameter or nested declaration reuses the name; renaming would cross
    a scope boundary."""
    if re.search(r"(?:\(|,)\s*(?:final\s+)?(?:[\w.<>\[\]?, ]+\s+)?" + re.escape(name) + r"\s*\)?\s*->", masked):
        return True
    return bool(re.search(r"->\s*\{[^}]*\b[\w.<>\[\]?, ]+\s+" + re.escape(name) + r"\s*[=;]", masked, re.DOTALL))


def _apply(source: str, replacements: list[tuple[int, int, str]]) -> str:
    """按位置从后往前替换，避免前面的改动移动后面的偏移量。
    Applies replacements back-to-front so earlier edits never shift later offsets."""
    result = source
    for start, end, text in sorted(replacements, key=lambda item: item[0], reverse=True):
        result = result[:start] + text + result[end:]
    return result


def rename_local_mock_to_field(method_source: str, old_name: str, new_name: str) -> tuple[str | None, str | None]:
    """
    删掉 `Type old = mock(X.class);` 这一整行，并把方法内其余的 `old` 引用改成 `new`。

    返回 (改写后的方法源码, None)，或者 (None, 放弃原因)。
    Deletes the whole `Type old = mock(X.class);` line and renames the method's
    remaining `old` references to `new`. Returns (rewritten method, None) or
    (None, bail reason).
    """
    masked = _mask(method_source)

    # 字段沿用局部变量的名字是常态，不是冲突：封装那一步本来就要求"名字没被占用时复用
    # variableName"。同名时删掉局部声明就够了，剩下的引用自然解析到类字段上，一个字都
    # 不用改。反过来，如果把它当成冲突而放弃，最常见的那一类反而走不了确定性路径。
    # A field reusing the local variable's name is the norm, not a clash: encapsulation is
    # told to reuse `variableName` when nothing else claims it. When the names match,
    # deleting the local declaration is the whole change — the remaining references resolve
    # to the class field on their own. Treating it as a conflict would push the single most
    # common case off the deterministic path.
    same_name = old_name == new_name
    if not same_name and _identifier_spans(masked, new_name):
        return None, f"field name {new_name!r} is already used as an identifier in this test method"
    if _declares_in_nested_scope(masked, old_name):
        return None, f"{old_name!r} is redeclared in a nested scope (lambda or inner block)"

    # 赋值点：声明式 `Type old = ...` 或裸赋值 `old = ...`。多于一个意味着这个变量
    # 代表了不止一个实例，共享字段无法表达。
    # Assignment sites: a declaration `Type old = ...` or a bare `old = ...`. More
    # than one means the variable stands for several instances, which a single
    # shared field cannot represent.
    assignments = [match for match in re.finditer(
        r"(?m)^([ \t]*)((?:final\s+)?[\w.<>\[\]?, ]+\s+)?" + re.escape(old_name) + r"\s*=\s*([^;]*;)", masked)]
    if not assignments:
        return None, f"no assignment to {old_name!r} found in this test method"
    if len(assignments) > 1:
        return None, f"{old_name!r} is assigned {len(assignments)} times; a shared field cannot represent both"

    assignment = assignments[0]
    if not re.search(_MOCK_CALL, assignment.group(3) or ""):
        return None, f"the assignment to {old_name!r} is not a mock/spy creation call"
    if assignment.group(2) is None:
        return None, f"{old_name!r} is assigned without a declaration; it may refer to an outer variable"

    replacements: list[tuple[int, int, str]] = []
    # 整行删除，连同行尾换行；缩进留给后面的行。
    # Delete the whole line including its trailing newline; the next line keeps its own indent.
    line_end = method_source.find("\n", assignment.end())
    line_end = len(method_source) if line_end == -1 else line_end + 1
    replacements.append((assignment.start(), line_end, ""))

    if not same_name:
        for start, end in _identifier_spans(masked, old_name):
            if assignment.start() <= start < line_end:
                continue
            replacements.append((start, end, new_name))

    rewritten = _apply(method_source, replacements)
    if not same_name:
        leftover = _identifier_spans(_mask(rewritten), old_name)
        if leftover:
            return None, f"{len(leftover)} reference(s) to {old_name!r} would survive the rename"
    return rewritten, None


def inline_mock_to_field(method_source: str, expression: str, new_name: str) -> tuple[str | None, str | None]:
    """
    把内联的 `Mockito.mock(X.class)` 表达式换成字段名——这种 mock 根本没有变量。

    只在该表达式在方法内恰好出现一次时动手：出现多次意味着那是多个互相独立的
    mock 实例，全部换成同一个字段会把它们合并成一个，改变语义。
    Replaces an inline `Mockito.mock(X.class)` expression with the field name —
    this kind of mock has no variable at all. Acts only when the expression occurs
    exactly once in the method: several occurrences are independent instances, and
    pointing them all at one field would merge them and change behaviour.
    """
    masked = _mask(method_source)
    expression = expression.strip().rstrip(";").strip()
    if not expression:
        return None, "no inline mock expression supplied"

    spans = [match.span() for match in re.finditer(re.escape(expression), masked)]
    if not spans:
        return None, f"inline expression {expression!r} not found in this test method"
    if len(spans) > 1:
        return None, (f"inline expression {expression!r} occurs {len(spans)} times; "
                      "these are independent mock instances")

    start, end = spans[0]
    return _apply(method_source, [(start, end, new_name)]), None
