from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ConflictError, UnauthorizedError
from app.db.session import get_db_session
from app.modules.sessions.cookies import (
    VISITOR_SESSION_COOKIE,
    delete_visitor_session_cookie,
    set_visitor_session_cookie,
)
from app.modules.sessions.dependencies import get_visitor_session_service
from app.modules.sessions.service import VisitorSessionService
from app.modules.users.repository import UserRepository
from app.modules.users.schemas import LoginRequest, RegisterRequest, UserResponse
from app.modules.users.service import (
    AuthService,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
)

router = APIRouter(prefix="/v1/auth", tags=["authentication"])

DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
VisitorSessionCookie = Annotated[
    str | None,
    Cookie(alias=VISITOR_SESSION_COOKIE),
]


def get_auth_service() -> AuthService:
    return AuthService(
        UserRepository(),
        get_visitor_session_service(),
    )


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: RegisterRequest,
    db_session: DatabaseSession,
) -> UserResponse:
    try:
        user = await get_auth_service().register(
            db_session,
            email=str(payload.email),
            password=payload.password,
        )
    except EmailAlreadyRegisteredError as exc:
        raise ConflictError(
            "Email is already registered",
            code="EMAIL_ALREADY_REGISTERED",
        ) from exc

    return UserResponse.model_validate(user)


@router.post("/login", response_model=UserResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    db_session: DatabaseSession,
    visitor_session_cookie: VisitorSessionCookie = None,
) -> UserResponse:
    try:
        result = await get_auth_service().login(
            db_session,
            email=str(payload.email),
            password=payload.password,
            raw_session_token=visitor_session_cookie,
        )
    except InvalidCredentialsError as exc:
        raise UnauthorizedError(
            "Invalid email or password",
            code="INVALID_CREDENTIALS",
        ) from exc

    set_visitor_session_cookie(response, result.raw_session_token, get_settings())
    return UserResponse.model_validate(result.user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    db_session: DatabaseSession,
    visitor_session_cookie: VisitorSessionCookie = None,
) -> None:
    if visitor_session_cookie is not None:
        service: VisitorSessionService = get_visitor_session_service()
        await service.invalidate(db_session, visitor_session_cookie)

    delete_visitor_session_cookie(response, get_settings())
