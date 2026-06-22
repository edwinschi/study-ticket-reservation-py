from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from httpx import AsyncClient
from pytest import MonkeyPatch
from sqlalchemy import update

from app.db.session import async_session_factory
from app.modules.reservations.models import Reservation
from app.modules.stress_admin import service as stress_admin_service
from tests.integration.test_quantity_reservations import (
    create_anonymous_session,
    create_quantity_inventory,
    reservation_payload,
)


async def test_assert_consistency_returns_aggregate_database_status(
    client: AsyncClient,
) -> None:
    response = await client.get("/v1/admin/stress/assert-consistency")

    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert set(body["checks"]) == {
        "ticket_quantity_not_oversold",
        "ticket_quantity_not_negative",
        "no_duplicate_active_seats",
        "no_orphan_reservation_items",
        "no_orphan_reservation_seats",
        "no_stale_active_reservations",
    }
    assert body["ok"] is all(body["checks"].values())
    assert all(detail["check"] in body["checks"] for detail in body["details"])
    assert all(not body["checks"][detail["check"]] for detail in body["details"])


async def test_assert_consistency_reports_stale_active_reservation(
    client: AsyncClient,
    monkeypatch: MonkeyPatch,
) -> None:
    await create_anonymous_session(client)
    event_id, ticket_type_id = await create_quantity_inventory(
        client,
        total_quantity=5,
    )
    reservation_response = await client.post(
        "/v1/reservations/quantity",
        json=reservation_payload(
            event_id=event_id,
            ticket_type_id=ticket_type_id,
            quantity=1,
        ),
    )
    assert reservation_response.status_code == 201
    reservation_id = UUID(reservation_response.json()["reservation_id"])

    database_now = datetime.now(UTC)
    expires_at = database_now + timedelta(seconds=10)
    async with async_session_factory() as session:
        await session.execute(
            update(Reservation)
            .where(Reservation.id == reservation_id)
            .values(expires_at=expires_at)
        )
        await session.commit()

    audit_now = database_now + timedelta(minutes=2)
    with monkeypatch.context() as patch:
        patch.setattr(stress_admin_service, "utc_now", lambda: audit_now)
        response = await client.get("/v1/admin/stress/assert-consistency")

    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["ok"] is False
    assert body["checks"] == {
        "ticket_quantity_not_oversold": True,
        "ticket_quantity_not_negative": True,
        "no_duplicate_active_seats": True,
        "no_orphan_reservation_items": True,
        "no_orphan_reservation_seats": True,
        "no_stale_active_reservations": False,
    }
    stale_detail = next(
        detail
        for detail in body["details"]
        if detail["data"].get("reservation_id") == str(reservation_id)
    )
    assert stale_detail["check"] == "no_stale_active_reservations"
    assert stale_detail["message"] == (
        "Active reservation has remained expired beyond the worker tolerance"
    )
    assert stale_detail["data"]["expires_at"] == expires_at.isoformat()
    assert stale_detail["data"]["seconds_overdue"] == 110

    cancel_response = await client.post(f"/v1/reservations/{reservation_id}/cancel")
    assert cancel_response.status_code == 200
