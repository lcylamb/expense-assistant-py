from __future__ import annotations

import json
import logging
import time
from typing import Dict, Iterable, List, Optional, Set

from .db import Expense, SessionLocal, User
from .llm import Completion, create_provider
from .models import (
    AgentStep,
    AskMetrics,
    AskResult,
    ExpenseListResult,
    HistoryTurn,
    LlmUsage,
    ParsedAnswer,
)
from .stream_parse import partial_conclusion
from .tools import TOOL_SPECS, run_tool

logger = logging.getLogger(__name__)

MAX_STEPS = 4

SYSTEM_PROMPT = """你是报销单问答助手。你只能依据工具返回的真实数据回答，不能编造字段或金额。
需要查数据时调用工具；拿到结果后，只输出一个 JSON 对象，不要 Markdown：
{"conclusion": "一句话结论", "evidence": ["本次工具返回过的字段名"], "insufficient": false}
如果数据不足，把 insufficient 设为 true，并在 conclusion 里说明缺什么。
"""


class ExpenseNotFound(LookupError):
    pass


def parse_answer(raw: str) -> ParsedAnswer:
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("模型输出里没有 JSON 对象")

    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("模型输出不是合法 JSON") from exc

    if not isinstance(data, dict):
        raise ValueError("模型输出必须是 JSON 对象")
    conclusion = data.get("conclusion")
    evidence = data.get("evidence")
    insufficient = data.get("insufficient")

    if not isinstance(conclusion, str) or not conclusion.strip():
        raise ValueError("conclusion 必须是非空字符串")
    if not isinstance(evidence, list) or any(not isinstance(item, str) for item in evidence):
        raise ValueError("evidence 必须是字符串数组")
    if not isinstance(insufficient, bool):
        raise ValueError("insufficient 必须是布尔值")

    cleaned: List[str] = []
    for item in evidence:
        value = item.strip()
        if value and value not in cleaned:
            cleaned.append(value)

    return ParsedAnswer(
        conclusion=conclusion.strip(),
        evidence=cleaned,
        insufficient=insufficient,
    )


def filter_evidence(evidence: Iterable[str], allowed: Set[str]) -> List[str]:
    kept: List[str] = []
    for item in evidence:
        if item in allowed:
            kept.append(item)
        else:
            logger.warning("剔除未知 evidence：%s", item)
    return kept


def _tool_message(call_id: str, payload: Dict) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(payload, ensure_ascii=False),
    }


def _assistant_message(completion: Completion) -> dict:
    return {
        "role": "assistant",
        "content": completion.text or "",
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in completion.tool_calls
        ],
    }


def _visible_query(session, user: User):
    query = session.query(Expense)
    if user.role == "APPLICANT":
        query = query.filter(Expense.applicant_id == user.id)
    elif user.role == "APPROVER":
        query = query.filter(Expense.status != "DRAFT")
    return query


def list_expenses(user: User, category: Optional[str] = None) -> ExpenseListResult:
    with SessionLocal() as session:
        query = _visible_query(session, user)
        if category:
            query = query.filter(Expense.category == category)
        rows = query.order_by(Expense.submitted_at.desc(), Expense.code.asc()).all()

        by_status: Dict[str, int] = {}
        total_cents = 0
        rejected = 0
        expenses = []
        for row in rows:
            by_status[row.status] = by_status.get(row.status, 0) + 1
            total_cents += round(row.amount * 100)
            if row.status == "REJECTED":
                rejected += 1
            expenses.append(
                {
                    "code": row.code,
                    "title": row.title,
                    "applicant": row.applicant,
                    "department": row.department,
                    "category": row.category,
                    "status": row.status,
                    "amount": f"{row.amount:.2f}",
                    "submittedAt": row.submitted_at.isoformat(),
                }
            )

        return ExpenseListResult(
            viewer={"name": user.name, "role": user.role.lower()},
            expenses=expenses,
            summary={
                "count": len(expenses),
                "byStatus": by_status,
                "totalAmount": f"{total_cents / 100:.2f}",
                "rejectedCount": rejected,
            },
        )


def _history_messages(history: List[HistoryTurn]) -> List[dict]:
    messages: List[dict] = []
    for turn in history[-3:]:
        messages.append({"role": "user", "content": turn.question})
        messages.append(
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "conclusion": turn.conclusion,
                        "evidence": turn.evidence,
                        "insufficient": turn.insufficient,
                    },
                    ensure_ascii=False,
                ),
            }
        )
    return messages


def ask(
    user: User,
    code: str,
    question: str,
    history: Optional[List[HistoryTurn]] = None,
) -> AskResult:
    with SessionLocal() as session:
        expense = _visible_query(session, user).filter(Expense.code == code).first()
        if expense is None:
            # 不可见和不存在统一 404，避免枚举别人的单号。
            raise ExpenseNotFound(f"报销单不存在：{code}")

        provider = create_provider()
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(_history_messages(history or []))
        messages.append({"role": "user", "content": f"单号：{code}\n问题：{question}"})

        allowed_fields: Set[str] = set()
        trace: List[AgentStep] = []
        started = time.perf_counter()
        model_calls = 0
        tool_ms = 0.0
        usage: Optional[LlmUsage] = None

        for _ in range(MAX_STEPS):
            completion = provider.complete(messages, TOOL_SPECS)
            model_calls += 1
            if completion.usage:
                usage = LlmUsage(**completion.usage)

            if completion.tool_calls:
                messages.append(_assistant_message(completion))
                for call in completion.tool_calls:
                    try:
                        arguments = json.loads(call.arguments or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    tool_started = time.perf_counter()
                    outcome = run_tool(call.name, expense.id, session)
                    tool_ms += (time.perf_counter() - tool_started) * 1000
                    allowed_fields.update(outcome.fields)
                    trace.append(AgentStep(tool=call.name, ok=outcome.ok))
                    messages.append(_tool_message(call.id, outcome.payload))
                continue

            parsed = parse_answer(completion.text)
            parsed.evidence = filter_evidence(parsed.evidence, allowed_fields)
            return AskResult(
                conclusion=parsed.conclusion,
                evidence=parsed.evidence,
                insufficient=parsed.insufficient,
                trace=trace,
                metrics=AskMetrics(
                    elapsedMs=int((time.perf_counter() - started) * 1000),
                    ttftMs=None,
                    modelCalls=model_calls,
                    toolMs=int(tool_ms),
                    usage=usage,
                ),
            )

    raise RuntimeError("模型在最大步数内没有给出最终答案")


def prepare(user: User, code: str) -> int:
    """校验可见性并返回内部 id。响应头之前调用，404 还能正常返回。"""
    with SessionLocal() as session:
        expense = _visible_query(session, user).filter(Expense.code == code).first()
        if expense is None:
            raise ExpenseNotFound(f"报销单不存在：{code}")
        return expense.id


def stream_answer(
    user: User,
    expense_id: int,
    code: str,
    question: str,
    history: Optional[List[HistoryTurn]] = None,
):
    """逐 token 产出 delta，最后产出 done；错误产出 error。

    流开始后不再抛 HTTP 异常：响应头已经发出去了，只能把错误写成 error 事件。
    """
    provider = create_provider()
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(_history_messages(history or []))
    messages.append({"role": "user", "content": f"单号：{code}\n问题：{question}"})

    allowed_fields: Set[str] = set()
    trace: List[AgentStep] = []
    started = time.perf_counter()
    model_calls = 0
    tool_ms = 0.0
    usage: Optional[LlmUsage] = None
    ttft_ms: Optional[int] = None
    emitted = ""

    with SessionLocal() as session:
        for _ in range(MAX_STEPS):
            text = ""
            calls = []
            tool_phase = False
            try:
                for event in provider.stream(messages, TOOL_SPECS):
                    if event.type == "usage" and event.usage:
                        usage = LlmUsage(**event.usage)
                    elif event.type == "tool_call" and event.call:
                        tool_phase = True
                        calls.append(event.call)
                    elif event.type == "text":
                        text += event.text
                        if tool_phase:
                            continue
                        partial = partial_conclusion(text)
                        if not partial.startswith(emitted):
                            logger.warning("增量解析结果不是单调的，本块丢弃")
                            continue
                        if len(partial) > len(emitted):
                            if ttft_ms is None:
                                ttft_ms = int((time.perf_counter() - started) * 1000)
                            yield {"type": "delta", "text": partial[len(emitted) :]}
                            emitted = partial
            except Exception:
                logger.exception("模型流式调用失败")
                yield {"type": "error", "message": "模型调用失败，请重试"}
                return

            model_calls += 1

            if calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": text,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": call.arguments,
                                },
                            }
                            for call in calls
                        ],
                    }
                )
                for call in calls:
                    try:
                        arguments = json.loads(call.arguments or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    tool_started = time.perf_counter()
                    outcome = run_tool(call.name, expense_id, session)
                    tool_ms += (time.perf_counter() - tool_started) * 1000
                    allowed_fields.update(outcome.fields)
                    trace.append(AgentStep(tool=call.name, ok=outcome.ok))
                    messages.append(_tool_message(call.id, outcome.payload))
                if emitted:
                    logger.warning("工具轮之前已经向客户端吐出过文本，已重置展示")
                    emitted = ""
                continue

            try:
                parsed = parse_answer(text)
            except ValueError as exc:
                yield {"type": "error", "message": f"模型输出格式异常：{exc}"}
                return
            parsed.evidence = filter_evidence(parsed.evidence, allowed_fields)
            yield {
                "type": "done",
                "data": AskResult(
                    conclusion=parsed.conclusion,
                    evidence=parsed.evidence,
                    insufficient=parsed.insufficient,
                    trace=trace,
                    metrics=AskMetrics(
                        elapsedMs=int((time.perf_counter() - started) * 1000),
                        ttftMs=ttft_ms,
                        modelCalls=model_calls,
                        toolMs=int(tool_ms),
                        usage=usage,
                    ),
                ),
            }
            return

    yield {"type": "error", "message": "模型反复调用工具，已停止"}

