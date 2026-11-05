"""Password hashing, JWT handling and login throttling."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.database.session import get_db
from app.models.entities import User

_hasher = PasswordHasher()
_bearer = HTTPBearer(auto_error=False)
ALGORITHM = "HS256"


def _secret() -> str:
    key = get_settings().secret_key
    if len(key) < 32:
        raise RuntimeError("ECHOAI_SECRET_KEY must be set to at least 32 characters (setup.ps1 generates one).")
    return key


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def create_access_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    claims = {"sub": user_id, "iat": now, "exp": now + timedelta(minutes=get_settings().access_token_minutes)}
    return jwt.encode(claims, _secret(), algorithm=ALGORITHM)


def decode_token(token: str) -> str | None:
    try:
        return jwt.decode(token, _secret(), algorithms=[ALGORITHM])["sub"]
    except jwt.PyJWTError:
        return None


class LoginThrottle:
    """Sliding-window limiter for failed logins, keyed by client address and email."""

    def __init__(self, limit: int = 8, window_seconds: float = 300) -> None:
        self.limit = limit
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def blocked(self, key: str) -> bool:
        attempts = self._failures[key]
        cutoff = time.monotonic() - self.window
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        return len(attempts) >= self.limit

    def record_failure(self, key: str) -> None:
        self._failures[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)


login_throttle = LoginThrottle()


async def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    user_id = decode_token(creds.credentials) if creds else None
    user = await db.get(User, user_id) if user_id else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", {"WWW-Authenticate": "Bearer"})
    return user


async def user_from_token(db: AsyncSession, token: str) -> User | None:
    user_id = decode_token(token)
    if not user_id:
        return None
    return (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
