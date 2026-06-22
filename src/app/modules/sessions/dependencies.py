from typing import Annotated

from fastapi import Cookie, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError
from app.db.session import get_db_session
from app.modules.sessions.cookies import VISITOR_SESSION_COOKIE
from app.modules.sessions.models import VisitorSession
from app.modules.sessions.repository import VisitorSessionRepository
from app.modules.sessions.service import VisitorSessionService
from app.modules.users.models import User
from app.modules.users.repository import UserRepository

DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
VisitorSessionCookie = Annotated[
    str | None,
    Cookie(alias=VISITOR_SESSION_COOKIE),
]


def get_visitor_session_service() -> VisitorSessionService:
    return VisitorSessionService(VisitorSessionRepository(), get_settings())


async def get_optional_session(
    db_session: DatabaseSession,
    visitor_session_cookie: VisitorSessionCookie = None,
) -> VisitorSession | None:
    if visitor_session_cookie is None:
        return None

    return await get_visitor_session_service().get_active(
        db_session,
        visitor_session_cookie,
        touch=False,
    )


async def get_current_session(
    visitor_session: Annotated[VisitorSession | None, Depends(get_optional_session)],
) -> VisitorSession:
    if visitor_session is None:
        raise UnauthorizedError(
            "A valid visitor session is required",
            code="SESSION_REQUIRED",
        )
    return visitor_session


async def get_optional_user(
    db_session: DatabaseSession,
    visitor_session: Annotated[VisitorSession | None, Depends(get_optional_session)],
) -> User | None:
    if visitor_session is None or visitor_session.user_id is None:
        return None

    return await UserRepository().get_by_id(db_session, visitor_session.user_id)


async def get_current_user(
    user: Annotated[User | None, Depends(get_optional_user)],
) -> User:
    if user is None:
        raise UnauthorizedError(
            "Authentication is required",
            code="AUTHENTICATION_REQUIRED",
        )
    return user
