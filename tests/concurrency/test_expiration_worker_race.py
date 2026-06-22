import asyncio
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import async_session_factory
from app.modules.events.models import TicketType
from app.modules.reservations.enums import ReservationStatus
from app.modules.reservations.models import Reservation, ReservationItem
from app.modules.reservations.repository import ReservationRepository
from app.modules.reservations.service import ReservationLifecycleService
from tests.concurrency.conftest import ConsistencyAsserter, RaceSeedFactory
from tests.integration.test_quantity_reservations import reservation_payload


async def test_two_workers_expire_each_reservation_once_logically(
    client: AsyncClient,
    race_seed_factory: RaceSeedFactory,
    assert_event_consistency: ConsistencyAsserter,
) -> None:
    reservation_count = 20
    quantity_per_reservation = 2
    seed = await race_seed_factory.quantity(
        total_quantity=reservation_count * quantity_per_reservation
    )

    responses = await asyncio.gather(
        *(
            client.post(
                "/v1/reservations/quantity",
                json=reservation_payload(
                    event_id=seed.event_id,
                    ticket_type_id=seed.ticket_type_id,
                    quantity=quantity_per_reservation,
                    idempotency_key=f"worker-race-{index}-{uuid4().hex}",
                ),
            )
            for index in range(reservation_count)
        )
    )
    assert {response.status_code for response in responses} == {201}
    reservation_ids = [UUID(response.json()["reservation_id"]) for response in responses]
    expires_at_values = [
        datetime.fromisoformat(response.json()["expires_at"]) for response in responses
    ]
    worker_now = max(expires_at_values) + timedelta(seconds=1)

    async def run_worker() -> int:
        async with async_session_factory() as session:
            return await ReservationLifecycleService(ReservationRepository()).expire_batch(
                session,
                batch_size=reservation_count,
                now=worker_now,
                reservation_ids=reservation_ids,
            )

    processed_by_workers = await asyncio.gather(run_worker(), run_worker())

    async with async_session_factory() as session:
        ticket_type = await session.get(TicketType, UUID(seed.ticket_type_id))
        reservations = list(
            await session.scalars(select(Reservation).where(Reservation.id.in_(reservation_ids)))
        )
        items = list(
            await session.scalars(
                select(ReservationItem).where(ReservationItem.reservation_id.in_(reservation_ids))
            )
        )

    assert sum(processed_by_workers) == reservation_count
    assert ticket_type is not None
    assert ticket_type.reserved_quantity == 0
    assert ticket_type.sold_quantity == 0
    assert len(reservations) == reservation_count
    assert len(items) == reservation_count
    assert {reservation.status for reservation in reservations} == {ReservationStatus.EXPIRED}
    assert {item.status for item in items} == {ReservationStatus.EXPIRED}
    await assert_event_consistency([seed.event_id], worker_now)
