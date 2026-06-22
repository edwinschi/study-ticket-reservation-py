import asyncio
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import async_session_factory
from app.modules.events.models import TicketType
from tests.integration.test_quantity_reservations import (
    create_anonymous_session,
    create_quantity_inventory,
    reservation_payload,
)


async def test_quantity_reservation_prevents_overselling_under_concurrency(
    client: AsyncClient,
) -> None:
    await create_anonymous_session(client)
    event_id, ticket_type_id = await create_quantity_inventory(
        client,
        total_quantity=100,
    )

    responses = await asyncio.gather(
        *(
            client.post(
                "/v1/reservations/quantity",
                json=reservation_payload(
                    event_id=event_id,
                    ticket_type_id=ticket_type_id,
                    quantity=1,
                    idempotency_key=f"concurrency-{uuid4().hex}",
                ),
            )
            for _ in range(500)
        )
    )

    status_codes = [response.status_code for response in responses]
    assert status_codes.count(201) == 100
    assert status_codes.count(409) == 400
    assert set(status_codes) == {201, 409}

    async with async_session_factory() as session:
        ticket_type = await session.scalar(
            select(TicketType).where(TicketType.id == UUID(ticket_type_id))
        )

    assert ticket_type is not None
    assert ticket_type.reserved_quantity == 100
    assert ticket_type.sold_quantity + ticket_type.reserved_quantity <= (ticket_type.total_quantity)


async def test_concurrent_idempotent_requests_increment_stock_once(
    client: AsyncClient,
) -> None:
    await create_anonymous_session(client)
    event_id, ticket_type_id = await create_quantity_inventory(
        client,
        total_quantity=1,
    )
    payload = reservation_payload(
        event_id=event_id,
        ticket_type_id=ticket_type_id,
        quantity=1,
        idempotency_key=f"idempotent-concurrency-{uuid4().hex}",
    )

    responses = await asyncio.gather(
        *(client.post("/v1/reservations/quantity", json=payload) for _ in range(20))
    )

    assert {response.status_code for response in responses} == {201}
    assert len({response.json()["reservation_id"] for response in responses}) == 1

    async with async_session_factory() as session:
        ticket_type = await session.scalar(
            select(TicketType).where(TicketType.id == UUID(ticket_type_id))
        )

    assert ticket_type is not None
    assert ticket_type.reserved_quantity == 1
