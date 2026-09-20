"""
按白名单构造发给模型的 payload，并在发送前断言其中的代码文本确实是源文件里的原文。

原先的做法是黑名单：把检测器的整个 JSON 塞进 payload，再由 `_strip_duplicate_source()`
删掉两个已知有害的字段。这种结构**失败时是开放的**——检测器以后新增任何带规范化文本
的字段都会自动泄漏，而且不会有人发现。实测 Dubbo 3.3.6：当前 payload 里 1587 条代码
文本有 631 条（40%）无法在它自己附带的源文件中逐字节找到，来自 `abstractedStatement`、
`sharedStatements`、`rawStatementInfo[*].code` 以及按行号索引的那几个字段。模型一边收
着这些规范化文本，一边被要求产出逐字节精确的 `oldString`，只能靠猜该抄哪一份。

这里改成白名单，payload 只有三个区：

- `verbatim`：由 source_map 从源文件现取的原文。**只有这一区允许被照抄进 oldString**，
  并且发送前会逐条断言它确实出现在所声称的文件里；对不上就直接报错，不发这次请求。
- `facts`：不是代码的结构化事实（变量名、类名、行号、scope、计数）。
- `hints`：检测器的抽象描述（`abstractedStatement` 这类模板语句）。它本来就不该逐字节
  对应源码，prompt 里会明说它只是线索、不能照抄。

Builds model payloads from a whitelist, and asserts before sending that their code text
really is the source file's own.

The previous approach was a blacklist: push the detector's whole JSON into the payload,
then have `_strip_duplicate_source()` remove two known-harmful fields. That structure
fails open — any new detector field carrying normalized text leaks automatically, and
nobody notices. Measured on Dubbo 3.3.6: 631 of 1587 code strings (40%) in the current
payload cannot be found byte-for-byte in the source files it ships, coming from
`abstractedStatement`, `sharedStatements`, `rawStatementInfo[*].code` and the
line-number-keyed fields. The model receives that normalized text alongside real file
content and is asked for a byte-exact `oldString`, so it can only guess which to copy.

The whitelist gives the payload three regions: `verbatim` (taken fresh from the file by
source_map — the only region that may be copied into an `oldString`, and every entry is
asserted to occur in the file it claims), `facts` (structured non-code facts), and
`hints` (the detector's abstracted templates, which prompts state are never to be copied).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from studio.source_map import enclosing_method, locate

STAGE_ENCAPSULATION = "ENCAPSULATION"
STAGE_INTEGRATION = "INTEGRATION"


class PayloadError(Exception):
    """无法构造出可信的 payload；调用方应当放弃这一步，而不是发一个会失败的请求。
    A trustworthy payload could not be built; callers bail instead of sending a request
    that is going to fail."""


def route_encapsulation(instance: dict[str, Any]) -> str:
    """有共享 stub 就抽 helper 方法，没有就提成类级别字段（论文 1.1 / 1.2 两条分支）。
    Shared stubbing means a helper method, none means a class-level field (the paper's
    1.1 / 1.2 branches)."""
    return "helper" if instance.get("sharedStatementLineCount", 0) > 0 else "attribute"


def route_integration(instance: dict[str, Any], sequence: dict[str, Any]) -> str:
    """封装走了字段那条路，集成就只是改名；否则按这条 sequence 有没有 setup 里的共享语句
    分成 @Before 变体和局部变体（论文 2.1 / 2.2 / 2.3）。
    When encapsulation produced a field, integration is just a rename; otherwise the
    sequence splits by whether setup holds shared statements (the paper's 2.1 / 2.2 / 2.3)."""
    if instance.get("sharedStatementLineCount", 0) == 0:
        return "attribute"
    return "before" if sequence.get("shareableMockLines") else "local"


def _relative(project_root: Path, file_path: str) -> Path | None:
    raw = Path(file_path)
    try:
        resolved = (raw if raw.is_absolute() else project_root / raw).resolve()
        return resolved.relative_to(project_root.resolve())
    except (OSError, ValueError):
        return None


def _mock_statements(sequence: dict[str, Any], content: str) -> list[dict[str, Any]]:
    """把检测器给的 mock 语句逐条定位回源文件，返回文件里的原文。定位不到的整条丢弃——
    宁可少给模型一条线索，也不要给它一条抄不得的文本。
    Locates each detector mock statement back in the source and returns the file's own
    text. An entry that cannot be located is dropped: better to withhold a hint than to
    hand over text that must not be copied."""
    located = []
    for line_number, code in (sequence.get("testMockLines") or {}).items():
        try:
            number = int(line_number)
        except (TypeError, ValueError):
            continue
        span = locate(content, str(code), near_line=number)
        if span is None:
            continue
        located.append({"line": number, "text": content[span[0]:span[1]]})
    return located


def _test_method(sequence: dict[str, Any], content: str) -> str | None:
    """锚定这条 sequence 的第一条 mock 语句，再扩展到包含它的完整测试方法，取原文。
    Anchors on the sequence's first mock statement and expands to the whole enclosing
    test method, verbatim."""
    for line_number, code in (sequence.get("testMockLines") or {}).items():
        try:
            number = int(line_number)
        except (TypeError, ValueError):
            continue
        span = locate(content, str(code), near_line=number)
        if span is None:
            continue
        method = enclosing_method(content, span[0])
        if method is not None:
            return content[method[0]:method[1]]
    return None


def _line_occurrences(content: str, texts: list[str]) -> dict[str, int]:
    """每条语句在整份文件里出现几次。大于 1 就意味着模型必须给 oldString 补上下文才唯一。
    How often each statement occurs in the whole file. Above 1 means the model must add
    context to make its `oldString` unique."""
    return {text: content.count(text) for text in texts}



# 已经被占用的名字。模型没有任何办法自己知道这些——payload 里不给，它就只能猜。
# 实测代价：模型新建了一个 helper 类叫 MockServiceDiscovery，而目标测试文件第 33 行本来就
# 写着 `import org.apache.dubbo.registry.client.support.MockServiceDiscovery;`。Java 里显式
# 单类型 import 的优先级高于同包解析，于是文件里所有 MockServiceDiscovery 仍然指向旧类，
# 新方法"找不到符号"，编译失败，22249 tokens 作废。Dubbo 里同名的 MockServiceDiscovery
# 一共有三个——这不是偶然，测试代码里 MockXxx 这种命名本来就高度重复。
# Names that are already taken. The model has no way to know them on its own: absent from the
# payload, it can only guess. Measured cost: it created a helper class named
# MockServiceDiscovery while line 33 of the target test file already read
# `import org.apache.dubbo.registry.client.support.MockServiceDiscovery;`. An explicit
# single-type import outranks same-package resolution in Java, so every MockServiceDiscovery in
# that file still meant the old class, the new method was "cannot find symbol", compilation
# failed and 22249 tokens were lost. Dubbo holds three classes by that name — not a fluke, since
# MockXxx naming repeats heavily across test code.
_IMPORTED_TYPE = re.compile(r"(?m)^\s*import\s+(?:static\s+)?([\w.]+)\s*;")
_FIELD_OR_LOCAL = re.compile(
    r"(?m)^\s*(?:(?:public|protected|private|static|final|transient|volatile)\s+)*"
    r"[\w.<>\[\]?,\s]+?\s+(\w+)\s*(?:=|;)")


def _taken_class_names(target: Path, files: dict[Path, str], project_root: Path) -> list[str]:
    """目标文件已 import 的简单类名，加上同一个包目录下已有的类名。
    The simple names this file already imports, plus the classes that already exist in its
    package directory."""
    taken: set[str] = set()
    content = files.get(target, "")
    for qualified in _IMPORTED_TYPE.findall(content):
        simple = qualified.rsplit(".", 1)[-1]
        if simple and simple[0].isupper():
            taken.add(simple)
    package_directory = (project_root / target).parent
    try:
        for sibling in package_directory.glob("*.java"):
            taken.add(sibling.stem)
    except OSError:
        pass
    return sorted(taken)


def _taken_identifiers(content: str) -> list[str]:
    """这个类里已经声明过的字段与局部变量名。新字段撞上任何一个都会编译失败。
    Field and local-variable names already declared in this class. A new field colliding with
    any of them fails to compile."""
    return sorted({name for name in _FIELD_OR_LOCAL.findall(content) if name})


def encapsulation_payload(project_root: Path, instance: dict[str, Any], files: dict[Path, str],
                          user_instruction: str = "") -> dict[str, Any]:
    variant = route_encapsulation(instance)
    sequences = instance.get("sequences") or []
    if not sequences:
        raise PayloadError("MCI has no sequences / MCI 没有 sequence")

    paths: list[Path] = []
    for sequence in sequences:
        relative = _relative(project_root, sequence.get("filePath", ""))
        if relative is not None and relative in files and relative not in paths:
            paths.append(relative)
    if not paths:
        raise PayloadError("no readable source file for this MCI / 该 MCI 没有可读源文件")

    scope = "method level" if len(paths) == 1 else "class level"
    roles = {str(sequence.get("mockRole", "mock")).lower() for sequence in sequences}
    role = "spy" if roles == {"spy"} else "mock"

    methods: list[dict[str, Any]] = []
    statements: list[dict[str, Any]] = []
    for sequence in sequences:
        relative = _relative(project_root, sequence.get("filePath", ""))
        if relative is None or relative not in files:
            continue
        content = files[relative]
        text = _test_method(sequence, content)
        if text is not None and not any(entry["text"] == text for entry in methods):
            methods.append({"path": relative.as_posix(), "name": sequence.get("testMethodName", ""),
                            "text": text})
        for entry in _mock_statements(sequence, content):
            statements.append({"path": relative.as_posix(), **entry})
    if not methods:
        raise PayloadError("could not locate any test method in the source / 无法在源码中定位任何测试方法")

    verbatim: dict[str, Any] = {"testMethods": methods, "mockStatements": statements}
    if scope == "method level":
        verbatim["targetFile"] = {"path": paths[0].as_posix(), "content": files[paths[0]]}

    return {
        "stage": STAGE_ENCAPSULATION,
        "variant": variant,
        "facts": {
            "scope": scope,
            "mockRole": role,
            "mockedClass": instance.get("mockedClass", ""),
            "packageName": instance.get("packageName", ""),
            "variableName": _dominant_variable(sequences),
            "testCaseCount": instance.get("testCaseCount", 0),
            "sequenceCount": instance.get("sequenceCount", len(sequences)),
            "sourceRoots": [path.as_posix() for path in paths],
            "userInstruction": user_instruction,
            # 新建 helper 类不能叫这些名字，新字段也不能。两者都是代码几毫秒就能扫出来的
            # 确定性事实——让模型自己去"查"只会更慢、更贵，而且不保证查全。
            # A new helper class must avoid these names, and so must a new field. Both are
            # deterministic facts the code scans in milliseconds; having the model look them
            # up instead would be slower, costlier and not guaranteed to be complete.
            "takenClassNames": _taken_class_names(paths[0], files, project_root),
            "takenIdentifiers": _taken_identifiers(files.get(paths[0], "")),
        },
        "hints": {
            "needStubStatements": list(instance.get("sharedStatements") or []),
            "note": ("These are abstracted templates from the detector, not source text. "
                     "Never copy them into an oldString."),
        },
        "verbatim": verbatim,
    }


def integration_payload(project_root: Path, instance: dict[str, Any], sequence: dict[str, Any],
                        files: dict[Path, str], reusable_code: str,
                        new_field_name: str = "") -> dict[str, Any]:
    variant = route_integration(instance, sequence)
    relative = _relative(project_root, sequence.get("filePath", ""))
    if relative is None or relative not in files:
        raise PayloadError(f"source file is not available: {sequence.get('filePath', '')!r}")

    content = files[relative]
    method = _test_method(sequence, content)
    if method is None:
        raise PayloadError(
            f"could not locate test method {sequence.get('testMethodName', '')!r} in {relative.as_posix()}")
    statements = _mock_statements(sequence, content)

    facts: dict[str, Any] = {
        "path": relative.as_posix(),
        "testMethodName": sequence.get("testMethodName", ""),
        "variableName": sequence.get("variableName", ""),
        "mockedClass": instance.get("mockedClass", ""),
    }
    if variant == "attribute":
        facts["oldVariableName"] = sequence.get("variableName", "")
        facts["newVariableName"] = new_field_name

    recommended = statements[1]["text"] if len(statements) > 1 else (
        statements[0]["text"] if statements else "")

    return {
        "stage": STAGE_INTEGRATION,
        "variant": variant,
        "facts": facts,
        "hints": {
            "sharedStatements": dict(sequence.get("shareableMockLines") or {}),
            "recommendedInsertionPoint": recommended,
            "note": ("sharedStatements come from the detector and are not source text. "
                     "Never copy them into an oldString."),
        },
        "verbatim": {
            "testMethod": {"path": relative.as_posix(), "text": method},
            "mockStatements": [{"path": relative.as_posix(), **entry} for entry in statements],
            "reusableCode": reusable_code,
        },
        "lineOccurrences": _line_occurrences(content, [entry["text"] for entry in statements] + [method]),
    }


def _dominant_variable(sequences: list[dict[str, Any]]) -> str:
    names = [str(sequence.get("variableName", "")) for sequence in sequences if sequence.get("variableName")]
    return max(set(names), key=names.count) if names else ""


def verbatim_failures(payload: dict[str, Any], files: dict[Path, str]) -> list[str]:
    """
    发送前的断言：`verbatim` 区里每条文本都必须出现在它所声称的那份文件里。

    这一步把静默失败换成响亮失败。没有它，一条抄错的文本会变成模型给出的无效
    `oldString`、两轮白烧的 repair，最后被归因成"模型能力不行"。成本只是一次子串检查。
    The pre-send assertion: every entry under `verbatim` must occur in the file it names.
    This turns a silent failure loud. Without it, one wrong string becomes an invalid
    `oldString`, two wasted repair rounds, and a verdict of "the model isn't good enough".
    The cost is one substring check.
    """
    failures: list[str] = []
    verbatim = payload.get("verbatim") or {}

    def check(path_text: str, text: str, label: str) -> None:
        relative = Path(path_text)
        content = files.get(relative)
        if content is None:
            failures.append(f"{label}: payload references a file that was not supplied: {path_text}")
        elif text not in content:
            failures.append(f"{label}: text is not present verbatim in {path_text}")

    target = verbatim.get("targetFile")
    if isinstance(target, dict):
        check(target.get("path", ""), target.get("content", ""), "targetFile")
    method = verbatim.get("testMethod")
    if isinstance(method, dict):
        check(method.get("path", ""), method.get("text", ""), "testMethod")
    for index, entry in enumerate(verbatim.get("testMethods") or []):
        check(entry.get("path", ""), entry.get("text", ""), f"testMethods[{index}]")
    for index, entry in enumerate(verbatim.get("mockStatements") or []):
        check(entry.get("path", ""), entry.get("text", ""), f"mockStatements[{index}]")
    return failures
