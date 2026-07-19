"""Aurora enterprise agent web application."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent.react_agent import ReactAgent
from model.factory import chat_model
from services.auth import (
    SESSION_COOKIE,
    AuthService,
    hash_password,
    require_admin,
    require_user,
)
from services.database import AppDatabase, User
from services.kb_scheduler import KnowledgeBaseScheduler
from services.long_term_memory import LongTermMemoryService
from utils import settings
from utils.logger_handler import logger
from utils.path_tool import get_abs_path

WEB_DIR = get_abs_path("web")
_rate_hits: dict[str, list[float]] = {}
_rate_lock = threading.Lock()
_prune_lock = threading.Lock()
_last_prune = 0.0
_PRUNE_INTERVAL = 60


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = AppDatabase(get_abs_path(settings.APP_DB_PATH))
    if settings.APP_DEBUG:
        logger.warning(
            "[startup] APP_DEBUG=true: authentication and RBAC checks are bypassed"
        )
    if not settings.APP_DEBUG and "*" in settings.ALLOWED_ORIGINS:
        raise RuntimeError("ALLOWED_ORIGINS must be explicit when APP_DEBUG=false.")
    if not settings.APP_DEBUG and db.count_users() == 0:
        raise RuntimeError(
            "No users exist. Run `python -m scripts.create_admin --username <name>` first."
        )
    if not settings.APP_DEBUG and not settings.COOKIE_SECURE:
        logger.warning("[startup] COOKIE_SECURE=false; enable it when serving over HTTPS")
    app.state.db = db
    app.state.auth = AuthService(db)
    app.state.agent = ReactAgent()
    app.state.memory = LongTermMemoryService(db, chat_model)
    app.state.memory_executor = ThreadPoolExecutor(
        max_workers=2,
        thread_name_prefix="aurora-memory",
    )
    app.state.kb_scheduler = KnowledgeBaseScheduler(
        interval_seconds=settings.KB_SCAN_INTERVAL_SECONDS,
        enabled=settings.KB_SCAN_ENABLED,
    )
    app.state.kb_scheduler.start()
    logger.info(
        "[startup] mode=%s users=%s",
        "debug" if settings.APP_DEBUG else "production",
        db.count_users(),
    )
    yield
    app.state.kb_scheduler.stop()
    app.state.memory_executor.shutdown(wait=False, cancel_futures=True)
    app.state.agent.close()


app = FastAPI(
    title="Aurora Robot Vacuum Support",
    docs_url="/api/docs" if settings.APP_DEBUG else None,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=settings.ALLOWED_ORIGINS != ["*"],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)


class LoginPayload(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class CreateUserPayload(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.@-]+$")
    business_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_.@-]+$",
    )
    password: str = Field(min_length=10, max_length=256)
    role: str = Field(default="user", pattern=r"^(admin|user)$")


class UpdateUserPayload(BaseModel):
    role: str | None = Field(default=None, pattern=r"^(admin|user)$")
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=256)


class ChatPayload(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str = Field(
        min_length=8,
        max_length=100,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )


class ConversationPayload(BaseModel):
    conversation_id: str = Field(
        min_length=8,
        max_length=100,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )


def _user_json(user: User) -> dict:
    return {
        "id": user.id,
        "business_id": user.business_id,
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at,
    }


def _debug_chat_user(conversation_id: str) -> User:
    # Keep demo users 1001-1010 stable within each conversation.
    index = int(hashlib.sha256(conversation_id.encode()).hexdigest()[:8], 16) % 10
    user_id = str(1001 + index)
    return User(
        id="debug-admin",
        business_id=user_id,
        username="debug",
        role="admin",
        is_active=True,
        created_at=0,
    )


def _rate_ok(client_key: str, limit: int | None = None) -> bool:
    now = time.time()
    limit = settings.RATE_LIMIT_PER_MIN if limit is None else limit
    with _rate_lock:
        hits = [stamp for stamp in _rate_hits.get(client_key, []) if now - stamp < 60]
        if len(hits) >= limit:
            _rate_hits[client_key] = hits
            return False
        hits.append(now)
        _rate_hits[client_key] = hits
    return True


def _maybe_prune(app_instance: FastAPI) -> None:
    global _last_prune
    now = time.time()
    if now - _last_prune < _PRUNE_INTERVAL:
        return
    with _prune_lock:
        if now - _last_prune < _PRUNE_INTERVAL:
            return
        _last_prune = now

        with _rate_lock:
            stale = [
                key
                for key, stamps in _rate_hits.items()
                if all(now - stamp >= 60 for stamp in stamps)
            ]
            for key in stale:
                _rate_hits.pop(key, None)

        app_instance.state.db.prune_expired_auth_sessions(now)

        if settings.SESSION_TTL_MINUTES > 0:
            cutoff = now - settings.SESSION_TTL_MINUTES * 60
            for thread_id in app_instance.state.db.pop_expired_conversations(cutoff):
                try:
                    app_instance.state.agent.checkpointer.delete_thread(thread_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[prune] failed thread=%s err=%s", thread_id, exc
                    )


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.middleware("http")
async def observability(request: Request, call_next):
    request_id = uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    started = time.time()
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if request.url.path.startswith("/api"):
        logger.info(
            "[req %s] %s %s -> %s %sms",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            int((time.time() - started) * 1000),
        )
    return response


@app.get("/api/health/live")
def health_live():
    return {"status": "ok"}


@app.get("/api/health")
def health_ready(request: Request):
    checks = {"database": False, "knowledge_base": False}
    try:
        with request.app.state.db.connect() as conn:
            conn.execute("SELECT 1").fetchone()
        checks["database"] = True
        kb_status = request.app.state.kb_scheduler.status()
        last_result = kb_status.get("last_result")
        checks["knowledge_base"] = not kb_status.get("enabled") or (
            last_result is not None
            and last_result.get("status") == "ok"
            and kb_status.get("last_error") is None
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[health] readiness failed: %s", exc)
    ready = all(checks.values())
    return JSONResponse(
        {"status": "ok" if ready else "degraded", "checks": checks},
        status_code=200 if ready else 503,
    )


@app.post("/api/auth/login")
def login(payload: LoginPayload, request: Request):
    if settings.APP_DEBUG:
        return {"user": _user_json(require_user(request)), "debug": True}
    client_ip = request.client.host if request.client else "unknown"
    if not _rate_ok(
        f"login:{client_ip}",
        limit=settings.LOGIN_RATE_LIMIT_PER_MIN,
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again shortly.",
        )
    user = request.app.state.auth.authenticate(payload.username, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )
    auth_session = request.app.state.auth.create_session(user.id)
    response = JSONResponse({"user": _user_json(user)})
    response.set_cookie(
        SESSION_COOKIE,
        auth_session.token,
        max_age=settings.AUTH_SESSION_HOURS * 3600,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="strict",
        path="/",
    )
    request.app.state.db.audit(user.id, "login", "user", user.id)
    return response


@app.post("/api/auth/logout")
def logout(request: Request):
    request.app.state.auth.revoke(request.cookies.get(SESSION_COOKIE))
    response = JSONResponse({"detail": "Logged out."})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/api/auth/me")
def me(user: User = Depends(require_user)):
    return {"user": _user_json(user), "debug": settings.APP_DEBUG}


@app.get("/api/admin/users")
def list_users(request: Request, admin: User = Depends(require_admin)):
    return {"users": [_user_json(user) for user in request.app.state.db.list_users()]}


@app.post("/api/admin/users", status_code=201)
def create_user(
    payload: CreateUserPayload,
    request: Request,
    admin: User = Depends(require_admin),
):
    try:
        user = request.app.state.db.create_user(
            username=payload.username.strip(),
            password_hash=hash_password(payload.password),
            role=payload.role,
            created_by=admin.id,
            business_id=payload.business_id,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="Username or business user ID already exists.",
        ) from exc
    request.app.state.db.audit(
        admin.id, "user.create", "user", user.id, f"role={user.role}"
    )
    return {"user": _user_json(user)}


@app.patch("/api/admin/users/{user_id}")
def update_user(
    user_id: str,
    payload: UpdateUserPayload,
    request: Request,
    admin: User = Depends(require_admin),
):
    if user_id == admin.id and (
        payload.is_active is False or payload.role == "user"
    ):
        raise HTTPException(
            status_code=400,
            detail="You cannot disable or demote your own administrator account.",
        )
    password_hash = hash_password(payload.password) if payload.password else None
    user = request.app.state.db.update_user(
        user_id,
        role=payload.role,
        is_active=payload.is_active,
        password_hash=password_hash,
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if payload.password is not None or payload.is_active is False:
        request.app.state.db.revoke_user_sessions(user_id)
    request.app.state.db.audit(
        admin.id, "user.update", "user", user_id, "account fields updated"
    )
    return {"user": _user_json(user)}


@app.get("/api/memories")
def own_memories(request: Request, user: User = Depends(require_user)):
    return {"memories": request.app.state.db.list_memories(user.id)}


@app.delete("/api/memories")
def clear_own_memories(request: Request, user: User = Depends(require_user)):
    count = request.app.state.db.clear_memories(user.id)
    request.app.state.db.audit(user.id, "memory.clear", "user", user.id)
    return {"deleted": count}


@app.get("/api/admin/users/{user_id}/memories")
def admin_memories(
    user_id: str,
    request: Request,
    admin: User = Depends(require_admin),
):
    return {"memories": request.app.state.db.list_memories(user_id)}


@app.delete("/api/admin/users/{user_id}/memories")
def admin_clear_memories(
    user_id: str,
    request: Request,
    admin: User = Depends(require_admin),
):
    count = request.app.state.db.clear_memories(user_id)
    request.app.state.db.audit(admin.id, "memory.clear", "user", user_id)
    return {"deleted": count}


@app.post("/api/chat")
def chat(
    payload: ChatPayload,
    request: Request,
    authenticated_user: User = Depends(require_user),
):
    request_id = getattr(request.state, "request_id", "-")
    user = (
        _debug_chat_user(payload.conversation_id)
        if settings.APP_DEBUG
        else authenticated_user
    )
    query = payload.message.strip()
    if not query:
        raise HTTPException(status_code=422, detail="Message cannot be empty.")
    client_ip = request.client.host if request.client else "unknown"
    if not _rate_ok(f"{user.id}:{client_ip}"):
        raise HTTPException(status_code=429, detail="Too many requests.")

    thread_id = f"v2:{user.id}:{payload.conversation_id}"
    try:
        request.app.state.db.ensure_conversation(
            user.id, payload.conversation_id, thread_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conversation identity conflict.",
        ) from exc
    _maybe_prune(request.app)
    memory_context = request.app.state.memory.context_for_user(user.id)
    logger.info(
        "[req %s] chat user=%s conversation=%s",
        request_id,
        user.id,
        payload.conversation_id,
    )

    def event_stream():
        buffers: dict[str, str] = {}
        order: list[str] = []
        tool_messages: set[str] = set()
        completed = False
        try:
            events = request.app.state.agent.execute_token_events(
                query,
                thread_id=thread_id,
                user_id=user.business_id,
                long_term_memory=memory_context,
            )
            for event in events:
                mid = event.get("mid")
                if event.get("type") == "token" and mid:
                    if mid not in buffers:
                        buffers[mid] = ""
                        order.append(mid)
                    buffers[mid] += event.get("content", "")
                elif event.get("type") == "tool" and mid:
                    tool_messages.add(mid)
                yield _sse(event)
            completed = True
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[req %s] chat failed user=%s err=%s",
                request_id,
                user.id,
                exc,
                exc_info=True,
            )
            yield _sse(
                {
                    "type": "error",
                    "content": "Something went wrong on our side. Please try again shortly.",
                }
            )
        finally:
            yield _sse({"type": "done", "request_id": request_id})
            final_answer = next(
                (
                    buffers[mid]
                    for mid in reversed(order)
                    if mid not in tool_messages and buffers[mid].strip()
                ),
                "",
            )
            if completed and final_answer:
                request.app.state.memory_executor.submit(
                    request.app.state.memory.extract_and_store,
                    user.id,
                    payload.conversation_id,
                    query,
                    final_answer,
                )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.delete("/api/conversations/current")
def clear_conversation(
    payload: ConversationPayload,
    request: Request,
    authenticated_user: User = Depends(require_user),
):
    user = (
        _debug_chat_user(payload.conversation_id)
        if settings.APP_DEBUG
        else authenticated_user
    )
    thread_id = request.app.state.db.conversation_thread(
        user.id, payload.conversation_id
    )
    if thread_id:
        try:
            request.app.state.agent.checkpointer.delete_thread(thread_id)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[conversation] failed to clear checkpoint thread=%s err=%s",
                thread_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Conversation could not be cleared. Please retry.",
            ) from exc
        request.app.state.db.delete_conversation(user.id, payload.conversation_id)
    return {"deleted": bool(thread_id)}


@app.get("/api/admin/knowledge/status")
def knowledge_status(request: Request, admin: User = Depends(require_admin)):
    return request.app.state.kb_scheduler.status()


@app.post("/api/admin/knowledge/scan", status_code=202)
def knowledge_scan(request: Request, admin: User = Depends(require_admin)):
    result = request.app.state.kb_scheduler.trigger()
    request.app.state.db.audit(admin.id, "knowledge.scan", "knowledge_base")
    return result


def _page_user(request: Request) -> User | None:
    if settings.APP_DEBUG:
        return require_user(request)
    return request.app.state.auth.user_for_token(request.cookies.get(SESSION_COOKIE))


@app.get("/login")
def login_page(request: Request):
    if _page_user(request):
        return RedirectResponse("/")
    return FileResponse(os.path.join(WEB_DIR, "login.html"))


@app.get("/admin")
def admin_page(request: Request):
    user = _page_user(request)
    if not user:
        return RedirectResponse("/login")
    if user.role != "admin":
        return RedirectResponse("/")
    return FileResponse(os.path.join(WEB_DIR, "admin.html"))


@app.get("/")
def index(request: Request):
    if not _page_user(request):
        return RedirectResponse("/login")
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
