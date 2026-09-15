"""解析并重放我们自己生成的 unified diff（difflib.unified_diff 的输出格式），
用于 --replay 模式：复用上一轮已经拿到的模型候选，不用为了重新验证 harness/PIT
而再花一次 token 调用模型。只处理我们自己受控生成的格式，不是通用 diff/patch 引擎。

Parses and replays unified diffs in exactly the shape our own
difflib.unified_diff() calls produce, so --replay mode can reuse a previous
run's already-generated model candidates without spending tokens on the model
again just to re-verify the harness/PIT. This is not a general-purpose
diff/patch engine — only the controlled format we generate ourselves.
"""
from __future__ import annotations

import re
from pathlib import Path

_FILE_HEADER = re.compile(r"^--- a/(?P<from>.+)\n\+\+\+ b/(?P<to>.+)\n", re.MULTILINE)
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def split_diff_by_file(diff_text: str) -> dict[Path, str]:
    """把 changes.diff 按 `--- a/...` 边界切成每个文件自己的一段 diff 文本。
    Splits changes.diff at each `--- a/...` boundary into that file's own diff text."""
    if not diff_text:
        return {}
    matches = list(_FILE_HEADER.finditer(diff_text))
    per_file: dict[Path, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(diff_text)
        per_file[Path(match.group("to"))] = diff_text[start:end]
    return per_file


def apply_unified_diff(original: str, hunks_text: str) -> str:
    """把某一个文件自己的那段 diff（不含 ---/+++ 头，只有 @@ 开头的若干 hunk）套用到
    original 上，重建出候选文件的完整内容。假设 hunk 之间上下文没有重叠、行号严格递增
    ——这正是 difflib.unified_diff 对同一个文件、一次性 diff 出来的保证。
    Applies one file's own diff body (no ---/+++ header, just the @@ hunks) to
    `original` to reconstruct the candidate file's full content. Assumes hunks don't
    overlap and line numbers strictly increase — exactly what difflib.unified_diff
    guarantees for a single one-shot diff of one file."""
    original_lines = original.splitlines(keepends=True)
    lines = hunks_text.splitlines(keepends=True)
    result: list[str] = []
    orig_index = 0  # 0-based cursor into original_lines
    i = 0
    while i < len(lines):
        header_match = _HUNK_HEADER.match(lines[i])
        if not header_match:
            i += 1
            continue
        old_start = int(header_match.group(1))
        result.extend(original_lines[orig_index:old_start - 1])
        orig_index = old_start - 1
        i += 1
        while i < len(lines) and not _HUNK_HEADER.match(lines[i]):
            body_line = lines[i]
            marker, content = body_line[0], body_line[1:]
            if marker == "-":
                orig_index += 1
            elif marker == "+":
                result.append(content)
            elif marker == " ":
                result.append(original_lines[orig_index])
                orig_index += 1
            # any other marker (e.g. stray blank line) is ignored
            i += 1
    result.extend(original_lines[orig_index:])
    return "".join(result)


def replay_replacements(diff_text: str, files: dict[Path, str]) -> dict[Path, str]:
    """给定原始文件内容（files，和上一轮生成 diff 时用的必须是同一份源码）和保存下来的
    changes.diff，重建出这次候选的 {path: newContent}，不用再调用模型。
    Given the original file content (files — must be the exact same source used when
    the diff was generated) and the saved changes.diff, reconstructs this candidate's
    {path: newContent} without calling the model again."""
    replacements: dict[Path, str] = {}
    for relative, hunks_text in split_diff_by_file(diff_text).items():
        original = files.get(relative)
        if original is None:
            continue
        replacements[relative] = apply_unified_diff(original, hunks_text)
    return replacements
