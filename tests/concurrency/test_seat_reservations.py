import asyncio
import random
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import func, select

from app.db.session import async_session_factory
from app.modules.reservations.enums import ReservationStatus
from app.modules.reservations.models import ReservationSeat
from tests.integration.test_quantity_reservations import create_anonymous_session
from tests.integration.test_seat_reservations import (
    create_seat_inventory,
    seat_reservation_payload,
)


async def test_concurrent_random_seat_reservations_never_duplicate_active_seat(
    client: AsyncClient,
) -> None:
    await create_anonymous_session(client)
    event_id, seat_ids = await create_seat_inventory(client, seat_count=20)
    random_generator = random.Random(20260622)
    requests = [
        random_generator.sample(seat_ids, k=random_generator.randint(1, 3)) for _ in range(500)
    ]

    responses = await asyncio.gather(
        *(
            client.post(
                "/v1/reservations/seats",
                json=seat_reservation_payload(
                    event_id=event_id,
                    seat_ids=request_seat_ids,
                    idempotency_key=f"seat-concurrency-{uuid4().hex}",
                ),
            )
            for request_seat_ids in requests
        )
    )

    status_codes = [response.status_code for response in responses]
    assert set(status_codes) <= {201, 409}
    assert 201 in status_codes
    assert 409 in status_codes
    assert 500 not in status_codes

    async with async_session_factory() as session:
        duplicate_active_seats = list(
            await session.execute(
                select(
                    ReservationSeat.seat_id,
                    func.count(ReservationSeat.id).label("active_count"),
                )
                .where(
                    ReservationSeat.status.in_(
                        [
                            ReservationStatus.RESERVED,
                            ReservationStatus.CONFIRMED,
                        ]
                    )
                )
                .group_by(ReservationSeat.seat_id)
                .having(func.count(ReservationSeat.id) > 1)
            )
        )

    assert duplicate_active_seats == []


async def test_reversed_seat_order_does_not_deadlock(
    client: AsyncClient,
) -> None:
    await create_anonymous_session(client)

    for _ in range(10):
        event_id, seat_ids = await create_seat_inventory(client, seat_count=2)
        request_a = client.post(
            "/v1/reservations/seats",
            json=seat_reservation_payload(
                event_id=event_id,
                seat_ids=[seat_ids[0], seat_ids[1]],
                idempotency_key=f"deadlock-a-{uuid4().hex}",
            ),
        )
        request_b = client.post(
            "/v1/reservations/seats",
            json=seat_reservation_payload(
                event_id=event_id,
                seat_ids=[seat_ids[1], seat_ids[0]],
                idempotency_key=f"deadlock-b-{uuid4().hex}",
            ),
        )

        responses = await asyncio.wait_for(
            asyncio.gather(request_a, request_b),
            timeout=10,
        )

        assert sorted(response.status_code for response in responses) == [201, 409]


async def test_concurrent_idempotent_seat_requests_create_one_reservation(
    client: AsyncClient,
) -> None:
    await create_anonymous_session(client)
    event_id, seat_ids = await create_seat_inventory(client, seat_count=2)
    payload = seat_reservation_payload(
        event_id=event_id,
        seat_ids=[seat_ids[1], seat_ids[0]],
        idempotency_key=f"seat-idempotency-{uuid4().hex}",
    )

    responses = await asyncio.gather(
        *(client.post("/v1/reservations/seats", json=payload) for _ in range(20))
    )

    assert {response.status_code for response in responses} == {201}
    assert len({response.json()["reservation_id"] for response in responses}) == 1

    reservation_id = responses[0].json()["reservation_id"]
    async with async_session_factory() as session:
        reservation_seat_count = await session.scalar(
            select(func.count())
            .select_from(ReservationSeat)
            .where(ReservationSeat.reservation_id == UUID(reservation_id))
        )

    assert reservation_seat_count == 2
