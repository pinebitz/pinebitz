# pinebitz

Python backend scaffold for a DCA trading platform with:

- PostgreSQL persistence
- FastAPI control plane
- Docker Compose runtime
- Prometheus scraping for API metrics

## Services

- `postgres` -> `127.0.0.1:15432`
- `api` -> `http://127.0.0.1:18000`
- `prometheus` -> `http://127.0.0.1:19090`

## Quick Start

From repo root:

```powershell
Copy-Item .env.compose.example .env.compose
docker compose up -d --build
python -m alembic upgrade head
python scripts/api_smoke.py
```

Or use the shortcuts:

```powershell
make up
make migrate
make smoke
```

Windows helpers:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\up.ps1 -Build
powershell -ExecutionPolicy Bypass -File .\scripts\status.ps1
```

## Common Ops

Start / rebuild:

```powershell
make up
```

Stop:

```powershell
make down
powershell -ExecutionPolicy Bypass -File .\scripts\down.ps1
```

Check service state:

```powershell
make ps
make status
```

Tail logs:

```powershell
make logs
make logs-api
make logs-db
make logs-prom
```

## Compose Config

Create local overrides before first run:

```powershell
Copy-Item .env.compose.example .env.compose
```

Main values:

- `POSTGRES_HOST_PORT`
- `API_HOST_PORT`
- `PROMETHEUS_HOST_PORT`
- `METRICS_PROM_ENABLED`
- `METRICS_PROM_AUTH_MODE`
- `METRICS_PROM_TOKEN`
- `METRICS_PROM_ALLOWLIST`

Note: `deploy/prometheus/prometheus.yml` uses bearer token `change-me` by default. If you rotate
`METRICS_PROM_TOKEN`, update the scrape config token at the same time.

## Health Endpoints

- `GET /health`
- `GET /health/live`
- `GET /health/ready`

Examples:

```powershell
python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:18000/health/live').status)"
python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:18000/health/ready').status)"
```

## Metrics

JSON snapshot:

- `GET /metrics` with `X-Owner-Key`

Prometheus scrape:

- `GET /metrics/prometheus`

The scrape endpoint is protected by environment policy:

- `METRICS_PROM_ENABLED`
- `METRICS_PROM_AUTH_MODE`
- `METRICS_PROM_TOKEN`
- `METRICS_PROM_ALLOWLIST`

Example scrape:

```powershell
python -c "import urllib.request; req=urllib.request.Request('http://127.0.0.1:18000/metrics/prometheus', headers={'X-Metrics-Token':'change-me'}); print(urllib.request.urlopen(req).status)"
```

## Database

Default local connection:

```text
postgresql+asyncpg://pinebitz:pinebitz@127.0.0.1:15432/pinebitz
```

Run migrations:

```powershell
python -m alembic upgrade head
```

Ping DB:

```powershell
python scripts/pg_ping.py
```

## Smoke Tests

API / auth / CRUD / soft-delete:

```powershell
python scripts/api_smoke.py
```

Binance USD-M testnet connectivity:

```powershell
python scripts/smoke_usdm_testnet.py
```

Optional order helper:

```powershell
python scripts/order_usdm_testnet.py
```

Trade start conditions (AND gate) validation:

```powershell
# Required: OWNER_KEY and PLAN_NAME
make test-trade-start OWNER_KEY=smoke-owner PLAN_NAME="BTC USDT DCA Demo"
```

Verbose trade start details (prints `risk_checks.trade_start` for each case):

```powershell
make test-trade-start OWNER_KEY=smoke-owner PLAN_NAME="BTC USDT DCA Demo" VERBOSE_DETAIL=1
```

Optional overrides:

```powershell
make test-trade-start OWNER_KEY=smoke-owner PLAN_NAME="BTC USDT DCA Demo" API_BASE_URL=http://127.0.0.1:8000
make test-trade-start OWNER_KEY=smoke-owner PLAN_NAME="BTC USDT DCA Demo" WEBHOOK_SECRET=your-secret
```

## Handoff Notes

- API container does not run migrations automatically. Run `make migrate` after schema changes.
- Prometheus is configured to scrape the API container using bearer token `change-me`; rotate it before real deployment.
- `METRICS_PROM_ALLOWLIST` should be narrowed from broad ranges before non-local deployment.
- `scripts/api_smoke.py` is a useful post-deploy sanity check after infra or env changes.
- For Windows operators, `scripts/up.ps1`, `scripts/down.ps1`, and `scripts/status.ps1` are the
  quickest entry points for day-to-day stack operations.

## Branch and PR Convention

- Branch names: `feat/<short-topic>`, `fix/<short-topic>`, `chore/<short-topic>`.
- Keep PRs small and single-purpose when possible.
- PR title format: `<type>: <what changed>` (example: `fix: enforce trade-start AND gate`).
- Use `.github/pull_request_template.md` and complete `Summary`, `Why`, and `Test Plan`.
