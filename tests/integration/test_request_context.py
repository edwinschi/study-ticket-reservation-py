import logging
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from tests.integration.test_quantity_reservations import (
    create_anonymous_session,
    create_quantity_inventory,
    reservation_payload,
)


async def test_request_id_is_generated_and_returned(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    request_id = response.headers["X-Request-ID"]
    assert str(UUID(request_id)) == request_id


async def test_request_id_header_is_preserved(client: AsyncClient) -> None:
    request_id = f"client-{uuid4()}"

    response = await client.get("/health", headers={"X-Request-ID": request_id})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == request_id


async def test_unauthorized_error_uses_standard_shape(client: AsyncClient) -> None:
    request_id = str(uuid4())

    response = await client.post(
        "/v1/reservations/quantity",
        headers={"X-Request-ID": request_id},
        json={
            "event_id": str(uuid4()),
            "ticket_type_id": str(uuid4()),
            "quantity": 1,
            "idempotency_key": uuid4().hex,
        },
    )

    assert response.status_code == 401
    assert response.headers["X-Request-ID"] == request_id
    assert response.json() == {
        "error": {
            "code": "SESSION_REQUIRED",
            "message": "A valid visitor session is required",
            "request_id": request_id,
        }
    }


async def test_conflict_error_uses_standard_shape(client: AsyncClient) -> None:
    await create_anonymous_session(client)
    event_id, ticket_type_id = await create_quantity_inventory(
        client,
        total_quantity=0,
    )
    request_id = str(uuid4())

    response = await client.post(
        "/v1/reservations/quantity",
        headers={"X-Request-ID": request_id},
        json=reservation_payload(
            event_id=event_id,
            ticket_type_id=ticket_type_id,
            quantity=1,
        ),
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "INSUFFICIENT_STOCK",
            "message": "Not enough stock available",
            "request_id": request_id,
        }
    }


async def test_validation_error_uses_standard_shape(client: AsyncClient) -> None:
    request_id = str(uuid4())

    response = await client.post(
        "/v1/events",
        headers={"X-Request-ID": request_id},
        json={
            "name": "",
            "starts_at": "not-a-datetime",
            "ends_at": "not-a-datetime",
        },
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "request_id": request_id,
        }
    }


async def test_not_found_error_uses_standard_shape(client: AsyncClient) -> None:
    request_id = str(uuid4())

    response = await client.get(
        "/v1/events/not-a-valid-uuid",
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "VALIDATION_ERROR",
        "message": "Request validation failed",
        "request_id": request_id,
    }

    missing_route_response = await client.get(
        "/missing-route",
        headers={"X-Request-ID": request_id},
    )
    assert missing_route_response.status_code == 404
    assert missing_route_response.json() == {
        "error": {
            "code": "NOT_FOUND",
            "message": "Not Found",
            "request_id": request_id,
        }
    }


async def test_http_access_log_contains_safe_request_metadata(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    request_id = str(uuid4())

    with caplog.at_level(logging.INFO, logger="app.http"):
        response = await client.get(
            "/health",
            headers={
                "X-Request-ID": request_id,
                "Cookie": "visitor_session=secret-cookie-value",
            },
        )

    assert response.status_code == 200
    access_log = next(
        record
        for record in caplog.records
        if record.name == "app.http" and getattr(record, "request_id", None) == request_id
    )
    assert access_log.getMessage() == "HTTP request completed"
    log_fields = access_log.__dict__
    assert log_fields["method"] == "GET"
    assert log_fields["path"] == "/health"
    assert log_fields["status_code"] == 200
    assert isinstance(log_fields["duration_ms"], float)
    assert "secret-cookie-value" not in access_log.getMessage()
