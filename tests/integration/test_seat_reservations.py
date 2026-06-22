from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import async_session_factory
from app.modules.reservations.enums import ReservationStatus, ReservationType
from app.modules.reservations.models import Reservation, ReservationSeat
from tests.integration.test_quantity_reservations import create_anonymous_session
from tests.integration.test_sessions_auth import (
    TEST_PASSWORD,
    register_user,
    unique_email,
)


async def create_seat_inventory(
    client: AsyncClient,
    *,
    seat_count: int,
) -> tuple[str, list[str]]:
    starts_at = datetime.now(UTC) + timedelta(days=1)
    event_response = await client.post(
        "/v1/events",
        json={
            "name": f"Seat Event {uuid4().hex}",
            "starts_at": starts_at.isoformat(),
            "ends_at": (starts_at + timedelta(hours=2)).isoformat(),
        },
    )
    assert event_response.status_code == 201
    event_id = str(cast(dict[str, Any], event_response.json())["id"])

    seats_response = await client.post(
        f"/v1/events/{event_id}/seats",
        json={
            "seats": [
                {
                    "section": "A",
                    "row_name": "1",
                    "seat_number": str(seat_number),
                }
                for seat_number in range(1, seat_count + 1)
            ]
        },
    )
    assert seats_response.status_code == 201
    seat_ids = [
        str(seat["id"])
        for seat in cast(dict[str, list[dict[str, Any]]], seats_response.json())["seats"]
    ]
    return event_id, seat_ids


def seat_reservation_payload(
    *,
    event_id: str,
    seat_ids: list[str],
    idempotency_key: str | None = None,
) -> dict[str, str | list[str]]:
    return {
        "event_id": event_id,
        "seat_ids": seat_ids,
        "idempotency_key": idempotency_key or uuid4().hex,
    }


async def test_reserve_single_seat(client: AsyncClient) -> None:
    await create_anonymous_session(client)
    event_id, seat_ids = await create_seat_inventory(client, seat_count=1)

    response = await client.post(
        "/v1/reservations/seats",
        json=seat_reservation_payload(
            event_id=event_id,
            seat_ids=seat_ids,
        ),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["reservation_id"]
    assert body["status"] == "reserved"
    assert body["reservation_type"] == "seats"
    assert body["expires_at"]
    assert body["seats"] == [{"seat_id": seat_ids[0]}]


async def test_reserve_multiple_seats(client: AsyncClient) -> None:
    await create_anonymous_session(client)
    event_id, seat_ids = await create_seat_inventory(client, seat_count=3)

    response = await client.post(
        "/v1/reservations/seats",
        json=seat_reservation_payload(
            event_id=event_id,
            seat_ids=[seat_ids[2], seat_ids[0], seat_ids[1]],
        ),
    )

    assert response.status_code == 201
    assert {seat["seat_id"] for seat in response.json()["seats"]} == set(seat_ids)


async def test_reserve_seats_without_session_returns_401(client: AsyncClient) -> None:
    event_id, seat_ids = await create_seat_inventory(client, seat_count=1)

    response = await client.post(
        "/v1/reservations/seats",
        json=seat_reservation_payload(
            event_id=event_id,
            seat_ids=seat_ids,
        ),
    )

    assert response.status_code == 401


async def test_seat_from_another_event_returns_404(client: AsyncClient) -> None:
    await create_anonymous_session(client)
    event_id, _ = await create_seat_inventory(client, seat_count=1)
    _, other_event_seat_ids = await create_seat_inventory(client, seat_count=1)

    response = await client.post(
        "/v1/reservations/seats",
        json=seat_reservation_payload(
            event_id=event_id,
            seat_ids=other_event_seat_ids,
        ),
    )

    assert response.status_code == 404


async def test_reserving_an_active_seat_returns_409(client: AsyncClient) -> None:
    await create_anonymous_session(client)
    event_id, seat_ids = await create_seat_inventory(client, seat_count=1)

    first_response = await client.post(
        "/v1/reservations/seats",
        json=seat_reservation_payload(
            event_id=event_id,
            seat_ids=seat_ids,
        ),
    )
    second_response = await client.post(
        "/v1/reservations/seats",
        json=seat_reservation_payload(
            event_id=event_id,
            seat_ids=seat_ids,
        ),
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 409


async def test_seat_reservation_is_idempotent(client: AsyncClient) -> None:
    await create_anonymous_session(client)
    event_id, seat_ids = await create_seat_inventory(client, seat_count=2)
    payload = seat_reservation_payload(
        event_id=event_id,
        seat_ids=seat_ids,
        idempotency_key=uuid4().hex,
    )

    first_response = await client.post("/v1/reservations/seats", json=payload)
    second_response = await client.post("/v1/reservations/seats", json=payload)

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert second_response.json() == first_response.json()

    async with async_session_factory() as session:
        reservation_seats = list(
            await session.scalars(
                select(ReservationSeat).where(
                    ReservationSeat.reservation_id == UUID(first_response.json()["reservation_id"])
                )
            )
        )

    assert len(reservation_seats) == 2


async def test_expired_seat_reservation_is_released_inside_transaction(
    client: AsyncClient,
) -> None:
    await create_anonymous_session(client)
    session_response = await client.get("/v1/me/session")
    visitor_session_id = UUID(session_response.json()["id"])
    event_id, seat_ids = await create_seat_inventory(client, seat_count=1)
    old_reservation_id = uuid4()
    expired_at = datetime.now(UTC) - timedelta(minutes=1)

    async with async_session_factory() as session:
        session.add(
            Reservation(
                id=old_reservation_id,
                event_id=UUID(event_id),
                visitor_session_id=visitor_session_id,
                status=ReservationStatus.RESERVED,
                reservation_type=ReservationType.SEATS,
                idempotency_key=uuid4().hex,
                expires_at=expired_at,
            )
        )
        await session.flush()
        session.add(
            ReservationSeat(
                reservation_id=old_reservation_id,
                seat_id=UUID(seat_ids[0]),
                status=ReservationStatus.RESERVED,
                expires_at=expired_at,
            )
        )
        await session.commit()

    response = await client.post(
        "/v1/reservations/seats",
        json=seat_reservation_payload(
            event_id=event_id,
            seat_ids=seat_ids,
        ),
    )

    assert response.status_code == 201

    async with async_session_factory() as session:
        old_reservation = await session.get(Reservation, old_reservation_id)
        old_reservation_seat = await session.scalar(
            select(ReservationSeat).where(ReservationSeat.reservation_id == old_reservation_id)
        )

    assert old_reservation is not None
    assert old_reservation.status is ReservationStatus.EXPIRED
    assert old_reservation_seat is not None
    assert old_reservation_seat.status is ReservationStatus.EXPIRED


async def test_authenticated_session_populates_reservation_user_id(
    client: AsyncClient,
) -> None:
    await create_anonymous_session(client)
    email = unique_email()
    user = await register_user(client, email)
    login_response = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
    )
    assert login_response.status_code == 200
    event_id, seat_ids = await create_seat_inventory(client, seat_count=1)

    response = await client.post(
        "/v1/reservations/seats",
        json=seat_reservation_payload(
            event_id=event_id,
            seat_ids=seat_ids,
        ),
    )

    assert response.status_code == 201
    async with async_session_factory() as session:
        reservation = await session.get(
            Reservation,
            UUID(response.json()["reservation_id"]),
        )

    assert reservation is not None
    assert reservation.user_id == UUID(user["id"])
