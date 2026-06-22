from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AnonymousSessionResponse(BaseModel):
    visitor_session_id: UUID


class VisitorSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID | None
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime
    expires_at: datetime
