# Ticket Reservation Lab

`ticket-reservation-lab` is a professional study project for a concurrency-safe ticket
reservation API.

The goal is to model the hard parts of a real reservation backend: limited inventory,
anonymous visitor sessions, logged-in users, idempotent requests, reservation expiration,
concurrent writes, and local stress testing with thousands of simultaneous requests.

The project intentionally keeps PostgreSQL as the source of truth for inventory consistency.
Redis is available as infrastructure, but stock correctness does not depend on Redis.

## 1. Overview

This API supports two reservation modes:

- Quantity-based reservations, where users reserve a number of tickets from a ticket type.
- Seat-based reservations, where users reserve specific seats for an event.

The main engineering focus is correctness under concurrency. Expected business conflicts, such
as exhausted stock or an already reserved seat, are returned as `409 Conflict` and must not become
`500 Internal Server Error`.

Core capabilities:

- FastAPI REST API.
- PostgreSQL-backed visitor sessions.
- Basic user registration and login.
- Quantity inventory protected by an atomic PostgreSQL `UPDATE`.
- Seat inventory protected by ordered row locks and a partial unique index.
- Idempotent reservation creation.
- Reservation cancel, confirm, and expiration lifecycle.
- Background expiration worker using `FOR UPDATE SKIP LOCKED`.
- Administrative consistency assertion endpoint.
- Local stress testing with k6.
- Strong pytest concurrency tests.
- Ruff formatting/linting and strict mypy type checking.

## 2. Stack

- Python 3.12
- FastAPI
- Pydantic v2 and Pydantic Settings
- SQLAlchemy 2 async
- asyncpg
- PostgreSQL 16
- Redis 7
- Alembic
- pytest
- pytest-asyncio
- httpx
- k6
- Ruff
- mypy
- uv
- Docker Compose

No local Python installation is required for the standard workflow. Docker Compose builds the API
image and installs dependencies through `uv`.

## 3. Architecture

The code is organized around backend boundaries instead of framework-only files:

```text
src/app/
  main.py
  core/              # settings, logging, errors, request context, middleware
  db/                # SQLAlchemy async session, metadata, Redis client
  modules/
    users/           # user model, schemas, repository, auth service
    sessions/        # visitor session model and dependencies
    events/          # events, ticket types, seats, inventory
    reservations/    # quantity/seat reservation and lifecycle logic
    stress_admin/    # local seed/reset/consistency endpoints
  workers/           # expiration worker
alembic/             # migrations
tests/               # integration and concurrency tests
stress/              # k6 scripts and optional Locust workload
```

Runtime services:

- `api`: FastAPI application served by Uvicorn.
- `postgres`: PostgreSQL database with healthcheck.
- `redis`: Redis instance with healthcheck.
- `migrate`: one-shot Alembic migration service.
- `worker`: reservation expiration worker.
- `k6`: standard stress-test runner used for project comparison.
- `locust`: optional legacy stress-test runner.

The API and worker depend on successful migrations and healthy infrastructure services.

## 4. How to run locally

Start the full local environment:

```bash
docker compose up --build
```

Or use the Makefile:

```bash
make up
```

The API is exposed at:

```text
http://localhost:8000
```

Check the API process:

```bash
curl http://localhost:8000/health
```

Check PostgreSQL and Redis readiness:

```bash
curl http://localhost:8000/ready
```

Stop the environment:

```bash
docker compose down
make down
```

Remove local PostgreSQL and Redis volumes:

```bash
docker compose down -v
```

Use this only when you want to delete all local data.

### API testing with Bruno

The repository includes a versioned Bruno collection in:

```text
api/bruno/
```

Start the API with `docker compose up --build`, open Bruno, choose
**Open Collection**, and select the `api/bruno/` folder. The local Bruno
environment uses:

```text
baseURL=http://localhost:8000
```

Suggested manual flow:

1. Run `health/Healthcheck` and `health/Readiness`.
2. Run `sessions/Create Anonymous Session` so Bruno stores the `visitor_session` cookie.
3. Run `stress-admin/Seed Stress Fixture`.
4. Copy the returned `event_id`, `ticket_type_id`, and relevant `seat_ids` into the active Bruno environment.
5. Run reservation requests and then `stress-admin/Assert Consistency`.

See `api/bruno/README.md` for the full collection notes.

## 5. Environment variables

The default local values are defined in `.env.example`.

```bash
cp .env.example .env
```

Main variables:

| Variable | Purpose | Default |
| --- | --- | --- |
| `APP_ENV` | Runtime environment. Stress admin endpoints are disabled in production. | `development` |
| `LOG_LEVEL` | Application log level. | `INFO` |
| `API_PORT` | Host port mapped to the API container. | `8000` |
| `UVICORN_WORKERS` | Number of Uvicorn worker processes used by the API service. | `4` |
| `COOKIE_SECURE` | Whether session cookies require HTTPS. | `false` |
| `VISITOR_SESSION_TTL_SECONDS` | Visitor session lifetime. | `2592000` |
| `RESERVATION_TTL_SECONDS` | Reservation hold lifetime. | `900` |
| `EXPIRATION_WORKER_INTERVAL_SECONDS` | Worker loop interval. | `5` |
| `EXPIRATION_WORKER_BATCH_SIZE` | Worker batch size. | `100` |
| `DATABASE_POOL_SIZE` | Base SQLAlchemy connection pool size per API worker process. | `5` |
| `DATABASE_MAX_OVERFLOW` | Extra SQLAlchemy connections allowed per API worker process. | `10` |
| `DATABASE_POOL_TIMEOUT_SECONDS` | Maximum wait for a pooled database connection. | `30` |
| `DATABASE_POOL_RECYCLE_SECONDS` | Maximum age for pooled database connections before recycling. | `1800` |
| `WORKER_DATABASE_POOL_SIZE` | Base SQLAlchemy connection pool size for the expiration worker. | `2` |
| `WORKER_DATABASE_MAX_OVERFLOW` | Extra SQLAlchemy connections allowed for the expiration worker. | `2` |
| `POSTGRES_DB` | PostgreSQL database name. | `ticket_reservation` |
| `POSTGRES_USER` | PostgreSQL user. | `ticket_reservation` |
| `POSTGRES_PASSWORD` | PostgreSQL password. | `ticket_reservation` |
| `DATABASE_URL` | SQLAlchemy async PostgreSQL URL. | Set by Compose |
| `REDIS_URL` | Redis URL. | Set by Compose |

For local development, Compose configures `DATABASE_URL` and `REDIS_URL` on the internal Docker
network. The Compose API service runs without Uvicorn hot reload and uses multiple workers by
default so local stress runs are closer to the Go comparison environment.

## 6. Migrations

Migrations are executed automatically by the `migrate` service when the Compose environment starts.

Run migrations manually:

```bash
docker compose run --rm migrate
make migrate
```

Check whether SQLAlchemy metadata and Alembic migration state are aligned:

```bash
docker compose exec api alembic check
```

## 7. Tests, linting, formatting, and typing

Run all tests:

```bash
docker compose exec api pytest
make test
```

Run only concurrency tests:

```bash
docker compose exec api pytest tests/concurrency
```

Run lint:

```bash
docker compose exec api ruff check .
make lint
```

Format:

```bash
docker compose exec api ruff format .
make format
```

Run strict type checking:

```bash
docker compose exec api mypy
make typecheck
```

Useful Makefile commands:

| Command | Description |
| --- | --- |
| `make up` | Builds and starts the full Docker Compose environment. |
| `make stress-up` | Builds and starts the environment with request logs reduced for stress tests. |
| `make down` | Stops the Docker Compose environment without removing volumes. |
| `make test` | Runs the pytest suite inside the API container. |
| `make lint` | Runs Ruff lint checks. |
| `make format` | Formats Python code with Ruff. |
| `make typecheck` | Runs strict mypy type checking. |
| `make migrate` | Runs Alembic migrations through the migration service. |
| `make stress-reset` | Resets local stress-test fixtures through the admin endpoint. |
| `make stress-seed` | Creates local stress-test fixtures through the admin endpoint. |
| `make stress` | Runs the mixed k6 stress script. |
| `make k6` | Alias for `make stress`. |
| `make stress-quantity` | Runs the quantity-only k6 stress script. |
| `make stress-seats` | Runs the seat-only k6 stress script. |
| `make stress-locust` | Runs the previous headless Locust stress test. |
| `make assert` | Calls the consistency assertion endpoint and fails when `ok` is not `true`. |

## 8. Stress testing with k6

Start the application:

```bash
make stress-up
```

The Compose API service runs Uvicorn without `--reload` and uses `UVICORN_WORKERS=4` by default.
Each worker has its own SQLAlchemy pool, so total possible API database connections are roughly
`UVICORN_WORKERS * (DATABASE_POOL_SIZE + DATABASE_MAX_OVERFLOW)`.

Each k6 virtual user creates one anonymous visitor session and reuses its cookie across
iterations, matching browser-like traffic more closely than creating a new session per request.

Reset previous stress fixtures:

```bash
curl -X POST http://localhost:8000/v1/admin/stress/reset
make stress-reset
```

Create a local stress fixture:

```bash
curl -X POST http://localhost:8000/v1/admin/stress/seed
make stress-seed
```

Run a mixed quantity/seat stress test:

```bash
docker compose run --rm --no-deps k6 run \
  -e BASE_URL=http://api:8000 \
  -e VUS=100 \
  -e DURATION=30s \
  /scripts/mixed.js

make stress
```

Validate database consistency after the run:

```bash
curl http://localhost:8000/v1/admin/stress/assert-consistency
make assert
```

Suggested load profiles:

```bash
K6_VUS=500 K6_DURATION=30s make stress

K6_VUS=1000 K6_DURATION=1m make stress

K6_VUS=5000 K6_DURATION=2m make stress
```

Focused scripts:

```bash
make stress-quantity
make stress-seats
```

How to interpret the result:

- `201 Created` means a reservation was created.
- `409 Conflict` is an expected business outcome when stock is exhausted or a seat is already held.
- `409` should not be hidden or treated as a system failure.
- `500`, excessive timeouts, stalled workers, or `"ok": false` from the consistency endpoint are
  real failures.
- High `409` volume is normal after the seeded inventory becomes heavily contested.
- The final signal should be a successful k6 exit plus `"ok": true` from the consistency audit.

The optional Locust workload is still available for historical comparison:

```bash
make stress-locust
```

## 9. Main endpoints

Health:

- `GET /health`
- `GET /ready`

Visitor session and authentication:

- `POST /v1/sessions/anonymous`
- `GET /v1/me/session`
- `POST /v1/auth/register`
- `POST /v1/auth/login`
- `POST /v1/auth/logout`

Events and inventory:

- `POST /v1/events`
- `GET /v1/events/{event_id}`
- `POST /v1/events/{event_id}/ticket-types`
- `POST /v1/events/{event_id}/seats`
- `GET /v1/events/{event_id}/inventory`

Reservations:

- `POST /v1/reservations/quantity`
- `POST /v1/reservations/seats`
- `GET /v1/reservations/{reservation_id}`
- `POST /v1/reservations/{reservation_id}/cancel`
- `POST /v1/reservations/{reservation_id}/confirm`

Local stress administration:

- `POST /v1/admin/stress/seed`
- `POST /v1/admin/stress/reset`
- `GET /v1/admin/stress/assert-consistency`

Stress administration endpoints return `404` when `APP_ENV=production`.

## 10. Data model summary

Main tables:

- `users`: registered users with unique email and password hash.
- `visitor_sessions`: anonymous or authenticated browser sessions.
- `events`: event metadata.
- `ticket_types`: quantity-based inventory per event.
- `seats`: physical/logical seats per event.
- `reservations`: reservation header with owner, type, status, idempotency key, and expiration.
- `reservation_items`: quantity reservation lines.
- `reservation_seats`: seat reservation lines.

Important constraints and indexes:

- `users.email` is unique.
- `visitor_sessions.anonymous_token_hash` is unique.
- `ticket_types` enforces non-negative quantities.
- `ticket_types` enforces `sold_quantity + reserved_quantity <= total_quantity`.
- `seats` enforces unique `(event_id, section, row_name, seat_number)`.
- `reservations` enforces unique `(visitor_session_id, idempotency_key)`.
- `reservation_items.quantity > 0`.
- `reservation_seats` has a partial unique index preventing duplicate active seat holds.
- Reservation, item, seat, ticket type, user, and status columns have indexes for lifecycle and
  inventory queries.

## 11. Concurrency strategy

The project uses the database as the consistency boundary.

Application-level checks are useful for validation and error messages, but critical inventory
invariants are protected by PostgreSQL transactions, constraints, locks, and indexes.

Design rules:

- Every request receives its own SQLAlchemy `AsyncSession`.
- Sessions are not shared across requests, tasks, or workers.
- Transactions are kept short.
- Quantity reservation does not use read-then-write stock calculation in application code.
- Seat reservation locks rows in deterministic order to reduce deadlock risk.
- Expected conflicts return `409`, not `500`.
- Redis is not used as the source of truth for available stock.

## 12. Quantity reservation: atomic PostgreSQL UPDATE

Quantity inventory is protected by a single conditional `UPDATE`.

The API does not first `SELECT` availability and then calculate the new value in Python. That
pattern is vulnerable to race conditions because multiple transactions can observe the same old
stock value.

Instead, PostgreSQL checks availability and increments `reserved_quantity` in one atomic statement:

```sql
UPDATE ticket_types
SET reserved_quantity = reserved_quantity + :quantity
WHERE id = :ticket_type_id
  AND event_id = :event_id
  AND total_quantity - sold_quantity - reserved_quantity >= :quantity
RETURNING id, total_quantity, sold_quantity, reserved_quantity;
```

If PostgreSQL returns a row, the reservation can be created in the same transaction. If it returns
no row, there was not enough available stock and the API returns `409 Conflict`.

This works under high concurrency because each successful update acquires the necessary row-level
write lock and commits a new `reserved_quantity`. Competing transactions re-check the `WHERE`
condition against the current committed row state.

## 13. Seat reservation: ordered locks and partial unique index

Seat reservation has a different concurrency shape. The user is not reserving an anonymous
quantity; they are reserving specific seat IDs.

The service first validates and locks the requested seats in deterministic order:

```sql
SELECT id
FROM seats
WHERE event_id = :event_id
  AND id = ANY(:seat_ids)
ORDER BY id
FOR UPDATE;
```

Why ordered `FOR UPDATE` matters:

- It serializes concurrent attempts that touch the same seats.
- It reduces deadlock risk when two requests ask for the same seats in different input orders.
- It lets the transaction expire stale holds for those seats before inserting new active holds.

The final protection is a PostgreSQL partial unique index:

```sql
CREATE UNIQUE INDEX uq_active_reservation_seat
ON reservation_seats(seat_id)
WHERE status IN ('reserved', 'confirmed');
```

That index guarantees that one seat cannot have two active reservation rows. Even if application
logic regresses, PostgreSQL rejects duplicate active occupancy.

## 14. Idempotency

Reservation creation requires an `idempotency_key`.

The database enforces:

```text
unique(visitor_session_id, idempotency_key)
```

If the same visitor session repeats the same request, the API returns the already created
reservation instead of creating another one.

This protects clients from duplicate reservations caused by retries, network failures, browser
refreshes, or concurrent duplicate submissions. It also prevents duplicate stock increments for
quantity reservations.

## 15. Reservation expiration

Reservations start as `reserved` and have an `expires_at` timestamp. The default hold time is
15 minutes.

A background worker scans expired reservations every five seconds in local development.

The worker:

- Selects expired `reserved` reservations in batches.
- Uses `FOR UPDATE SKIP LOCKED` so multiple workers can run concurrently.
- Releases quantity reservations by decrementing `reserved_quantity`.
- Expires seat reservations by changing `reservation_seats.status` to `expired`.
- Marks the parent reservation as `expired`.
- Commits each batch atomically.

Lifecycle rules:

- Cancel is idempotent.
- Confirm is idempotent.
- Expiration is idempotent.
- Cancelled or expired reservations cannot be confirmed.
- Confirmed reservations cannot be cancelled.
- Quantity confirmation moves inventory from `reserved_quantity` to `sold_quantity`.
- Seat confirmation keeps the seat unavailable by moving the seat hold to `confirmed`.

## 16. Consistency assertion endpoint

The local admin endpoint:

```bash
curl http://localhost:8000/v1/admin/stress/assert-consistency
```

returns:

```json
{
  "ok": true,
  "checks": {
    "ticket_quantity_not_oversold": true,
    "ticket_quantity_not_negative": true,
    "no_duplicate_active_seats": true,
    "no_orphan_reservation_items": true,
    "no_orphan_reservation_seats": true,
    "no_stale_active_reservations": true
  },
  "details": []
}
```

It checks:

- No negative `total_quantity`, `sold_quantity`, or `reserved_quantity`.
- No `sold_quantity + reserved_quantity > total_quantity`.
- No seat has more than one active reservation seat.
- No orphan `reservation_items`.
- No orphan `reservation_seats`.
- No active reservation remains expired beyond the local tolerance window.

This endpoint is intentionally useful after concurrency tests and k6 runs. It gives a simple
database-level signal that the stress workload did not violate core invariants.

## 17. Errors, request IDs, and logs

Every response includes `X-Request-ID`.

If the client sends `X-Request-ID`, the API preserves it. Otherwise, middleware generates a UUID.

Application errors use a consistent shape:

```json
{
  "error": {
    "code": "INSUFFICIENT_STOCK",
    "message": "Not enough stock available",
    "request_id": "uuid"
  }
}
```

Access logs are structured JSON and include:

- `method`
- `path`
- `status_code`
- `duration_ms`
- `request_id`

The middleware does not log request bodies, passwords, full cookies, or authorization headers.

## 18. Current limitations

This is a backend concurrency lab, not a complete production ticketing platform.

Current limitations:

- No real payment integration.
- No frontend.
- No deployment setup.
- Simplified authentication for study purposes.
- Local stress testing is limited by the hardware of the machine running Docker.
- No distributed tracing stack.
- No production-grade rate limiting.
- No external secrets manager.
- No multi-region or distributed database design.

## 19. Suggested next steps

Potential improvements:

- Full JWT authentication and refresh token flow.
- OpenTelemetry tracing.
- Prometheus metrics and dashboards.
- Production deployment setup.
- GitHub Actions CI pipeline.
- Rate limiting.
- Testcontainers-based integration tests.
- More detailed load-test reports.
- API versioning strategy.
- A Go implementation of the same project for comparison.
