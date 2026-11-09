from __future__ import annotations

import os

# Test suite must never read real model credentials or make network calls.
# Assignment (not setdefault) also overrides values loaded from .env.
os.environ["MEDIPEEPER_LLM_PROVIDER"] = "stub"
os.environ["MEDIPEEPER_LLM_MODEL"] = "medipeer-stub"
os.environ["MEDIPEEPER_LLM_API_KEY"] = ""
os.environ["DASHSCOPE_API_KEY"] = ""
os.environ["DASHSCOPE_BASE_URL"] = ""
os.environ["DASHSCOPE_MODEL"] = ""
os.environ["MEDIPEEPER_DATABASE_URL"] = "sqlite:///:memory:"

# Invalidate the cached settings singleton created before these overrides.
from backend.app.core.config import get_settings

get_settings.cache_clear()

import pytest
from fastapi.testclient import TestClient

from backend.app.db.models import Base
from backend.app.db.session import SessionLocal, configure_database, get_engine
from backend.app.main import app
from backend.app.services.auth_service import ensure_seed_users


@pytest.fixture(autouse=True)
def reset_db():
    configure_database("sqlite:///:memory:")
    Base.metadata.drop_all(bind=get_engine())
    Base.metadata.create_all(bind=get_engine())
    with SessionLocal() as db:
        ensure_seed_users(db)
    yield


@pytest.fixture()
def client():
    return TestClient(app)
