# expense-assistant-py

FastAPI + Pydantic + SQLAlchemy 的报销单问答后端。它是 TypeScript 版
`expense-assistant` 的 **Python 后端实现**：前端复用现有的 React + Ant Design
应用，Python 侧只负责 API、工具调用、权限、校验和 SSE。

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/login` | 登录，设置 httpOnly cookie |
| POST | `/auth/logout` | 退出，清 cookie |
| GET | `/auth/me` | 当前登录账号 |
| GET | `/assistant/expenses` | 当前账号可见的单据 + 汇总 |
| POST | `/assistant/ask` | 结构化问答 |
| POST | `/assistant/askStream` | SSE 问答 |
| GET | `/health` | 健康检查 |

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

本地首次启动会建表并 seed；生产/迁移用 Alembic：

```bash
alembic upgrade head
```

演示账号（密码都是 `demo1234`）：

| 账号 | 角色 | 能看到什么 |
|---|---|---|
| `zhangsan` | applicant | 自己的 2 张单 |
| `lisi` | applicant | 自己的 3 张单（含已打款） |
| `wangwu` | applicant | 自己的 2 张单（含被驳回） |
| `zhaoliu` | applicant | 自己的 2 张单（含草稿） |
| `sunqi` | applicant | 自己的 1 张草稿 |
| `wangjl` | approver | 所有非草稿单 |
| `zhengshi` | auditor | 所有单 |

## 接前端

复用 `expense-assistant/web` 的 React 应用，不要另写单页 HTML：

```bash
cd /Users/lcy/project/expense-assistant
BACKEND_ORIGIN=http://127.0.0.1:8000 npm run dev:web
```

Vite 会把 `/auth`、`/assistant`、`/health` 代理到 Python 后端。前端代码不改。

## 测试

```bash
pytest -q
```

当前 16 个测试，覆盖登录、未登录、越权、列表可见性、问答、SSE 多帧、错误体、解析、增量解析、evidence 过滤和前端入口。

## 接真实模型

```bash
export LLM_PROVIDER=openai
export LLM_API_KEY=...
export LLM_BASE_URL=https://api.deepseek.com
export LLM_MODEL=deepseek-chat
```

默认 `mock`，不配 key 也能跑、能测。`openai` provider 使用 OpenAI
`/chat/completions` 兼容协议。

## 设计要点

1. **单号服务端绑定**：请求带 `code`，服务端先按当前用户可见性查记录，再把内部
   `id` 传给工具；工具参数里没有单号，模型不能选择记录。
2. **可见性两层**：`prepare` 阶段 404 拒绝整次请求；工具查询再按 `applicant_id`
   过滤。申请人看不到别人的单，审批人看不到草稿，审计可看全部。
3. **evidence 白名单**：最终 evidence 只保留本次工具真实返回过的字段；未知字段
   丢弃并记录警告。
4. **结构化输出校验**：`parse_answer` 逐字段检查 `conclusion` / `evidence` /
   `insufficient`，坏 JSON 直接 502。
5. **SSE**：`text/event-stream`，`delta` + `done` 事件，`X-Accel-Buffering: no`；
   cookie 会话由浏览器自动携带。
6. **错误体**：统一 `{ "message": "..." }`，和前端 `ApiError` 对齐。

## 依赖选择

能用成熟库的，不手写：

- **会话**：Starlette `SessionMiddleware`（底层 `itsdangerous`）——签名 cookie、
  SameSite、过期都交给框架；
- **密码**：`pwdlib[argon2]`——Argon2id，不自己写 PBKDF2；
- **SSE**：`sse-starlette` 的 `EventSourceResponse`——分帧、心跳、客户端断开由库处理；
- **配置**：`pydantic-settings`——环境变量和 `.env` 统一管理；
- **限流**：`slowapi`——登录/问答/流式端点按 IP 限流；
- **迁移**：`alembic`——生产 schema 变更不靠启动时 `drop_all`；
- **校验/ORM**：Pydantic + SQLAlchemy。

仍然手写的只有业务逻辑：`partial_conclusion`（从半截 JSON 抠 `conclusion`）、
工具循环、evidence 白名单。这些没有合适的通用库，硬套反而更难维护。

## 单服务部署

前端构建产物已经放在 `web_dist/`，FastAPI 在同一个域名下托管页面和 API，避免
cookie/CORS 的跨域问题。

重新构建前端：

```bash
./build_web.sh
```

本地验证：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
# 打开 http://127.0.0.1:8000/
```

Docker：

```bash
docker build -t expense-assistant-py .
docker run --rm -p 8000:8000 --env-file .env expense-assistant-py
```

Render：

1. 把本目录推到 GitHub（`web_dist/` 要一起提交，或由 CI 构建）；
2. Render → New → Blueprint，选择仓库，读取 `render.yaml`；
3. 要接真实模型，在 Render 环境变量里填 `LLM_PROVIDER=openai` 和 `LLM_API_KEY`；
4. 部署完成后用 `/health` 检查，再打开根路径登录。

## 当前边界

- `askStream` 已接 provider 的流式接口：文本增量经过 `partial_conclusion` 解析后
  以 `delta` 事件发出，最终仍以 `parse_answer` 的结果为准。
- 还没有心跳帧；工具轮如果长时间无输出，部分代理可能按空闲连接处理。
- SQLite 用于零配置演示；首次启动 `create_all` + seed，生产用
  `alembic upgrade head` + PostgreSQL。
- 限流默认 `100/minute` 是演示值，生产要按网关/租户调低；当前按 IP，不按用户。
- 没有审计日志；这些属于生产化，不在当前 MVP 范围。
- 演示账号密码固定为 `demo123`，只用于本地/演示环境。
