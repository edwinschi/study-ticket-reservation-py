from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db_session
from app.modules.sessions.cookies import set_visitor_session_cookie
from app.modules.sessions.dependencies import get_current_session
from app.modules.sessions.models import VisitorSession
from app.modules.sessions.repository import VisitorSessionRepository
from app.modules.sessions.schemas import (
    AnonymousSessionResponse,
    VisitorSessionResponse,
)
from app.modules.sessions.service import VisitorSessionService

router = APIRouter(prefix="/v1", tags=["sessions"])

DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
CurrentVisitorSession = Annotated[VisitorSession, Depends(get_current_session)]


def get_visitor_session_service() -> VisitorSessionService:
    return VisitorSessionService(VisitorSessionRepository(), get_settings())


@router.post(
    "/sessions/anonymous",
    response_model=AnonymousSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_anonymous_session(
    response: Response,
    db_session: DatabaseSession,
) -> AnonymousSessionResponse:
    settings = get_settings()
    created_session = await get_visitor_session_service().create(db_session)
    set_visitor_session_cookie(response, created_session.raw_token, settings)
    return AnonymousSessionResponse(
        visitor_session_id=created_session.visitor_session.id,
    )


@router.get("/me/session", response_model=VisitorSessionResponse)
async def read_current_session(
    visitor_session: CurrentVisitorSession,
) -> VisitorSession:
    return visitor_session
