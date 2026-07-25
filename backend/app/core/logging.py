from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

SAFE_EVENT_FIELDS = frozenset(
    {
        "agent_version",
        "access_state",
        "api_version",
        "artifact_bytes",
        "attempt",
        "byte_count",
        "crawl_id",
        "dependency_exit_code",
        "device_ref",
        "duration_ms",
        "error_category",
        "fallback_provider",
        "http_method",
        "item_count",
        "max_attempts",
        "operation",
        "path",
        "phase",
        "provider",
        "request_id",
        "response_model",
        "retry_count",
        "retryable",
        "scroll_count",
        "session_id",
        "screenshot_count",
        "state",
        "status_code",
        "source_adapter",
        "target_package",
        "timeout_ms",
        "transfer_id",
        "validation_issues",
    }
)


class StructuredJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "component": record.name,
            "event": record.getMessage(),
        }
        for key in SAFE_EVENT_FIELDS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True, default=str)


def configure_acquisition_logging(level: int = logging.INFO) -> None:
    logger = logging.getLogger("siksik.acquisition")
    if not any(getattr(handler, "_siksik_structured", False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredJsonFormatter())
        setattr(handler, "_siksik_structured", True)
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
