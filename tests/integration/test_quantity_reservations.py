from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from httpx import AsyncClient


async def create_quantity_inventory(
    client: AsyncClient,
    *,
    total_quantity: int,
) -> tuple[str, str]:
    starts_at = datetime.now(UTC) + timedelta(days=1)
    event_response = await client.post(
        "/v1/events",
        json={
            "name": f"Quantity Event {uuid4().hex}",
            "starts_at": starts_at.isoformat(),
            "ends_at": (starts_at + timedelta(hours=2)).isoformat(),
        },
    )
    assert event_response.status_code == 201
    event_id = cast(dict[str, Any], event_response.json())["id"]

    ticket_type_response = await client.post(
        f"/v1/events/{event_id}/ticket-types",
        json={
            "name": "General Admission",
            "total_quantity": total_quantity,
        },
    )
    assert ticket_type_response.status_code == 201
    ticket_type_id = cast(dict[str, Any], ticket_type_response.json())["id"]
    return str(event_id), str(ticket_type_id)


async def create_anonymous_session(client: AsyncClient) -> None:
    response = await client.post("/v1/sessions/anonymous")
    assert response.status_code == 201


def reservation_payload(
    *,
    event_id: str,
    ticket_type_id: str,
    quantity: int,
    idempotency_key: str | None = None,
) -> dict[str, str | int]:
    return {
        "event_id": event_id,
        "ticket_type_id": ticket_type_id,
        "quantity": quantity,
        "idempotency_key": idempotency_key or uuid4().hex,
    }


async def test_quantity_reservation_succeeds(client: AsyncClient) -> None:
    await create_anonymous_session(client)
    event_id, ticket_type_id = await create_quantity_inventory(
        client,
        total_quantity=10,
    )

    response = await client.post(
        "/v1/reservations/quantity",
        json=reservation_payload(
            event_id=event_id,
            ticket_type_id=ticket_type_id,
            quantity=2,
        ),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["reservation_id"]
    assert body["status"] == "reserved"
    assert body["reservation_type"] == "quantity"
    assert body["expires_at"]
    assert body["items"] == [
        {
            "ticket_type_id": ticket_type_id,
            "quantity": 2,
        }
    ]


async def test_quantity_reservation_without_session_returns_401(
    client: AsyncClient,
) -> None:
    event_id, ticket_type_id = await create_quantity_inventory(
        client,
        total_quantity=10,
    )

    response = await client.post(
        "/v1/reservations/quantity",
        json=reservation_payload(
            event_id=event_id,
            ticket_type_id=ticket_type_id,
            quantity=1,
        ),
    )

    assert response.status_code == 401


async def test_quantity_reservation_above_available_returns_409(
    client: AsyncClient,
) -> None:
    await create_anonymous_session(client)
    event_id, ticket_type_id = await create_quantity_inventory(
        client,
        total_quantity=3,
    )

    response = await client.post(
        "/v1/reservations/quantity",
        json=reservation_payload(
            event_id=event_id,
            ticket_type_id=ticket_type_id,
            quantity=4,
        ),
    )

    assert response.status_code == 409


async def test_quantity_reservation_is_idempotent(client: AsyncClient) -> None:
    await create_anonymous_session(client)
    event_id, ticket_type_id = await create_quantity_inventory(
        client,
        total_quantity=10,
    )
    payload = reservation_payload(
        event_id=event_id,
        ticket_type_id=ticket_type_id,
        quantity=3,
        idempotency_key=uuid4().hex,
    )

    first_response = await client.post("/v1/reservations/quantity", json=payload)
    second_response = await client.post("/v1/reservations/quantity", json=payload)
    inventory_response = await client.get(f"/v1/events/{event_id}/inventory")

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert second_response.json() == first_response.json()

    ticket_inventory = inventory_response.json()["ticket_types"][0]
    assert ticket_inventory["reserved"] == 3
    assert ticket_inventory["available"] == 7
