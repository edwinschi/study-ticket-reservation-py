from dataclasses import dataclass

from anyio import to_thread
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.modules.sessions.models import VisitorSession
from app.modules.sessions.service import VisitorSessionService
from app.modules.users.models import User
from app.modules.users.repository import UserRepository


class EmailAlreadyRegisteredError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class LoginResult:
    user: User
    visitor_session: VisitorSession
    raw_session_token: str


class AuthService:
    def __init__(
        self,
        user_repository: UserRepository,
        visitor_session_service: VisitorSessionService,
    ) -> None:
        self.user_repository = user_repository
        self.visitor_session_service = visitor_session_service

    async def register(
        self,
        session: AsyncSession,
        *,
        email: str,
        password: str,
    ) -> User:
        normalized_email = email.strip().lower()
        encoded_password = await to_thread.run_sync(hash_password, password)

        if await self.user_repository.get_by_email(session, normalized_email) is not None:
            await session.rollback()
            raise EmailAlreadyRegisteredError

        user = User(email=normalized_email, password_hash=encoded_password)
        self.user_repository.add(session, user)

        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise EmailAlreadyRegisteredError from exc

        await session.refresh(user)
        return user

    async def login(
        self,
        session: AsyncSession,
        *,
        email: str,
        password: str,
        raw_session_token: str | None,
    ) -> LoginResult:
        user = await self.user_repository.get_by_email(session, email.strip().lower())
        if user is None:
            await session.rollback()
            raise InvalidCredentialsError

        user_id = user.id
        encoded_password = user.password_hash
        await session.rollback()

        password_is_valid = await to_thread.run_sync(
            verify_password,
            password,
            encoded_password,
        )
        if not password_is_valid:
            raise InvalidCredentialsError

        user = await self.user_repository.get_by_id(session, user_id)
        if user is None:
            await session.rollback()
            raise InvalidCredentialsError

        visitor_session = None
        if raw_session_token is not None:
            visitor_session = await self.visitor_session_service.get_active(
                session,
                raw_session_token,
                touch=False,
            )

        if visitor_session is None:
            created_session = await self.visitor_session_service.create(
                session,
                user_id=user.id,
            )
            return LoginResult(
                user=user,
                visitor_session=created_session.visitor_session,
                raw_session_token=created_session.raw_token,
            )

        assert raw_session_token is not None
        await self.visitor_session_service.link_user(session, visitor_session, user.id)
        return LoginResult(
            user=user,
            visitor_session=visitor_session,
            raw_session_token=raw_session_token,
        )
