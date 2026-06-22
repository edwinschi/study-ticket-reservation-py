from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.sessions.models import VisitorSession


class VisitorSessionRepository:
    async def get_by_token_hash(
        self,
        session: AsyncSession,
        token_hash: str,
    ) -> VisitorSession | None:
        result = await session.execute(
            select(VisitorSession).where(VisitorSession.anonymous_token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    def add(self, session: AsyncSession, visitor_session: VisitorSession) -> None:
        session.add(visitor_session)

    def touch(
        self,
        visitor_session: VisitorSession,
        *,
        seen_at: datetime,
    ) -> None:
        visitor_session.last_seen_at = seen_at

    def link_user(
        self,
        visitor_session: VisitorSession,
        *,
        user_id: UUID,
        seen_at: datetime,
        expires_at: datetime,
    ) -> None:
        visitor_session.user_id = user_id
        visitor_session.last_seen_at = seen_at
        visitor_session.expires_at = expires_at

    def invalidate(
        self,
        visitor_session: VisitorSession,
        *,
        invalidated_at: datetime,
    ) -> None:
        visitor_session.user_id = None
        visitor_session.last_seen_at = invalidated_at
        visitor_session.expires_at = invalidated_at
