from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from .db import Expense


@dataclass
class ToolOutcome:
    ok: bool
    payload: Dict[str, Any]
    fields: List[str] = field(default_factory=list)


TOOL_SPECS: List[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_expense_summary",
            "description": "查报销单表头：单号、标题、申请人、部门、分类、总额、状态、提交时间。不含明细和审批意见。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_expense_items",
            "description": "列出报销单的所有费用明细：名称、分类、金额、费用发生日期、备注，以及合计金额。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_approval_record",
            "description": "查报销单审批记录：状态、决策时间、审批人、审批意见、驳回原因。草稿/待审批没有决策信息，表示数据不足，不是出错。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
]


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _payload_fields(payload: Dict[str, Any]) -> List[str]:
    fields = []
    for key, value in payload.items():
        if value is not None and value != []:
            fields.append(key)
    return fields


def _find_expense(session: Session, expense_id: int) -> Optional[Expense]:
    return session.get(Expense, expense_id)


def run_tool(name: str, expense_id: int, session: Session) -> ToolOutcome:
    expense = _find_expense(session, expense_id)
    if expense is None:
        return ToolOutcome(ok=True, payload={"error": "这张报销单不存在"}, fields=[])

    if name == "get_expense_summary":
        payload = {
            "code": expense.code,
            "title": expense.title,
            "applicant": expense.applicant,
            "department": expense.department,
            "category": expense.category,
            "amount": expense.amount,
            "status": expense.status,
            "submittedAt": _iso(expense.submitted_at),
        }
        return ToolOutcome(ok=True, payload=payload, fields=_payload_fields(payload))

    if name == "list_expense_items":
        items = [
            {
                "name": item.name,
                "category": item.category,
                "amount": item.amount,
                "spentOn": item.spent_on.date().isoformat(),
                "note": item.note,
            }
            for item in sorted(expense.items, key=lambda x: (x.spent_on, x.name))
        ]
        payload = {
            "code": expense.code,
            "items": items,
            "total": round(sum(item.amount for item in expense.items), 2),
        }
        return ToolOutcome(ok=True, payload=payload, fields=_payload_fields(payload))

    if name == "get_approval_record":
        payload = {
            "code": expense.code,
            "status": expense.status,
            "decidedAt": _iso(expense.decided_at),
            "approverName": expense.approver_name,
            "approvalNote": expense.approval_note,
            "rejectReason": expense.reject_reason,
        }
        payload = {key: value for key, value in payload.items() if value is not None}
        return ToolOutcome(ok=True, payload=payload, fields=_payload_fields(payload))

    return ToolOutcome(ok=False, payload={"error": f"未知工具：{name}"}, fields=[])
