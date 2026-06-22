import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.reservations.enums import ReservationStatus, ReservationType
from app.modules.reservations.models import Reservation, ReservationItem, ReservationSeat
from app.modules.reservations.repository import (
    QuantityReservationRecord,
    ReservationRepository,
    SeatReservationRecord,
)
from app.modules.reservations.schemas import (
    QuantityReservationItemResponse,
    QuantityReservationResponse,
    ReservationDetailResponse,
    ReservationItemDetailResponse,
    ReservationSeatDetailResponse,
    SeatReservationItemResponse,
    SeatReservationResponse,
)
from app.modules.sessions.models import VisitorSession

logger = logging.getLogger(__name__)


class QuantityReservationConflictError(Exception):
    pass


class SeatReservationConflictError(Exception):
    pass


class SeatsNotFoundError(Exception):
    pass


class ReservationNotFoundError(Exception):
    pass


class ReservationTransitionConflictError(Exception):
    pass


class ReservationInventoryConflictError(Exception):
    pass


class QuantityReservationService:
    """Coordinate quantity reservations without trusting application-side stock reads."""

    def __init__(
        self,
        repository: ReservationRepository,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.settings = settings

    async def reserve(
        self,
        session: AsyncSession,
        *,
        visitor_session: VisitorSession,
        event_id: UUID,
        ticket_type_id: UUID,
        quantity: int,
        idempotency_key: str,
    ) -> QuantityReservationResponse:
        """
        Reserve tickets by quantity using a short database transaction.

        The stock validation is delegated to PostgreSQL through an atomic UPDATE. This avoids
        the classic race where two concurrent requests both read the same available quantity
        before either one writes the new reserved quantity.
        """
        visitor_session_id = visitor_session.id
        user_id = visitor_session.user_id

        # Idempotency is checked before touching inventory so client retries can safely replay
        # an already-created reservation without incrementing reserved_quantity again.
        existing = await self.repository.get_quantity_by_idempotency_key(
            session,
            visitor_session_id=visitor_session_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return self._to_response(existing)

        # This call performs the availability check and the increment in one SQL statement.
        # A false result means PostgreSQL could not update any row, which maps to a business
        # conflict instead of an application error.
        stock_reserved = await self.repository.reserve_ticket_quantity(
            session,
            event_id=event_id,
            ticket_type_id=ticket_type_id,
            quantity=quantity,
        )
        if not stock_reserved:
            return await self._resolve_conflict_or_replay(
                session,
                visitor_session_id=visitor_session_id,
                idempotency_key=idempotency_key,
            )

        reservation_id = uuid4()
        expires_at = datetime.now(UTC) + timedelta(seconds=self.settings.reservation_ttl_seconds)
        reservation = Reservation(
            id=reservation_id,
            event_id=event_id,
            visitor_session_id=visitor_session_id,
            user_id=user_id,
            status=ReservationStatus.RESERVED,
            reservation_type=ReservationType.QUANTITY,
            idempotency_key=idempotency_key,
            expires_at=expires_at,
        )
        reservation_item = ReservationItem(
            reservation_id=reservation_id,
            ticket_type_id=ticket_type_id,
            quantity=quantity,
            status=ReservationStatus.RESERVED,
        )

        try:
            # The reservation header is flushed before the item so database constraints can
            # validate the parent row inside the same transaction.
            self.repository.add_reservation(session, reservation)
            await session.flush()
            self.repository.add_reservation_item(session, reservation_item)
            await session.commit()
        except IntegrityError:
            # A concurrent request with the same idempotency key may win the unique constraint.
            # Roll back this transaction, then replay the existing reservation if it exists.
            return await self._resolve_conflict_or_replay(
                session,
                visitor_session_id=visitor_session_id,
                idempotency_key=idempotency_key,
            )

        return QuantityReservationResponse(
            reservation_id=reservation_id,
            status=ReservationStatus.RESERVED,
            reservation_type=ReservationType.QUANTITY,
            expires_at=expires_at,
            items=[
                QuantityReservationItemResponse(
                    ticket_type_id=ticket_type_id,
                    quantity=quantity,
                )
            ],
        )

    async def _resolve_conflict_or_replay(
        self,
        session: AsyncSession,
        *,
        visitor_session_id: UUID,
        idempotency_key: str,
    ) -> QuantityReservationResponse:
        """Replay an idempotent reservation or raise a stock conflict after rollback."""
        await session.rollback()
        existing = await self.repository.get_quantity_by_idempotency_key(
            session,
            visitor_session_id=visitor_session_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return self._to_response(existing)

        await session.rollback()
        raise QuantityReservationConflictError

    @staticmethod
    def _to_response(
        record: QuantityReservationRecord,
    ) -> QuantityReservationResponse:
        return QuantityReservationResponse(
            reservation_id=record.reservation_id,
            status=record.status,
            reservation_type=record.reservation_type,
            expires_at=record.expires_at,
            items=[
                QuantityReservationItemResponse(
                    ticket_type_id=record.ticket_type_id,
                    quantity=record.quantity,
                )
            ],
        )


class SeatReservationService:
    """Coordinate seat reservations with deterministic locks and database uniqueness."""

    def __init__(
        self,
        repository: ReservationRepository,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.settings = settings

    async def reserve(
        self,
        session: AsyncSession,
        *,
        visitor_session: VisitorSession,
        event_id: UUID,
        seat_ids: list[UUID],
        idempotency_key: str,
    ) -> SeatReservationResponse:
        """
        Reserve explicit seats in a short transaction.

        Seats are locked in sorted UUID order before inserting reservation rows. The ordered
        lock acquisition keeps competing requests from taking locks in opposite orders, which
        is one of the easiest ways to create deadlocks.
        """
        visitor_session_id = visitor_session.id
        user_id = visitor_session.user_id

        # Replays must return the original reservation instead of trying to insert another
        # active seat row and fighting the partial unique index.
        existing = await self.repository.get_seats_by_idempotency_key(
            session,
            visitor_session_id=visitor_session_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return self._to_response(existing)

        # Sorting here is part of the concurrency contract. The repository also orders the
        # SELECT FOR UPDATE result, and the equality check proves every requested seat exists.
        ordered_seat_ids = sorted(seat_ids)
        locked_seat_ids = await self.repository.lock_event_seats(
            session,
            event_id=event_id,
            seat_ids=ordered_seat_ids,
        )
        if locked_seat_ids != ordered_seat_ids:
            await session.rollback()
            raise SeatsNotFoundError

        now = datetime.now(UTC)
        # Before creating a new active hold, stale holds for the same locked seats are expired
        # in the same transaction. That keeps the partial unique index from rejecting seats that
        # are only blocked by already-expired reservations.
        await self.repository.expire_reservations_for_seats(
            session,
            seat_ids=ordered_seat_ids,
            expired_at=now,
        )

        reservation_id = uuid4()
        expires_at = now + timedelta(seconds=self.settings.reservation_ttl_seconds)
        reservation = Reservation(
            id=reservation_id,
            event_id=event_id,
            visitor_session_id=visitor_session_id,
            user_id=user_id,
            status=ReservationStatus.RESERVED,
            reservation_type=ReservationType.SEATS,
            idempotency_key=idempotency_key,
            expires_at=expires_at,
        )
        reservation_seats = [
            ReservationSeat(
                reservation_id=reservation_id,
                seat_id=seat_id,
                status=ReservationStatus.RESERVED,
                expires_at=expires_at,
            )
            for seat_id in ordered_seat_ids
        ]

        try:
            # The partial unique index on reservation_seats(seat_id) remains the final safety
            # net if two transactions still race to create an active hold for the same seat.
            self.repository.add_reservation(session, reservation)
            await session.flush()
            self.repository.add_reservation_seats(session, reservation_seats)
            await session.commit()
        except IntegrityError:
            return await self._resolve_conflict_or_replay(
                session,
                visitor_session_id=visitor_session_id,
                idempotency_key=idempotency_key,
            )

        return SeatReservationResponse(
            reservation_id=reservation_id,
            status=ReservationStatus.RESERVED,
            reservation_type=ReservationType.SEATS,
            expires_at=expires_at,
            seats=[SeatReservationItemResponse(seat_id=seat_id) for seat_id in ordered_seat_ids],
        )

    async def _resolve_conflict_or_replay(
        self,
        session: AsyncSession,
        *,
        visitor_session_id: UUID,
        idempotency_key: str,
    ) -> SeatReservationResponse:
        """Replay an idempotent seat reservation or raise an active-seat conflict."""
        await session.rollback()
        existing = await self.repository.get_seats_by_idempotency_key(
            session,
            visitor_session_id=visitor_session_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return self._to_response(existing)

        await session.rollback()
        raise SeatReservationConflictError

    @staticmethod
    def _to_response(record: SeatReservationRecord) -> SeatReservationResponse:
        return SeatReservationResponse(
            reservation_id=record.reservation_id,
            status=record.status,
            reservation_type=record.reservation_type,
            expires_at=record.expires_at,
            seats=[SeatReservationItemResponse(seat_id=seat_id) for seat_id in record.seat_ids],
        )


class ReservationLifecycleService:
    """Handle reservation reads and state transitions under row-level locks."""

    def __init__(self, repository: ReservationRepository) -> None:
        self.repository = repository

    async def get(
        self,
        session: AsyncSession,
        *,
        reservation_id: UUID,
        visitor_session: VisitorSession,
    ) -> ReservationDetailResponse:
        """Return a reservation only when it belongs to the current visitor or user."""
        reservation = await self.repository.get_owned_reservation(
            session,
            reservation_id=reservation_id,
            visitor_session_id=visitor_session.id,
            user_id=visitor_session.user_id,
        )
        if reservation is None:
            await session.rollback()
            raise ReservationNotFoundError

        response = await self._to_detail_response(session, reservation)
        await session.rollback()
        return response

    async def cancel(
        self,
        session: AsyncSession,
        *,
        reservation_id: UUID,
        visitor_session: VisitorSession,
    ) -> ReservationDetailResponse:
        """
        Cancel a reservation idempotently and release its inventory.

        The parent reservation is locked before any child rows or ticket quantities are changed.
        That makes concurrent cancel/confirm/expire attempts serialize around the same row.
        """
        reservation = await self._lock_owned(
            session,
            reservation_id=reservation_id,
            visitor_session=visitor_session,
        )

        # Returning the current cancelled state makes repeated cancel calls safe for clients.
        if reservation.status is ReservationStatus.CANCELLED:
            response = await self._to_detail_response(session, reservation)
            await session.rollback()
            return response
        if reservation.status in {
            ReservationStatus.CONFIRMED,
            ReservationStatus.EXPIRED,
        }:
            await session.rollback()
            raise ReservationTransitionConflictError
        if reservation.expires_at < datetime.now(UTC):
            # A stale reservation is expired before responding. That keeps inventory state
            # consistent even if the background worker has not processed this row yet.
            try:
                await self._transition_reserved(
                    session,
                    reservation=reservation,
                    target_status=ReservationStatus.EXPIRED,
                )
            except ReservationInventoryConflictError:
                await session.rollback()
                raise
            await session.commit()
            raise ReservationTransitionConflictError

        try:
            await self._transition_reserved(
                session,
                reservation=reservation,
                target_status=ReservationStatus.CANCELLED,
            )
        except ReservationInventoryConflictError:
            await session.rollback()
            raise
        await session.flush()
        response = await self._to_detail_response(session, reservation)
        await session.commit()
        return response

    async def confirm(
        self,
        session: AsyncSession,
        *,
        reservation_id: UUID,
        visitor_session: VisitorSession,
    ) -> ReservationDetailResponse:
        """
        Confirm a reservation idempotently and move inventory to the sold/confirmed state.

        Confirmation is the point where quantity inventory moves from reserved to sold. It must
        be protected by the same parent-row lock so repeated concurrent confirms do not sell the
        same reservation more than once.
        """
        reservation = await self._lock_owned(
            session,
            reservation_id=reservation_id,
            visitor_session=visitor_session,
        )

        # Repeated confirm calls return the existing confirmed reservation instead of trying to
        # increment sold_quantity again.
        if reservation.status is ReservationStatus.CONFIRMED:
            response = await self._to_detail_response(session, reservation)
            await session.rollback()
            return response
        if reservation.status in {
            ReservationStatus.CANCELLED,
            ReservationStatus.EXPIRED,
        }:
            await session.rollback()
            raise ReservationTransitionConflictError
        if reservation.expires_at < datetime.now(UTC):
            # Expired reservations cannot be confirmed. Transitioning them here avoids leaving
            # stale inventory reserved while the client receives a conflict.
            try:
                await self._transition_reserved(
                    session,
                    reservation=reservation,
                    target_status=ReservationStatus.EXPIRED,
                )
            except ReservationInventoryConflictError:
                await session.rollback()
                raise
            await session.commit()
            raise ReservationTransitionConflictError

        try:
            await self._transition_reserved(
                session,
                reservation=reservation,
                target_status=ReservationStatus.CONFIRMED,
            )
        except ReservationInventoryConflictError:
            await session.rollback()
            raise
        await session.flush()
        response = await self._to_detail_response(session, reservation)
        await session.commit()
        return response

    async def expire_batch(
        self,
        session: AsyncSession,
        *,
        batch_size: int,
        now: datetime | None = None,
        reservation_ids: list[UUID] | None = None,
    ) -> int:
        """
        Expire one batch of stale reservations.

        The repository locks rows with FOR UPDATE SKIP LOCKED, allowing multiple worker
        processes to run at the same time without processing the same reservation concurrently.
        """
        effective_now = now or datetime.now(UTC)
        reservations = await self.repository.lock_expired_reservations(
            session,
            expired_before=effective_now,
            batch_size=batch_size,
            reservation_ids=reservation_ids,
        )
        if not reservations:
            await session.rollback()
            return 0

        processed = 0
        for reservation in reservations:
            reservation_id = reservation.id
            try:
                # Each reservation uses a savepoint. One historically inconsistent row should
                # not poison the whole batch and prevent other valid expirations from completing.
                async with session.begin_nested():
                    await self._transition_reserved(
                        session,
                        reservation=reservation,
                        target_status=ReservationStatus.EXPIRED,
                    )
                processed += 1
            except ReservationInventoryConflictError:
                logger.warning(
                    "Skipping inconsistent reservation during expiration: %s",
                    reservation_id,
                )

        await session.commit()
        return processed

    async def _lock_owned(
        self,
        session: AsyncSession,
        *,
        reservation_id: UUID,
        visitor_session: VisitorSession,
    ) -> Reservation:
        """Lock the owned reservation row so lifecycle transitions serialize safely."""
        visitor_session_id = visitor_session.id
        user_id = visitor_session.user_id
        reservation = await self.repository.get_owned_reservation(
            session,
            reservation_id=reservation_id,
            visitor_session_id=visitor_session_id,
            user_id=user_id,
            for_update=True,
        )
        if reservation is None:
            await session.rollback()
            raise ReservationNotFoundError
        return reservation

    async def _transition_reserved(
        self,
        session: AsyncSession,
        *,
        reservation: Reservation,
        target_status: ReservationStatus,
    ) -> None:
        """Move a reserved parent and its children to one terminal/active target state."""
        if reservation.status is not ReservationStatus.RESERVED:
            return

        if reservation.reservation_type is ReservationType.QUANTITY:
            await self._transition_quantity(
                session,
                reservation_id=reservation.id,
                target_status=target_status,
            )
        else:
            await self._transition_seats(
                session,
                reservation_id=reservation.id,
                target_status=target_status,
            )

        reservation.status = target_status

    async def _transition_quantity(
        self,
        session: AsyncSession,
        *,
        reservation_id: UUID,
        target_status: ReservationStatus,
    ) -> None:
        """
        Apply a quantity reservation transition with guarded inventory updates.

        The repository methods include WHERE predicates that prevent reserved_quantity from
        becoming negative and prevent sold/reserved values from violating table constraints.
        """
        items = await self.repository.list_reservation_items(session, reservation_id)
        if not items:
            raise ReservationInventoryConflictError

        for item in items:
            if item.status is not ReservationStatus.RESERVED:
                raise ReservationInventoryConflictError

            if target_status is ReservationStatus.CONFIRMED:
                updated = await self.repository.confirm_reserved_quantity(
                    session,
                    ticket_type_id=item.ticket_type_id,
                    quantity=item.quantity,
                )
            else:
                updated = await self.repository.release_reserved_quantity(
                    session,
                    ticket_type_id=item.ticket_type_id,
                    quantity=item.quantity,
                )

            if not updated:
                raise ReservationInventoryConflictError
            item.status = target_status

    async def _transition_seats(
        self,
        session: AsyncSession,
        *,
        reservation_id: UUID,
        target_status: ReservationStatus,
    ) -> None:
        """
        Apply a seat reservation transition.

        The partial unique index treats only reserved and confirmed rows as active. Changing a
        seat row to cancelled or expired releases that seat for a future reservation.
        """
        reservation_seats = await self.repository.list_reservation_seats(
            session,
            reservation_id,
        )
        if not reservation_seats:
            raise ReservationInventoryConflictError

        for reservation_seat in reservation_seats:
            if reservation_seat.status is not ReservationStatus.RESERVED:
                raise ReservationInventoryConflictError
            reservation_seat.status = target_status

    async def _to_detail_response(
        self,
        session: AsyncSession,
        reservation: Reservation,
    ) -> ReservationDetailResponse:
        items = await self.repository.list_reservation_items(session, reservation.id)
        seats = await self.repository.list_reservation_seats(session, reservation.id)
        return ReservationDetailResponse(
            reservation_id=reservation.id,
            event_id=reservation.event_id,
            status=reservation.status,
            reservation_type=reservation.reservation_type,
            expires_at=reservation.expires_at,
            items=[
                ReservationItemDetailResponse(
                    ticket_type_id=item.ticket_type_id,
                    quantity=item.quantity,
                    status=item.status,
                )
                for item in items
            ],
            seats=[
                ReservationSeatDetailResponse(
                    seat_id=reservation_seat.seat_id,
                    status=reservation_seat.status,
                )
                for reservation_seat in seats
            ],
        )
