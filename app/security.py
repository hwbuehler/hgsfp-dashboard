"""Password hashing and the dashboard session.

argon2-cffi is used directly rather than through passlib: passlib is unmaintained
and adds nothing here beyond an extra layer to keep working.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import User

SESSION_USER_KEY = "user_id"

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def authenticate(session: Session, username: str, password: str) -> User | None:
    user = session.scalars(select(User).where(User.username == username)).first()
    if user is None or not user.is_active:
        # Hash anyway so a missing account is not distinguishable by timing.
        _hasher.hash(password)
        return None
    if not verify_password(user.password_hash, password):
        return None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        session.commit()
    return user


def login_session(request: Request, user: User) -> None:
    request.session.clear()
    request.session[SESSION_USER_KEY] = user.id


def logout_session(request: Request) -> None:
    request.session.clear()


def get_optional_user(
    request: Request, session: Session = Depends(get_session)
) -> User | None:
    user_id = request.session.get(SESSION_USER_KEY)
    if user_id is None:
        return None
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        request.session.clear()
        return None
    return user


class NotAuthenticated(HTTPException):
    """Signals the handler to redirect to the login page instead of returning 401."""

    def __init__(self, next_url: str) -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED)
        self.next_url = next_url


def require_user(
    request: Request, user: User | None = Depends(get_optional_user)
) -> User:
    if user is None:
        raise NotAuthenticated(next_url=str(request.url.path))
    return user


def redirect_to_login(next_url: str) -> RedirectResponse:
    from urllib.parse import quote

    return RedirectResponse(
        url=f"/login?next={quote(next_url, safe='/')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
