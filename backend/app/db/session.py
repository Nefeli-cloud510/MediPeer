from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ..core.config import get_settings


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str):
    kwargs = {"connect_args": {"check_same_thread": False}}
    if database_url.startswith("sqlite:///:memory:"):
        kwargs["poolclass"] = StaticPool
    elif database_url.startswith("sqlite:///"):
        kwargs["connect_args"]["timeout"] = 30
    engine = create_engine(database_url, **kwargs)
    if database_url.startswith("sqlite:///"):
        path = database_url.replace("sqlite:///", "", 1)
        if path not in (":memory:",):
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        @event.listens_for(engine, "connect")
        def _fk_on(conn, _):
            cursor = conn.cursor()
            if path not in (":memory:",):
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


_configured_engine = None
_SessionLocal = None


def configure_database(database_url: str | None = None) -> None:
    """Set the app database engine. Defaults to settings; tests call this with :memory:."""
    global _configured_engine, _SessionLocal
    url = database_url or get_settings().database_url
    _configured_engine = build_engine(url)
    _SessionLocal = sessionmaker(
        bind=_configured_engine, autoflush=False, expire_on_commit=False
    )


def _get_session_local():
    if _SessionLocal is None:
        configure_database()
    return _SessionLocal


def SessionLocal():
    """Backwards-compatible factory for code imported before configure_database."""
    return _get_session_local()()


def get_engine():
    if _configured_engine is None:
        configure_database()
    return _configured_engine


def get_db():
    db: Session = _get_session_local()()
    try:
        yield db
    finally:
        db.close()

