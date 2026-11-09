from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.security import create_token
from ..db.models import User
from ..db.session import get_db
from ..schemas import LoginRequest, LoginResponse, UserView
from ..services.auth_service import authenticate
from .deps import bearer_scheme, get_current_user


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    user = authenticate(db, body.username, body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid username or password",
        )
    settings = get_settings()
    token = create_token(user.username, settings.auth_secret, settings.token_ttl_seconds)
    return LoginResponse(
        token=token,
        user=UserView(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
        ),
    )


@router.get("/me", response_model=UserView)
def me(user: User = Depends(get_current_user)) -> UserView:
    return UserView(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
    )
