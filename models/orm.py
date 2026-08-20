"""
TESSA application database — ORM models.

Covers: Users, Conversations, Messages, Reviews, BugReports, Complaints,
Achievements/UserAchievements, Escalations, AdminAuditLog.

Design choices worth knowing about:

- There is ONE `User` table, used for both anonymous taxpayer visitors
  and admin accounts — not two separate models. An anonymous visitor is
  identified by a `session_id` (a random token set in an httpOnly
  cookie, see app.py); an admin is identified by `email`+`password_hash`
  and `role="admin"`. This matches the brief's instruction not to create
  a second/duplicate User model.
- `is_admin` / `is_active` are Python properties (not columns) derived
  from `role` and `status`, kept for compatibility with the admin-auth
  module's existing deps.py/routes.py, which already check
  `user.is_admin` / `user.is_active`.
- Nothing here stores more than it needs to. No plaintext passwords, no
  full browser fingerprints — just a free-text `browser_info` string a
  bug reporter can optionally supply.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .db import Base


def _now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)

    # Anonymous-visitor identity (set via cookie on first /chat or /api/* call).
    session_id = Column(String(64), unique=True, nullable=True, index=True)

    display_name = Column(String(120), nullable=True)

    # Auth identity — only set for accounts that can actually log in (admins,
    # and any future "registered taxpayer" accounts).
    email = Column(String(255), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=True)

    role = Column(String(20), nullable=False, default="user")       # "user" | "admin"
    status = Column(String(20), nullable=False, default="active")   # "active" | "suspended"

    created_at = Column(DateTime, default=_now)
    last_active_at = Column(DateTime, default=_now, onupdate=_now)

    conversations = relationship("Conversation", back_populates="user")
    audit_entries = relationship("AdminAuditLog", back_populates="actor")

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin" and self.is_active


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    topic = Column(String(120), nullable=True)          # e.g. detected intent of first message
    status = Column(String(20), nullable=False, default="open")     # "open" | "closed"
    escalated = Column(Boolean, nullable=False, default=False)

    user = relationship("User", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation",
                             order_by="Message.created_at", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False, index=True)

    sender = Column(String(20), nullable=False)   # "user" | "tessa"
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_now)

    intent = Column(String(40), nullable=True)     # only meaningful for sender="user"
    escalate = Column(Boolean, nullable=True)       # only meaningful for sender="tessa"

    conversation = relationship("Conversation", back_populates="messages")


class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=True)
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=True)

    rating = Column(Integer, nullable=True)          # e.g. 1-5, nullable if thumbs-only
    category = Column(String(60), nullable=True)
    comment = Column(Text, nullable=True)

    created_at = Column(DateTime, default=_now)
    resolved = Column(Boolean, nullable=False, default=False)


class BugReport(Base):
    __tablename__ = "bug_reports"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    severity = Column(String(20), nullable=False, default="normal")  # low|normal|high|critical
    status = Column(String(20), nullable=False, default="open")      # open|in_progress|resolved|wontfix

    page = Column(String(255), nullable=True)
    browser_info = Column(String(255), nullable=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=True)

    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
    admin_notes = Column(Text, nullable=True)


class Complaint(Base):
    __tablename__ = "complaints"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    category = Column(String(60), nullable=True)
    description = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="open")

    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=True)

    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
    admin_notes = Column(Text, nullable=True)


class Achievement(Base):
    """Catalog of badges — matches the BADGES table already defined in
    the frontend (templates/index.html, search "TESSA ACHIEVEMENTS
    MODULE") so the server and client speak the same badge keys."""
    __tablename__ = "achievements"

    id = Column(Integer, primary_key=True)
    key = Column(String(60), unique=True, nullable=False)   # e.g. "tax_basics"
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    badge = Column(String(20), nullable=True)                # emoji/icon, e.g. "🏅"
    points = Column(Integer, nullable=False, default=0)
    requirements = Column(Text, nullable=True)                # human-readable requirement text

    user_achievements = relationship("UserAchievement", back_populates="achievement")


class UserAchievement(Base):
    __tablename__ = "user_achievements"
    __table_args__ = (UniqueConstraint("user_id", "achievement_id", name="uq_user_achievement"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    achievement_id = Column(Integer, ForeignKey("achievements.id"), nullable=False, index=True)
    earned_at = Column(DateTime, default=_now)

    achievement = relationship("Achievement", back_populates="user_achievements")


class Escalation(Base):
    __tablename__ = "escalations"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=True)

    reason = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="open")   # open|assigned|resolved
    assigned_admin_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
    resolution_notes = Column(Text, nullable=True)


class AdminAuditLog(Base):
    """Append-only record of admin actions and rejected admin-access
    attempts. Nothing in the app updates or deletes rows here — routes
    only ever INSERT (see admin/deps.py)."""
    __tablename__ = "admin_audit_log"

    id = Column(Integer, primary_key=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    actor_email = Column(String(255), nullable=True)
    action = Column(String(120), nullable=False)
    target = Column(String(255), nullable=True)
    detail = Column(Text, nullable=True)
    ip_address = Column(String(64), nullable=True)
    success = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=_now)

    actor = relationship("User", back_populates="audit_entries")
