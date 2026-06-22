from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.events.models import Event, Seat, TicketType
from app.modules.reservations.enums import ReservationStatus
from app.modules.reservations.models import ReservationSeat


@dataclass(frozen=True, slots=True)
class SeatInventoryRecord:
    seat: Seat
    reservation_status: ReservationStatus | None


class EventRepository:
    async def get_event(self, session: AsyncSession, event_id: UUID) -> Event | None:
        result = await session.execute(select(Event).where(Event.id == event_id))
        return result.scalar_one_or_none()

    def add_event(self, session: AsyncSession, event: Event) -> None:
        session.add(event)

    def add_ticket_type(self, session: AsyncSession, ticket_type: TicketType) -> None:
        session.add(ticket_type)

    def add_seats(self, session: AsyncSession, seats: list[Seat]) -> None:
        session.add_all(seats)

    async def list_ticket_types(
        self,
        session: AsyncSession,
        event_id: UUID,
    ) -> list[TicketType]:
        result = await session.scalars(
            select(TicketType)
            .where(TicketType.event_id == event_id)
            .order_by(TicketType.name, TicketType.id)
        )
        return list(result)

    async def list_seat_inventory(
        self,
        session: AsyncSession,
        event_id: UUID,
    ) -> list[SeatInventoryRecord]:
        active_statuses = (
            ReservationStatus.RESERVED,
            ReservationStatus.CONFIRMED,
        )
        result = await session.execute(
            select(Seat, ReservationSeat.status)
            .outerjoin(
                ReservationSeat,
                and_(
                    ReservationSeat.seat_id == Seat.id,
                    ReservationSeat.status.in_(active_statuses),
                ),
            )
            .where(Seat.event_id == event_id)
            .order_by(Seat.section, Seat.row_name, Seat.seat_number, Seat.id)
        )
        rows = cast(
            list[tuple[Seat, ReservationStatus | None]],
            list(result.tuples()),
        )
        return [
            SeatInventoryRecord(seat=seat, reservation_status=reservation_status)
            for seat, reservation_status in rows
        ]
