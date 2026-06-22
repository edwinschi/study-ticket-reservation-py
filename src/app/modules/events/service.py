from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.events.models import Event, Seat, TicketType
from app.modules.events.repository import EventRepository
from app.modules.events.schemas import (
    EventInventoryResponse,
    SeatCreate,
    SeatInventoryResponse,
    SeatInventoryStatus,
    TicketTypeInventoryResponse,
)
from app.modules.reservations.enums import ReservationStatus


class EventNotFoundError(Exception):
    pass


class SeatConflictError(Exception):
    pass


class EventService:
    def __init__(self, repository: EventRepository) -> None:
        self.repository = repository

    async def create_event(
        self,
        session: AsyncSession,
        *,
        name: str,
        starts_at: datetime,
        ends_at: datetime,
    ) -> Event:
        event = Event(
            name=name.strip(),
            starts_at=starts_at,
            ends_at=ends_at,
        )
        self.repository.add_event(session, event)
        await session.commit()
        await session.refresh(event)
        return event

    async def get_event(self, session: AsyncSession, event_id: UUID) -> Event:
        event = await self.repository.get_event(session, event_id)
        if event is None:
            raise EventNotFoundError
        return event

    async def create_ticket_type(
        self,
        session: AsyncSession,
        *,
        event_id: UUID,
        name: str,
        total_quantity: int,
    ) -> TicketType:
        await self.get_event(session, event_id)
        ticket_type = TicketType(
            event_id=event_id,
            name=name.strip(),
            total_quantity=total_quantity,
        )
        self.repository.add_ticket_type(session, ticket_type)
        await session.commit()
        await session.refresh(ticket_type)
        return ticket_type

    async def create_seats(
        self,
        session: AsyncSession,
        *,
        event_id: UUID,
        seat_inputs: list[SeatCreate],
    ) -> list[Seat]:
        await self.get_event(session, event_id)
        seats = [
            Seat(
                event_id=event_id,
                section=seat.section.strip(),
                row_name=seat.row_name.strip(),
                seat_number=seat.seat_number.strip(),
            )
            for seat in seat_inputs
        ]
        self.repository.add_seats(session, seats)

        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise SeatConflictError from exc

        for seat in seats:
            await session.refresh(seat)
        return seats

    async def get_inventory(
        self,
        session: AsyncSession,
        event_id: UUID,
    ) -> EventInventoryResponse:
        await self.get_event(session, event_id)
        ticket_types = await self.repository.list_ticket_types(session, event_id)
        seat_records = await self.repository.list_seat_inventory(session, event_id)

        return EventInventoryResponse(
            event_id=event_id,
            ticket_types=[
                TicketTypeInventoryResponse(
                    id=ticket_type.id,
                    name=ticket_type.name,
                    total=ticket_type.total_quantity,
                    sold=ticket_type.sold_quantity,
                    reserved=ticket_type.reserved_quantity,
                    available=(
                        ticket_type.total_quantity
                        - ticket_type.sold_quantity
                        - ticket_type.reserved_quantity
                    ),
                )
                for ticket_type in ticket_types
            ],
            seats=[
                SeatInventoryResponse(
                    id=record.seat.id,
                    section=record.seat.section,
                    row_name=record.seat.row_name,
                    seat_number=record.seat.seat_number,
                    status=self._seat_status(record.reservation_status),
                )
                for record in seat_records
            ],
        )

    @staticmethod
    def _seat_status(
        reservation_status: ReservationStatus | None,
    ) -> SeatInventoryStatus:
        if reservation_status is ReservationStatus.RESERVED:
            return SeatInventoryStatus.RESERVED
        if reservation_status is ReservationStatus.CONFIRMED:
            return SeatInventoryStatus.CONFIRMED
        return SeatInventoryStatus.AVAILABLE
