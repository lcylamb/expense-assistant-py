import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sse_starlette.sse import EventSourceResponse
from starlette.middleware.sessions import SessionMiddleware

from .auth import Unauthorized, login as login_user, resolve_user, to_me
from .config import get_settings
from .db import SessionLocal, User, init_db
from .models import (
    AskRequest,
    AskResult,
    ExpenseListResult,
    LoginRequest,
    Me,
)
from .security import SESSION_COOKIE, SESSION_TTL_SECONDS, cookie_secure, session_secret
from .service import ExpenseNotFound, ask, list_expenses, prepare, stream_answer


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 本地演示：首次启动建表并 seed；生产用 `alembic upgrade head`。
    init_db()
    yield


app = FastAPI(title="expense-assistant-py", version="0.1.0", lifespan=lifespan)
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret(),
    session_cookie=SESSION_COOKIE,
    max_age=SESSION_TTL_SECONDS,
    same_site="lax",
    https_only=cookie_secure(),
)

WEB_DIST = Path(__file__).parent.parent / "web_dist"
if (WEB_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid4().hex
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response


@app.exception_handler(HTTPException)
async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": exc.detail},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    messages = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", []))
        messages.append(f"{location}: {error.get('msg', '参数不合法')}")
    return JSONResponse(status_code=422, content={"message": "；".join(messages)})


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(_: Request, __: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"message": "请求过于频繁，请稍后再试"},
    )


def current_user(request: Request) -> User:
    user_id = request.session.get("user_id")
    if not isinstance(user_id, int):
        raise HTTPException(status_code=401, detail="请先登录")

    with SessionLocal() as session:
        user = resolve_user(session, user_id)
        if user is None:
            request.session.clear()
            raise HTTPException(status_code=401, detail="请先登录")
        return user


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "provider": get_settings().llm_provider}


@app.post("/auth/login", response_model=Me)
@limiter.limit(lambda: get_settings().rate_limit_login)
def login_endpoint(request: Request, body: LoginRequest) -> Me:
    with SessionLocal() as session:
        try:
            user = login_user(session, body.username, body.password)
        except Unauthorized as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        request.session["user_id"] = user.id
        return to_me(user)


@app.post("/auth/logout", status_code=204, response_class=Response)
def logout_endpoint(request: Request) -> Response:
    request.session.clear()
    return Response(status_code=204)


@app.get("/auth/me", response_model=Me)
def me_endpoint(user: User = Depends(current_user)) -> Me:
    return to_me(user)


@app.get("/assistant/expenses", response_model=ExpenseListResult)
@limiter.limit(lambda: get_settings().rate_limit_ask)
def expenses_endpoint(
    request: Request,
    category: Optional[str] = None,
    user: User = Depends(current_user),
) -> ExpenseListResult:
    return list_expenses(user, category)


@app.post("/assistant/ask", response_model=AskResult)
@limiter.limit(lambda: get_settings().rate_limit_ask)
def ask_endpoint(
    request: Request,
    body: AskRequest,
    user: User = Depends(current_user),
) -> AskResult:
    try:
        return ask(user, body.code, body.question, body.history)
    except ExpenseNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"模型输出格式异常：{exc}") from exc


def _next_event(iterator):
    try:
        return next(iterator)
    except StopIteration:
        return None


@app.post("/assistant/askStream")
@limiter.limit(lambda: get_settings().rate_limit_stream)
def ask_stream_endpoint(
    request: Request,
    body: AskRequest,
    user: User = Depends(current_user),
) -> EventSourceResponse:
    try:
        # 先校验可见性：404 必须在写响应头之前返回。
        expense_id = prepare(user, body.code)
    except ExpenseNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def frames():
        iterator = stream_answer(
            user, expense_id, body.code, body.question, body.history
        )
        while True:
            # provider/SQLAlchemy 都是同步的，放到线程池里，避免阻塞事件循环。
            event = await asyncio.to_thread(_next_event, iterator)
            if event is None:
                break
            if event["type"] == "delta":
                payload = {"text": event["text"]}
                yield {
                    "event": "delta",
                    "data": json.dumps(payload, ensure_ascii=False),
                }
            elif event["type"] == "done":
                data = event["data"].model_dump()
                yield {
                    "event": "done",
                    "data": json.dumps(data, ensure_ascii=False),
                }
            else:
                payload = {"message": event["message"]}
                yield {
                    "event": "error",
                    "data": json.dumps(payload, ensure_ascii=False),
                }

    return EventSourceResponse(
        frames(),
        ping=15,
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str) -> FileResponse:
    index = WEB_DIST / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="前端构建产物不存在")

    if full_path.startswith(("auth/", "assistant/", "health", "docs", "openapi.json")):
        raise HTTPException(status_code=404, detail="Not Found")

    candidate = WEB_DIST / full_path
    if full_path and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(index)
