from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import init_db
from app.main import app

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def reset_db():
    init_db(reset=True)
    yield


async def test_sse_frames_and_done_shape() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/auth/login",
            json={"username": "wangwu", "password": "demo1234"},
        )
        assert login.status_code == 200

        response = await client.post(
            "/assistant/askStream",
            json={"code": "BX-2026-0003", "question": "这张单为什么被驳回？"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        text = response.text.replace("\r\n", "\n")
        assert text.count("event: delta") >= 2
        assert "event: done" in text

        done = text.split("event: done\ndata: ", 1)[1].strip()
        payload = json.loads(done)
        assert payload["insufficient"] is False
        assert payload["metrics"]["modelCalls"] >= 1
        assert payload["trace"][0]["tool"] == "get_approval_record"


async def test_sse_requires_login() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/assistant/askStream",
            json={"code": "BX-2026-0003", "question": "这张单为什么被驳回？"},
        )
        assert response.status_code == 401
