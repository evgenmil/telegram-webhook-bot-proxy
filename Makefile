# См. docs/vision.md: uv + короткие команды. Зависимости: `uv` в PATH, Docker — опционально.

.PHONY: install run test docker-build docker-up

install:
	uv sync

run: install
	uv run python -m app.main

test:
	uv sync --extra dev
	uv run pytest -q

docker-build:
	docker build -t telegram-webhook-bot-proxy:local .

docker-up:
	docker compose up --build
