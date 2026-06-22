import logging
import random
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import requests
from gevent.lock import Semaphore
from locust import HttpUser, between, events, task
from locust.clients import ResponseContextManager
from locust.env import Environment
from locust.exception import StopUser

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10
EXPECTED_CONFLICT_LOG_INTERVAL = 1_000


@dataclass(frozen=True, slots=True)
class StressSeed:
    event_id: str
    ticket_type_id: str
    seat_ids: tuple[str, ...]


_seed_lock = Semaphore()
_metrics_lock = Semaphore()
_stress_seed: StressSeed | None = None
_expected_conflicts = 0
_server_errors = 0


def _response_body(response: ResponseContextManager) -> str:
    return response.text[:500].replace("\n", " ")


def _record_expected_conflict(operation: str) -> None:
    """Count expected HTTP 409 responses without marking the Locust request as failed."""
    global _expected_conflicts

    with _metrics_lock:
        _expected_conflicts += 1
        count = _expected_conflicts

    if count == 1 or count % EXPECTED_CONFLICT_LOG_INTERVAL == 0:
        logger.info(
            "Expected HTTP 409 conflicts observed: count=%s latest_operation=%s",
            count,
            operation,
        )


def _record_server_error(
    operation: str,
    response: ResponseContextManager,
) -> None:
    """Count HTTP 5xx responses as critical failures for the whole stress run."""
    global _server_errors

    with _metrics_lock:
        _server_errors += 1
        count = _server_errors

    logger.error(
        "CRITICAL server error: operation=%s status=%s count=%s body=%s",
        operation,
        response.status_code,
        count,
        _response_body(response),
    )


def _accept_response(
    response: ResponseContextManager,
    *,
    operation: str,
    expected_statuses: set[int],
    conflict_is_expected: bool = False,
) -> bool:
    """
    Normalize Locust response handling for business conflicts and system errors.

    Under contention, 409 is not a hidden failure: it means PostgreSQL correctly rejected an
    impossible reservation. HTTP 5xx responses, timeouts, and unexpected statuses remain failures.
    """
    if response.status_code in expected_statuses:
        response.success()
        return True

    if response.status_code == 409 and conflict_is_expected:
        response.success()
        _record_expected_conflict(operation)
        return False

    if response.status_code >= 500:
        _record_server_error(operation, response)
        response.failure(f"CRITICAL {operation} returned HTTP {response.status_code}")
        return False

    response.failure(
        f"Unexpected {operation} response: HTTP {response.status_code} "
        f"body={_response_body(response)}"
    )
    return False


def _parse_seed(response: ResponseContextManager) -> StressSeed | None:
    """Validate the stress seed payload before thousands of users depend on it."""
    try:
        payload: dict[str, Any] = response.json()
        event_id = str(payload["event_id"])
        ticket_type_id = str(payload["ticket_type_id"])
        seat_ids = tuple(str(seat_id) for seat_id in payload["seat_ids"])
    except (KeyError, TypeError, ValueError):
        response.failure("Stress seed returned an invalid JSON payload")
        return None

    if not seat_ids:
        response.failure("Stress seed did not return any seats")
        return None

    return StressSeed(
        event_id=event_id,
        ticket_type_id=ticket_type_id,
        seat_ids=seat_ids,
    )


@events.test_start.add_listener
def on_test_start(environment: Environment, **_: object) -> None:
    """Reset process-level counters before each Locust run."""
    global _expected_conflicts, _server_errors, _stress_seed

    with _seed_lock:
        _stress_seed = None
    with _metrics_lock:
        _expected_conflicts = 0
        _server_errors = 0

    logger.info(
        "Starting ticket reservation stress test: host=%s",
        environment.host,
    )


@events.test_stop.add_listener
def on_test_stop(environment: Environment, **_: object) -> None:
    """
    Fail the stress process if the final database consistency audit fails.

    Locust can report high throughput even when the database is corrupted. The final audit makes
    consistency an explicit success criterion for the load test.
    """
    host = environment.host
    logger.info(
        "Stress traffic stopped: expected_conflicts=%s server_errors=%s",
        _expected_conflicts,
        _server_errors,
    )
    if _server_errors:
        environment.process_exit_code = 1

    if not host:
        logger.error("Cannot run final consistency check because no Locust host is configured")
        environment.process_exit_code = 1
        return

    try:
        response = requests.get(
            f"{host.rstrip('/')}/v1/admin/stress/assert-consistency",
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Final consistency check could not be completed: %s", exc)
        environment.process_exit_code = 1
        return

    if result.get("ok") is not True:
        logger.error("Database consistency check failed: %s", result)
        environment.process_exit_code = 1
        return

    logger.info("Database consistency check passed: %s", result["checks"])


class AnonymousReservationUser(HttpUser):
    """Base user that owns one visitor session and shares one stress fixture."""

    abstract = True
    wait_time = between(0.05, 0.25)

    seed: StressSeed

    def on_start(self) -> None:
        # Every simulated user gets its own anonymous session cookie. This matches browser
        # behavior and exercises the session-scoped idempotency constraint.
        self.seed = self._get_or_create_seed()
        with self.client.post(
            "/v1/sessions/anonymous",
            name="/v1/sessions/anonymous",
            catch_response=True,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:
            session_created = _accept_response(
                response,
                operation="create anonymous session",
                expected_statuses={201},
            )

        if not session_created:
            raise StopUser

    def _get_or_create_seed(self) -> StressSeed:
        global _stress_seed

        with _seed_lock:
            # Only one greenlet should create the shared stress fixture. Without this lock,
            # simultaneous users could create many independent events and dilute contention.
            if _stress_seed is not None:
                return _stress_seed

            with self.client.post(
                "/v1/admin/stress/seed",
                name="/v1/admin/stress/seed [bootstrap]",
                catch_response=True,
                timeout=REQUEST_TIMEOUT_SECONDS,
            ) as response:
                seed_created = _accept_response(
                    response,
                    operation="create stress seed",
                    expected_statuses={201},
                )
                seed = _parse_seed(response) if seed_created else None

            if seed is None:
                raise StopUser

            _stress_seed = seed
            logger.info(
                "Stress seed created: event_id=%s ticket_type_id=%s seats=%s",
                seed.event_id,
                seed.ticket_type_id,
                len(seed.seat_ids),
            )
            return seed


class QuantityReservationUser(AnonymousReservationUser):
    """Simulate users competing for finite quantity inventory."""

    weight = 1

    @task
    def reserve_quantity(self) -> None:
        # Random quantities create more realistic contention than always reserving one ticket.
        # Each request uses a unique idempotency key so the test exercises stock exhaustion,
        # not mostly idempotent replays.
        payload = {
            "event_id": self.seed.event_id,
            "ticket_type_id": self.seed.ticket_type_id,
            "quantity": random.randint(1, 3),
            "idempotency_key": f"locust-quantity-{uuid4().hex}",
        }
        with self.client.post(
            "/v1/reservations/quantity",
            json=payload,
            name="/v1/reservations/quantity",
            catch_response=True,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:
            reserved = _accept_response(
                response,
                operation="reserve quantity",
                expected_statuses={201},
                conflict_is_expected=True,
            )
            reservation_id: str | None = None
            if reserved:
                try:
                    reservation_id = str(response.json()["reservation_id"])
                except (KeyError, TypeError, ValueError):
                    response.failure("Quantity reservation returned an invalid JSON payload")

        if reservation_id is None:
            return

        # Some reservations are cancelled, some are confirmed, and some are left for the worker.
        # This mixes all lifecycle paths during the same stress run.
        lifecycle_roll = random.random()
        if lifecycle_roll < 0.4:
            self._transition_reservation(reservation_id, action="cancel")
        elif lifecycle_roll < 0.8:
            self._transition_reservation(reservation_id, action="confirm")

    def _transition_reservation(self, reservation_id: str, *, action: str) -> None:
        with self.client.post(
            f"/v1/reservations/{reservation_id}/{action}",
            name=f"/v1/reservations/[reservation_id]/{action}",
            catch_response=True,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:
            _accept_response(
                response,
                operation=f"{action} quantity reservation",
                expected_statuses={200},
            )


class SeatReservationUser(AnonymousReservationUser):
    weight = 1

    @task
    def reserve_seat(self) -> None:
        # One random seat per request creates heavy write contention on a small seat set.
        # A 409 response is expected when another user already holds that seat.
        payload = {
            "event_id": self.seed.event_id,
            "seat_ids": [random.choice(self.seed.seat_ids)],
            "idempotency_key": f"locust-seat-{uuid4().hex}",
        }
        with self.client.post(
            "/v1/reservations/seats",
            json=payload,
            name="/v1/reservations/seats",
            catch_response=True,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:
            _accept_response(
                response,
                operation="reserve seat",
                expected_statuses={201},
                conflict_is_expected=True,
            )
