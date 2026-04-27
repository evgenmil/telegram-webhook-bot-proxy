# telegram-webhook-bot-proxy

HTTP layer between your **main application** and the **Telegram Bot API**: outbound calls to Telegram go through this service; incoming webhooks from Telegram are received here and forwarded to the main app. The target is a minimal MVP to validate an integration idea (see [vision](docs/vision.md) for details).

## Features

- **Outbound traffic (transparent Bot API)** — same path shape as `https://api.telegram.org`: `https://<proxy>/bot<token>/<method>`. The token is **not** stored by the service; it is passed in the URL by the client (as in the official API). Works with libraries such as `TelegramBot\Api` (PHP): set the host to the proxy URL; the path layout stays the same.
- **Incoming webhook** — `POST` to a configurable path (default `/webhook/telegram`): the request body is forwarded to the configured main-app URL.
- **Health check** — `GET /health` for probes and monitoring.
- **Configuration from env** (including [`.env.example`](.env.example)) — main app URL, paths, timeouts, optional bearer to the backend. The Telegram webhook secret is **not** verified on the proxy (it is passed to the main app in a header when present). The bot token is **not** set in the proxy’s env.
- **Logging** — stdout; the `/bot<token>/...` path in logs is masked (no real token).

## Tech stack

| Component | Role |
|-----------|------|
| Python 3.12+ | target version |
| [uv](https://github.com/astral-sh/uv) | dependencies, lock, venv, `uv run` |
| [FastAPI](https://fastapi.tiangolo.com/) | ASGI, HTTP API |
| [Uvicorn](https://www.uvicorn.org/) | ASGI server |
| [httpx](https://www.python-httpx.org/) (async) | outbound requests to Telegram and the main app |
| [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) | environment settings validation |

## Setup and run

**Requirements:** [uv](https://github.com/astral-sh/uv) installed, and (optionally) Docker if you use containers.

### 1. Clone and dependencies

```bash
git clone <repo-url> telegram-webhook-bot-proxy
cd telegram-webhook-bot-proxy
cp .env.example .env
# edit .env: at least MAIN_APP_BASE_URL; no bot token needed in the proxy .env
```

Install dependencies (one of):

```bash
uv sync
```

Or with the Makefile:

```bash
make install
```

### 2. Run without Docker

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Or:

```bash
make run
```

(On startup, `.env` in the project directory is loaded if present; see [`.env.example`](`.env.example`).)

**Check:** `GET http://localhost:8000/health` should return `{"status":"ok"}`.

**Outbound from PHP (`TelegramBot\Api` and similar):** use the proxy’s public URL instead of `https://api.telegram.org` (same scheme/port/path as Bot API: `/bot<token>/sendMessage`, etc.). The token is set only in the client app, not in the proxy’s environment variables.

### 3. Tests (dev dependencies)

```bash
make test
# or: uv sync --extra dev && uv run pytest
```

### 4. Docker (local)

```bash
make docker-build
# place .env next to docker-compose, then:
make docker-up
```

By default the service listens on port `8000` (see [Dockerfile](Dockerfile) and [docker-compose.yaml](docker-compose.yaml)).

---

**Note (Windows, Git Bash):** when using `export` for variables whose values look like `/api/...`, MSYS may rewrite the path. Put such values in `.env` or use the `//` prefix for the first segment, as in [`.env.example`](.env.example).

More: [MVP scope and vision](docs/vision.md), [iteration plan](docs/tasklist.md).
