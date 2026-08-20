"""
FastAPI dependencies that do the actual admin-access enforcement.

Every /admin/dashboard page and every /api/admin/* route depends on
`require_admin`, which:

  1. Reads the session cookie (httpOnly, browser-managed — never a
     header the client could set to whatever it wants).
  2. Verifies the JWT signature and expiry.
  3. Looks the user up in the DATABASE by the id inside the token.
  4. Checks `user.is_admin` (role == "admin" AND status == "active")
     FROM THE DATABASE ROW, not from anything the token itself claims.

Any failure at any step raises HTTPException(401/403) before a single
byte of admin data is touched or returned.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from models.db import get_db
from models.orm import AdminAuditLog, User
from .security import SESSION_COOKIE_NAME, decode_session_token


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def log_admin_action(db: Session, action: str, success: bool, user: Optional[User] = None,
                      actor_email: Optional[str] = None, detail: Optional[str] = None,
                      ip: Optional[str] = None, target: Optional[str] = None) -> None:
    entry = AdminAuditLog(
        actor_user_id=user.id if user else None,
        actor_email=actor_email or (user.email if user else None),
        action=action,
        success=success,
        detail=detail,
        ip_address=ip,
        target=target,
    )
    db.add(entry)
    db.commit()


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    tessa_admin_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> Optional[User]:
    """Returns the logged-in User, or None. Does not raise — some
    routes (the login page itself) need to know "already logged in?"
    without forcing a 401."""
    if not tessa_admin_session:
        return None
    payload = decode_session_token(tessa_admin_session)
    if not payload:
        return None
    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError, TypeError):
        return None
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        return None
    return user


def require_admin(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
) -> User:
    """The dependency every /admin/dashboard page and every
    /api/admin/* route must declare."""
    ip = _client_ip(request)

    if current_user is None:
        log_admin_action(db, action="admin_access_denied", success=False,
                          detail=f"no valid session for {request.url.path}", ip=ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not current_user.is_admin:
        log_admin_action(db, action="admin_access_denied", success=False, user=current_user,
                          detail=f"authenticated but not admin, path={request.url.path}", ip=ip)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this resource.",
        )

    return current_user
