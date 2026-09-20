"""FastAPI dependencies: database session and the authenticated user (cookie session)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import Unauthorized
from app.core.security import hash_token
from app.db.session import get_db
from app.models import User, UserSession

DB = Annotated[Session, Depends(get_db)]


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def get_current_user(request: Request, db: DB) -> User:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise Unauthorized("Please sign in to continue.", code="not_authenticated")
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    now = datetime.now(timezone.utc)
    if sess is None or sess.revoked_at is not None or sess.expires_at <= now:
        raise Unauthorized("Your session has expired. Please sign in again.", code="session_expired")
    user = db.get(User, sess.user_id)
    if user is None or user.deleted_at is not None or not user.is_active:
        raise Unauthorized("This account is not active.", code="account_inactive")
    request.state.session_id = sess.id
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
