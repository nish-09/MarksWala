from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, CurrentUser, client_ip
from app.core.config import settings
from app.core.errors import Conflict, Forbidden, Unauthorized
from app.core.security import hash_password, hash_token, needs_rehash, new_session_token, verify_password
from app.models import User, UserSession
from app.models.enums import UserRole
from app.schemas.auth import LoginIn, RegisterIn, UserOut
from app.schemas.common import Message
from app.services import audit, ratelimit

router = APIRouter(prefix="/auth", tags=["auth"])


def _start_session(db, user: User, request: Request, response: Response) -> None:
    token = new_session_token()
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=expires,
            user_agent=(request.headers.get("user-agent") or "")[:300],
            ip_address=client_ip(request),
        )
    )
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: RegisterIn, request: Request, response: Response, db: DB):
    if not settings.allow_registration:
        raise Forbidden("Registration is disabled on this deployment.", code="registration_disabled")
    ratelimit.hit(f"register:{client_ip(request)}", limit=20, window_seconds=3600)
    email = body.email.lower()
    if db.scalar(select(User.id).where(func.lower(User.email) == email, User.deleted_at.is_(None))):
        raise Conflict("An account with this email already exists.", code="email_taken")
    user = User(email=email, password_hash=hash_password(body.password), full_name=body.full_name, role=UserRole.TEACHER)
    db.add(user)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise Conflict("An account with this email already exists.", code="email_taken") from e
    audit.record(db, actor_id=user.id, action="user.register", entity_type="user", entity_id=user.id, ip=client_ip(request))
    _start_session(db, user, request, response)
    db.commit()
    return user


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, response: Response, db: DB):
    email = body.email.lower()
    bucket = f"login:{client_ip(request)}:{email}"
    ratelimit.hit(bucket, limit=settings.login_rate_limit_attempts, window_seconds=settings.login_rate_limit_window_seconds)
    user = db.scalar(select(User).where(func.lower(User.email) == email, User.deleted_at.is_(None)))
    if not verify_password(body.password, user.password_hash if user else None) or user is None or not user.is_active:
        audit.record(db, actor_id=None, action="user.login_failed", entity_type="user", after={"email": email}, ip=client_ip(request))
        db.commit()
        raise Unauthorized("Incorrect email or password.", code="invalid_credentials")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    ratelimit.reset(bucket)
    _start_session(db, user, request, response)
    audit.record(db, actor_id=user.id, action="user.login", entity_type="user", entity_id=user.id, ip=client_ip(request))
    db.commit()
    return user


@router.post("/logout", response_model=Message)
def logout(request: Request, response: Response, db: DB):
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        sess = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
        if sess and sess.revoked_at is None:
            sess.revoked_at = datetime.now(timezone.utc)
            db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")
    return Message(message="Signed out.")


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user
