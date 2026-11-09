from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import auth, cases
from .core.config import get_settings
from .db.migrations import ensure_compat_columns
from .db.models import Base
from .db.session import SessionLocal, configure_database, get_engine
from .services.auth_service import ensure_seed_users


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_database()
    Base.metadata.create_all(bind=get_engine())
    ensure_compat_columns(get_engine())
    with SessionLocal() as db:
        ensure_seed_users(db)
    yield


settings = get_settings()
app = FastAPI(
    title="MediPeer API",
    version="0.1.0",
    description="医知协同 MediPeer 医生回复联合审阅系统",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(cases.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "medipeer", "env": settings.app_env}
