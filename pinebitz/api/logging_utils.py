from __future__ import annotations

import json
import logging
from datetime import datetime, timezone


LOGGER_NAME = "pinebitz.api"


def configure_logging() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def emit_request_log(
    logger: logging.Logger,
    *,
    request_id: str,
    method: str,
    path: str,
    status_code: int,
    duration_ms: int,
    owner_key: str | None,
) -> None:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "method": method,
        "path": path,
        "status_code": status_code,
        "duration_ms": duration_ms,
        "owner_key": owner_key,
    }
    logger.info(json.dumps(payload, separators=(",", ":"), ensure_ascii=True))
