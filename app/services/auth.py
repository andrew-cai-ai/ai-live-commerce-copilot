from __future__ import annotations

import hmac
import os
from hashlib import sha256

from dotenv import load_dotenv
from fastapi import Request, Response

load_dotenv()

AUTH_COOKIE_NAME = "live_copilot_auth"


def auth_enabled() -> bool:
    return bool(os.getenv("APP_PASSWORD"))


def is_authenticated(request: Request) -> bool:
    if not auth_enabled():
        return True
    token = request.cookies.get(AUTH_COOKIE_NAME, "")
    return hmac.compare_digest(token, _auth_token())


def password_matches(password: str) -> bool:
    expected = os.getenv("APP_PASSWORD", "")
    return bool(expected) and hmac.compare_digest(password, expected)


def set_auth_cookie(response: Response) -> None:
    response.set_cookie(
        AUTH_COOKIE_NAME,
        _auth_token(),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 12,
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(AUTH_COOKIE_NAME)


def _auth_token() -> str:
    password = os.getenv("APP_PASSWORD", "")
    secret = os.getenv("APP_SECRET") or password
    return hmac.new(secret.encode("utf-8"), password.encode("utf-8"), sha256).hexdigest()
