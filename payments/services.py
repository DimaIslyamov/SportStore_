import logging
from uuid import UUID

from django.db import transaction
from rest_framework.exceptions import PermissionDenied, ValidationError

from core.logging import log_on_commit

from accounts.models import User
from orders import services as order_services
from orders.models import Order
from orders.querysets import get_order_or_404

from .errors import PaymentError
from .models import Payment
from .providers import FakePaymentProvider


@transaction.atomic
def create_payment(*, user, order_id, key, outcome="success"):
    if user is None or not (
        user.is_authenticated and user.is_active and user.role == User.Role.CUSTOMER
    ):
        raise PermissionDenied("Active customer role is required.")
    if not isinstance(key, UUID):
        raise ValidationError({"idempotency_key": ["Use a parsed UUID key."]})
    if outcome not in Payment.FakeOutcome.values:
        raise ValidationError({"outcome": ["Use success or failed."]})

    order = get_order_or_404(
        Order.objects.select_for_update().filter(user_id=user.pk),
        order_id=order_id,
    )

    previous = Payment.objects.filter(order=order, key=key).first()
    if previous is not None:
        if previous.fake_outcome != outcome:
            raise PaymentError(
                "IDEMPOTENCY_KEY_REUSED",
                "This key was used with a different payment payload.",
            )
        return previous, False

    if order.status == Order.Status.CANCELLED:
        raise PaymentError("ORDER_NOT_PAYABLE", "Cancelled order cannot be paid.")
    if (
        order.status != Order.Status.PENDING
        or Payment.objects.filter(order=order, status=Payment.Status.SUCCESS).exists()
    ):
        raise PaymentError("ALREADY_PAID", "Order has already been paid.")

    payment = Payment.objects.create(
        order=order,
        key=key,
        fake_outcome=outcome,
        provider="FAKE",
        amount=order.total,
        currency=order.currency,
    )
    result = FakePaymentProvider().pay(
        amount=payment.amount,
        currency=payment.currency,
        outcome=outcome,
    )
    payment.status = (
        Payment.Status.SUCCESS if result.succeeded else Payment.Status.FAILED
    )
    payment.external_id = result.external_id
    payment.save(update_fields=["status", "external_id", "updated_at"])

    if result.succeeded:
        order_services.change_order_status(
            order_id=order.pk, target_status=Order.Status.PAID
        )

    log_on_commit(
        "payment_succeeded" if result.succeeded else "payment_failed",
        level=logging.INFO if result.succeeded else logging.WARNING,
        payment_id=payment.pk,
        order_id=order.pk,
        user_id=user.pk,
    )

    return payment, True
