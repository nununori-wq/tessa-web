"""
Persistence helpers: the glue between TESSA's request handlers and the
application database (models/orm.py). Nothing in here talks to Pinecone
or Gemini — this module is only about "what happened", not "what TESSA
knows" or how a reply gets generated.

Every function takes an open `db: Session` and either returns a fresh
ORM object or mutates one; callers are responsible for the request's
overall commit, except where noted.
"""

from __future__ import annotations

import logging
import secrets
from typing import Optional

from sqlalchemy.orm import Session

from models.orm import (
    Achievement, BugReport, Complaint, Conversation, Escalation, Message,
    Review, User, UserAchievement,
)

logger = logging.getLogger("chatbot")

SESSION_COOKIE_NAME = "tessa_session"


def new_session_id() -> str:
    return secrets.token_urlsafe(32)


def get_or_create_user_by_session(db: Session, session_id: Optional[str]) -> User:
    """Anonymous-visitor identity. If session_id is missing or unknown,
    a new User row is created — callers are responsible for setting the
    session cookie on the response afterward (see app.py)."""
    user = None
    if session_id:
        user = db.query(User).filter(User.session_id == session_id).first()
    if user is None:
        user = User(session_id=session_id or new_session_id(), role="user", status="active")
        db.add(user)
        db.flush()  # assigns user.id without a full commit
    else:
        from datetime import datetime, timezone
        user.last_active_at = datetime.now(timezone.utc)
    return user


def get_or_create_open_conversation(db: Session, user: User) -> Conversation:
    """Reuses the visitor's most recent open conversation if one exists,
    otherwise starts a new one. Keeps the widget's single-thread feel
    (matches the frontend's single in-memory `conversationHistory`
    array) while still giving every exchange a durable home."""
    convo = (
        db.query(Conversation)
        .filter(Conversation.user_id == user.id, Conversation.status == "open")
        .order_by(Conversation.updated_at.desc())
        .first()
    )
    if convo is None:
        convo = Conversation(user_id=user.id, status="open")
        db.add(convo)
        db.flush()
    return convo


def add_message(db: Session, conversation: Conversation, sender: str, content: str,
                 intent: Optional[str] = None, escalate: Optional[bool] = None) -> Message:
    msg = Message(conversation_id=conversation.id, sender=sender, content=content,
                   intent=intent, escalate=escalate)
    db.add(msg)
    return msg


def create_escalation(db: Session, user: User, conversation: Conversation, reason: str) -> Escalation:
    esc = Escalation(user_id=user.id, conversation_id=conversation.id, reason=reason, status="open")
    db.add(esc)
    conversation.escalated = True
    return esc


def create_review(db: Session, user: Optional[User], conversation_id: Optional[int],
                   message_id: Optional[int], rating: Optional[int], category: Optional[str],
                   comment: Optional[str]) -> Review:
    review = Review(
        user_id=user.id if user else None,
        conversation_id=conversation_id,
        message_id=message_id,
        rating=rating,
        category=category,
        comment=comment,
    )
    db.add(review)
    return review


def create_bug_report(db: Session, user: Optional[User], title: str, description: str,
                       severity: str = "normal", page: Optional[str] = None,
                       browser_info: Optional[str] = None,
                       conversation_id: Optional[int] = None) -> BugReport:
    bug = BugReport(
        user_id=user.id if user else None,
        title=title, description=description, severity=severity,
        page=page, browser_info=browser_info, conversation_id=conversation_id,
    )
    db.add(bug)
    return bug


def create_complaint(db: Session, user: Optional[User], category: Optional[str], description: str,
                      conversation_id: Optional[int] = None) -> Complaint:
    complaint = Complaint(
        user_id=user.id if user else None,
        category=category, description=description, conversation_id=conversation_id,
    )
    db.add(complaint)
    return complaint


def record_achievement_event(db: Session, user: User, achievement_key: str) -> tuple[Optional[UserAchievement], bool]:
    """Idempotent per (user, achievement): mirrors the frontend's own
    'already earned?' check (see templates/index.html, recordActivity())
    so re-sending the same event never double-awards points. Returns
    (row, newly_created). If the achievement key isn't in the catalog,
    returns (None, False) rather than raising — an unknown key from an
    old/mismatched frontend build shouldn't break the request."""
    achievement = db.query(Achievement).filter(Achievement.key == achievement_key).first()
    if achievement is None:
        logger.warning("record_achievement_event: unknown achievement key %r", achievement_key)
        return None, False

    existing = (
        db.query(UserAchievement)
        .filter(UserAchievement.user_id == user.id, UserAchievement.achievement_id == achievement.id)
        .first()
    )
    if existing:
        return existing, False

    row = UserAchievement(user_id=user.id, achievement_id=achievement.id)
    db.add(row)
    db.flush()
    return row, True
