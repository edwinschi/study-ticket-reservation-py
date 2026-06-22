import asyncio
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import async_session_factory
from app.modules.events.models import TicketType
from app.modules.reservations.enums import ReservationStatus
from app.modules.reservations.models import Reservation, ReservationItem
from tests.concurrency.conftest import ConsistencyAsserter, RaceSeedFactory
from tests.integration.test_quantity_reservations import reservation_payload


async def create_reserved_quantity(
    client: AsyncClient,
    race_seed_factory: RaceSeedFactory,
    *,
    quantity: int,
) -> tuple[str, str, str]:
    seed = await race_seed_factory.quantity(total_quantity=10)
    response = await client.post(
        "/v1/reservations/quantity",
        json=reservation_payload(
            event_id=seed.event_id,
            ticket_type_id=seed.ticket_type_id,
            quantity=quantity,
            idempotency_key=f"lifecycle-race-{uuid4().hex}",
        ),
    )
    assert response.status_code == 201
    return (
        seed.event_id,
        seed.ticket_type_id,
        str(response.json()["reservation_id"]),
    )


async def test_100_concurrent_confirms_move_stock_once(
    client: AsyncClient,
    race_seed_factory: RaceSeedFactory,
    assert_event_consistency: ConsistencyAsserter,
) -> None:
    event_id, ticket_type_id, reservation_id = await create_reserved_quantity(
        client,
        race_seed_factory,
        quantity=4,
    )

    responses = await asyncio.gather(
        *(client.post(f"/v1/reservations/{reservation_id}/confirm") for _ in range(100))
    )

    assert {response.status_code for response in responses} == {200}
    assert {response.json()["status"] for response in responses} == {"confirmed"}

    async with async_session_factory() as session:
        ticket_type = await session.get(TicketType, UUID(ticket_type_id))
        reservation = await session.get(Reservation, UUID(reservation_id))
        item = await session.scalar(
            select(ReservationItem).where(ReservationItem.reservation_id == UUID(reservation_id))
        )

    assert ticket_type is not None
    assert ticket_type.reserved_quantity == 0
    assert ticket_type.sold_quantity == 4
    assert reservation is not None
    assert reservation.status is ReservationStatus.CONFIRMED
    assert item is not None
    assert item.status is ReservationStatus.CONFIRMED
    await assert_event_consistency([event_id], None)


async def test_100_concurrent_cancels_release_stock_once(
    client: AsyncClient,
    race_seed_factory: RaceSeedFactory,
    assert_event_consistency: ConsistencyAsserter,
) -> None:
    event_id, ticket_type_id, reservation_id = await create_reserved_quantity(
        client,
        race_seed_factory,
        quantity=4,
    )

    responses = await asyncio.gather(
        *(client.post(f"/v1/reservations/{reservation_id}/cancel") for _ in range(100))
    )

    assert {response.status_code for response in responses} == {200}
    assert {response.json()["status"] for response in responses} == {"cancelled"}

    async with async_session_factory() as session:
        ticket_type = await session.get(TicketType, UUID(ticket_type_id))
        reservation = await session.get(Reservation, UUID(reservation_id))
        item = await session.scalar(
            select(ReservationItem).where(ReservationItem.reservation_id == UUID(reservation_id))
        )

    assert ticket_type is not None
    assert ticket_type.reserved_quantity == 0
    assert ticket_type.sold_quantity == 0
    assert reservation is not None
    assert reservation.status is ReservationStatus.CANCELLED
    assert item is not None
    assert item.status is ReservationStatus.CANCELLED
    await assert_event_consistency([event_id], None)
