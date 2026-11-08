from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .config import get_settings


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class Completion:
    text: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: Optional[Dict[str, int]] = None


@dataclass
class StreamEvent:
    type: str  # text | tool_call | usage
    text: str = ""
    call: Optional[ToolCall] = None
    usage: Optional[Dict[str, int]] = None


class MockProvider:
    """无需 API Key 的规则版：按问题选工具，再根据工具结果组织答案。

    它不是“假装成模型”，而是让 API、工具循环、校验、SSE 在本地可测。
    配置 LLM_PROVIDER=openai 后才会调用真实模型。
    """

    name = "mock"

    def complete(self, messages: List[dict], tools: List[dict]) -> Completion:
        last = messages[-1] if messages else {}
        if last.get("role") == "tool":
            return Completion(text=self._answer_from_tools(messages))

        question = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                question = message.get("content", "")
                break

        name = self._choose_tool(question)
        return Completion(
            text="",
            tool_calls=[ToolCall(id=f"call_{name}", name=name, arguments="{}")],
        )

    def stream(self, messages: List[dict], tools: List[dict]):
        """把 complete 的结果拆成小块，模拟真实 provider 的 token 流。

        这只用于本地演示和测试；它拿不到真实模型的首字延迟，但能验证
        “增量解析 → SSE 分帧 → 前端拼字”这条链路。
        """
        completion = self.complete(messages, tools)
        if completion.tool_calls:
            for call in completion.tool_calls:
                yield StreamEvent(type="tool_call", call=call)
            return

        for index in range(0, len(completion.text), 6):
            yield StreamEvent(type="text", text=completion.text[index : index + 6])
        if completion.usage:
            yield StreamEvent(type="usage", usage=completion.usage)


    @staticmethod
    def _choose_tool(question: str) -> str:
        if any(word in question for word in ["驳回", "拒绝", "为什么", "审批", "意见"]):
            return "get_approval_record"
        if any(word in question for word in ["明细", "金额", "花", "发票", "多少", "合计"]):
            return "list_expense_items"
        return "get_expense_summary"

    @staticmethod
    def _answer_from_tools(messages: List[dict]) -> str:
        payloads = []
        for message in messages:
            if message.get("role") == "tool":
                try:
                    payloads.append(json.loads(message.get("content", "{}")))
                except json.JSONDecodeError:
                    continue

        merged: Dict[str, Any] = {}
        for payload in payloads:
            merged.update(payload)

        if "rejectReason" in merged:
            answer = {
                "conclusion": f"这张单被驳回，原因是：{merged['rejectReason']}。",
                "evidence": ["rejectReason", "status"],
                "insufficient": False,
            }
        elif "approvalNote" in merged:
            answer = {
                "conclusion": f"这张单已通过，审批意见：{merged['approvalNote']}。",
                "evidence": ["approvalNote", "status"],
                "insufficient": False,
            }
        elif "items" in merged:
            names = "、".join(item["name"] for item in merged["items"])
            answer = {
                "conclusion": f"这张单共 {merged['total']:.2f} 元，包含：{names}。",
                "evidence": ["items", "total"],
                "insufficient": False,
            }
        elif merged.get("status") in {"DRAFT", "SUBMITTED"}:
            answer = {
                "conclusion": "这张单还在草稿或待审批状态，当前没有审批意见。",
                "evidence": ["status"],
                "insufficient": True,
            }
        elif "status" in merged:
            answer = {
                "conclusion": f"这张单当前状态是 {merged['status']}，金额 {merged.get('amount', 0):.2f} 元。",
                "evidence": ["status", "amount"],
                "insufficient": False,
            }
        else:
            answer = {
                "conclusion": "当前数据里没有足够的审批信息。",
                "evidence": [],
                "insufficient": True,
            }
        return json.dumps(answer, ensure_ascii=False)


class OpenAIProvider:
    name = "openai"

    def __init__(self) -> None:
        from openai import OpenAI

        settings = get_settings()
        if not settings.llm_api_key or not settings.llm_model:
            raise RuntimeError(
                "LLM_PROVIDER=openai 需要同时配置 LLM_API_KEY 和 LLM_MODEL；"
                "LLM_BASE_URL 可选，留空使用 OpenAI 默认地址。"
            )
        self.model = settings.llm_model
        self.client = OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url or None,
        )

    def complete(self, messages: List[dict], tools: List[dict]) -> Completion:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            temperature=0,
        )
        message = response.choices[0].message
        calls = [
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=call.function.arguments or "{}",
            )
            for call in (message.tool_calls or [])
        ]
        usage = None
        if response.usage is not None:
            usage = {
                "promptTokens": response.usage.prompt_tokens,
                "completionTokens": response.usage.completion_tokens,
            }
        return Completion(text=message.content or "", tool_calls=calls, usage=usage)

    def stream(self, messages: List[dict], tools: List[dict]):
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            temperature=0,
            stream=True,
        )
        pending: Dict[int, Dict[str, str]] = {}
        try:
            for chunk in response:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    yield StreamEvent(
                        type="usage",
                        usage={
                            "promptTokens": usage.prompt_tokens,
                            "completionTokens": usage.completion_tokens,
                        },
                    )

                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = choices[0].delta
                if getattr(delta, "content", None):
                    yield StreamEvent(type="text", text=delta.content)

                for fragment in getattr(delta, "tool_calls", None) or []:
                    index = fragment.index
                    item = pending.setdefault(
                        index, {"id": "", "name": "", "arguments": ""}
                    )
                    if fragment.id:
                        item["id"] = fragment.id
                    function = getattr(fragment, "function", None)
                    if function is not None:
                        if function.name:
                            item["name"] = function.name
                        if function.arguments:
                            item["arguments"] += function.arguments

            for index in sorted(pending):
                item = pending[index]
                yield StreamEvent(
                    type="tool_call",
                    call=ToolCall(
                        id=item["id"] or f"call_{index}",
                        name=item["name"],
                        arguments=item["arguments"] or "{}",
                    ),
                )
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()


def create_provider():
    kind = get_settings().llm_provider.lower()
    if kind == "mock":
        return MockProvider()
    if kind == "openai":
        return OpenAIProvider()
    raise RuntimeError(f"不支持的 LLM_PROVIDER：{kind}")
