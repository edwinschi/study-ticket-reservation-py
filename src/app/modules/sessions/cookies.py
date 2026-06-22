from fastapi import Response

from app.core.config import Settings

VISITOR_SESSION_COOKIE = "visitor_session"


def set_visitor_session_cookie(
    response: Response,
    raw_token: str,
    settings: Settings,
) -> None:
    response.set_cookie(
        key=VISITOR_SESSION_COOKIE,
        value=raw_token,
        max_age=settings.visitor_session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def delete_visitor_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=VISITOR_SESSION_COOKIE,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
