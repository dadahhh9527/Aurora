"""
Aurora Robot Vacuum Support — FastAPI entrypoint

- GET  /                 Frontend page
- GET  /api/chat         SSE streaming chat (token-level, EventSource)
- POST /api/admin/reload Reload the knowledge base (auth required)
- GET  /api/health       Health check
- /static/*              Frontend static assets
"""
import json
import os
import threading
import time
import uuid

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from agent.react_agent import ReactAgent
from utils import settings
from utils.path_tool import get_abs_path
from utils.logger_handler import logger

WEB_DIR = get_abs_path("web")

app = FastAPI(title="Aurora Robot Vacuum Support", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# 全局唯一的 Agent 实例（应用启动时创建一次）
agent = ReactAgent()

# —— 限流：内存滑动窗口（客户端IP+会话 -> 最近一分钟的请求时间戳） ——
_rate_hits: dict[str, list[float]] = {}
_rate_lock = threading.Lock()

# —— 会话最近活跃时间，用于惰性清理过期会话记忆 ——
_last_seen: dict[str, float] = {}


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _check_auth(key: str | None) -> bool:
    # 未配置 APP_API_KEY 时不校验（本地开发友好）
    if not settings.APP_API_KEY:
        return True
    return key == settings.APP_API_KEY


def _rate_ok(client_key: str) -> bool:
    now = time.time()
    with _rate_lock:
        hits = [t for t in _rate_hits.get(client_key, []) if now - t < 60]
        if len(hits) >= settings.RATE_LIMIT_PER_MIN:
            _rate_hits[client_key] = hits
            return False
        hits.append(now)
        _rate_hits[client_key] = hits
    return True


def _prune_expired_sessions() -> None:
    """惰性清理空闲超过 TTL 的会话记忆，避免 SQLite 无限增长。"""
    if settings.SESSION_TTL_MINUTES <= 0:
        return
    ttl = settings.SESSION_TTL_MINUTES * 60
    now = time.time()
    expired = [sid for sid, ts in list(_last_seen.items()) if now - ts > ttl]
    for sid in expired:
        try:
            agent.checkpointer.delete_thread(sid)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[prune]清理会话失败 session={sid} err={str(e)}")
        _last_seen.pop(sid, None)


@app.middleware("http")
async def observability(request: Request, call_next):
    request_id = uuid.uuid4().hex[:8]
    request.state.request_id = request_id
    start = time.time()
    response = await call_next(request)
    cost_ms = int((time.time() - start) * 1000)
    # 静态资源不记录，避免噪音
    if request.url.path.startswith("/api"):
        logger.info(f"[req {request_id}] {request.method} {request.url.path} "
                    f"-> {response.status_code} {cost_ms}ms")
    return response


@app.get("/api/health")
def health():
    return JSONResponse({"status": "ok"})


@app.get("/api/chat")
def chat(
    request: Request,
    message: str = Query(..., min_length=1, description="用户输入内容"),
    session: str = Query(None, description="会话ID，同一会话内保持多轮记忆；刷新页面会换新ID"),
    key: str = Query(None, description="鉴权 key（当服务端配置了 APP_API_KEY 时必填）"),
):
    request_id = getattr(request.state, "request_id", "-")

    if not _check_auth(key):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    client_ip = request.client.host if request.client else "unknown"
    client_key = f"{client_ip}:{session or '-'}"
    if not _rate_ok(client_key):
        logger.warning(f"[req {request_id}] rate limit hit client={client_key}")
        return JSONResponse({"detail": "Too many requests, please try again shortly."}, status_code=429)

    query = message.strip()
    if session:
        _last_seen[session] = time.time()
    _prune_expired_sessions()

    logger.info(f"[req {request_id}] chat session={session or '-'} query={query[:50]!r}")

    def event_stream():
        if not query:
            yield _sse({"type": "error", "content": "Message cannot be empty."})
            yield _sse({"type": "done"})
            return

        try:
            for event in agent.execute_token_events(query, thread_id=session):
                yield _sse(event)
        except Exception as e:  # noqa: BLE001 —— fallback so the stream never dies silently
            logger.error(f"[req {request_id}] chat handling failed: {str(e)}", exc_info=True)
            yield _sse({"type": "error", "content": "Something went wrong on our side. Please try again shortly."})
        finally:
            yield _sse({"type": "done"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/admin/reload")
def reload_knowledge(request: Request, key: str = Query(None)):
    if not _check_auth(key):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    def _run():
        try:
            from rag.vector_store import VectorStoreService
            VectorStoreService().load_document()
            logger.info("[admin] knowledge base reloaded")
        except Exception as e:  # noqa: BLE001
            logger.error(f"[admin] knowledge base reload failed: {str(e)}", exc_info=True)

    threading.Thread(target=_run, daemon=True).start()
    return JSONResponse({"detail": "Knowledge base reload started in the background."})


@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
