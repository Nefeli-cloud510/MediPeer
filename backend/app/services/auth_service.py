from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.security import hash_password, verify_password
from ..db.models import User


def ensure_seed_users(db: Session) -> None:
    """Create local demo accounts used for review sessions."""
    settings = get_settings()
    candidates = [
        ("demo", "评审医生", "doctor", settings.demo_password),
        ("judge", "评审员", "judge", settings.judge_password),
    ]
    for username, display_name, role, password in candidates:
        existing = db.scalar(select(User).where(User.username == username))
        if existing is None:
            db.add(
                User(
                    username=username,
                    display_name=display_name,
                    role=role,
                    password_hash=hash_password(password),
                    is_demo=True,
                )
            )
    db.commit()


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username))
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user
