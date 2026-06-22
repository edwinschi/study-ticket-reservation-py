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
        visitor_session_id = visitor_session.id
        user_id = visitor_session.user_id
        existing = await self.repository.get_quantity_by_idempotency_key(
            session,
            visitor_session_id=visitor_session_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return self._to_response(existing)

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
            self.repository.add_reservation(session, reservation)
            await session.flush()
            self.repository.add_reservation_item(session, reservation_item)
            await session.commit()
        except IntegrityError:
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
        visitor_session_id = visitor_session.id
        user_id = visitor_session.user_id
        existing = await self.repository.get_seats_by_idempotency_key(
            session,
            visitor_session_id=visitor_session_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return self._to_response(existing)

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
    def __init__(self, repository: ReservationRepository) -> None:
        self.repository = repository

    async def get(
        self,
        session: AsyncSession,
        *,
        reservation_id: UUID,
        visitor_session: VisitorSession,
    ) -> ReservationDetailResponse:
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
        reservation = await self._lock_owned(
            session,
            reservation_id=reservation_id,
            visitor_session=visitor_session,
        )

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
        reservation = await self._lock_owned(
            session,
            reservation_id=reservation_id,
            visitor_session=visitor_session,
        )

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
