# Локальная проверка (vision); образ без dev-зависимостей
FROM python:3.12-slim-bookworm

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip uv

COPY pyproject.toml uv.lock ./
COPY app ./app

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

# Соответствует GET /health в app; порт — как у uvicorn (env PORT, иначе 8000)
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; p=os.environ.get('PORT', '8000'); urllib.request.urlopen('http://127.0.0.1:' + p + '/health', timeout=2)"]

# Переменные — через env / docker-compose, см. .env.example
CMD ["python", "-m", "app.main"]
