# Локальная проверка (vision); образ без dev-зависимостей
FROM python:3.12-slim-bookworm

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip uv

COPY pyproject.toml uv.lock ./
COPY app ./app

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

# Переменные — через env / docker-compose, см. .env.example
CMD ["python", "-m", "app.main"]
