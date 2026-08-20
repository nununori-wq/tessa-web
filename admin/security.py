"""
Password hashing + JWT session helpers for TESSA Admin auth.

Design notes (read before changing anything here):

- Passwords are hashed with bcrypt (via the `bcrypt` package directly).
  Never store or log a plaintext password anywhere, including in the
  audit log.

- Sessions are short-lived signed JWTs (default 30 min) stored in an
  httpOnly, Secure, SameSite=Strict cookie — NOT localStorage. A JWT in
  localStorage is readable by any JS on the page (stealable via any XSS
  bug); an httpOnly cookie is invisible to JS entirely.

- The JWT's only job is to say "this session belongs to user id X, and
  was valid until time Y". It does NOT carry the user's role — role is
  looked up fresh from the database on every request (see deps.py), so
  revoking admin access takes effect on the very next request.

- JWT_SECRET_KEY must come from the environment. Refuses to start
  rather than silently falling back to a guessable default.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ADMIN_JWT_EXPIRE_MINUTES", "30"))
SESSION_COOKIE_NAME = "tessa_admin_session"


def _get_jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET_KEY")
    if not secret:
        raise RuntimeError(
            "JWT_SECRET_KEY is not set. Generate one (e.g. "
            "`python -c \"import secrets; print(secrets.token_urlsafe(64))\"`) "
            "and set it as an environment variable before starting the server."
        )
    if len(secret) < 32:
        raise RuntimeError("JWT_SECRET_KEY is too short to be a safe signing key (need >= 32 chars).")
    return secret


def hash_password(plain_password: str) -> str:
    if len(plain_password.encode("utf-8")) > 72:
        raise ValueError("Password must be 72 bytes or fewer.")
    hashed = bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt(rounds=12))
    return hashed.decode("utf-8")


def verify_password(plain_password: str, password_hash: Optional[str]) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_session_token(user_id: int) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": expires_at,
        "jti": secrets.token_hex(16),
    }
    token = jwt.encode(payload, _get_jwt_secret(), algorithm=JWT_ALGORITHM)
    return token, expires_at


def decode_session_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
