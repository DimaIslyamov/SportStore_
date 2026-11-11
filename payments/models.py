from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from orders.models import MAX_ORDER_MONEY


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"
        REFUNDED = "REFUNDED", "Refunded"

    class FakeOutcome(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    order = models.ForeignKey(
        "orders.Order", on_delete=models.PROTECT, related_name="payments"
    )
    key = models.UUIDField()
    fake_outcome = models.CharField(max_length=7, choices=FakeOutcome.choices)
    status = models.CharField(
        max_length=8, choices=Status.choices, default=Status.PENDING
    )
    provider = models.CharField(max_length=16, default="FAKE")
    external_id = models.CharField(max_length=80, blank=True, default="")
    amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    currency = models.CharField(max_length=3, default="PLN")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["order", "key"], name="pay_order_key_unique"
            ),
            models.UniqueConstraint(
                fields=["order"],
                condition=Q(status="SUCCESS"),
                name="pay_one_success_per_order",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["PENDING", "SUCCESS", "FAILED", "REFUNDED"]),
                name="pay_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(amount__gte=Decimal("0.00"), amount__lte=MAX_ORDER_MONEY),
                name="pay_amount_range",
            ),
            models.CheckConstraint(
                condition=Q(currency="PLN"), name="pay_currency_pln"
            ),
            models.CheckConstraint(
                condition=Q(provider="FAKE"), name="pay_provider_fake"
            ),
            models.CheckConstraint(
                condition=Q(fake_outcome__in=["success", "failed"]),
                name="pay_fake_outcome_valid",
            ),
        ]
