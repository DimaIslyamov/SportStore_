from decimal import Decimal
from uuid import uuid4

import pytest
from rest_framework.exceptions import PermissionDenied, ValidationError

from catalog.models import Inventory, ProductVariant
from orders import services as order_services
from orders.errors import OrderError
from orders.models import Order
from payments.errors import PaymentError
from payments.models import Payment
from payments.providers import FakePaymentProvider
from payments.services import create_payment

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "outcome, status", [("success", "SUCCESS"), ("failed", "FAILED")]
)
def test_payment_snapshots_and_effects_stage13(
    order_factory, customer, order_variants, monkeypatch, outcome, status
):
    order = order_factory(quantities=(2,))
    original_updated_at = order.updated_at
    ProductVariant.objects.filter(pk=order_variants[0].pk).update(
        price=Decimal("99.00")
    )
    calls = []
    original_pay = FakePaymentProvider.pay

    def record_call(self, **kwargs):
        calls.append(kwargs)
        return original_pay(self, **kwargs)

    monkeypatch.setattr(FakePaymentProvider, "pay", record_call)
    payment, created = create_payment(
        user=customer, order_id=order.pk, key=uuid4(), outcome=outcome
    )

    assert created
    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == status
    assert payment.order_id == order.pk
    assert payment.provider == "FAKE"
    assert payment.external_id.startswith("fake_")
    assert payment.amount == order.total == Decimal("39.80")
    assert payment.currency == order.currency == "PLN"
    assert calls == [{"amount": order.total, "currency": "PLN", "outcome": outcome}]
    assert order.status == ("PAID" if outcome == "success" else "PENDING")
    if outcome == "failed":
        assert order.updated_at == original_updated_at
    assert Inventory.objects.get(variant=order_variants[0]).quantity == 8


@pytest.mark.parametrize("outcome", ["success", "failed"])
def test_same_key_replays_without_provider_stage13(
    order_factory, customer, monkeypatch, outcome
):
    order = order_factory(quantities=(2,))
    key = uuid4()
    first, _ = create_payment(
        user=customer, order_id=order.pk, key=key, outcome=outcome
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Provider was called during replay")

    monkeypatch.setattr(FakePaymentProvider, "pay", forbidden)
    repeated, created = create_payment(
        user=customer, order_id=order.pk, key=key, outcome=outcome
    )
    assert not created
    assert repeated.pk == first.pk
    assert repeated.external_id == first.external_id
    assert repeated.updated_at == first.updated_at
    assert Payment.objects.count() == 1


def test_failed_attempt_then_new_key_success_stage13(order_factory, customer):
    order = order_factory(quantities=(2,))
    old_key = uuid4()
    failed, _ = create_payment(
        user=customer, order_id=order.pk, key=old_key, outcome="failed"
    )
    success, _ = create_payment(user=customer, order_id=order.pk, key=uuid4())
    old_receipt, created = create_payment(
        user=customer, order_id=order.pk, key=old_key, outcome="failed"
    )
    order.refresh_from_db()
    assert order.status == "PAID"
    assert failed.status == old_receipt.status == "FAILED"
    assert old_receipt.pk == failed.pk and not created
    assert success.status == "SUCCESS"
    assert Payment.objects.count() == 2


def test_changed_payload_and_second_charge_are_rejected_stage13(
    order_factory, customer
):
    order = order_factory(quantities=(2,))
    key = uuid4()
    create_payment(user=customer, order_id=order.pk, key=key)
    for repeated_key, outcome, code in [
        (key, "failed", "IDEMPOTENCY_KEY_REUSED"),
        (uuid4(), "success", "ALREADY_PAID"),
    ]:
        with pytest.raises(PaymentError) as caught:
            create_payment(
                user=customer, order_id=order.pk, key=repeated_key, outcome=outcome
            )
        assert caught.value.status_code == 409
        assert caught.value.detail["code"] == code
    assert Payment.objects.count() == 1


@pytest.mark.parametrize(
    "status", ["PAID", "PROCESSING", "SHIPPED", "DELIVERED", "CANCELLED"]
)
def test_non_pending_order_cannot_start_payment_stage13(
    order_factory, customer, status
):
    order = order_factory(quantities=(2,))
    # Only test preparation: also covers legacy manually marked PAID orders.
    Order.objects.filter(pk=order.pk).update(status=status)
    with pytest.raises(PaymentError) as caught:
        create_payment(user=customer, order_id=order.pk, key=uuid4())
    expected = "ORDER_NOT_PAYABLE" if status == "CANCELLED" else "ALREADY_PAID"
    assert caught.value.detail["code"] == expected
    assert not Payment.objects.exists()


def test_owner_and_service_role_checks_stage13(
    order_factory, customer, other_customer, manager
):
    order = order_factory(quantities=(2,))
    with pytest.raises(OrderError) as caught:
        create_payment(user=other_customer, order_id=order.pk, key=uuid4())
    assert caught.value.status_code == 404
    assert caught.value.detail["code"] == "ORDER_NOT_FOUND"
    for actor in (None, manager):
        with pytest.raises(PermissionDenied):
            create_payment(user=actor, order_id=order.pk, key=uuid4())
    customer.is_active = False
    with pytest.raises(PermissionDenied):
        create_payment(user=customer, order_id=order.pk, key=uuid4())
    assert not Payment.objects.exists()


@pytest.mark.parametrize("kwargs", [{"key": "bad"}, {"outcome": "unknown"}])
def test_service_rejects_invalid_input_stage13(order_factory, customer, kwargs):
    order = order_factory(quantities=(2,))
    inputs = {"key": uuid4(), "outcome": "success", **kwargs}
    with pytest.raises(ValidationError):
        create_payment(user=customer, order_id=order.pk, **inputs)
    assert not Payment.objects.exists()


def test_state_machine_requires_matching_payment_stage13(order_factory, customer):
    order = order_factory(quantities=(2,))
    with pytest.raises(OrderError) as caught:
        order_services.change_order_status(order_id=order.pk, target_status="PAID")
    assert caught.value.detail["code"] == "PAYMENT_CONFIRMATION_REQUIRED"
    payment = Payment.objects.create(
        order=order,
        key=uuid4(),
        fake_outcome="success",
        status="SUCCESS",
        amount=order.total + Decimal("1.00"),
        currency="PLN",
        external_id="fake_wrong",
    )
    with pytest.raises(OrderError) as caught:
        order_services.change_order_status(order_id=order.pk, target_status="PAID")
    assert caught.value.detail["code"] == "PAYMENT_CONFIRMATION_REQUIRED"
    payment.delete()
    create_payment(user=customer, order_id=order.pk, key=uuid4())
    order.refresh_from_db()
    assert order.status == "PAID"


@pytest.mark.parametrize("where", ["provider", "after_order_update"])
def test_unexpected_error_rolls_back_everything_stage13(
    order_factory, customer, order_variants, monkeypatch, where
):
    order = order_factory(quantities=(2,))
    before = (order.status, order.updated_at)
    key = uuid4()
    with monkeypatch.context() as patch:
        if where == "provider":

            def broken(*args, **kwargs):
                raise RuntimeError("Simulated provider exception")

            patch.setattr(FakePaymentProvider, "pay", broken)
        else:
            original = order_services.change_order_status

            def broken(**kwargs):
                original(**kwargs)
                raise RuntimeError("Simulated late exception")

            patch.setattr(order_services, "change_order_status", broken)
        with pytest.raises(RuntimeError):
            create_payment(user=customer, order_id=order.pk, key=key)
    order.refresh_from_db()
    assert (order.status, order.updated_at) == before
    assert not Payment.objects.exists()
    assert Inventory.objects.get(variant=order_variants[0]).quantity == 8
    # No published PENDING reservation survives: retry can safely execute the fake.
    payment, created = create_payment(user=customer, order_id=order.pk, key=key)
    assert created and payment.status == "SUCCESS"


@pytest.mark.parametrize("actor", ["customer", "manager"])
def test_paid_cancel_preserves_fake_receipt_and_restores_once_stage13(
    order_factory, customer, order_variants, actor
):
    order = order_factory(quantities=(2,))
    key = uuid4()
    payment, _ = create_payment(user=customer, order_id=order.pk, key=key)
    before = Payment.objects.values().get(pk=payment.pk)
    if actor == "customer":
        order_services.cancel_order(user=customer, order_id=order.pk)
    else:
        # Like Stage 12: caller of this service has already checked IsManager.
        order_services.change_order_status(order_id=order.pk, target_status="CANCELLED")
    order_services.cancel_order(user=customer, order_id=order.pk)
    receipt, created = create_payment(user=customer, order_id=order.pk, key=key)
    assert not created and receipt.pk == payment.pk
    assert Payment.objects.values().get(pk=payment.pk) == before
    order.refresh_from_db()
    assert order.status == "CANCELLED"
    assert Inventory.objects.get(variant=order_variants[0]).quantity == 10
    with pytest.raises(PaymentError) as caught:
        create_payment(user=customer, order_id=order.pk, key=uuid4())
    assert caught.value.detail["code"] == "ORDER_NOT_PAYABLE"
