from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    missing_owner_key = "missing_owner_key"
    webhook_auth_failed = "webhook_auth_failed"
    metrics_auth_failed = "metrics_auth_failed"
    metrics_disabled = "metrics_disabled"
    validation_error = "validation_error"
    signal_not_found = "signal_not_found"
    job_not_found = "job_not_found"
    connection_not_found = "connection_not_found"
    plan_not_found = "plan_not_found"
    http_error = "http_error"
    internal_server_error = "internal_server_error"
