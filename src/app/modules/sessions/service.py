from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import generate_session_token, hash_session_token
from app.modules.sessions.models import VisitorSession
from app.modules.sessions.repository import VisitorSessionRepository


@dataclass(frozen=True, slots=True)
class CreatedVisitorSession:
    visitor_session: VisitorSession
    raw_token: str


class VisitorSessionService:
    def __init__(
        self,
        repository: VisitorSessionRepository,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.settings = settings

    async def create(
        self,
        session: AsyncSession,
        *,
        user_id: UUID | None = None,
    ) -> CreatedVisitorSession:
        now = datetime.now(UTC)
        raw_token = generate_session_token()
        visitor_session = VisitorSession(
            user_id=user_id,
            anonymous_token_hash=hash_session_token(raw_token),
            last_seen_at=now,
            expires_at=now + timedelta(seconds=self.settings.visitor_session_ttl_seconds),
        )
        self.repository.add(session, visitor_session)
        await session.commit()
        await session.refresh(visitor_session)
        return CreatedVisitorSession(visitor_session=visitor_session, raw_token=raw_token)

    async def get_active(
        self,
        session: AsyncSession,
        raw_token: str,
        *,
        touch: bool = True,
    ) -> VisitorSession | None:
        visitor_session = await self.repository.get_by_token_hash(
            session,
            hash_session_token(raw_token),
        )
        now = datetime.now(UTC)

        if visitor_session is None or visitor_session.expires_at <= now:
            return None

        if touch:
            self.repository.touch(visitor_session, seen_at=now)
            await session.commit()
            await session.refresh(visitor_session)

        return visitor_session

    async def link_user(
        self,
        session: AsyncSession,
        visitor_session: VisitorSession,
        user_id: UUID,
    ) -> None:
        now = datetime.now(UTC)
        self.repository.link_user(
            visitor_session,
            user_id=user_id,
            seen_at=now,
            expires_at=now + timedelta(seconds=self.settings.visitor_session_ttl_seconds),
        )
        await session.commit()
        await session.refresh(visitor_session)

    async def invalidate(self, session: AsyncSession, raw_token: str) -> None:
        visitor_session = await self.repository.get_by_token_hash(
            session,
            hash_session_token(raw_token),
        )
        if visitor_session is None:
            return

        self.repository.invalidate(visitor_session, invalidated_at=datetime.now(UTC))
        await session.commit()
