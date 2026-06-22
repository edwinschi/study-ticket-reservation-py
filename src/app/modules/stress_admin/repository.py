from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.events.models import Seat, TicketType
from app.modules.reservations.enums import ReservationStatus
from app.modules.reservations.models import (
    Reservation,
    ReservationItem,
    ReservationSeat,
)

ACTIVE_RESERVATION_STATUSES = (
    ReservationStatus.RESERVED,
    ReservationStatus.CONFIRMED,
)


@dataclass(frozen=True, slots=True)
class TicketQuantityViolation:
    ticket_type_id: UUID
    total_quantity: int
    sold_quantity: int
    reserved_quantity: int


@dataclass(frozen=True, slots=True)
class DuplicateActiveSeatViolation:
    seat_id: UUID
    active_reservation_count: int


@dataclass(frozen=True, slots=True)
class OrphanReservationChildViolation:
    record_id: UUID
    reservation_id: UUID


@dataclass(frozen=True, slots=True)
class StaleActiveReservationViolation:
    reservation_id: UUID
    expires_at: datetime


class StressConsistencyRepository:
    async def list_negative_ticket_quantities(
        self,
        session: AsyncSession,
        *,
        limit: int,
        event_ids: list[UUID] | None = None,
    ) -> list[TicketQuantityViolation]:
        statement = (
            select(
                TicketType.id,
                TicketType.total_quantity,
                TicketType.sold_quantity,
                TicketType.reserved_quantity,
            )
            .where(
                or_(
                    TicketType.total_quantity < 0,
                    TicketType.sold_quantity < 0,
                    TicketType.reserved_quantity < 0,
                )
            )
            .order_by(TicketType.id)
            .limit(limit)
        )
        if event_ids is not None:
            statement = statement.where(TicketType.event_id.in_(event_ids))

        result = await session.execute(statement)
        return [
            TicketQuantityViolation(
                ticket_type_id=cast(UUID, row.id),
                total_quantity=cast(int, row.total_quantity),
                sold_quantity=cast(int, row.sold_quantity),
                reserved_quantity=cast(int, row.reserved_quantity),
            )
            for row in result
        ]

    async def list_oversold_ticket_quantities(
        self,
        session: AsyncSession,
        *,
        limit: int,
        event_ids: list[UUID] | None = None,
    ) -> list[TicketQuantityViolation]:
        statement = (
            select(
                TicketType.id,
                TicketType.total_quantity,
                TicketType.sold_quantity,
                TicketType.reserved_quantity,
            )
            .where(
                TicketType.sold_quantity + TicketType.reserved_quantity > TicketType.total_quantity
            )
            .order_by(TicketType.id)
            .limit(limit)
        )
        if event_ids is not None:
            statement = statement.where(TicketType.event_id.in_(event_ids))

        result = await session.execute(statement)
        return [
            TicketQuantityViolation(
                ticket_type_id=cast(UUID, row.id),
                total_quantity=cast(int, row.total_quantity),
                sold_quantity=cast(int, row.sold_quantity),
                reserved_quantity=cast(int, row.reserved_quantity),
            )
            for row in result
        ]

    async def list_duplicate_active_seats(
        self,
        session: AsyncSession,
        *,
        limit: int,
        event_ids: list[UUID] | None = None,
    ) -> list[DuplicateActiveSeatViolation]:
        active_count = func.count(ReservationSeat.id)
        statement = (
            select(
                ReservationSeat.seat_id,
                active_count.label("active_reservation_count"),
            )
            .join(Seat, Seat.id == ReservationSeat.seat_id)
            .where(ReservationSeat.status.in_(ACTIVE_RESERVATION_STATUSES))
            .group_by(ReservationSeat.seat_id)
            .having(active_count > 1)
            .order_by(ReservationSeat.seat_id)
            .limit(limit)
        )
        if event_ids is not None:
            statement = statement.where(Seat.event_id.in_(event_ids))

        result = await session.execute(statement)
        return [
            DuplicateActiveSeatViolation(
                seat_id=cast(UUID, row.seat_id),
                active_reservation_count=cast(int, row.active_reservation_count),
            )
            for row in result
        ]

    async def list_orphan_reservation_items(
        self,
        session: AsyncSession,
        *,
        limit: int,
    ) -> list[OrphanReservationChildViolation]:
        result = await session.execute(
            select(
                ReservationItem.id,
                ReservationItem.reservation_id,
            )
            .outerjoin(
                Reservation,
                Reservation.id == ReservationItem.reservation_id,
            )
            .where(Reservation.id.is_(None))
            .order_by(ReservationItem.id)
            .limit(limit)
        )
        return [
            OrphanReservationChildViolation(
                record_id=cast(UUID, row.id),
                reservation_id=cast(UUID, row.reservation_id),
            )
            for row in result
        ]

    async def list_orphan_reservation_seats(
        self,
        session: AsyncSession,
        *,
        limit: int,
    ) -> list[OrphanReservationChildViolation]:
        result = await session.execute(
            select(
                ReservationSeat.id,
                ReservationSeat.reservation_id,
            )
            .outerjoin(
                Reservation,
                Reservation.id == ReservationSeat.reservation_id,
            )
            .where(Reservation.id.is_(None))
            .order_by(ReservationSeat.id)
            .limit(limit)
        )
        return [
            OrphanReservationChildViolation(
                record_id=cast(UUID, row.id),
                reservation_id=cast(UUID, row.reservation_id),
            )
            for row in result
        ]

    async def list_stale_active_reservations(
        self,
        session: AsyncSession,
        *,
        expired_before: datetime,
        limit: int,
        event_ids: list[UUID] | None = None,
    ) -> list[StaleActiveReservationViolation]:
        statement = (
            select(
                Reservation.id,
                Reservation.expires_at,
            )
            .where(
                Reservation.status == ReservationStatus.RESERVED,
                Reservation.expires_at < expired_before,
            )
            .order_by(Reservation.expires_at, Reservation.id)
            .limit(limit)
        )
        if event_ids is not None:
            statement = statement.where(Reservation.event_id.in_(event_ids))

        result = await session.execute(statement)
        return [
            StaleActiveReservationViolation(
                reservation_id=cast(UUID, row.id),
                expires_at=cast(datetime, row.expires_at),
            )
            for row in result
        ]
