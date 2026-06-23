.PHONY: up stress-up down test lint format typecheck migrate k6 stress stress-quantity stress-seats stress-reset stress-seed stress-locust assert

K6_BASE_URL ?= http://api:8000
K6_VUS ?= 100
K6_DURATION ?= 30s

up:
	docker compose up --build

stress-up:
	LOG_LEVEL=WARN docker compose up --build -d

down:
	docker compose down

test:
	docker compose exec api pytest

lint:
	docker compose exec api ruff check .

format:
	docker compose exec api ruff format .

typecheck:
	docker compose exec api mypy

migrate:
	docker compose run --rm migrate

stress-reset:
	curl -X POST http://localhost:8000/v1/admin/stress/reset

stress-seed:
	curl -X POST http://localhost:8000/v1/admin/stress/seed

stress:
	docker compose run --rm --no-deps k6 run -e BASE_URL=$(K6_BASE_URL) -e VUS=$(K6_VUS) -e DURATION=$(K6_DURATION) /scripts/mixed.js

k6: stress

stress-quantity:
	docker compose run --rm --no-deps k6 run -e BASE_URL=$(K6_BASE_URL) -e VUS=$(K6_VUS) -e DURATION=$(K6_DURATION) /scripts/quantity.js

stress-seats:
	docker compose run --rm --no-deps k6 run -e BASE_URL=$(K6_BASE_URL) -e VUS=$(K6_VUS) -e DURATION=$(K6_DURATION) /scripts/seats.js

stress-locust:
	docker compose run --rm locust -f /app/stress/locustfile.py --headless -u 1000 -r 100 --run-time 1m --host http://api:8000

assert:
	docker compose exec api python -c 'import json, sys, urllib.request; data = json.load(urllib.request.urlopen("http://localhost:8000/v1/admin/stress/assert-consistency")); print(json.dumps(data)); sys.exit(0 if data.get("ok") is True else 1)'
