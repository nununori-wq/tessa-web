"""
Integration tests exercising the real app through FastAPI's TestClient
against a real (temporary, file-based) SQLite database. The only thing
mocked is run_tessa itself (services.tessa_graph.run_tessa), because it
calls out to Gemini/Pinecone, which need real API keys this environment
doesn't have. Everything else — persistence, cookies, admin auth,
authorization — runs for real.

Run with:  python -m pytest tests/test_integration.py -v
"""
import os
import sys
import uuid

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("GEMINI_API_KEY", "dummy-key-for-import-test")
os.environ["ADMIN_COOKIE_SECURE"] = "false"  # TestClient uses plain http
os.environ["RATE_LIMIT_ENABLED"] = "false"  # tests intentionally fire many rapid requests

TEST_DB_PATH = f"./test_tessa_{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

import app as app_module
from models.db import init_db, SessionLocal
from models.orm import User, Achievement
from admin.security import hash_password


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    init_db()
    db = SessionLocal()
    # Seed achievements catalog (mirrors seed_achievements.py) so
    # achievement-event tests have a real key to record against.
    db.add(Achievement(key="tax_basics", name="Tax Basics", badge="🏅", points=10))
    # Seed one admin account directly (mirrors admin/seed_admin.py).
    db.add(User(email="admin@ird.gov.gd", password_hash=hash_password("a-strong-admin-password"),
                role="admin", status="active"))
    # And a plain non-admin user with a password, to prove role-gating.
    db.add(User(email="taxpayer@example.com", password_hash=hash_password("not-an-admin-password-123"),
                role="user", status="active"))
    db.commit()
    db.close()
    yield
    try:
        os.remove(TEST_DB_PATH)
    except OSError:
        pass


@pytest.fixture
def client(monkeypatch):
    def fake_run_tessa(message, history=None, channel="web", language="en"):
        if "escalate me" in message.lower():
            return {"response": "I don't have verified information on that yet.", "intent": "escalation_request", "escalate": True}
        return {"response": f"TESSA says: I received '{message}'", "intent": "general_inquiry", "escalate": False}

    monkeypatch.setattr(app_module, "run_tessa", fake_run_tessa)
    return TestClient(app_module.app)


# ---------------------------------------------------------------------
# 1. Normal TESSA homepage works
# ---------------------------------------------------------------------

def test_homepage_serves(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "TESSA" in r.text or "IRD" in r.text


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------
# 2. Chat: reply field fix + persistence
# ---------------------------------------------------------------------

def test_chat_returns_reply_field_frontend_expects(client):
    r = client.post("/chat", json={"message": "How do I register for a TIN?", "history": []})
    assert r.status_code == 200
    data = r.json()
    assert "reply" in data and data["reply"], "frontend reads data.reply -- must be present and non-empty"
    assert data["intent"] == "general_inquiry"
    assert data["escalate"] is False
    assert data["conversation_id"] is not None


def test_chat_persists_conversation_and_messages(client):
    r1 = client.post("/chat", json={"message": "What is GCT?", "history": []})
    convo_id = r1.json()["conversation_id"]
    session_cookie = r1.cookies.get("tessa_session")
    assert session_cookie, "session cookie must be set for a new visitor"

    # Second message in the same browser session should land in the
    # SAME conversation (matches the frontend's single-thread widget).
    r2 = client.post("/chat", json={"message": "And when is it due?", "history": [
        {"role": "user", "content": "What is GCT?"},
        {"role": "assistant", "content": r1.json()["reply"]},
    ]})
    assert r2.json()["conversation_id"] == convo_id

    db = SessionLocal()
    from models.orm import Conversation, Message
    convo = db.query(Conversation).filter(Conversation.id == convo_id).first()
    assert convo is not None
    msgs = db.query(Message).filter(Message.conversation_id == convo_id).order_by(Message.id).all()
    assert [m.sender for m in msgs] == ["user", "tessa", "user", "tessa"]
    assert msgs[0].content == "What is GCT?"
    assert msgs[0].intent == "general_inquiry"
    db.close()


def test_chat_creates_escalation_when_flagged(client):
    r = client.post("/chat", json={"message": "please escalate me to a human", "history": []})
    assert r.json()["escalate"] is True
    convo_id = r.json()["conversation_id"]

    db = SessionLocal()
    from models.orm import Escalation, Conversation
    esc = db.query(Escalation).filter(Escalation.conversation_id == convo_id).first()
    assert esc is not None, "an Escalation row must be created when TESSA flags escalate=True"
    convo = db.query(Conversation).filter(Conversation.id == convo_id).first()
    assert convo.escalated is True
    db.close()


def test_chat_rejects_empty_message(client):
    r = client.post("/chat", json={"message": "   ", "history": []})
    assert r.status_code == 400


# ---------------------------------------------------------------------
# 3. Public write API (reviews / bugs / complaints / achievements)
# ---------------------------------------------------------------------

def test_submit_review_and_bug_and_complaint(client):
    r = client.post("/api/reviews", json={"rating": 5, "category": "helpfulness", "comment": "Great!"})
    assert r.status_code == 200 and r.json()["status"] == "recorded"

    r = client.post("/api/bugs", json={"title": "Button misaligned", "description": "On mobile the send button overlaps text.", "severity": "low"})
    assert r.status_code == 200 and r.json()["status"] == "recorded"

    r = client.post("/api/complaints", json={"category": "wait_time", "description": "Waited too long for a reply."})
    assert r.status_code == 200 and r.json()["status"] == "recorded"


def test_achievement_event_is_idempotent(client):
    r1 = client.post("/api/achievements/event", json={"achievement_key": "tax_basics"})
    assert r1.json()["status"] == "newly_earned"
    r2 = client.post("/api/achievements/event", json={"achievement_key": "tax_basics"}, cookies=r1.cookies)
    assert r2.json()["status"] == "already_earned"

    r3 = client.get("/api/achievements/me", cookies=r1.cookies)
    keys = [a["key"] for a in r3.json()["earned"]]
    assert keys == ["tax_basics"], "must not double-award on repeated identical events"


def test_unknown_achievement_key_does_not_error(client):
    r = client.post("/api/achievements/event", json={"achievement_key": "does_not_exist"})
    assert r.status_code == 200
    assert r.json()["status"] == "unknown_achievement_key"


# ---------------------------------------------------------------------
# 4/5/6/7. Admin auth: unauthenticated redirect, login, dashboard, APIs
# ---------------------------------------------------------------------

def test_unauthenticated_admin_dashboard_redirects_to_login(client):
    r = client.get("/admin/dashboard", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/admin/login"


def test_normal_user_cannot_reach_admin_dashboard(client):
    # A non-admin with 100% correct credentials still cannot even
    # establish an admin session -- admin_login_submit rejects role!=admin
    # at the login step itself (stricter than "let them in, then bounce").
    r = client.post("/admin/login", data={"email": "taxpayer@example.com", "password": "not-an-admin-password-123"}, follow_redirects=False)
    assert r.status_code == 401
    assert "tessa_admin_session" not in r.cookies

    # And even if they somehow reached the dashboard URL with no/garbage
    # session, they're redirected to login rather than shown anything.
    r2 = client.get("/admin/dashboard", follow_redirects=False)
    assert r2.status_code == 302
    assert r2.headers["location"] == "/admin/login"


def test_normal_user_cannot_call_admin_api(client):
    # No valid admin session exists for this user at all (see above) --
    # confirm the API layer independently rejects them too.
    r2 = client.get("/api/admin/users")
    assert r2.status_code == 401


def test_taxpayer_session_cookie_never_grants_admin_access(client):
    """A normal taxpayer's chat session (tessa_session cookie) is a
    completely different cookie/mechanism from the admin session
    (tessa_admin_session) -- confirm it grants nothing admin-side."""
    r = client.post("/chat", json={"message": "hello", "history": []})
    assert "tessa_session" in r.cookies
    assert "tessa_admin_session" not in r.cookies
    r2 = client.get("/api/admin/users", cookies=r.cookies)
    assert r2.status_code == 401


def test_unauthenticated_admin_api_rejected(client):
    r = client.get("/api/admin/users")
    assert r.status_code == 401


def test_wrong_password_rejected(client):
    r = client.post("/admin/login", data={"email": "admin@ird.gov.gd", "password": "totally-wrong"}, follow_redirects=False)
    assert r.status_code == 401
    assert "tessa_admin_session" not in r.cookies


def test_admin_login_dashboard_api_and_logout(client):
    # Chat + write a couple of records first so the dashboard has real data to show.
    client.post("/chat", json={"message": "Do I need to register my business?", "history": []})
    client.post("/api/bugs", json={"title": "test bug for dashboard", "description": "desc"})

    r = client.post("/admin/login", data={"email": "admin@ird.gov.gd", "password": "a-strong-admin-password"}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/admin/dashboard"
    cookies = r.cookies
    assert "tessa_admin_session" in cookies

    r2 = client.get("/admin/dashboard", cookies=cookies)
    assert r2.status_code == 200
    assert "admin@ird.gov.gd" in r2.text

    r3 = client.get("/api/admin/users", cookies=cookies)
    assert r3.status_code == 200
    emails = [u["email"] for u in r3.json()["users"]]
    assert "admin@ird.gov.gd" in emails
    assert "taxpayer@example.com" in emails

    r4 = client.get("/api/admin/conversations", cookies=cookies)
    assert r4.status_code == 200 and len(r4.json()["conversations"]) >= 1

    r5 = client.get("/api/admin/bug-reports", cookies=cookies)
    assert any(b["title"] == "test bug for dashboard" for b in r5.json()["bug_reports"])

    r6 = client.get("/api/admin/analytics", cookies=cookies)
    assert r6.status_code == 200 and "total_conversations" in r6.json()

    r7 = client.get("/api/admin/audit", cookies=cookies)
    assert r7.status_code == 200
    actions = [e["action"] for e in r7.json()["entries"]]
    assert "admin_login_success" in actions

    # Logout
    r8 = client.post("/admin/logout", cookies=cookies, follow_redirects=False)
    assert r8.status_code == 302
    r9 = client.get("/api/admin/users", cookies=r8.cookies)
    assert r9.status_code == 401, "cookie must be cleared/invalid after logout"


def test_expired_session_rejected(monkeypatch, client):
    from admin.security import create_session_token
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone

    db = SessionLocal()
    admin_user = db.query(User).filter(User.email == "admin@ird.gov.gd").first()
    admin_id = admin_user.id
    db.close()

    # Hand-craft an already-expired token using the same secret/algorithm.
    secret = os.environ["JWT_SECRET_KEY"]
    expired_payload = {
        "sub": str(admin_id),
        "iat": datetime.now(timezone.utc) - timedelta(hours=2),
        "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        "jti": "expired-test-token",
    }
    expired_token = pyjwt.encode(expired_payload, secret, algorithm="HS256")

    r = client.get("/api/admin/users", cookies={"tessa_admin_session": expired_token})
    assert r.status_code == 401


def test_admin_role_checked_against_database_not_token(client):
    """Log in as admin, then demote the account in the DB directly (no
    new token issued) -- the *next* request must be rejected, proving
    role is re-checked from the DB every time, not trusted from the JWT."""
    r = client.post("/admin/login", data={"email": "admin@ird.gov.gd", "password": "a-strong-admin-password"}, follow_redirects=False)
    cookies = r.cookies

    r_ok = client.get("/api/admin/users", cookies=cookies)
    assert r_ok.status_code == 200

    db = SessionLocal()
    admin_user = db.query(User).filter(User.email == "admin@ird.gov.gd").first()
    admin_user.role = "user"
    db.commit()
    db.close()

    r_after = client.get("/api/admin/users", cookies=cookies)
    assert r_after.status_code == 403, "same still-valid token must lose access once DB role changes"

    # restore for any later tests
    db = SessionLocal()
    admin_user = db.query(User).filter(User.email == "admin@ird.gov.gd").first()
    admin_user.role = "admin"
    db.commit()
    db.close()


# ---------------------------------------------------------------------
# Persistence survives a fresh session (simulates server restart: new
# SessionLocal() connections against the same on-disk sqlite file).
# ---------------------------------------------------------------------

def test_chat_rejects_oversized_message(client):
    r = client.post("/chat", json={"message": "x" * 5000, "history": []})
    assert r.status_code == 400


def test_chat_passes_language_code_through(client, monkeypatch):
    captured = {}

    def spy_run_tessa(message, history=None, channel="web", language="en"):
        captured["language"] = language
        return {"response": "ok", "intent": "general_inquiry", "escalate": False}

    monkeypatch.setattr(app_module, "run_tessa", spy_run_tessa)
    client.post("/chat", json={"message": "hello", "history": [], "language": "gcl"})
    assert captured["language"] == "gcl"


def test_rate_limit_blocks_after_threshold(monkeypatch):
    """Rate limiting is disabled globally for the rest of this suite (see
    module-level RATE_LIMIT_ENABLED env var) -- this test explicitly
    re-enables it against a fresh app instance to verify it actually
    works, rather than just trusting the config exists."""
    os.environ["RATE_LIMIT_ENABLED"] = "true"
    os.environ["RATE_LIMIT_MAX_REQUESTS"] = "3"
    os.environ["RATE_LIMIT_WINDOW_SECONDS"] = "60"
    import importlib
    import app as app_module_fresh
    importlib.reload(app_module_fresh)

    def fake_run_tessa(message, history=None, channel="web", language="en"):
        return {"response": "ok", "intent": "general_inquiry", "escalate": False}
    monkeypatch.setattr(app_module_fresh, "run_tessa", fake_run_tessa)

    test_client = TestClient(app_module_fresh.app)
    statuses = []
    for _ in range(5):
        r = test_client.post("/chat", json={"message": "hi", "history": []})
        statuses.append(r.status_code)

    assert 429 in statuses, f"expected a 429 after the 3-request threshold, got {statuses}"

    # restore for any tests that import app_module_fresh's globals afterward
    os.environ["RATE_LIMIT_ENABLED"] = "false"
    importlib.reload(app_module_fresh)


def test_visualization_service_real_extraction():
    from services.visualization_service import detect_and_extract, compute_derived_stats

    context = "Individual TIN fee: 50. Business TIN fee: 100."
    extraction = detect_and_extract("compare individual vs business TIN fees", context)
    assert extraction["needs_visualization"] is True
    assert len(extraction["data_points"]) >= 2

    stats = compute_derived_stats(extraction["data_points"])
    assert stats["difference"] == 50.0


def test_visualization_service_no_fabrication_without_comparison_intent():
    from services.visualization_service import detect_and_extract
    extraction = detect_and_extract("what is a TIN", "Individual TIN fee: 50.")
    assert extraction["needs_visualization"] is False


def test_looks_like_creole_heuristic():
    from services.intent_service import looks_like_creole
    assert looks_like_creole("Ah need help wid me taxes") is True
    assert looks_like_creole("What is the deadline for filing income tax?") is False


def test_data_persists_across_new_db_sessions(client):
    r = client.post("/chat", json={"message": "persistence check message", "history": []})
    convo_id = r.json()["conversation_id"]

    # Brand new session object, same DATABASE_URL/file -- simulates the
    # app restarting and reconnecting.
    fresh_db = SessionLocal()
    from models.orm import Message
    msgs = fresh_db.query(Message).filter(Message.conversation_id == convo_id).all()
    assert any(m.content == "persistence check message" for m in msgs)
    fresh_db.close()
