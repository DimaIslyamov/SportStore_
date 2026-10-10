PYTHON ?= .venv/bin/python

.PHONY: install check migrations migrate run test lint format up down logs
.PHONY: docker-check docker-test docker-migrate docker-migrations
.PHONY: tokens-clean docker-tokens-clean

tokens-clean:
	$(PYTHON) manage.py flushexpiredtokens

docker-tokens-clean:
	docker compose exec django python manage.py flushexpiredtokens

install:
	$(PYTHON) -m pip install -r requirements-dev.txt

check:
	$(PYTHON) manage.py check
	$(PYTHON) manage.py makemigrations --check --dry-run

migrations:
	$(PYTHON) manage.py makemigrations

migrate:
	$(PYTHON) manage.py migrate

run:
	$(PYTHON) manage.py runserver 127.0.0.1:8000

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

format:
	$(PYTHON) -m ruff format .

up:
	docker compose up -d --build --wait

down:
	docker compose down

logs:
	docker compose logs -f django

docker-check:
	docker compose exec django python manage.py check
	docker compose exec django python manage.py makemigrations --check --dry-run

docker-test:
	docker compose exec django python -m pytest -q

docker-migrate:
	docker compose exec django python manage.py migrate

docker-migrations:
	docker compose exec django python manage.py makemigrations