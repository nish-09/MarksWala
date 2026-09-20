"""Password hashing and session-token primitives."""
from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings

_hasher = PasswordHasher()  # argon2id with library defaults

# Verified against when the account does not exist, so login timing does not reveal valid emails.
_DUMMY_HASH = _hasher.hash("MarksWala-dummy-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Keyed hash so a database leak does not yield usable session tokens."""
    return hmac.new(settings.secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()
