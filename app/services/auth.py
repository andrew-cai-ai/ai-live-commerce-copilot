from __future__ import annotations

import hmac
import os
from hashlib import sha256

from dotenv import load_dotenv
from fastapi import Request, Response
from fastapi.responses import JSONResponse

from app.services.security_utils import safe_compare_digest

load_dotenv()

AUTH_COOKIE_NAME = "live_copilot_auth"


def auth_enabled() -> bool:
    return bool(os.getenv("APP_PASSWORD"))


def is_authenticated(request: Request) -> bool:
    if not auth_enabled():
        return True
    token = request.cookies.get(AUTH_COOKIE_NAME, "")
    return safe_compare_digest(token, _auth_token())


def password_matches(password: str) -> bool:
    expected = os.getenv("APP_PASSWORD", "")
    if not expected:
        return False
    return safe_compare_digest(password, expected)


def unauthorized_json() -> JSONResponse:
    return JSONResponse({"error": "unauthorized"}, status_code=401)


def require_api_auth(request: Request) -> JSONResponse | None:
    if is_authenticated(request):
        return None
    return unauthorized_json()


def set_auth_cookie(response: Response) -> None:
    secure = os.getenv("APP_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"}
    response.set_cookie(
        AUTH_COOKIE_NAME,
        _auth_token(),
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=60 * 60 * 12,
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(AUTH_COOKIE_NAME)


def _auth_token() -> str:
    password = os.getenv("APP_PASSWORD", "")
    secret = os.getenv("APP_SECRET") or password
    return hmac.new(secret.encode("utf-8"), password.encode("utf-8"), sha256).hexdigest()
