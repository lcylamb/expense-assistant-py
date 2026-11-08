from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app
from app.service import filter_evidence, parse_answer
from app.stream_parse import partial_conclusion


@pytest.fixture(autouse=True)
def reset_db():
    init_db(reset=True)
    client.cookies.clear()
    yield


client = TestClient(app)


def login(username: str = "zhangsan", password: str = "demo1234") -> None:
    response = client.post(
        "/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text


def test_health_is_public() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_spa_index_is_served() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text


def test_login_and_me() -> None:
    login()
    response = client.get("/auth/me")
    assert response.status_code == 200
    assert response.json()["username"] == "zhangsan"
    assert response.json()["role"] == "applicant"


def test_wrong_password_returns_401() -> None:
    response = client.post(
        "/auth/login",
        json={"username": "zhangsan", "password": "wrong"},
    )
    assert response.status_code == 401
    assert response.json()["message"] == "用户名或密码不对"


def test_ask_requires_login() -> None:
    response = client.post(
        "/assistant/ask",
        json={"code": "BX-2026-0003", "question": "这张单为什么被驳回？"},
    )
    assert response.status_code == 401
    assert response.json()["message"] == "请先登录"


def test_ask_rejected_reason() -> None:
    login("wangwu")
    response = client.post(
        "/assistant/ask",
        json={"code": "BX-2026-0003", "question": "这张单为什么被驳回？"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "驳回" in body["conclusion"]
    assert "rejectReason" in body["evidence"]
    assert body["insufficient"] is False
    assert body["metrics"]["modelCalls"] >= 1


def test_applicant_cannot_see_another_users_expense() -> None:
    login()
    response = client.post(
        "/assistant/ask",
        json={"code": "BX-2026-0002", "question": "这张单审批意见是什么？"},
    )
    assert response.status_code == 404


def test_approver_can_see_submitted_expense() -> None:
    login("wangjl")
    response = client.post(
        "/assistant/ask",
        json={"code": "BX-2026-0003", "question": "这张单为什么被驳回？"},
    )
    assert response.status_code == 200


def test_draft_is_insufficient() -> None:
    login("zhaoliu")
    response = client.post(
        "/assistant/ask",
        json={"code": "BX-2026-0005", "question": "审批意见是什么？"},
    )
    assert response.status_code == 200
    assert response.json()["insufficient"] is True


def test_expenses_list_uses_visibility() -> None:
    login()
    response = client.get("/assistant/expenses")
    assert response.status_code == 200
    body = response.json()
    assert body["viewer"]["name"] == "张三"
    assert {item["code"] for item in body["expenses"]} == {"BX-2026-0001", "BX-2026-0007"}
    assert body["summary"]["count"] == 2


def test_logout_clears_session() -> None:
    login()
    assert client.post("/auth/logout").status_code == 204
    assert client.post(
        "/assistant/ask",
        json={"code": "BX-2026-0003", "question": "这张单为什么被驳回？"},
    ).status_code == 401


def test_parse_answer_rejects_bad_shapes() -> None:
    with pytest.raises(ValueError):
        parse_answer("not json")
    with pytest.raises(ValueError):
        parse_answer('{"conclusion": "", "evidence": [], "insufficient": false}')
    with pytest.raises(ValueError):
        parse_answer('{"conclusion": "x", "evidence": "x", "insufficient": false}')
    with pytest.raises(ValueError):
        parse_answer('{"conclusion": "x", "evidence": [], "insufficient": "no"}')


def test_filter_evidence_drops_unknown() -> None:
    assert filter_evidence(["status", "madeUp"], {"status"}) == ["status"]


def test_partial_conclusion_handles_incomplete_json() -> None:
    assert partial_conclusion('{"conclusion": "这张单被驳') == "这张单被驳"
    assert partial_conclusion('{"conclusion": "a\\"b') == 'a"b'
    assert partial_conclusion('{"conclusion": "断在转义\\') == "断在转义"
    assert partial_conclusion('{"conclusion": "还没到') != ""
