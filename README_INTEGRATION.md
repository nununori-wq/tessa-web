# TESSA — persistence layer + admin portal integration

This README covers Phases 1–9 as delivered. Read the **Gaps / what I could
not do** section before deploying — a few things are stubbed because the
source files for them were never provided.

## Architecture

```
User → TESSA Frontend (templates/index.html) → FastAPI (app.py)
                                                    │
                                     ┌──────────────┼──────────────────┐
                                     ▼                                 ▼
                        LangGraph / Gemini / RAG            Application Database
                        services/tessa_graph.py             models/db.py, models/orm.py
                        Pinecone = "what TESSA knows"        SQLite (dev) / Postgres (prod)
                        (untouched — still separate)          = "what happened"
                                                                  │
                                                                  ▼
                                                         Secure Admin Portal
                                                         /admin/login, /admin/dashboard
                                                         admin/*.py
```

Pinecone was **not** touched or treated as the application database — it
remains purely the RAG knowledge layer, exactly as before.

## What's new

**`models/`** — `db.py` (SQLAlchemy engine/session, `DATABASE_URL`-driven)
and `orm.py` (`User`, `Conversation`, `Message`, `Review`, `BugReport`,
`Complaint`, `Achievement`/`UserAchievement`, `Escalation`,
`AdminAuditLog`). One `User` table for both anonymous taxpayers
(identified by a `session_id` cookie) and admins (`email`+`password_hash`,
`role="admin"`) — no duplicate user models.

**`services/persistence_service.py`** — the Phase 2 wiring: creates/reuses
a conversation per browser session, saves every user + TESSA message,
auto-creates an `Escalation` row whenever TESSA's own `escalate` flag
fires, and records reviews/bugs/complaints/achievement events.

**`app.py`** — `/chat` now persists everything above, and:
- **fixes a live bug**: the frontend reads `data.reply`, but the route
  previously returned `response` — every real answer was silently
  falling through to the frontend's offline fallback text. Both fields
  are now returned.
- **fixes a resilience bug**: `gemini_service.py` raises on any API
  failure; that previously became a raw 500. `/chat` now catches it and
  returns a calm escalation-style message instead (still logs the real
  traceback server-side).
- removed ~25 lines of dead, unreachable streaming code that referenced
  an undefined `req` variable.

**`admin/`** — your auth scaffold (`security.py`, `deps.py`) is carried
over essentially unchanged; `routes.py` and `seed_admin.py` are adapted
to the unified `User`/`AdminAuditLog` models and now serve **real** data
(users, conversations, messages, reviews, bugs, complaints, escalations,
achievements, analytics, live system-health, Pinecone index status, audit
log) instead of stubs, plus two write actions (bug status, user role).

**`api/`** — new public write endpoints: `POST /api/reviews`,
`/api/bugs`, `/api/complaints`, `/api/achievements/event`,
`/api/escalations`, `GET /api/achievements/me`. These are the server-side
counterparts the frontend's own code comments say it was written to call
("swapping the storage layer for real POST /achievements/event calls...
is a localized change, not a rewrite" — see `templates/index.html`,
"TESSA ACHIEVEMENTS MODULE"). **The frontend JS itself has not been
rewired to call them** — see Phase 3 below.

## Phase 3 — localStorage classification (not fully migrated)

I inspected every `localStorage` key in `templates/index.html`:

| Key | Classification | Status |
|---|---|---|
| `tessaAchievements` | Server data | Backend ready (`/api/achievements/*`); **frontend still writes to localStorage only** |
| `tessa-theme` | Client UI state | Leave as-is |
| A11y prefs (`a11y-*`) | Client UI state | Leave as-is |
| Voice/language prefs | Client UI state | Leave as-is |
| `tessaVaultFolders` | Client UI state (no upload backend exists) | Leave as-is |
| "My Tax Journey" (`JOURNEY_KEY`) | Arguably server data (progress) | Flagged for later — same shape of work as achievements |
| Dismissed-card state, recent/favorite quick actions | Client UI state | Leave as-is |
| `conversationHistory` | **Not localStorage at all** — a plain in-memory JS array, lost on refresh already | Now durably saved server-side via `/chat`; wiring the widget to *reload* past turns on refresh is future work |

I did **not** rewrite `templates/index.html`'s JS to call the new
endpoints. It's a single 977KB/14,425-line file and rewiring ~5 separate
modules (achievements, journey, reviews, bugs, complaints) safely without
breaking any of the existing UI needs its own careful pass — happy to do
that as a follow-up if you'd like it.

## Gaps / what I could not do

1. **`services/visualization_service.py` was never uploaded**, but
   `tessa_graph.py` imports six functions from it — without *something*
   there, the app can't even import. I wrote a placeholder
   (`services/visualization_service.py`) that always reports "no chart
   needed" and never fabricates data, so the rest of the app runs. **This
   is not the real module** — statistical/chart reasoning is inert until
   you supply the actual file.
2. **`gemini_service.py` constructs its client at import time** with no
   fallback (`genai.Client(api_key=...)` at module load), so a missing
   `GEMINI_API_KEY` crashes the whole app on startup — unlike
   `pinecone_service.py`, which degrades gracefully. I did not change
   this file (out of scope for the admin/persistence task), but flagging
   it: set a real or dummy key before starting, or patch this to lazy-init
   like Pinecone does.
3. Live Gemini calls could not be tested in this sandbox (no network
   route to `generativelanguage.googleapis.com` here) — verified instead
   with `run_tessa` mocked at the boundary, which is the correct
   integration-test seam since everything on both sides of it (DB writes,
   auth, routing) is real.

## Testing — what was actually run

`tests/test_integration.py`, 19 tests, all passing against a real
temp-file SQLite DB through FastAPI's `TestClient`:
homepage, health, chat persistence (conversation/message rows, intent
tagging, auto-escalation row), reviews/bugs/complaints/achievement
idempotency, unauthenticated + non-admin rejection (both page and API),
wrong password, full admin login → dashboard → every `/api/admin/*`
endpoint → logout, expired-JWT rejection, **DB-role-is-source-of-truth**
(revoking admin mid-session immediately blocks the still-valid token),
and persistence across a fresh DB session (simulates a restart).

I also boot the **real** server with `uvicorn` and hit it with `curl`
(not just `TestClient`) to confirm the whole thing actually runs as a
process: homepage 200, health 200, admin dashboard 302→200, admin API
401→200 after login, and `/chat` degrading to a 200 escalation response
instead of a 500 when the Gemini call fails.

## Update — tagline, security hardening, data accuracy (this pass)

**1. Tagline changed.** "Building our Nation. Funding our Future." →
"Striving for Greater Voluntary Taxpayer Compliance" — there was exactly
one occurrence (`templates/index.html`, header). No other file referenced
the old tagline.

**2. "Google Sheets authentication/database" does not exist anywhere in
this project — confirmed by exhaustive search** (`sheet`, `gspread`,
`sheets.googleapis`, `script.google.com`, `gapi`, `spreadsheet`, "apps
script") across every `.py` and `.html` file, before and after all my
changes. There is no Google Sheets code to "preserve." The real,
already-built, already-tested persistence/auth system is the SQLAlchemy
database (`models/`) + JWT-cookie admin auth (`admin/`) described above —
that's what "existing authentication/database functionality" refers to
going forward.

**3. Fixed two incorrect office phone numbers.** The existing Offices
section listed Grenville as `+1 (473) 442-7748` and Gouyave as
`+1 (473) 444-7256` — neither matches IRD's published numbers. Corrected
to the real published numbers: Grenville `+1 (473) 442-7446`, Gouyave
`+1 (473) 444-8231` (source: ird.gd contact/sections pages). St. George's
HQ number was already correct. **Not done**: adding IRD's other three real
district offices (Sauteurs/St. Patrick, Victoria/St. Mark, St.
David/Carriacou) — the existing 3 office cards are wired to a
percentage-coordinate map-pin canvas in JS I didn't want to extend
blindly without testing the pin layout; flagging as a scoped follow-up.

**4. Fixed a real CORS security bug.** `allow_origins=["*"]` combined
with `allow_credentials=True` is invalid per the CORS spec — browsers
refuse it outright — and would have been a serious cross-site credential
leak if it *had* worked, now that `/chat` and `/api/*` rely on session
cookies. `app.py` now only allows explicitly configured origins (via
`ALLOWED_ORIGINS`, comma-separated) and logs a warning + falls back to
same-origin-only if `*` is set. Updated `.env.example` accordingly.

**5. Verified (did not need to fix) — user data isolation.** Every
review/bug/complaint/achievement/escalation write already resolves its
owner from the **server-side session cookie**
(`api/deps.py::get_session_user`), never from anything in the request
body. `user_id` cannot be manipulated from the frontend — there was
never a `user_id` field for the client to send in the first place.

**6. Verified — no admin surface leaks into the public site.** Searched
every "admin" occurrence in `templates/index.html`: all 132 are either
the English word "administration" (tax law administration) or one
specific, clearly-labeled feature — see next point.

**7. Found, not removed — a pre-existing client-side "Admin Preview."**
`templates/index.html` (search `id="admin-view"`) already contains an
in-page panel — staff directory entry, achievements insights, wait-time
tracking — explicitly labeled *"Preview only — changes made here exist
in this browser tab... not saved anywhere... A production build would
gate this behind secure IRD staff authentication and a real backend."*
It's inert: no backend calls, no real data, disclosed as fake. But now
that a real, secured `/admin/dashboard` exists, this mock is redundant
and could confuse someone about which "admin" surface is real. **I did
not remove it** — that's a ~200-line, non-trivial edit to an already
huge file and wasn't explicitly requested — but recommend removing or
clearly relabeling it now that the real thing exists.

**8. Re-ran full regression**: `py_compile` on every `.py` file, all 19
integration tests, tagline-presence check, admin-nav-leak check, and a
secrets scan — all clean.

### Still deferred (full "make TESSA the main IRD website" scope)

The request's item 2 (forms/downloads, notices/announcements, TIN
guidance, complaint-reporting UI, curated external links, newsletters)
is a substantial net-new content and UI project layered on an already
14,425-line frontend — not a same-pass fix like the above. Concretely
still missing, roughly in priority order:

1. **Announcements/notices** — no mechanism exists to show current,
   time-sensitive notices (e.g. property tax discount deadlines) without
   risking stale/wrong info baked into static HTML. Recommend an
   `Announcement` DB table + one new `/api/admin/announcements` CRUD
   surface (admins edit) + one public read endpoint + a small banner
   component — bounded, reuses the admin auth already built.
2. **Complaint-reporting UI** — the backend (`/api/complaints`) exists;
   there's no dedicated frontend form for it (only Feedback/reviews and
   bug reports have UI).
3. **Forms/downloads directory** — no page lists downloadable IRD forms.
4. **Curated external-links section** (G-TAX portal, ird.gd, gov.gd) with
   the explicit "Official IRD service ↗" pattern this task asked for —
   the existing Offices section already does this correctly for map
   directions; extending the pattern to a dedicated links section is
   straightforward but not yet built.
5. Removing/relabeling the redundant client-side Admin Preview (#7 above).

Happy to take these on next, prioritized however you like — this is a
genuinely large scope and I'd rather build it carefully in a follow-up
pass than rush it into this one.


```bash
cp .env.example .env   # fill in real keys
pip install -r requirements.txt
python -m admin.seed_admin        # requires ADMIN_EMAIL, ADMIN_PASSWORD, JWT_SECRET_KEY set
python seed_achievements.py
uvicorn app:app --reload
```

`DATABASE_URL` unset → `sqlite:///./tessa.db`, created automatically on
startup (`init_db()`), never dropped/recreated.

## Deploying to Render

1. Add a **Render Postgres** instance; Render injects `DATABASE_URL`
   automatically when linked to your web service (or copy its connection
   string into your service's env vars manually).
2. Set `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `JWT_SECRET_KEY` (generate via
   `python -c "import secrets; print(secrets.token_urlsafe(64))"`),
   `PINECONE_API_KEY`, `GEMINI_API_KEY` as Render environment variables —
   never commit them.
3. Run `python -m admin.seed_admin` once (Render's shell, or a one-off
   job) after first deploy to create the admin account; it's safe to
   re-run.
4. `psycopg2-binary` in `requirements.txt` covers the Postgres driver —
   no code changes needed between SQLite and Postgres, only
   `DATABASE_URL`.
