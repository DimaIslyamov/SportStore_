from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError

from payments.models import Payment

pytestmark = pytest.mark.django_db


def payment_data(order, **overrides):
    return {
        "order": order,
        "key": uuid4(),
        "fake_outcome": "success",
        "amount": order.total,
        "currency": order.currency,
        **overrides,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "UNKNOWN"},
        {"provider": "STRIPE"},
        {"currency": "EUR"},
        {"fake_outcome": "unknown"},
        {"amount": Decimal("-0.01")},
    ],
)
def test_database_checks_stage13(order_factory, overrides):
    order = order_factory(quantities=(1,))
    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.create(**payment_data(order, **overrides))
    assert not Payment.objects.exists()


def test_key_unique_within_order_stage13(order_factory):
    order = order_factory(quantities=(1,))
    key = uuid4()
    Payment.objects.create(**payment_data(order, key=key, status="FAILED"))
    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.create(**payment_data(order, key=key, status="FAILED"))
    other_order = order_factory(quantities=(1,))
    Payment.objects.create(**payment_data(other_order, key=key, status="FAILED"))
    assert Payment.objects.count() == 2


def test_only_one_success_per_order_stage13(order_factory):
    order = order_factory(quantities=(1,))
    Payment.objects.create(**payment_data(order, status="SUCCESS"))
    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.create(**payment_data(order, status="SUCCESS"))
    Payment.objects.create(**payment_data(order, status="FAILED"))
    assert Payment.objects.count() == 2


def test_payment_protects_order_history_stage13(order_factory):
    order = order_factory(quantities=(1,))
    Payment.objects.create(**payment_data(order, status="FAILED"))
    with pytest.raises(ProtectedError):
        order.delete()
