from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.events.models import Seat, TicketType
from app.modules.reservations.enums import ReservationStatus, ReservationType
from app.modules.reservations.models import (
    Reservation,
    ReservationItem,
    ReservationSeat,
)


@dataclass(frozen=True, slots=True)
class QuantityReservationRecord:
    reservation_id: UUID
    status: ReservationStatus
    reservation_type: ReservationType
    expires_at: datetime
    ticket_type_id: UUID
    quantity: int


@dataclass(frozen=True, slots=True)
class SeatReservationRecord:
    reservation_id: UUID
    status: ReservationStatus
    reservation_type: ReservationType
    expires_at: datetime
    seat_ids: list[UUID]


class ReservationRepository:
    """Persistence layer for reservation queries and concurrency-sensitive writes."""

    async def get_owned_reservation(
        self,
        session: AsyncSession,
        *,
        reservation_id: UUID,
        visitor_session_id: UUID,
        user_id: UUID | None,
        for_update: bool = False,
    ) -> Reservation | None:
        """
        Load a reservation visible to the current visitor session or authenticated user.

        Lifecycle operations pass ``for_update=True`` so concurrent cancel, confirm, or expire
        attempts serialize on the same parent row before changing inventory.
        """
        ownership = Reservation.visitor_session_id == visitor_session_id
        if user_id is not None:
            ownership = or_(ownership, Reservation.user_id == user_id)

        statement = select(Reservation).where(
            Reservation.id == reservation_id,
            ownership,
        )
        if for_update:
            statement = statement.with_for_update()

        result = await session.execute(statement)
        return result.scalar_one_or_none()

    async def list_reservation_items(
        self,
        session: AsyncSession,
        reservation_id: UUID,
    ) -> list[ReservationItem]:
        result = await session.scalars(
            select(ReservationItem)
            .where(ReservationItem.reservation_id == reservation_id)
            .order_by(ReservationItem.ticket_type_id, ReservationItem.id)
        )
        return list(result)

    async def list_reservation_seats(
        self,
        session: AsyncSession,
        reservation_id: UUID,
    ) -> list[ReservationSeat]:
        result = await session.scalars(
            select(ReservationSeat)
            .where(ReservationSeat.reservation_id == reservation_id)
            .order_by(ReservationSeat.seat_id, ReservationSeat.id)
        )
        return list(result)

    async def lock_expired_reservations(
        self,
        session: AsyncSession,
        *,
        expired_before: datetime,
        batch_size: int,
        reservation_ids: list[UUID] | None = None,
    ) -> list[Reservation]:
        """
        Lock a batch of expired reservations for worker processing.

        ``SKIP LOCKED`` lets multiple workers scan the same index concurrently. Rows already
        locked by another worker are skipped instead of blocking the whole batch.
        """
        statement = select(Reservation).where(
            Reservation.status == ReservationStatus.RESERVED,
            Reservation.expires_at < expired_before,
        )
        if reservation_ids is not None:
            statement = statement.where(Reservation.id.in_(reservation_ids))

        result = await session.scalars(
            statement.order_by(Reservation.expires_at, Reservation.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        return list(result)

    async def get_quantity_by_idempotency_key(
        self,
        session: AsyncSession,
        *,
        visitor_session_id: UUID,
        idempotency_key: str,
    ) -> QuantityReservationRecord | None:
        """
        Return a previously created quantity reservation for an idempotent retry.

        The unique ``(visitor_session_id, idempotency_key)`` constraint is the hard guarantee.
        This read is how the API turns that database guarantee into a replayed response.
        """
        result = await session.execute(
            select(
                Reservation.id,
                Reservation.status,
                Reservation.reservation_type,
                Reservation.expires_at,
                ReservationItem.ticket_type_id,
                ReservationItem.quantity,
            )
            .join(
                ReservationItem,
                ReservationItem.reservation_id == Reservation.id,
            )
            .where(
                Reservation.visitor_session_id == visitor_session_id,
                Reservation.idempotency_key == idempotency_key,
                Reservation.reservation_type == ReservationType.QUANTITY,
            )
        )
        row = result.one_or_none()
        if row is None:
            return None

        return QuantityReservationRecord(
            reservation_id=cast(UUID, row.id),
            status=cast(ReservationStatus, row.status),
            reservation_type=cast(ReservationType, row.reservation_type),
            expires_at=cast(datetime, row.expires_at),
            ticket_type_id=cast(UUID, row.ticket_type_id),
            quantity=cast(int, row.quantity),
        )

    async def get_seats_by_idempotency_key(
        self,
        session: AsyncSession,
        *,
        visitor_session_id: UUID,
        idempotency_key: str,
    ) -> SeatReservationRecord | None:
        """Return a previously created seat reservation for an idempotent retry."""
        result = await session.execute(
            select(
                Reservation.id,
                Reservation.status,
                Reservation.reservation_type,
                Reservation.expires_at,
                ReservationSeat.seat_id,
            )
            .join(
                ReservationSeat,
                ReservationSeat.reservation_id == Reservation.id,
            )
            .where(
                Reservation.visitor_session_id == visitor_session_id,
                Reservation.idempotency_key == idempotency_key,
                Reservation.reservation_type == ReservationType.SEATS,
            )
            .order_by(ReservationSeat.seat_id)
        )
        rows = list(result)
        if not rows:
            return None

        first_row = rows[0]
        return SeatReservationRecord(
            reservation_id=cast(UUID, first_row.id),
            status=cast(ReservationStatus, first_row.status),
            reservation_type=cast(ReservationType, first_row.reservation_type),
            expires_at=cast(datetime, first_row.expires_at),
            seat_ids=[cast(UUID, row.seat_id) for row in rows],
        )

    async def reserve_ticket_quantity(
        self,
        session: AsyncSession,
        *,
        event_id: UUID,
        ticket_type_id: UUID,
        quantity: int,
    ) -> bool:
        """
        Atomically reserve quantity inventory in PostgreSQL.

        The WHERE clause is the stock check and the SET clause is the mutation. Keeping both
        in one UPDATE means PostgreSQL re-evaluates availability while holding the row write
        lock, which prevents overselling under concurrent requests.
        """
        result = await session.execute(
            update(TicketType)
            .where(
                TicketType.id == ticket_type_id,
                TicketType.event_id == event_id,
                (
                    TicketType.total_quantity
                    - TicketType.sold_quantity
                    - TicketType.reserved_quantity
                )
                >= quantity,
            )
            .values(
                reserved_quantity=TicketType.reserved_quantity + quantity,
            )
            .returning(TicketType.id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none() is not None

    async def lock_event_seats(
        self,
        session: AsyncSession,
        *,
        event_id: UUID,
        seat_ids: list[UUID],
    ) -> list[UUID]:
        """
        Lock event seats in deterministic order.

        The caller passes sorted UUIDs, and the query orders by the same column before
        ``FOR UPDATE``. Consistent lock order reduces deadlocks when requests contain the same
        seats in different client-provided orders.
        """
        result = await session.scalars(
            select(Seat.id)
            .where(
                Seat.event_id == event_id,
                Seat.id.in_(seat_ids),
            )
            .order_by(Seat.id)
            .with_for_update()
        )
        return list(result)

    async def expire_reservations_for_seats(
        self,
        session: AsyncSession,
        *,
        seat_ids: list[UUID],
        expired_at: datetime,
    ) -> None:
        """
        Expire stale active holds for seats that are already locked by the caller.

        This runs inside the new reservation transaction. If an old hold has passed its
        expiration time, releasing it before insertion allows the partial unique index to accept
        a new active hold for the same seat.
        """
        expired_reservation_ids = list(
            await session.scalars(
                select(ReservationSeat.reservation_id)
                .where(
                    ReservationSeat.seat_id.in_(seat_ids),
                    ReservationSeat.status == ReservationStatus.RESERVED,
                    ReservationSeat.expires_at <= expired_at,
                )
                .order_by(ReservationSeat.reservation_id)
            )
        )
        reservation_ids = sorted(set(expired_reservation_ids))
        if not reservation_ids:
            return

        # Lock parent reservations in a stable order before updating children. That keeps
        # expiration and lifecycle transitions from racing on the same reservation.
        await session.scalars(
            select(Reservation.id)
            .where(Reservation.id.in_(reservation_ids))
            .order_by(Reservation.id)
            .with_for_update()
        )
        await session.execute(
            update(ReservationSeat)
            .where(
                ReservationSeat.reservation_id.in_(reservation_ids),
                ReservationSeat.status == ReservationStatus.RESERVED,
            )
            .values(status=ReservationStatus.EXPIRED)
            .execution_options(synchronize_session=False)
        )
        await session.execute(
            update(Reservation)
            .where(
                Reservation.id.in_(reservation_ids),
                Reservation.status == ReservationStatus.RESERVED,
            )
            .values(status=ReservationStatus.EXPIRED)
            .execution_options(synchronize_session=False)
        )

    async def release_reserved_quantity(
        self,
        session: AsyncSession,
        *,
        ticket_type_id: UUID,
        quantity: int,
    ) -> bool:
        """
        Release reserved quantity without allowing negative inventory.

        The guarded WHERE clause makes repeated or concurrent release attempts safe: only the
        first valid transition can decrement the quantity.
        """
        result = await session.execute(
            update(TicketType)
            .where(
                TicketType.id == ticket_type_id,
                TicketType.reserved_quantity >= quantity,
            )
            .values(
                reserved_quantity=TicketType.reserved_quantity - quantity,
            )
            .returning(TicketType.id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none() is not None

    async def confirm_reserved_quantity(
        self,
        session: AsyncSession,
        *,
        ticket_type_id: UUID,
        quantity: int,
    ) -> bool:
        """
        Move reserved quantity to sold quantity in one guarded UPDATE.

        This is used by confirmation. The reserved quantity is decremented and sold quantity is
        incremented together so another concurrent transition cannot observe a half-applied sale.
        """
        result = await session.execute(
            update(TicketType)
            .where(
                TicketType.id == ticket_type_id,
                TicketType.reserved_quantity >= quantity,
                (
                    TicketType.sold_quantity + TicketType.reserved_quantity
                    <= TicketType.total_quantity
                ),
            )
            .values(
                reserved_quantity=TicketType.reserved_quantity - quantity,
                sold_quantity=TicketType.sold_quantity + quantity,
            )
            .returning(TicketType.id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none() is not None

    def add_reservation(
        self,
        session: AsyncSession,
        reservation: Reservation,
    ) -> None:
        session.add(reservation)

    def add_reservation_item(
        self,
        session: AsyncSession,
        reservation_item: ReservationItem,
    ) -> None:
        session.add(reservation_item)

    def add_reservation_seats(
        self,
        session: AsyncSession,
        reservation_seats: list[ReservationSeat],
    ) -> None:
        session.add_all(reservation_seats)
