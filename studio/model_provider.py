from __future__ import annotations

import json
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


class MockModelProvider:
    """本地调试用桩实现，不消耗 token，也不发起网络请求。
    Debug stub that never calls out to a real model or spends tokens.

    对每个源文件原样返回（可选前缀一行标记注释），用于在不消耗
    真实 API 配额的情况下练习 Encapsulation/Integration/Harness 全链路。
    Echoes each source file back unchanged (optionally with a marker
    comment), so the full pipeline (diff, harness, repair loop wiring)
    can be exercised without spending real API quota.
    """

    def generate(self, instructions: str, input_text: str, model: str) -> ModelResult:
        request = json.loads(input_text)
        files = [
            {"path": entry["path"], "newContent": entry["content"]}
            for entry in request.get("sourceFiles", [])
        ]
        response = {
            "canRefactor": True,
            "reason": "mock provider / 本地调试桩，未调用真实模型",
            "summary": "mock provider returned files unchanged / 桩实现原样返回源码",
            "files": files,
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
            raise RuntimeError("Install the openai package / 请安装 openai 包") from error
        self.client = OpenAI(api_key=api_key, base_url=base_url)

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
