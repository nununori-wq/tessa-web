"""
Public-facing write endpoints for taxpayer-submitted data: reviews,
bug reports, complaints, achievement events, and escalation requests.

These are the server-side counterparts the frontend's localStorage-based
modules are already shaped to call — e.g. templates/index.html's
achievements module says outright: "swapping the storage layer for real
POST /achievements/event calls to a FastAPI backend later is a
localized change, not a rewrite." This file is that change.

None of these require the visitor to be logged in — see api/deps.py's
get_session_user, which transparently creates/reuses an anonymous
identity via cookie.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models.db import get_db
from models.orm import User
from services import persistence_service as db_service
from .deps import get_session_user

logger = logging.getLogger("chatbot")
router = APIRouter(prefix="/api")


class ReviewIn(BaseModel):
    rating: Optional[int] = None
    category: Optional[str] = None
    comment: Optional[str] = None
    conversation_id: Optional[int] = None
    message_id: Optional[int] = None


class BugReportIn(BaseModel):
    title: str
    description: str
    severity: str = "normal"
    page: Optional[str] = None
    browser_info: Optional[str] = None
    conversation_id: Optional[int] = None


class ComplaintIn(BaseModel):
    category: Optional[str] = None
    description: str
    conversation_id: Optional[int] = None


class AchievementEventIn(BaseModel):
    achievement_key: str


class EscalationIn(BaseModel):
    reason: str
    conversation_id: Optional[int] = None


@router.post("/reviews")
def submit_review(payload: ReviewIn, db: Session = Depends(get_db),
                   user: User = Depends(get_session_user)):
    review = db_service.create_review(
        db, user, payload.conversation_id, payload.message_id,
        payload.rating, payload.category, payload.comment,
    )
    db.commit()
    return {"id": review.id, "status": "recorded"}


@router.post("/bugs")
def submit_bug_report(payload: BugReportIn, db: Session = Depends(get_db),
                       user: User = Depends(get_session_user)):
    bug = db_service.create_bug_report(
        db, user, payload.title, payload.description, payload.severity,
        payload.page, payload.browser_info, payload.conversation_id,
    )
    db.commit()
    return {"id": bug.id, "status": "recorded"}


@router.post("/complaints")
def submit_complaint(payload: ComplaintIn, db: Session = Depends(get_db),
                      user: User = Depends(get_session_user)):
    complaint = db_service.create_complaint(
        db, user, payload.category, payload.description, payload.conversation_id,
    )
    db.commit()
    return {"id": complaint.id, "status": "recorded"}


@router.post("/achievements/event")
def submit_achievement_event(payload: AchievementEventIn, db: Session = Depends(get_db),
                              user: User = Depends(get_session_user)):
    row, newly_earned = db_service.record_achievement_event(db, user, payload.achievement_key)
    db.commit()
    if row is None:
        return {"status": "unknown_achievement_key"}
    return {"status": "newly_earned" if newly_earned else "already_earned", "achievement_key": payload.achievement_key}


@router.get("/achievements/me")
def get_my_achievements(db: Session = Depends(get_db), user: User = Depends(get_session_user)):
    from models.orm import Achievement, UserAchievement
    rows = (
        db.query(UserAchievement, Achievement)
        .join(Achievement, Achievement.id == UserAchievement.achievement_id)
        .filter(UserAchievement.user_id == user.id)
        .all()
    )
    return {"earned": [{
        "key": a.key, "name": a.name, "points": a.points,
        "earned_at": ua.earned_at.isoformat() if ua.earned_at else None,
    } for ua, a in rows]}


@router.post("/escalations")
def submit_escalation(payload: EscalationIn, db: Session = Depends(get_db),
                       user: User = Depends(get_session_user)):
    from models.orm import Conversation
    conversation = None
    if payload.conversation_id:
        conversation = db.query(Conversation).filter(Conversation.id == payload.conversation_id).first()
    if conversation is None:
        conversation = db_service.get_or_create_open_conversation(db, user)
    esc = db_service.create_escalation(db, user, conversation, payload.reason)
    db.commit()
    return {"id": esc.id, "status": "recorded"}
