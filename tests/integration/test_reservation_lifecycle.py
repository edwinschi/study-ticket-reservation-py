from datetime import UTC, datetime, timedelta
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import async_session_factory
from app.modules.reservations.enums import ReservationStatus
from app.modules.reservations.models import Reservation
from app.modules.reservations.repository import ReservationRepository
from app.modules.reservations.service import ReservationLifecycleService
from tests.integration.test_quantity_reservations import (
    create_anonymous_session,
    create_quantity_inventory,
    reservation_payload,
)
from tests.integration.test_seat_reservations import (
    create_seat_inventory,
    seat_reservation_payload,
)
from tests.integration.test_sessions_auth import (
    TEST_PASSWORD,
    register_user,
    unique_email,
)


async def expire_reservations(
    expired_before: datetime,
    reservation_ids: list[str],
) -> int:
    async with async_session_factory() as session:
        return await ReservationLifecycleService(ReservationRepository()).expire_batch(
            session,
            batch_size=len(reservation_ids),
            now=expired_before,
            reservation_ids=[UUID(value) for value in reservation_ids],
        )


async def create_quantity_reservation(
    client: AsyncClient,
    *,
    total_quantity: int = 10,
    quantity: int = 3,
) -> tuple[str, str, str]:
    event_id, ticket_type_id = await create_quantity_inventory(
        client,
        total_quantity=total_quantity,
    )
    response = await client.post(
        "/v1/reservations/quantity",
        json=reservation_payload(
            event_id=event_id,
            ticket_type_id=ticket_type_id,
            quantity=quantity,
        ),
    )
    assert response.status_code == 201
    return event_id, ticket_type_id, str(response.json()["reservation_id"])


async def create_seat_reservation(
    client: AsyncClient,
) -> tuple[str, str, str]:
    event_id, seat_ids = await create_seat_inventory(client, seat_count=1)
    response = await client.post(
        "/v1/reservations/seats",
        json=seat_reservation_payload(
            event_id=event_id,
            seat_ids=seat_ids,
        ),
    )
    assert response.status_code == 201
    return event_id, seat_ids[0], str(response.json()["reservation_id"])


async def test_get_reservation_requires_ownership(client: AsyncClient) -> None:
    await create_anonymous_session(client)
    _, _, reservation_id = await create_quantity_reservation(client)

    owned_response = await client.get(f"/v1/reservations/{reservation_id}")
    assert owned_response.status_code == 200

    client.cookies.clear()
    await create_anonymous_session(client)
    foreign_response = await client.get(f"/v1/reservations/{reservation_id}")
    assert foreign_response.status_code == 404


async def test_user_can_access_reservation_from_another_owned_session(
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
    _, _, reservation_id = await create_quantity_reservation(client)

    await client.post("/v1/auth/logout")
    second_login_response = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
    )
    assert second_login_response.status_code == 200
    second_session_response = await client.get("/v1/me/session")

    response = await client.get(f"/v1/reservations/{reservation_id}")

    assert second_session_response.json()["user_id"] == user["id"]
    assert response.status_code == 200
    assert response.json()["reservation_id"] == reservation_id


async def test_cancel_quantity_reservation_releases_stock(client: AsyncClient) -> None:
    await create_anonymous_session(client)
    event_id, _, reservation_id = await create_quantity_reservation(client)

    first_response = await client.post(f"/v1/reservations/{reservation_id}/cancel")
    second_response = await client.post(f"/v1/reservations/{reservation_id}/cancel")
    inventory = await client.get(f"/v1/events/{event_id}/inventory")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["status"] == "cancelled"
    assert second_response.json() == first_response.json()
    assert first_response.json()["items"][0]["status"] == "cancelled"
    assert inventory.json()["ticket_types"][0]["reserved"] == 0
    assert inventory.json()["ticket_types"][0]["sold"] == 0
    assert inventory.json()["ticket_types"][0]["available"] == 10


async def test_confirm_quantity_reservation_moves_reserved_to_sold(
    client: AsyncClient,
) -> None:
    await create_anonymous_session(client)
    event_id, _, reservation_id = await create_quantity_reservation(client)

    first_response = await client.post(f"/v1/reservations/{reservation_id}/confirm")
    second_response = await client.post(f"/v1/reservations/{reservation_id}/confirm")
    inventory = await client.get(f"/v1/events/{event_id}/inventory")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["status"] == "confirmed"
    assert second_response.json() == first_response.json()
    assert first_response.json()["items"][0]["status"] == "confirmed"
    assert inventory.json()["ticket_types"][0]["reserved"] == 0
    assert inventory.json()["ticket_types"][0]["sold"] == 3
    assert inventory.json()["ticket_types"][0]["available"] == 7


async def test_expire_quantity_reservation_releases_stock(client: AsyncClient) -> None:
    await create_anonymous_session(client)
    event_id, _, reservation_id = await create_quantity_reservation(client)

    processed = await expire_reservations(
        datetime.now(UTC) + timedelta(hours=1),
        [reservation_id],
    )

    inventory = await client.get(f"/v1/events/{event_id}/inventory")
    response = await client.get(f"/v1/reservations/{reservation_id}")

    assert processed == 1
    assert response.status_code == 200
    assert response.json()["status"] == "expired"
    assert response.json()["items"][0]["status"] == "expired"
    assert inventory.json()["ticket_types"][0]["reserved"] == 0
    assert inventory.json()["ticket_types"][0]["sold"] == 0


async def test_cancel_seat_reservation_releases_seat(client: AsyncClient) -> None:
    await create_anonymous_session(client)
    event_id, seat_id, reservation_id = await create_seat_reservation(client)

    first_response = await client.post(f"/v1/reservations/{reservation_id}/cancel")
    second_response = await client.post(f"/v1/reservations/{reservation_id}/cancel")
    inventory = await client.get(f"/v1/events/{event_id}/inventory")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["status"] == "cancelled"
    assert first_response.json()["seats"] == [{"seat_id": seat_id, "status": "cancelled"}]
    assert inventory.json()["seats"][0]["status"] == "available"

    new_reservation = await client.post(
        "/v1/reservations/seats",
        json=seat_reservation_payload(
            event_id=event_id,
            seat_ids=[seat_id],
        ),
    )
    assert new_reservation.status_code == 201


async def test_confirm_seat_reservation_keeps_seat_unavailable(
    client: AsyncClient,
) -> None:
    await create_anonymous_session(client)
    event_id, seat_id, reservation_id = await create_seat_reservation(client)

    first_response = await client.post(f"/v1/reservations/{reservation_id}/confirm")
    second_response = await client.post(f"/v1/reservations/{reservation_id}/confirm")
    inventory = await client.get(f"/v1/events/{event_id}/inventory")
    conflicting_reservation = await client.post(
        "/v1/reservations/seats",
        json=seat_reservation_payload(
            event_id=event_id,
            seat_ids=[seat_id],
        ),
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["status"] == "confirmed"
    assert first_response.json()["seats"][0]["status"] == "confirmed"
    assert inventory.json()["seats"][0]["status"] == "confirmed"
    assert conflicting_reservation.status_code == 409


async def test_expire_seat_reservation_releases_seat(client: AsyncClient) -> None:
    await create_anonymous_session(client)
    event_id, seat_id, reservation_id = await create_seat_reservation(client)

    await expire_reservations(
        datetime.now(UTC) + timedelta(hours=1),
        [reservation_id],
    )

    inventory = await client.get(f"/v1/events/{event_id}/inventory")
    response = await client.get(f"/v1/reservations/{reservation_id}")
    new_reservation = await client.post(
        "/v1/reservations/seats",
        json=seat_reservation_payload(
            event_id=event_id,
            seat_ids=[seat_id],
        ),
    )

    assert response.json()["status"] == "expired"
    assert response.json()["seats"][0]["status"] == "expired"
    assert inventory.json()["seats"][0]["status"] == "available"
    assert new_reservation.status_code == 201


async def test_invalid_terminal_transitions_return_409(client: AsyncClient) -> None:
    await create_anonymous_session(client)
    _, _, cancelled_id = await create_quantity_reservation(client)
    await client.post(f"/v1/reservations/{cancelled_id}/cancel")

    _, _, confirmed_id = await create_quantity_reservation(client)
    await client.post(f"/v1/reservations/{confirmed_id}/confirm")

    assert (await client.post(f"/v1/reservations/{cancelled_id}/confirm")).status_code == 409
    assert (await client.post(f"/v1/reservations/{confirmed_id}/cancel")).status_code == 409


async def test_worker_expiration_updates_reservation_status_in_database(
    client: AsyncClient,
) -> None:
    await create_anonymous_session(client)
    _, _, reservation_id = await create_quantity_reservation(client, quantity=1)

    await expire_reservations(
        datetime.now(UTC) + timedelta(hours=1),
        [reservation_id],
    )

    async with async_session_factory() as session:
        reservation = await session.scalar(
            select(Reservation).where(Reservation.id == UUID(reservation_id))
        )

    assert reservation is not None
    assert reservation.status is ReservationStatus.EXPIRED
