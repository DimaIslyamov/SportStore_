import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone

from django.db import transaction

request_id_context = ContextVar("request_id", default="-")
logger = logging.getLogger("sportstore")
EVENTS = {
    "api_error",
    "request_completed",
    "order_created",
    "order_cancelled",
    "order_status_changed",
    "payment_succeeded",
    "payment_failed",
}


def get_request_id():
    return request_id_context.get()


class JsonFormatter(logging.Formatter):
    def format(self, record):
        event = (
            record.msg
            if isinstance(record.msg, str) and record.msg in EVENTS
            else "application_log"
        )
        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created, timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": event,
            "request_id": getattr(
                record,
                "request_id",
                getattr(
                    getattr(record, "request", None), "request_id", get_request_id()
                ),
            ),
        }

        for name in ("order_id", "payment_id", "user_id", "status_code"):
            value = getattr(record, name, None)
            if type(value) is int:
                payload[name] = value

        for name in ("error_code", "from_status", "to_status", "exception_type"):
            value = getattr(record, name, None)
            if (
                isinstance(value, str)
                and value.isascii()
                and value.replace("_", "").isalnum()
                and len(value) <= 80
            ):
                payload[name] = value
        # No getMessage(), request, body, headers, args or traceback serialization.
        return json.dumps(payload, ensure_ascii=False)


def log_on_commit(event, *, level=logging.INFO, **fields):
    fields = {**fields, "request_id": get_request_id()}
    transaction.on_commit(lambda: logger.log(level, event, extra=fields), robust=True)
