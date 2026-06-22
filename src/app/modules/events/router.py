from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.db.session import get_db_session
from app.modules.events.models import Event, TicketType
from app.modules.events.repository import EventRepository
from app.modules.events.schemas import (
    EventCreate,
    EventInventoryResponse,
    EventResponse,
    SeatBatchCreate,
    SeatBatchResponse,
    SeatResponse,
    TicketTypeCreate,
    TicketTypeResponse,
)
from app.modules.events.service import (
    EventNotFoundError,
    EventService,
    SeatConflictError,
)

router = APIRouter(prefix="/v1/events", tags=["events"])

DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]


def get_event_service() -> EventService:
    return EventService(EventRepository())


def event_not_found() -> NotFoundError:
    return NotFoundError(
        "Event not found",
        code="EVENT_NOT_FOUND",
    )


@router.post("", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
async def create_event(
    payload: EventCreate,
    db_session: DatabaseSession,
) -> Event:
    return await get_event_service().create_event(
        db_session,
        name=payload.name,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
    )


@router.get("/{event_id}", response_model=EventResponse)
async def get_event(event_id: UUID, db_session: DatabaseSession) -> Event:
    try:
        return await get_event_service().get_event(db_session, event_id)
    except EventNotFoundError as exc:
        raise event_not_found() from exc


@router.post(
    "/{event_id}/ticket-types",
    response_model=TicketTypeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_ticket_type(
    event_id: UUID,
    payload: TicketTypeCreate,
    db_session: DatabaseSession,
) -> TicketType:
    try:
        return await get_event_service().create_ticket_type(
            db_session,
            event_id=event_id,
            name=payload.name,
            total_quantity=payload.total_quantity,
        )
    except EventNotFoundError as exc:
        raise event_not_found() from exc


@router.post(
    "/{event_id}/seats",
    response_model=SeatBatchResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_seats(
    event_id: UUID,
    payload: SeatBatchCreate,
    db_session: DatabaseSession,
) -> SeatBatchResponse:
    try:
        seats = await get_event_service().create_seats(
            db_session,
            event_id=event_id,
            seat_inputs=payload.seats,
        )
    except EventNotFoundError as exc:
        raise event_not_found() from exc
    except SeatConflictError as exc:
        raise ConflictError(
            "One or more seats already exist for this event",
            code="SEAT_ALREADY_EXISTS",
        ) from exc

    return SeatBatchResponse(
        seats=[SeatResponse.model_validate(seat) for seat in seats],
    )


@router.get("/{event_id}/inventory", response_model=EventInventoryResponse)
async def get_inventory(
    event_id: UUID,
    db_session: DatabaseSession,
) -> EventInventoryResponse:
    try:
        return await get_event_service().get_inventory(db_session, event_id)
    except EventNotFoundError as exc:
        raise event_not_found() from exc
