"""API smoke including owner header, create, patch and soft-delete flows."""

from fastapi.testclient import TestClient

from pinebitz.api import app


def main() -> None:
    owner = "smoke-owner"
    h = {"X-Owner-Key": owner, "X-Request-ID": "req-smoke-001", "X-Forwarded-For": "127.0.0.1"}
    c = TestClient(app)

    r = c.get("/health")
    print("/health", r.status_code, r.headers.get("X-Request-ID"), r.headers.get("X-Response-Time-Ms"))
    print("/health/live", c.get("/health/live").status_code)
    print("/health/ready", c.get("/health/ready").status_code)

    r_empty_connections = c.get("/connections?limit=5&offset=0", headers=h)
    print("GET /connections", r_empty_connections.status_code)

    rc = c.post(
        "/connections",
        headers=h,
        json={
            "label": "Smoke Binance UM",
            "venue": "binance",
            "market_lane": "futures_um",
            "environment": "testnet",
            "credential_ref": "vault://smoke/binance",
        },
    )
    print("POST /connections", rc.status_code)
    connection_id = rc.json()["id"]

    rp = c.post(
        "/bot-plans",
        headers=h,
        json={
            "connection_id": connection_id,
            "name": "Smoke Plan",
            "instrument_kind": "futures",
            "enabled": True,
            "config_json": {"pair": "BTC/USDT:USDT"},
        },
    )
    print("POST /bot-plans", rp.status_code)
    plan_id = rp.json()["id"]

    r_get_conn = c.get(f"/connections/{connection_id}", headers=h)
    print("GET /connections/{id}", r_get_conn.status_code)
    r_get_plan = c.get(f"/bot-plans/{plan_id}", headers=h)
    print("GET /bot-plans/{id}", r_get_plan.status_code)

    rpatch = c.patch(f"/bot-plans/{plan_id}", headers=h, json={"enabled": False, "plan_version": 2})
    print("PATCH /bot-plans/{id}", rpatch.status_code, rpatch.json().get("plan_version"))

    rlist = c.get("/dashboard/bots", headers=h)
    print("GET /dashboard/bots", rlist.status_code, len(rlist.json()))

    r_metrics = c.get("/metrics", headers=h)
    print("GET /metrics", r_metrics.status_code, r_metrics.json().get("total_requests"))
    r_metrics_prom = c.get("/metrics/prometheus", headers=h)
    print("GET /metrics/prometheus", r_metrics_prom.status_code, "pinebitz_http_requests_total" in r_metrics_prom.text)

    r_list_conn = c.get("/connections?sort_by=label&sort_dir=asc&limit=10&offset=0", headers=h)
    print("GET /connections paged", r_list_conn.status_code, r_list_conn.json()["meta"]["total"])

    r_list_plan = c.get("/bot-plans?sort_by=name&sort_dir=asc&limit=10&offset=0", headers=h)
    print("GET /bot-plans paged", r_list_plan.status_code, r_list_plan.json()["meta"]["total"])

    rdel_plan = c.delete(f"/bot-plans/{plan_id}", headers=h)
    print("DELETE /bot-plans/{id}", rdel_plan.status_code, rdel_plan.json().get("status"))

    r_deleted_get = c.get(f"/bot-plans/{plan_id}", headers=h)
    print(
        "GET deleted /bot-plans/{id}",
        r_deleted_get.status_code,
        r_deleted_get.json().get("code"),
        r_deleted_get.json().get("request_id"),
    )

    rdel_conn = c.delete(f"/connections/{connection_id}", headers=h)
    print("DELETE /connections/{id}", rdel_conn.status_code, rdel_conn.json().get("status"))


if __name__ == "__main__":
    main()
