from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class UserView(BaseModel):
    id: str
    username: str
    display_name: str
    role: str


class LoginResponse(BaseModel):
    token: str
    token_type: str = "bearer"
    user: UserView
