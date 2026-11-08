from __future__ import annotations

import secrets

from pwdlib import PasswordHash

from .config import get_settings

# Argon2id by default; pwdlib picks the recommended parameters.
_password_hash = PasswordHash.recommended()

SESSION_COOKIE = "ea_session"
SESSION_TTL_SECONDS = 8 * 60 * 60

_FALLBACK_SECRET = secrets.token_urlsafe(48)


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, stored: str) -> bool:
    try:
        return _password_hash.verify(password, stored)
    except Exception:
        # Malformed/legacy hashes must not turn a login attempt into a 500.
        return False


def session_secret() -> str:
    raw = get_settings().session_secret.strip()
    if len(raw) >= 32:
        return raw
    # Demo fallback: restart invalidates sessions; multi-instance needs a real secret.
    return _FALLBACK_SECRET


def cookie_secure() -> bool:
    return get_settings().cookie_secure
