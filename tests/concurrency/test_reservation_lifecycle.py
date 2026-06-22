import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import async_session_factory
from app.modules.events.models import TicketType
from app.modules.reservations.enums import ReservationStatus
from app.modules.reservations.models import Reservation
from app.modules.reservations.repository import ReservationRepository
from app.modules.reservations.service import ReservationLifecycleService
from tests.integration.test_quantity_reservations import create_anonymous_session
from tests.integration.test_reservation_lifecycle import (
    create_quantity_reservation,
)


async def test_two_workers_do_not_release_the_same_reservation_twice(
    client: AsyncClient,
) -> None:
    await create_anonymous_session(client)
    event_id, ticket_type_id, first_reservation_id = await create_quantity_reservation(
        client,
        total_quantity=8,
        quantity=2,
    )
    reservation_ids = [first_reservation_id]
    for _ in range(3):
        response = await client.post(
            "/v1/reservations/quantity",
            json={
                "event_id": event_id,
                "ticket_type_id": ticket_type_id,
                "quantity": 2,
                "idempotency_key": f"worker-{len(reservation_ids)}",
            },
        )
        assert response.status_code == 201
        reservation_ids.append(str(response.json()["reservation_id"]))

    future_now = datetime.now(UTC) + timedelta(hours=1)

    async def run_worker_batch() -> int:
        async with async_session_factory() as session:
            return await ReservationLifecycleService(ReservationRepository()).expire_batch(
                session,
                batch_size=2,
                now=future_now,
                reservation_ids=[UUID(value) for value in reservation_ids],
            )

    await asyncio.gather(run_worker_batch(), run_worker_batch())

    async with async_session_factory() as session:
        ticket_type = await session.get(TicketType, UUID(ticket_type_id))
        reservations = list(
            await session.scalars(
                select(Reservation).where(
                    Reservation.id.in_([UUID(value) for value in reservation_ids])
                )
            )
        )

    assert ticket_type is not None
    assert ticket_type.reserved_quantity == 0
    assert ticket_type.sold_quantity == 0
    assert {reservation.status for reservation in reservations} == {ReservationStatus.EXPIRED}


async def test_concurrent_confirmation_does_not_duplicate_sold_quantity(
    client: AsyncClient,
) -> None:
    await create_anonymous_session(client)
    _, ticket_type_id, reservation_id = await create_quantity_reservation(
        client,
        total_quantity=10,
        quantity=4,
    )

    responses = await asyncio.gather(
        *(client.post(f"/v1/reservations/{reservation_id}/confirm") for _ in range(20))
    )

    assert {response.status_code for response in responses} == {200}
    assert {response.json()["status"] for response in responses} == {"confirmed"}

    async with async_session_factory() as session:
        ticket_type = await session.get(TicketType, UUID(ticket_type_id))

    assert ticket_type is not None
    assert ticket_type.reserved_quantity == 0
    assert ticket_type.sold_quantity == 4
    assert ticket_type.sold_quantity + ticket_type.reserved_quantity <= (ticket_type.total_quantity)
