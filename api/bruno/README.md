# Bruno API Collection

This folder contains a versioned Bruno collection for testing the Ticket Reservation Python Lab API.

The collection uses Bruno's native `.bru` format for broad UI compatibility.

## How to open

1. Open Bruno.
2. Choose **Open Collection**.
3. Select this folder:

```text
api/bruno/
```

If Bruno shows empty folders, remove the collection from Bruno's sidebar, restart Bruno, and open
this same `api/bruno/` folder again. The `bruno.json` file in this folder is the collection root.

The collection is organized by API domain:

- `health`
- `sessions`
- `auth`
- `events`
- `reservations`
- `stress-admin`

## Local environment

The local environment uses:

```text
baseURL=http://localhost:8000
```

This matches the FastAPI port exposed by `docker-compose.yml`.

Start the API before sending requests:

```bash
docker compose up --build
```

## Local secret file

Copy the sample file if you want local-only values outside the versioned environment files:

```bash
cp api/bruno/.env.sample api/bruno/.env
```

Do not commit `.env` files. They are ignored by `.gitignore`.

The app currently uses an HTTP-only `visitor_session` cookie for session-protected flows.
Run `sessions/Create Anonymous Session` first. Bruno should keep the returned cookie in its cookie
jar for subsequent requests.

The `Authorization: Bearer {{token}}` header is included only on session-protected request files as
a placeholder for future bearer-token auth. The current local API does not require a bearer token.

## Suggested manual flow

1. Run `health/Healthcheck`.
2. Run `health/Readiness`.
3. Run `sessions/Create Anonymous Session`.
4. Run `sessions/Get Current Session`.
5. Run `stress-admin/Seed Stress Fixture`.
6. Copy the returned `event_id`, `ticket_type_id`, and one or two `seat_ids` into the active Bruno environment.
7. Run `reservations/Reserve Quantity` or `reservations/Reserve Seats`.
8. Copy the returned `reservation_id` into the active environment.
9. Run `reservations/Get Reservation`, `Cancel Reservation`, or `Confirm Reservation`.
10. Run `stress-admin/Assert Consistency`.

## Adding new requests

Keep requests grouped by domain. For example:

```text
api/bruno/events/new-request.yml
api/bruno/reservations/new-request.yml
```

Use `{{baseURL}}` instead of hardcoding the host:

```text
{{baseURL}}/v1/example
```

Never commit real tokens, cookies, passwords, API keys, or production URLs with credentials.
