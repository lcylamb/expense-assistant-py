from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class Me(BaseModel):
    username: str
    name: str
    role: str
    department: str


class HistoryTurn(BaseModel):
    question: str = Field(max_length=200)
    conclusion: str = Field(max_length=500)
    evidence: List[str] = Field(default_factory=list)
    insufficient: bool


class AskRequest(BaseModel):
    code: str = Field(min_length=1)
    question: str = Field(min_length=1)
    history: List[HistoryTurn] = Field(default_factory=list)


class ParsedAnswer(BaseModel):
    conclusion: str
    evidence: List[str]
    insufficient: bool


class AgentStep(BaseModel):
    tool: str
    ok: bool


class LlmUsage(BaseModel):
    promptTokens: int
    completionTokens: int


class AskMetrics(BaseModel):
    elapsedMs: int
    ttftMs: Optional[int] = None
    modelCalls: int
    toolMs: int
    usage: Optional[LlmUsage] = None


class AskResult(ParsedAnswer):
    trace: List[AgentStep] = Field(default_factory=list)
    metrics: AskMetrics


class ExpenseListItem(BaseModel):
    code: str
    title: str
    applicant: str
    department: str
    category: str
    status: str
    amount: str
    submittedAt: str


class ExpenseListSummary(BaseModel):
    count: int
    byStatus: dict
    totalAmount: str
    rejectedCount: int


class ExpenseListResult(BaseModel):
    viewer: Optional[dict]
    expenses: List[ExpenseListItem]
    summary: ExpenseListSummary
