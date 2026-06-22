from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.events.models import Event, Seat, TicketType
from app.modules.reservations.models import (
    Reservation,
    ReservationItem,
    ReservationSeat,
)
from app.modules.stress_admin.repository import (
    DuplicateActiveSeatViolation,
    OrphanReservationChildViolation,
    StaleActiveReservationViolation,
    StressConsistencyRepository,
    TicketQuantityViolation,
)
from app.modules.stress_admin.schemas import (
    StressConsistencyChecks,
    StressConsistencyDetail,
    StressConsistencyResponse,
    StressResetResponse,
    StressSeedResponse,
)

STRESS_EVENT_PREFIX = "__stress_seed__:"
CONSISTENCY_DETAIL_LIMIT = 100
STALE_RESERVATION_TOLERANCE = timedelta(seconds=60)


def utc_now() -> datetime:
    return datetime.now(UTC)


class StressAdminService:
    def __init__(
        self,
        consistency_repository: StressConsistencyRepository | None = None,
    ) -> None:
        self.consistency_repository = consistency_repository or StressConsistencyRepository()

    async def seed(self, session: AsyncSession) -> StressSeedResponse:
        now = datetime.now(UTC)
        event_id = uuid4()
        event = Event(
            id=event_id,
            name=f"{STRESS_EVENT_PREFIX}{uuid4().hex}",
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=1, hours=3),
        )
        ticket_type = TicketType(
            event_id=event_id,
            name="General Admission",
            total_quantity=1000,
        )
        seats = [
            Seat(
                event_id=event_id,
                section="A",
                row_name=str(row_number),
                seat_number=str(seat_number),
            )
            for row_number in range(1, 11)
            for seat_number in range(1, 11)
        ]

        session.add_all([event, ticket_type, *seats])
        await session.commit()

        return StressSeedResponse(
            event_id=event_id,
            ticket_type_id=ticket_type.id,
            seat_ids=[seat.id for seat in seats],
        )

    async def reset(self, session: AsyncSession) -> StressResetResponse:
        stress_event_ids = select(Event.id).where(Event.name.startswith(STRESS_EVENT_PREFIX))
        stress_reservation_ids = select(Reservation.id).where(
            Reservation.event_id.in_(stress_event_ids)
        )
        events_deleted = await session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.name.startswith(STRESS_EVENT_PREFIX))
        )

        await session.execute(
            delete(ReservationSeat).where(
                ReservationSeat.reservation_id.in_(stress_reservation_ids)
            )
        )
        await session.execute(
            delete(ReservationItem).where(
                ReservationItem.reservation_id.in_(stress_reservation_ids)
            )
        )
        await session.execute(delete(Reservation).where(Reservation.id.in_(stress_reservation_ids)))
        await session.execute(delete(Event).where(Event.id.in_(stress_event_ids)))
        await session.commit()

        return StressResetResponse(events_deleted=events_deleted or 0)

    async def assert_consistency(
        self,
        session: AsyncSession,
        *,
        now: datetime | None = None,
        event_ids: list[UUID] | None = None,
    ) -> StressConsistencyResponse:
        checked_at = now or utc_now()
        repository = self.consistency_repository

        negative_quantities = await repository.list_negative_ticket_quantities(
            session,
            limit=CONSISTENCY_DETAIL_LIMIT,
            event_ids=event_ids,
        )
        oversold_quantities = await repository.list_oversold_ticket_quantities(
            session,
            limit=CONSISTENCY_DETAIL_LIMIT,
            event_ids=event_ids,
        )
        duplicate_active_seats = await repository.list_duplicate_active_seats(
            session,
            limit=CONSISTENCY_DETAIL_LIMIT,
            event_ids=event_ids,
        )
        orphan_items = await repository.list_orphan_reservation_items(
            session,
            limit=CONSISTENCY_DETAIL_LIMIT,
        )
        orphan_seats = await repository.list_orphan_reservation_seats(
            session,
            limit=CONSISTENCY_DETAIL_LIMIT,
        )
        stale_reservations = await repository.list_stale_active_reservations(
            session,
            expired_before=checked_at - STALE_RESERVATION_TOLERANCE,
            limit=CONSISTENCY_DETAIL_LIMIT,
            event_ids=event_ids,
        )

        checks = StressConsistencyChecks(
            ticket_quantity_not_oversold=not oversold_quantities,
            ticket_quantity_not_negative=not negative_quantities,
            no_duplicate_active_seats=not duplicate_active_seats,
            no_orphan_reservation_items=not orphan_items,
            no_orphan_reservation_seats=not orphan_seats,
            no_stale_active_reservations=not stale_reservations,
        )
        details = [
            *self._ticket_quantity_details(
                "ticket_quantity_not_oversold",
                "Ticket type has more sold and reserved tickets than its total quantity",
                oversold_quantities,
            ),
            *self._ticket_quantity_details(
                "ticket_quantity_not_negative",
                "Ticket type has a negative quantity",
                negative_quantities,
            ),
            *self._duplicate_seat_details(duplicate_active_seats),
            *self._orphan_details(
                "no_orphan_reservation_items",
                "Reservation item points to a missing reservation",
                "reservation_item_id",
                orphan_items,
            ),
            *self._orphan_details(
                "no_orphan_reservation_seats",
                "Reservation seat points to a missing reservation",
                "reservation_seat_id",
                orphan_seats,
            ),
            *self._stale_reservation_details(
                stale_reservations,
                checked_at=checked_at,
            ),
        ]
        await session.rollback()
        return StressConsistencyResponse(
            ok=all(
                (
                    checks.ticket_quantity_not_oversold,
                    checks.ticket_quantity_not_negative,
                    checks.no_duplicate_active_seats,
                    checks.no_orphan_reservation_items,
                    checks.no_orphan_reservation_seats,
                    checks.no_stale_active_reservations,
                )
            ),
            checks=checks,
            details=details,
        )

    @staticmethod
    def _ticket_quantity_details(
        check: str,
        message: str,
        violations: list[TicketQuantityViolation],
    ) -> list[StressConsistencyDetail]:
        return [
            StressConsistencyDetail(
                check=check,
                message=message,
                data={
                    "ticket_type_id": str(violation.ticket_type_id),
                    "total_quantity": violation.total_quantity,
                    "sold_quantity": violation.sold_quantity,
                    "reserved_quantity": violation.reserved_quantity,
                },
            )
            for violation in violations
        ]

    @staticmethod
    def _duplicate_seat_details(
        violations: list[DuplicateActiveSeatViolation],
    ) -> list[StressConsistencyDetail]:
        return [
            StressConsistencyDetail(
                check="no_duplicate_active_seats",
                message="Seat has more than one active reservation",
                data={
                    "seat_id": str(violation.seat_id),
                    "active_reservation_count": violation.active_reservation_count,
                },
            )
            for violation in violations
        ]

    @staticmethod
    def _orphan_details(
        check: str,
        message: str,
        record_id_key: str,
        violations: list[OrphanReservationChildViolation],
    ) -> list[StressConsistencyDetail]:
        return [
            StressConsistencyDetail(
                check=check,
                message=message,
                data={
                    record_id_key: str(violation.record_id),
                    "reservation_id": str(violation.reservation_id),
                },
            )
            for violation in violations
        ]

    @staticmethod
    def _stale_reservation_details(
        violations: list[StaleActiveReservationViolation],
        *,
        checked_at: datetime,
    ) -> list[StressConsistencyDetail]:
        return [
            StressConsistencyDetail(
                check="no_stale_active_reservations",
                message="Active reservation has remained expired beyond the worker tolerance",
                data={
                    "reservation_id": str(violation.reservation_id),
                    "expires_at": violation.expires_at.isoformat(),
                    "seconds_overdue": int((checked_at - violation.expires_at).total_seconds()),
                },
            )
            for violation in violations
        ]
