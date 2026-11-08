import hashlib
import json
import re
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from django.db import OperationalError, connection, transaction
from django.utils import timezone
from rest_framework import status as http
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.renderers import JSONRenderer

from . import services
from .errors import CheckoutError
from .models import IdempotencyRecord
from .serializers import CheckoutOrderOutputSerializer

OPERATION = "checkout"
RESULT_TTL = timedelta(hours=24)
RECOVERY_AFTER = timedelta(minutes=2)
RESERVATION_LOCK_TIMEOUT = "1s"
ABORTED_CODE = "IDEMPOTENCY_ATTEMPT_ABORTED"
ABORTED_DETAIL = "Checkout did not finish. Use a new key for a new attempt."
UUID_TEXT = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
State = IdempotencyRecord.Status


@dataclass(frozen=True)
class CheckoutResult:
    status_code: int
    body: dict


def parse_idempotency_key(value: str | None) -> UUID:
    text = (value or "").strip()
    if UUID_TEXT.fullmatch(text) is None:
        raise ValidationError({"idempotency_key": ["Idempotency-Key must be a UUID."]})
    return UUID(text)


def checkout_request_hash(address_id: int) -> str:
    payload = json.dumps(
        {"address_id": address_id}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _require_autocommit():
    if not connection.get_autocommit():
        raise RuntimeError("Checkout idempotency requires an outer autocommit context.")


def _reserve(*, user_id, key, request_hash):
    identity = {
        "user_id": user_id,
        "operation": OPERATION,
        "key": key,
    }
    now = timezone.now()

    try:
        with transaction.atomic(durable=True):
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('lock_timeout', %s, true)",
                    [RESERVATION_LOCK_TIMEOUT],
                )
            return IdempotencyRecord.objects.get_or_create(
                **identity,
                defaults={
                    "request_hash": request_hash,
                    "created_at": now,
                    "expires_at": now + RESULT_TTL,
                },
            )

    except OperationalError as exc:
        if getattr(exc.__cause__, "sqlstate", None) != "55P03":
            raise

        record = IdempotencyRecord.objects.filter(**identity).first()
        if record is not None:
            return record, False

        raise CheckoutError(
            "IDEMPOTENCY_RESERVATION_BUSY",
            "Checkout key is not visible yet. Retry with the same key.",
            status_code=http.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc


def _saved(record):
    return CheckoutResult(record.response_status_code, record.response_body)


def _finish(record, *, state, status_code, body, resource_id=None):
    record.status = state
    record.resource_id = resource_id
    record.response_status_code = status_code
    record.response_body = json.loads(JSONRenderer().render(body))
    record.expires_at = timezone.now() + RESULT_TTL
    record.save(
        update_fields=[
            "status",
            "resource",
            "response_status_code",
            "response_body",
            "expires_at",
        ]
    )
    return _saved(record)


def _execute(*, record_id, user, address_id):
    with transaction.atomic(durable=True):
        record = (
            IdempotencyRecord.objects.select_for_update().filter(pk=record_id).first()
        )

        if record is None:
            raise CheckoutError(
                ABORTED_CODE,
                ABORTED_DETAIL,
                status_code=http.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if record.status != State.PROCESSING:
            return _saved(record)

        try:
            order = services.checkout(
                user=user,
                address_id=address_id,
            )
        except (CheckoutError, PermissionDenied) as exc:
            body = (
                exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
            )
            return _finish(
                record, state=State.FAILED, status_code=exc.status_code, body=body
            )

        return _finish(
            record,
            state=State.COMPLETED,
            status_code=http.HTTP_201_CREATED,
            body=CheckoutOrderOutputSerializer(order).data,
            resource_id=order.pk,
        )


def idempotent_checkout(*, user, address_id: int, key: UUID) -> CheckoutResult:
    _require_autocommit()
    request_hash = checkout_request_hash(address_id)
    record, created = _reserve(
        user_id=user.pk,
        key=key,
        request_hash=request_hash,
    )

    if created:
        return _execute(record_id=record.pk, user=user, address_id=address_id)

    if record.request_hash != request_hash:
        raise CheckoutError(
            "IDEMPOTENCY_KEY_REUSED",
            "This key was used with a different checkout payload.",
        )

    if record.status == State.PROCESSING:
        raise CheckoutError(
            "IDEMPOTENCY_IN_PROGRESS", "Checkout is still processing for this key."
        )

    return _saved(record)


def cleanup_expired_checkout_keys(*, batch_size: int) -> tuple[int, int]:
    _require_autocommit()
    now = timezone.now()
    records = IdempotencyRecord.objects.filter(operation=OPERATION)

    with transaction.atomic(durable=True):
        stale = list(
            records.select_for_update(skip_locked=True)
            .filter(status=State.PROCESSING, created_at__lte=now - RECOVERY_AFTER)
            .order_by("created_at", "pk")[:batch_size]
        )

        for record in stale:
            _finish(
                record,
                state=State.FAILED,
                status_code=http.HTTP_503_SERVICE_UNAVAILABLE,
                body={"code": ABORTED_CODE, "detail": ABORTED_DETAIL},
            )

        expired = list(
            records.select_for_update(skip_locked=True)
            .filter(status__in=[State.COMPLETED, State.FAILED], expires_at__lte=now)
            .order_by("expires_at", "pk")
            .values_list("pk", flat=True)[:batch_size]
        )

        deleted, _ = records.filter(pk__in=expired).delete()

    return len(stale), deleted
