from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import delete, select

from app.db.session import async_session_factory
from app.modules.events.models import Event
from app.modules.reservations.models import (
    Reservation,
    ReservationItem,
    ReservationSeat,
)
from app.modules.stress_admin.service import StressAdminService
from tests.integration.test_quantity_reservations import (
    create_anonymous_session,
    create_quantity_inventory,
)
from tests.integration.test_seat_reservations import create_seat_inventory


@dataclass(frozen=True, slots=True)
class QuantityRaceSeed:
    event_id: str
    ticket_type_id: str


@dataclass(frozen=True, slots=True)
class SeatRaceSeed:
    event_id: str
    seat_ids: list[str]


@dataclass(slots=True)
class RaceSeedFactory:
    client: AsyncClient
    event_ids: list[UUID] = field(default_factory=list)

    async def quantity(self, *, total_quantity: int) -> QuantityRaceSeed:
        event_id, ticket_type_id = await create_quantity_inventory(
            self.client,
            total_quantity=total_quantity,
        )
        self.event_ids.append(UUID(event_id))
        return QuantityRaceSeed(
            event_id=event_id,
            ticket_type_id=ticket_type_id,
        )

    async def seats(self, *, seat_count: int) -> SeatRaceSeed:
        event_id, seat_ids = await create_seat_inventory(
            self.client,
            seat_count=seat_count,
        )
        self.event_ids.append(UUID(event_id))
        return SeatRaceSeed(event_id=event_id, seat_ids=seat_ids)

    async def cleanup(self) -> None:
        if not self.event_ids:
            return

        reservation_ids = select(Reservation.id).where(Reservation.event_id.in_(self.event_ids))
        async with async_session_factory() as session:
            await session.execute(
                delete(ReservationSeat).where(ReservationSeat.reservation_id.in_(reservation_ids))
            )
            await session.execute(
                delete(ReservationItem).where(ReservationItem.reservation_id.in_(reservation_ids))
            )
            await session.execute(
                delete(Reservation).where(Reservation.event_id.in_(self.event_ids))
            )
            await session.execute(delete(Event).where(Event.id.in_(self.event_ids)))
            await session.commit()


ConsistencyAsserter = Callable[[list[str], datetime | None], Awaitable[None]]


@pytest_asyncio.fixture
async def race_seed_factory(
    client: AsyncClient,
) -> AsyncIterator[RaceSeedFactory]:
    await create_anonymous_session(client)
    factory = RaceSeedFactory(client)
    try:
        yield factory
    finally:
        await factory.cleanup()


@pytest.fixture
def assert_event_consistency() -> ConsistencyAsserter:
    async def assert_consistency(
        event_ids: list[str],
        now: datetime | None = None,
    ) -> None:
        async with async_session_factory() as session:
            result = await StressAdminService().assert_consistency(
                session,
                now=now,
                event_ids=[UUID(event_id) for event_id in event_ids],
            )

        assert result.ok, result.model_dump(mode="json")
        assert result.details == []

    return assert_consistency
