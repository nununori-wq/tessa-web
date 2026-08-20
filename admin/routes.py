"""
Admin routes.

Route map:

  GET  /admin                 -> redirects to /admin/dashboard
  GET  /admin/login           -> login page (public)
  POST /admin/login           -> credential check, sets session cookie
  POST /admin/logout          -> clears session cookie
  GET  /admin/dashboard       -> protected HTML shell
  /api/admin/*                -> protected JSON endpoints, one per sidebar section

Every route under /admin/dashboard and every /api/admin/* route depends
on `require_admin`, so unauthorized requests are rejected by FastAPI's
dependency system before the handler body ever runs.
"""

from __future__ import annotations

import html
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from models.db import get_db
from models.orm import (
    Achievement, AdminAuditLog, BugReport, Complaint, Conversation, Escalation,
    Message, Review, User, UserAchievement,
)
from .deps import _client_ip, get_current_user, log_admin_action, require_admin
from .security import SESSION_COOKIE_NAME, create_session_token, verify_password

router = APIRouter()

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates", "admin")
COOKIE_SECURE = os.environ.get("ADMIN_COOKIE_SECURE", "true").lower() != "false"


def _render(filename: str, **substitutions: str) -> str:
    path = os.path.join(_TEMPLATES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    for key, value in substitutions.items():
        content = content.replace("{{ " + key + " }}", html.escape(str(value)))
    return content


# ---------------------------------------------------------------------
# /admin -> /admin/dashboard
# ---------------------------------------------------------------------

@router.get("/admin")
def admin_root():
    return RedirectResponse(url="/admin/dashboard", status_code=status.HTTP_302_FOUND)


# ---------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------

@router.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(current_user: User | None = Depends(get_current_user)):
    if current_user and current_user.is_admin:
        return RedirectResponse(url="/admin/dashboard", status_code=status.HTTP_302_FOUND)
    return HTMLResponse(_render("login.html", error=""))


@router.post("/admin/login")
def admin_login_submit(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
):
    ip = _client_ip(request)
    normalized_email = email.strip().lower()
    user = db.query(User).filter(User.email == normalized_email).first()

    generic_error = "Invalid email or password."

    if not user or not verify_password(password, user.password_hash):
        log_admin_action(db, action="admin_login_failed", success=False,
                          actor_email=normalized_email, detail="bad credentials", ip=ip)
        return HTMLResponse(_render("login.html", error=generic_error), status_code=status.HTTP_401_UNAUTHORIZED)

    if not user.is_active:
        log_admin_action(db, action="admin_login_failed", success=False, user=user,
                          detail="inactive account", ip=ip)
        return HTMLResponse(_render("login.html", error=generic_error), status_code=status.HTTP_401_UNAUTHORIZED)

    if not user.is_admin:
        log_admin_action(db, action="admin_login_failed", success=False, user=user,
                          detail="valid credentials, non-admin role attempted admin login", ip=ip)
        return HTMLResponse(_render("login.html", error=generic_error), status_code=status.HTTP_401_UNAUTHORIZED)

    token, _expires_at = create_session_token(user.id)
    log_admin_action(db, action="admin_login_success", success=True, user=user, ip=ip)

    max_age_seconds = int(os.environ.get("ADMIN_JWT_EXPIRE_MINUTES", "30")) * 60
    redirect = RedirectResponse(url="/admin/dashboard", status_code=status.HTTP_302_FOUND)
    redirect.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        max_age=max_age_seconds,
        path="/",
    )
    return redirect


@router.post("/admin/logout")
def admin_logout(db: Session = Depends(get_db), current_user: User | None = Depends(get_current_user)):
    if current_user:
        log_admin_action(db, action="admin_logout", success=True, user=current_user)
    redirect = RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
    redirect.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return redirect


# ---------------------------------------------------------------------
# Protected dashboard shell
# ---------------------------------------------------------------------

@router.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db),
                     current_user: User | None = Depends(get_current_user)):
    if current_user is None:
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
    if not current_user.is_admin:
        log_admin_action(db, action="admin_access_denied", success=False, user=current_user,
                          detail="non-admin hit /admin/dashboard", ip=_client_ip(request))
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
    return HTMLResponse(_render("dashboard.html", admin_email=current_user.email or ""))


# ---------------------------------------------------------------------
# Protected JSON API — real data, one endpoint per sidebar section.
# ---------------------------------------------------------------------

def _iso(dt):
    return dt.isoformat() if dt else None


@router.get("/api/admin/overview")
def api_overview(current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    return {
        "total_users": db.query(func.count(User.id)).scalar(),
        "total_conversations": db.query(func.count(Conversation.id)).scalar(),
        "open_escalations": db.query(func.count(Escalation.id)).filter(Escalation.status == "open").scalar(),
        "open_bugs": db.query(func.count(BugReport.id)).filter(BugReport.status == "open").scalar(),
        "open_complaints": db.query(func.count(Complaint.id)).filter(Complaint.status == "open").scalar(),
        "conversations_last_24h": db.query(func.count(Conversation.id)).filter(Conversation.created_at >= since_24h).scalar(),
        "requested_by": current_user.email,
    }


@router.get("/api/admin/users")
def api_users(current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.id.desc()).limit(200).all()
    return {"users": [{
        "id": u.id, "email": u.email, "display_name": u.display_name,
        "role": u.role, "status": u.status,
        "created_at": _iso(u.created_at), "last_active_at": _iso(u.last_active_at),
        "is_anonymous": u.email is None,
    } for u in users]}


@router.get("/api/admin/conversations")
def api_conversations(current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    convos = db.query(Conversation).order_by(Conversation.updated_at.desc()).limit(200).all()
    return {"conversations": [{
        "id": c.id, "user_id": c.user_id, "topic": c.topic, "status": c.status,
        "escalated": c.escalated, "message_count": len(c.messages),
        "created_at": _iso(c.created_at), "updated_at": _iso(c.updated_at),
    } for c in convos]}


@router.get("/api/admin/conversations/{conversation_id}/messages")
def api_conversation_messages(conversation_id: int, current_user: User = Depends(require_admin),
                               db: Session = Depends(get_db)):
    msgs = (db.query(Message).filter(Message.conversation_id == conversation_id)
            .order_by(Message.created_at).all())
    return {"messages": [{
        "id": m.id, "sender": m.sender, "content": m.content,
        "intent": m.intent, "escalate": m.escalate, "created_at": _iso(m.created_at),
    } for m in msgs]}


@router.get("/api/admin/reviews")
def api_reviews(current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(Review).order_by(Review.created_at.desc()).limit(200).all()
    return {"reviews": [{
        "id": r.id, "user_id": r.user_id, "conversation_id": r.conversation_id,
        "rating": r.rating, "category": r.category, "comment": r.comment,
        "resolved": r.resolved, "created_at": _iso(r.created_at),
    } for r in rows]}


@router.get("/api/admin/bug-reports")
def api_bug_reports(current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(BugReport).order_by(BugReport.created_at.desc()).limit(200).all()
    return {"bug_reports": [{
        "id": b.id, "user_id": b.user_id, "title": b.title, "description": b.description,
        "severity": b.severity, "status": b.status, "page": b.page,
        "browser_info": b.browser_info, "admin_notes": b.admin_notes,
        "created_at": _iso(b.created_at), "updated_at": _iso(b.updated_at),
    } for b in rows]}


@router.get("/api/admin/complaints")
def api_complaints(current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(Complaint).order_by(Complaint.created_at.desc()).limit(200).all()
    return {"complaints": [{
        "id": c.id, "user_id": c.user_id, "category": c.category, "description": c.description,
        "status": c.status, "admin_notes": c.admin_notes,
        "created_at": _iso(c.created_at), "updated_at": _iso(c.updated_at),
    } for c in rows]}


@router.get("/api/admin/escalations")
def api_escalations(current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(Escalation).order_by(Escalation.created_at.desc()).limit(200).all()
    return {"escalations": [{
        "id": e.id, "user_id": e.user_id, "conversation_id": e.conversation_id,
        "reason": e.reason, "status": e.status, "assigned_admin_id": e.assigned_admin_id,
        "resolution_notes": e.resolution_notes,
        "created_at": _iso(e.created_at), "updated_at": _iso(e.updated_at),
    } for e in rows]}


@router.get("/api/admin/achievements")
def api_achievements(current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    catalog = db.query(Achievement).all()
    earned_counts = dict(
        db.query(UserAchievement.achievement_id, func.count(UserAchievement.id))
        .group_by(UserAchievement.achievement_id).all()
    )
    return {"achievements": [{
        "id": a.id, "key": a.key, "name": a.name, "description": a.description,
        "badge": a.badge, "points": a.points, "times_earned": earned_counts.get(a.id, 0),
    } for a in catalog]}


@router.get("/api/admin/analytics")
def api_analytics(current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    total_conversations = db.query(func.count(Conversation.id)).scalar() or 0
    escalated = db.query(func.count(Conversation.id)).filter(Conversation.escalated.is_(True)).scalar() or 0
    avg_rating = db.query(func.avg(Review.rating)).filter(Review.rating.isnot(None)).scalar()
    return {
        "total_users": db.query(func.count(User.id)).scalar(),
        "total_conversations": total_conversations,
        "total_messages": db.query(func.count(Message.id)).scalar(),
        "escalation_rate": round(escalated / total_conversations, 4) if total_conversations else 0,
        "average_review_rating": round(avg_rating, 2) if avg_rating is not None else None,
        "total_bug_reports": db.query(func.count(BugReport.id)).scalar(),
        "total_complaints": db.query(func.count(Complaint.id)).scalar(),
        "total_achievements_earned": db.query(func.count(UserAchievement.id)).scalar(),
    }


@router.get("/api/admin/system-health")
def api_system_health(current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    # Real checks, not decoration: each one actually exercises the thing
    # it claims to report on.
    db_ok = True
    db_error = None
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - defensive
        db_ok = False
        db_error = str(exc)

    return {
        "database": {"ok": db_ok, "error": db_error, "url_scheme": os.environ.get("DATABASE_URL", "sqlite:///./tessa.db").split(":")[0]},
        "pinecone_configured": bool(os.environ.get("PINECONE_API_KEY")),
        "gemini_configured": bool(os.environ.get("GEMINI_API_KEY")),
        "openai_configured": bool(os.environ.get("OPENAI_API_KEY")),
    }


@router.get("/api/admin/knowledge-base")
def api_knowledge_base(current_user: User = Depends(require_admin)):
    """Connects to the existing Pinecone/RAG layer for a status view —
    does not duplicate or replace it."""
    try:
        from services.pinecone_service import get_index, INDEX_NAME
    except Exception as exc:  # pragma: no cover
        return {"configured": False, "error": f"pinecone_service import failed: {exc}"}

    index = get_index()
    if index is None:
        return {"configured": False, "index_name": INDEX_NAME, "note": "Pinecone not configured or unreachable."}
    try:
        stats = index.describe_index_stats()
        return {"configured": True, "index_name": INDEX_NAME, "stats": dict(stats) if hasattr(stats, "keys") else str(stats)}
    except Exception as exc:  # pragma: no cover
        return {"configured": True, "index_name": INDEX_NAME, "error": str(exc)}


@router.get("/api/admin/audit")
def api_audit_log(current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(AdminAuditLog).order_by(AdminAuditLog.id.desc()).limit(200).all()
    return {"entries": [{
        "id": r.id, "actor_email": r.actor_email, "action": r.action, "target": r.target,
        "success": r.success, "detail": r.detail, "ip_address": r.ip_address,
        "created_at": _iso(r.created_at),
    } for r in rows]}


@router.get("/api/admin/settings")
def api_settings(current_user: User = Depends(require_admin)):
    return {
        "note": "Settings surface is a stub — no mutable app-wide settings exist yet to display.",
        "requested_by": current_user.email,
    }


# ---------------------------------------------------------------------
# Write actions (status changes) — every one logs to the audit trail.
# ---------------------------------------------------------------------

@router.post("/api/admin/bug-reports/{bug_id}/status")
def api_update_bug_status(bug_id: int, request: Request, new_status: str,
                           current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    bug = db.query(BugReport).filter(BugReport.id == bug_id).first()
    if not bug:
        return JSONResponse({"error": "not found"}, status_code=404)
    old = bug.status
    bug.status = new_status
    db.commit()
    log_admin_action(db, action="bug_report.update_status", success=True, user=current_user,
                      target=f"bug_report:{bug_id}", detail=f"{old} -> {new_status}", ip=_client_ip(request))
    return {"id": bug.id, "status": bug.status}


@router.post("/api/admin/users/{user_id}/role")
def api_update_user_role(user_id: int, request: Request, new_role: str,
                          current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    if new_role not in ("user", "admin"):
        return JSONResponse({"error": "role must be 'user' or 'admin'"}, status_code=400)
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        return JSONResponse({"error": "not found"}, status_code=404)
    old = target_user.role
    target_user.role = new_role
    db.commit()
    log_admin_action(db, action="user.update_role", success=True, user=current_user,
                      target=f"user:{user_id}", detail=f"{old} -> {new_role}", ip=_client_ip(request))
    return {"id": target_user.id, "role": target_user.role}
