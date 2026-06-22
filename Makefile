.PHONY: up down test lint format typecheck migrate stress assert

up:
	docker compose up --build

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

stress:
	docker compose run --rm locust -f /app/stress/locustfile.py --headless -u 1000 -r 100 --run-time 1m --host http://api:8000

assert:
	docker compose exec api python -c 'import json, sys, urllib.request; data = json.load(urllib.request.urlopen("http://localhost:8000/v1/admin/stress/assert-consistency")); print(json.dumps(data)); sys.exit(0 if data.get("ok") is True else 1)'
