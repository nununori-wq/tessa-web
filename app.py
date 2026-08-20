from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import os
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, Depends, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional

from models.db import get_db, init_db
from services.tessa_graph import run_tessa
from services import persistence_service as db_service
from api.deps import get_session_user
from api.public_routes import router as public_api_router
from admin.routes import router as admin_router

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chatbot")

# Initialization
app = FastAPI(title="TESSA")

STATIC_DIR = Path("static")
TEMPLATES_DIR = Path("templates")
STATIC_DIR.mkdir(parents=True, exist_ok=True)
(STATIC_DIR / "css").mkdir(parents=True, exist_ok=True)
(STATIC_DIR / "js").mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
(TEMPLATES_DIR / "admin").mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Middleware
#
# SECURITY NOTE: allow_origins=["*"] combined with allow_credentials=True
# is invalid per the CORS spec (browsers refuse to honor a wildcard origin
# on a credentialed request) and, if it ever did work, would let any
# website read cross-origin responses containing a signed-in visitor's
# session data. Since /chat and /api/* now rely on cookies
# (tessa_session, tessa_admin_session), "*" is no longer a safe default.
# ALLOWED_ORIGINS must be set to your real deployed origin(s) in
# production; unset/local falls back to no cross-origin credentialed
# access rather than silently allowing everything.
_raw_origins = os.environ.get("ALLOWED_ORIGINS", "").strip()
if _raw_origins and _raw_origins != "*":
    ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]
else:
    if _raw_origins == "*":
        logger.warning(
            "ALLOWED_ORIGINS=* is ignored for credentialed CORS (insecure/non-functional "
            "combination with cookies). Set ALLOWED_ORIGINS to your real origin(s), e.g. "
            "https://tessa.ird.gd. Falling back to no cross-origin access."
        )
    ALLOWED_ORIGINS = []

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_MESSAGE_LENGTH = int(os.environ.get("MAX_MESSAGE_LENGTH", "4000"))

# --- Minimal in-memory rate limiting -----------------------------------
# Not distributed/multi-process safe (fine for a single Render instance;
# would need Redis or similar behind a load balancer). Applies only to
# the routes that call out to Gemini/Pinecone, since those are the
# expensive/abusable ones -- static pages and health checks are exempt.
import time
import threading
from collections import defaultdict, deque
from starlette.middleware.base import BaseHTTPMiddleware

RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60"))
RATE_LIMIT_MAX_REQUESTS = int(os.environ.get("RATE_LIMIT_MAX_REQUESTS", "20"))
_RATE_LIMITED_PREFIXES = ("/chat", "/api/")

_rate_lock = threading.Lock()
_rate_buckets: dict[str, deque] = defaultdict(deque)


def _client_ip_for_rate_limit(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


RATE_LIMIT_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "true").lower() != "false"


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not RATE_LIMIT_ENABLED or not any(path.startswith(p) for p in _RATE_LIMITED_PREFIXES):
            return await call_next(request)

        key = _client_ip_for_rate_limit(request)
        now = time.monotonic()
        with _rate_lock:
            bucket = _rate_buckets[key]
            while bucket and now - bucket[0] > RATE_LIMIT_WINDOW_SECONDS:
                bucket.popleft()
            if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
                return JSONResponse(
                    {"detail": "Too many requests. Please slow down and try again shortly."},
                    status_code=429,
                )
            bucket.append(now)

        return await call_next(request)


app.add_middleware(RateLimitMiddleware)

# Mount the admin portal (/admin/*, /api/admin/*) and public write API
# (/api/reviews, /api/bugs, /api/complaints, /api/achievements/*,
# /api/escalations). Neither shows up in the normal taxpayer UI —
# templates/index.html has no admin navigation, and every /api/admin/*
# route is independently gated by admin.deps.require_admin regardless
# of what the frontend does or doesn't render.
app.include_router(admin_router)
app.include_router(public_api_router)


@app.on_event("startup")
async def startup_event():
    # Pinecone knowledge index — unchanged from before.
    from services.pinecone_service import init_index
    init_index()

    # Application database — creates tables if they don't exist yet.
    # Never drops or recreates existing tables/data.
    init_db()
    logger.info("Application database ready (DATABASE_URL=%s).",
                os.environ.get("DATABASE_URL", "sqlite:///./tessa.db (default)"))


@app.get("/", response_class=HTMLResponse)
async def serve_frontend(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/chat")
async def chat(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user=Depends(get_session_user),
):
    data = await request.json()

    message = data.get("message")
    history = data.get("history") or []  # frontend's in-memory conversationHistory, sent each turn

    # Client sends a short LANGUAGE CODE, never raw prompt text -- this is
    # validated against a whitelist in run_tessa()/SUPPORTED_LANGUAGES, so a
    # user editing devtools cannot inject arbitrary system-prompt content
    # (the frontend previously sent a full `system_prompt` string here,
    # which this route never read -- that field is intentionally ignored
    # if still present in older frontend builds).
    language = data.get("language") or "en"

    if not message or not message.strip():
        raise HTTPException(status_code=400, detail="message cannot be empty.")

    if len(message) > MAX_MESSAGE_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"message is too long (max {MAX_MESSAGE_LENGTH} characters).",
        )

    # --- Persist the incoming user message before generating a reply,
    # so a crash mid-generation still leaves a record of what was asked. ---
    conversation = db_service.get_or_create_open_conversation(db, user)
    db_service.add_message(db, conversation, sender="user", content=message)
    db.commit()

    # Runs the full TESSA workflow: language detection -> intent detection
    # (with follow-up-aware query rewriting from history) -> RAG retrieval ->
    # response generation -> safety check -> channel formatting.
    #
    # If the LLM call itself fails (rate limit, network, bad/missing key),
    # run_tessa currently propagates the exception rather than degrading
    # gracefully like services/pinecone_service.py does. Catching it here
    # keeps that failure mode from becoming a raw 500 — the taxpayer's
    # message is still safely persisted, and they get the same kind of
    # escalation-style answer TESSA gives for "no verified knowledge",
    # rather than a broken widget.
    try:
        result = run_tessa(message, history=history, channel="web", language=language)
    except Exception:
        logger.exception("run_tessa failed for conversation_id=%s", conversation.id)
        result = {
            "response": (
                "I'm having trouble reaching my systems right now, so I can't "
                "verify an answer for you. Please try again shortly, or contact "
                "an IRD officer directly if this is urgent."
            ),
            "intent": None,
            "escalate": True,
        }

    reply_text = result.get("response", "")
    intent = result.get("intent")
    escalate = bool(result.get("escalate", False))

    # --- Persist TESSA's reply and update conversation state. ---
    db.refresh(conversation)
    # Tag the user message we just saved with the detected intent, and
    # label the conversation's topic on its first exchange.
    last_user_msg = (
        db.query(db_service.Message)
        .filter(db_service.Message.conversation_id == conversation.id, db_service.Message.sender == "user")
        .order_by(db_service.Message.id.desc())
        .first()
    )
    if last_user_msg is not None:
        last_user_msg.intent = intent
    if conversation.topic is None:
        conversation.topic = intent

    db_service.add_message(db, conversation, sender="tessa", content=reply_text,
                            intent=None, escalate=escalate)

    if escalate:
        note = result.get("clarification_question") or "No verified knowledge was found for this question."
        db_service.create_escalation(db, user, conversation, reason=note)

    db.commit()

    logger.info(
        "Chat response generated | user_id=%s | conversation_id=%s | intent=%s | escalate=%s",
        user.id, conversation.id, intent, escalate,
    )

    # NOTE: the frontend (templates/index.html) reads `data.reply` — the
    # previous version of this route returned `response` instead, which
    # meant every real reply silently fell through to the frontend's
    # offline fallback answer. `reply` is now the primary field; `response`
    # is kept alongside it for any other caller that may depend on it.
    return {
        "reply": reply_text,
        "response": reply_text,
        "intent": intent,
        "escalate": escalate,
        "conversation_id": conversation.id,
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
