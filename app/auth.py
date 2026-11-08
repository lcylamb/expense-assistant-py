from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from .db import User
from .models import Me
from .security import hash_password, verify_password

_DUMMY_HASH = hash_password("not-a-real-password")


class Unauthorized(Exception):
    pass


def to_me(user: User) -> Me:
    return Me(
        username=user.username,
        name=user.name,
        role=user.role.lower(),
        department=user.department,
    )


def login(session: Session, username: str, password: str) -> User:
    user = session.query(User).filter(User.username == username).first()
    ok = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not ok:
        # 账号不存在和密码错误用同一句话，避免用户名枚举。
        raise Unauthorized("用户名或密码不对")
    return user


def resolve_user(session: Session, user_id: int) -> Optional[User]:
    return session.get(User, user_id)
