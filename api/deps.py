"""
Session-user resolution shared by /chat and the public /api/* write
routes (reviews, bugs, complaints, achievements, escalations).

Every taxpayer visiting TESSA gets a lightweight anonymous User row,
identified by a random token in an httpOnly cookie — not a login, just
enough identity to group their conversations/reviews/bugs together and
to let admins see "how many distinct visitors", without asking for any
personal information up front.
"""

from __future__ import annotations

from fastapi import Cookie, Depends, Response
from sqlalchemy.orm import Session
from typing import Optional

from models.db import get_db
from models.orm import User
from services.persistence_service import SESSION_COOKIE_NAME, get_or_create_user_by_session

COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365  # 1 year


def get_session_user(
    response: Response,
    db: Session = Depends(get_db),
    tessa_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> User:
    user = get_or_create_user_by_session(db, tessa_session)
    db.commit()
    db.refresh(user)
    # Always (re-)set the cookie so it never silently expires while the
    # visitor is still active, and so a brand-new visitor gets one.
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=user.session_id,
        httponly=True,
        samesite="lax",
        max_age=COOKIE_MAX_AGE_SECONDS,
        path="/",
    )
    return user
