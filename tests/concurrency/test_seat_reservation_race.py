"""Concurrency tests for seat reservations.

The important invariant is not only the number of successful HTTP responses. After the race,
each physical seat must have at most one active reservation row.
"""

import asyncio
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import func, select

from app.db.session import async_session_factory
from app.modules.reservations.enums import ReservationStatus
from app.modules.reservations.models import ReservationSeat
from tests.concurrency.conftest import ConsistencyAsserter, RaceSeedFactory
from tests.integration.test_seat_reservations import seat_reservation_payload

ACTIVE_STATUSES = [
    ReservationStatus.RESERVED,
    ReservationStatus.CONFIRMED,
]


async def test_300_requests_never_duplicate_ten_active_seats(
    client: AsyncClient,
    race_seed_factory: RaceSeedFactory,
    assert_event_consistency: ConsistencyAsserter,
) -> None:
    seed = await race_seed_factory.seats(seat_count=10)

    # Requests cycle through only ten seats, creating predictable contention. The partial unique
    # index must ensure that at most one active row exists per seat.
    responses = await asyncio.gather(
        *(
            client.post(
                "/v1/reservations/seats",
                json=seat_reservation_payload(
                    event_id=seed.event_id,
                    seat_ids=[seed.seat_ids[index % len(seed.seat_ids)]],
                    idempotency_key=f"seat-race-{index}-{uuid4().hex}",
                ),
            )
            for index in range(300)
        )
    )

    status_codes = [response.status_code for response in responses]
    assert status_codes.count(201) == 10
    assert status_codes.count(409) == 290
    assert set(status_codes) == {201, 409}

    # This aggregate query checks the same invariant as the production consistency endpoint,
    # but scoped to the seats created by this test.
    async with async_session_factory() as session:
        active_rows = list(
            await session.execute(
                select(
                    ReservationSeat.seat_id,
                    func.count(ReservationSeat.id).label("active_count"),
                )
                .where(
                    ReservationSeat.seat_id.in_([UUID(seat_id) for seat_id in seed.seat_ids]),
                    ReservationSeat.status.in_(ACTIVE_STATUSES),
                )
                .group_by(ReservationSeat.seat_id)
                .order_by(ReservationSeat.seat_id)
            )
        )

    assert len(active_rows) == status_codes.count(201)
    assert all(row.active_count == 1 for row in active_rows)
    await assert_event_consistency([seed.event_id], None)


async def test_200_requests_for_same_seat_have_one_winner(
    client: AsyncClient,
    race_seed_factory: RaceSeedFactory,
    assert_event_consistency: ConsistencyAsserter,
) -> None:
    seed = await race_seed_factory.seats(seat_count=1)
    seat_id = seed.seat_ids[0]

    # This is the highest-contention seat case: 200 independent idempotency keys all race for
    # the same seat, so there should be exactly one winner.
    responses = await asyncio.gather(
        *(
            client.post(
                "/v1/reservations/seats",
                json=seat_reservation_payload(
                    event_id=seed.event_id,
                    seat_ids=[seat_id],
                    idempotency_key=f"same-seat-race-{index}-{uuid4().hex}",
                ),
            )
            for index in range(200)
        )
    )

    status_codes = [response.status_code for response in responses]
    assert status_codes.count(201) == 1
    assert status_codes.count(409) == 199
    assert set(status_codes) == {201, 409}

    async with async_session_factory() as session:
        active_count = await session.scalar(
            select(func.count())
            .select_from(ReservationSeat)
            .where(
                ReservationSeat.seat_id == UUID(seat_id),
                ReservationSeat.status.in_(ACTIVE_STATUSES),
            )
        )

    assert active_count == 1
    await assert_event_consistency([seed.event_id], None)
