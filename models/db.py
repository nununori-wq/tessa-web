"""
TESSA application database — engine/session wiring.

This is the persistence layer for "what happened" (users, conversations,
messages, reviews, bugs, complaints, achievements, escalations, audit
log). It is intentionally separate from Pinecone, which remains "what
TESSA knows" (the RAG knowledge base) and is untouched by this module.

Local dev:  DATABASE_URL unset -> sqlite:///./tessa.db (zero-config)
Production: DATABASE_URL=postgresql://... (e.g. Render's managed Postgres)

No code elsewhere should hardcode a driver or connection string — always
go through get_db()/SessionLocal from this module.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./tessa.db")

# SQLite needs check_same_thread=False for use across FastAPI's threadpool;
# Postgres (and other real DBs) don't take that argument at all.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def init_db() -> None:
    """Create any tables that don't exist yet. Never drops or alters
    existing tables — safe to call on every startup, matching the same
    pattern already used by services/pinecone_service.py's init_index()."""
    # Importing here (not at module top) avoids a circular import: orm.py
    # imports Base from this module.
    from . import orm  # noqa: F401  (registers all mapped classes on Base)
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency: yields a session, always closes it after."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
