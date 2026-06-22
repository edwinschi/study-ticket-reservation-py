"""Concurrency tests for quantity reservations.

These tests intentionally create more concurrent requests than available inventory. They prove
that the atomic PostgreSQL UPDATE admits only real stock winners and returns 409 for the rest.
"""

import asyncio
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import func, select

from app.db.session import async_session_factory
from app.modules.events.models import TicketType
from app.modules.reservations.models import Reservation
from tests.concurrency.conftest import ConsistencyAsserter, RaceSeedFactory
from tests.integration.test_quantity_reservations import reservation_payload


async def test_500_requests_reserve_exactly_available_quantity(
    client: AsyncClient,
    race_seed_factory: RaceSeedFactory,
    assert_event_consistency: ConsistencyAsserter,
) -> None:
    seed = await race_seed_factory.quantity(total_quantity=50)

    # All requests target the same ticket_type row. If the service used a read-then-write stock
    # check, more than 50 requests could observe availability before any one commits.
    responses = await asyncio.gather(
        *(
            client.post(
                "/v1/reservations/quantity",
                json=reservation_payload(
                    event_id=seed.event_id,
                    ticket_type_id=seed.ticket_type_id,
                    quantity=1,
                    idempotency_key=f"quantity-race-{uuid4().hex}",
                ),
            )
            for _ in range(500)
        )
    )

    status_codes = [response.status_code for response in responses]
    assert status_codes.count(201) == 50
    assert status_codes.count(409) == 450
    assert set(status_codes) == {201, 409}

    # The database state is the real assertion. HTTP status counts are not enough if inventory
    # counters drift under concurrency.
    async with async_session_factory() as session:
        ticket_type = await session.get(TicketType, UUID(seed.ticket_type_id))

    assert ticket_type is not None
    assert ticket_type.total_quantity == 50
    assert ticket_type.reserved_quantity == 50
    assert ticket_type.sold_quantity == 0
    assert ticket_type.sold_quantity + ticket_type.reserved_quantity <= 50
    await assert_event_consistency([seed.event_id], None)


async def test_100_idempotent_requests_create_one_real_reservation(
    client: AsyncClient,
    race_seed_factory: RaceSeedFactory,
    assert_event_consistency: ConsistencyAsserter,
) -> None:
    seed = await race_seed_factory.quantity(total_quantity=10)
    # Every request uses the same idempotency key. The correct result is one real reservation
    # and many replayed 201 responses, not 100 stock increments.
    payload = reservation_payload(
        event_id=seed.event_id,
        ticket_type_id=seed.ticket_type_id,
        quantity=1,
        idempotency_key=f"quantity-idempotency-race-{uuid4().hex}",
    )

    responses = await asyncio.gather(
        *(client.post("/v1/reservations/quantity", json=payload) for _ in range(100))
    )

    assert {response.status_code for response in responses} == {201}
    reservation_ids = {str(response.json()["reservation_id"]) for response in responses}
    assert len(reservation_ids) == 1

    async with async_session_factory() as session:
        ticket_type = await session.get(TicketType, UUID(seed.ticket_type_id))
        reservation_count = await session.scalar(
            select(func.count())
            .select_from(Reservation)
            .where(Reservation.event_id == UUID(seed.event_id))
        )

    assert ticket_type is not None
    assert ticket_type.reserved_quantity == 1
    assert ticket_type.sold_quantity == 0
    assert reservation_count == 1
    await assert_event_consistency([seed.event_id], None)
