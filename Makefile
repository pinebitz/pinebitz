PYTHON ?= python
COMPOSE ?= docker compose

.PHONY: up down restart ps logs logs-api logs-db logs-prom build migrate smoke api-smoke pg-ping status test-trade-start

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) down
	$(COMPOSE) up -d --build

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs --tail=200

logs-api:
	$(COMPOSE) logs --tail=200 api

logs-db:
	$(COMPOSE) logs --tail=200 postgres

logs-prom:
	$(COMPOSE) logs --tail=200 prometheus

build:
	$(COMPOSE) build

migrate:
	$(PYTHON) -m alembic upgrade head

pg-ping:
	$(PYTHON) scripts/pg_ping.py

api-smoke:
	$(PYTHON) scripts/api_smoke.py

smoke:
	$(PYTHON) scripts/pg_ping.py
	$(PYTHON) scripts/api_smoke.py

status:
	$(PYTHON) -c "import urllib.request, json; print('live', urllib.request.urlopen('http://127.0.0.1:18000/health/live').status); print('ready', urllib.request.urlopen('http://127.0.0.1:18000/health/ready').status); req=urllib.request.Request('http://127.0.0.1:18000/metrics/prometheus', headers={'X-Metrics-Token':'change-me'}); print('metrics', urllib.request.urlopen(req).status); data=json.loads(urllib.request.urlopen('http://127.0.0.1:19090/api/v1/targets').read().decode()); print('prom_targets', len(data['data']['activeTargets']))"

test-trade-start:
	powershell -ExecutionPolicy Bypass -File ./scripts/test_trade_start_conditions.ps1 -ApiBaseUrl "$(or $(API_BASE_URL),http://127.0.0.1:8000)" -OwnerKey "$(OWNER_KEY)" -PlanName "$(PLAN_NAME)" -Symbol "$(or $(SYMBOL),BTCUSDT)" $(if $(WEBHOOK_SECRET),-WebhookSecret "$(WEBHOOK_SECRET)") $(if $(VERBOSE_DETAIL),-VerboseDetail)
