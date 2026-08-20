"""
Creates (or promotes) the initial administrator account from environment
variables. Safe to run repeatedly.

Usage:
    ADMIN_EMAIL=admin@ird.gov.gd ADMIN_PASSWORD='a-strong-password' \\
    JWT_SECRET_KEY='...' python -m admin.seed_admin
"""

from __future__ import annotations

import os
import sys

from models.db import SessionLocal, init_db
from models.orm import User
from .security import hash_password


def seed_admin() -> None:
    email = os.environ.get("ADMIN_EMAIL")
    password = os.environ.get("ADMIN_PASSWORD")

    if not email or not password:
        print("ADMIN_EMAIL and ADMIN_PASSWORD must both be set in the environment. Aborting.", file=sys.stderr)
        sys.exit(1)

    if len(password) < 12:
        print("ADMIN_PASSWORD is too short (need >= 12 characters). Aborting.", file=sys.stderr)
        sys.exit(1)

    init_db()
    db = SessionLocal()
    try:
        email = email.strip().lower()
        existing = db.query(User).filter(User.email == email).first()

        if existing:
            changed = False
            if existing.role != "admin":
                existing.role = "admin"
                changed = True
            if existing.status != "active":
                existing.status = "active"
                changed = True
            if changed:
                db.commit()
                print(f"Existing user {email} promoted to active admin.")
            else:
                print(f"{email} is already an active admin. No changes made.")
                print("(To rotate their password, use a proper 'change password' flow, not this script.)")
            return

        admin = User(
            email=email,
            password_hash=hash_password(password),
            role="admin",
            status="active",
        )
        db.add(admin)
        db.commit()
        print(f"Admin account created for {email}.")
    finally:
        db.close()


if __name__ == "__main__":
    seed_admin()
