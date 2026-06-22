from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from httpx import AsyncClient

from app.db.session import async_session_factory
from app.modules.reservations.enums import ReservationStatus, ReservationType
from app.modules.reservations.models import Reservation, ReservationSeat
from app.modules.sessions.models import VisitorSession


def event_payload(name: str | None = None) -> dict[str, str]:
    starts_at = datetime.now(UTC) + timedelta(days=1)
    return {
        "name": name or f"Event {uuid4().hex}",
        "starts_at": starts_at.isoformat(),
        "ends_at": (starts_at + timedelta(hours=3)).isoformat(),
    }


async def create_event(client: AsyncClient, name: str | None = None) -> dict[str, Any]:
    response = await client.post("/v1/events", json=event_payload(name))
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


async def test_create_and_get_event(client: AsyncClient) -> None:
    created_event = await create_event(client)

    response = await client.get(f"/v1/events/{created_event['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created_event["id"]
    assert response.json()["name"] == created_event["name"]


async def test_create_ticket_type_and_seats(client: AsyncClient) -> None:
    event = await create_event(client)
    event_id = event["id"]

    ticket_type_response = await client.post(
        f"/v1/events/{event_id}/ticket-types",
        json={"name": "General Admission", "total_quantity": 250},
    )
    seats_response = await client.post(
        f"/v1/events/{event_id}/seats",
        json={
            "seats": [
                {"section": "A", "row_name": "1", "seat_number": "1"},
                {"section": "A", "row_name": "1", "seat_number": "2"},
            ]
        },
    )

    assert ticket_type_response.status_code == 201
    assert ticket_type_response.json()["total_quantity"] == 250
    assert ticket_type_response.json()["sold_quantity"] == 0
    assert ticket_type_response.json()["reserved_quantity"] == 0
    assert seats_response.status_code == 201
    assert len(seats_response.json()["seats"]) == 2


async def test_duplicate_seat_returns_409(client: AsyncClient) -> None:
    event = await create_event(client)
    endpoint = f"/v1/events/{event['id']}/seats"
    payload = {
        "seats": [
            {"section": "A", "row_name": "1", "seat_number": "1"},
        ]
    }

    first_response = await client.post(endpoint, json=payload)
    second_response = await client.post(endpoint, json=payload)

    assert first_response.status_code == 201
    assert second_response.status_code == 409


async def test_inventory_reports_quantity_and_active_seat_statuses(
    client: AsyncClient,
) -> None:
    event = await create_event(client)
    event_id = event["id"]
    ticket_type_response = await client.post(
        f"/v1/events/{event_id}/ticket-types",
        json={"name": "Standard", "total_quantity": 20},
    )
    seats_response = await client.post(
        f"/v1/events/{event_id}/seats",
        json={
            "seats": [
                {"section": "A", "row_name": "1", "seat_number": "1"},
                {"section": "A", "row_name": "1", "seat_number": "2"},
                {"section": "A", "row_name": "1", "seat_number": "3"},
            ]
        },
    )
    seats = seats_response.json()["seats"]
    now = datetime.now(UTC)
    visitor_session_id = uuid4()
    reserved_reservation_id = uuid4()
    confirmed_reservation_id = uuid4()

    async with async_session_factory() as session:
        session.add(
            VisitorSession(
                id=visitor_session_id,
                anonymous_token_hash=uuid4().hex,
                last_seen_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
        await session.flush()
        session.add_all(
            [
                Reservation(
                    id=reserved_reservation_id,
                    event_id=UUID(event_id),
                    visitor_session_id=visitor_session_id,
                    status=ReservationStatus.RESERVED,
                    reservation_type=ReservationType.SEATS,
                    idempotency_key=uuid4().hex,
                    expires_at=now + timedelta(minutes=15),
                ),
                Reservation(
                    id=confirmed_reservation_id,
                    event_id=UUID(event_id),
                    visitor_session_id=visitor_session_id,
                    status=ReservationStatus.CONFIRMED,
                    reservation_type=ReservationType.SEATS,
                    idempotency_key=uuid4().hex,
                    expires_at=now + timedelta(minutes=15),
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                ReservationSeat(
                    reservation_id=reserved_reservation_id,
                    seat_id=UUID(seats[0]["id"]),
                    status=ReservationStatus.RESERVED,
                    expires_at=now + timedelta(minutes=15),
                ),
                ReservationSeat(
                    reservation_id=confirmed_reservation_id,
                    seat_id=UUID(seats[1]["id"]),
                    status=ReservationStatus.CONFIRMED,
                    expires_at=now + timedelta(minutes=15),
                ),
            ]
        )
        await session.commit()

    response = await client.get(f"/v1/events/{event_id}/inventory")

    assert response.status_code == 200
    inventory = response.json()
    assert inventory["ticket_types"] == [
        {
            "id": ticket_type_response.json()["id"],
            "name": "Standard",
            "total": 20,
            "sold": 0,
            "reserved": 0,
            "available": 20,
        }
    ]
    assert {seat["seat_number"]: seat["status"] for seat in inventory["seats"]} == {
        "1": "reserved",
        "2": "confirmed",
        "3": "available",
    }


async def test_stress_seed_and_safe_reset(client: AsyncClient) -> None:
    await client.post("/v1/admin/stress/reset")
    regular_event = await create_event(client, name=f"Regular {uuid4().hex}")

    seed_response = await client.post("/v1/admin/stress/seed")

    assert seed_response.status_code == 201
    seed = seed_response.json()
    assert seed["event_id"]
    assert seed["ticket_type_id"]
    assert len(seed["seat_ids"]) == 100

    inventory_response = await client.get(f"/v1/events/{seed['event_id']}/inventory")
    assert inventory_response.status_code == 200
    assert inventory_response.json()["ticket_types"][0]["total"] == 1000
    assert len(inventory_response.json()["seats"]) == 100

    reset_response = await client.post("/v1/admin/stress/reset")

    assert reset_response.status_code == 200
    assert reset_response.json()["events_deleted"] == 1
    assert (await client.get(f"/v1/events/{seed['event_id']}")).status_code == 404
    assert (await client.get(f"/v1/events/{regular_event['id']}")).status_code == 200
