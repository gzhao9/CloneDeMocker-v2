from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ModelUsage:
    """一次模型调用的 token 记录 / Token accounting for one model call."""

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class ModelResult:
    text: str
    response_id: str
    model: str
    usage: ModelUsage
    raw: Any


class ModelProvider(Protocol):
    """多供应商边界 / Provider-neutral model boundary."""

    def generate(self, instructions: str, input_text: str, model: str) -> ModelResult:
        ...


_STUB_MARKER = "// CloneDeMocker debug stub / 本地调试桩产生的改动，不是真实重构"


def _package_edit(path: str, content: str) -> dict[str, Any] | None:
    """在 `package x.y;` 行后插一条注释：Java 里合法，且该行在文件内天然唯一。
    Appends a comment after the `package x.y;` line: legal Java, and that line is
    naturally unique within a file, so it satisfies the edit protocol."""
    match = re.search(r"(?m)^\s*package\s+[\w.]+\s*;", content or "")
    if match is None:
        return None
    old = match.group(0)
    return {"path": path, "oldString": old, "newString": f"{old}\n{_STUB_MARKER}", "replaceAll": False}


class MockModelProvider:
    """本地调试用桩实现，不消耗 token，也不发起网络请求。
    Debug stub that never calls out to a real model or spends tokens.

    产出**真实可应用**的最小编辑（在 package 行后加一行注释，或给某条 mock 语句
    加行尾注释），而不是原样回显源码——回显出来的内容与原文件逐字节相同，会被
    `_apply_edits` 的"拒绝未改动内容"判定挡掉，等于这条调试路径从设计上就跑不通。
    改动本身是合法 Java，所以 harness 的编译/测试环节也能真的走一遍。
    Produces a **genuinely applicable** minimal edit (a comment after the package
    line, or a trailing comment on a mock statement) instead of echoing the source
    back — an echo is byte-identical to the original and is rejected by
    `_apply_edits`'s reject-unchanged rule, which made this debug path structurally
    incapable of succeeding. The edit is legal Java, so the harness's compile/test
    stages exercise a real build too.
    """

    def generate(self, instructions: str, input_text: str, model: str) -> ModelResult:
        request = json.loads(input_text)
        stage = request.get("stage", "")
        verbatim = request.get("verbatim") or {}
        facts = request.get("facts") or {}
        edits: list[dict[str, Any]] = []
        extra: dict[str, Any] = {}

        if stage == "INTEGRATION":
            # 只拿到单个测试方法，没有整份文件。整段方法在文件里天然唯一，拿它当
            # oldString 就不必去猜哪一行能唯一定位。
            # Only one test method is supplied, not the whole file. A whole method is
            # naturally unique there, so using it as the oldString avoids guessing which
            # single line can be addressed uniquely.
            method = verbatim.get("testMethod") or {}
            text = str(method.get("text", ""))
            if text.strip():
                marked = text.rstrip("\n") + f"  {_STUB_MARKER}\n"
                edits.append({"path": method.get("path", ""), "oldString": text,
                              "newString": marked, "replaceAll": False})
        elif stage == "ENCAPSULATION":
            target = verbatim.get("targetFile") or {}
            edit = _package_edit(str(target.get("path", "")), str(target.get("content", "")))
            if edit is not None:
                edits.append(edit)
            extra = {"reusableCode": _STUB_MARKER, "newFieldName": str(facts.get("variableName", ""))}
        else:
            # 修复轮次：原样退回收到的提案，让上层的重试预算照常走完。
            # A repair round: hand back the proposal as received so the caller's retry
            # budget still plays out normally.
            current = request.get("currentProposal") or {}
            edits = list(current.get("edits") or [])

        response = {
            "canRefactor": bool(edits),
            "reason": ("mock provider / 本地调试桩，未调用真实模型" if edits else
                       "debug stub found nothing it could address uniquely / 调试桩未找到可唯一定位的改动点"),
            "summary": "debug stub inserted a marker comment / 调试桩插入了一行标记注释",
            "caveat": "Not a real refactoring / 这不是真实重构结果",
            "edits": edits,
            "newFiles": [],
            **extra,
        }
        return ModelResult(
            text=json.dumps(response, ensure_ascii=False),
            response_id="mock-response",
            model=model,
            usage=ModelUsage(),
            raw=None,
        )


class OpenAIModelProvider:
    """OpenAI Responses API 适配器 / OpenAI Responses API adapter."""

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        try:
            from openai import OpenAI
        except ImportError as error:
            raise RuntimeError(
                "OpenAI SDK is missing. Restart the UI with start-ui.ps1 so uv can sync project dependencies; "
                "or run `uv sync`. / 缺少 OpenAI SDK：请用 start-ui.ps1 重启 UI 以同步依赖，或执行 `uv sync`。"
            ) from error
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=180.0, max_retries=5)

    def generate(self, instructions: str, input_text: str, model: str) -> ModelResult:
        response = self.client.responses.create(
            model=model,
            reasoning={"effort": "medium"},
            instructions=instructions,
            input=input_text,
        )
        usage = response.usage
        input_details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        return ModelResult(
            text=response.output_text,
            response_id=response.id,
            model=response.model,
            usage=ModelUsage(
                input_tokens=getattr(usage, "input_tokens", 0),
                cached_input_tokens=getattr(input_details, "cached_tokens", 0),
                output_tokens=getattr(usage, "output_tokens", 0),
                reasoning_tokens=getattr(output_details, "reasoning_tokens", 0),
                total_tokens=getattr(usage, "total_tokens", 0),
            ),
            raw=response,
        )
