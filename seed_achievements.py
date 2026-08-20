"""
Seeds (or updates) the Achievement catalog from the badges already
defined client-side in templates/index.html (search "var BADGES = {").
Safe to run repeatedly: upserts by `key`, never creates duplicates,
never touches earned UserAchievement rows.

Usage:
    python seed_achievements.py
"""
from models.db import SessionLocal, init_db
from models.orm import Achievement

CATALOG = [
    dict(key="tax_basics", name="Tax Basics", badge="🏅", points=10,
         description="Complete your first Tax Quiz.",
         requirements="Complete 1 Tax Quiz."),
    dict(key="tax_curious", name="Tax Curious", badge="🏅", points=10,
         description="Learn 5 different tax terms using TESSA.",
         requirements="View 5 distinct glossary terms."),
    dict(key="filing_ready", name="Filing Ready", badge="🏅", points=15,
         description="Complete the Before You File checklist.",
         requirements="Complete the filing readiness checklist."),
    dict(key="smart_taxpayer", name="Smart Taxpayer", badge="🏅", points=20,
         description="Successfully complete 3 educational activities.",
         requirements="Complete 3 distinct educational activities."),
    dict(key="business_starter", name="Business Starter", badge="🏅", points=20,
         description="Complete the business-tax learning journey.",
         requirements="Complete the business readiness checklist."),
    dict(key="tessa_explorer", name="TESSA Explorer", badge="🏅", points=15,
         description="Use 5 different TESSA assistance categories.",
         requirements="Use 5 distinct TESSA categories."),
    dict(key="tax_knowledge_pro", name="Tax Knowledge Pro", badge="🏅", points=25,
         description="Achieve a high score across multiple Tax Quizzes.",
         requirements="Score >= 80% on 2 Tax Quizzes."),
]


def seed_achievements() -> None:
    init_db()
    db = SessionLocal()
    try:
        for entry in CATALOG:
            existing = db.query(Achievement).filter(Achievement.key == entry["key"]).first()
            if existing:
                for field, value in entry.items():
                    setattr(existing, field, value)
            else:
                db.add(Achievement(**entry))
        db.commit()
        print(f"Seeded/updated {len(CATALOG)} achievements.")
    finally:
        db.close()


if __name__ == "__main__":
    seed_achievements()
