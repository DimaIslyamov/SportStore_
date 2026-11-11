from concurrent.futures import ThreadPoolExecutor
from functools import partial
from queue import Queue
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection, transaction

from catalog.models import Inventory
from orders.errors import OrderError
from orders.models import Order
from orders.services import cancel_order
from payments.errors import PaymentError
from payments.models import Payment
from payments.providers import FakePaymentProvider
from payments.services import create_payment
from tests.stage12.test_order_concurrency_stage12 import assert_waiting_for

pytestmark = pytest.mark.django_db(transaction=True)


def pay(**kwargs):
    return create_payment(**kwargs)[0]


def worker(call, pids):
    close_old_connections()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout = '15s'")
            cursor.execute("SET statement_timeout = '20s'")
            cursor.execute("SELECT pg_backend_pid()")
            pids.put(cursor.fetchone()[0])
        try:
            return ("ok", call().status)
        except (OrderError, PaymentError) as exc:
            return ("error", exc.status_code, str(exc.detail["code"]))
    finally:
        connection.close()


def run_race(first, second):
    assert connection.vendor == "postgresql"
    pids = Queue()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                blocking_pid = cursor.fetchone()[0]
            first_result = first()
            future = pool.submit(worker, second, pids)
            waiting_pid = pids.get(timeout=5)
            assert waiting_pid != blocking_pid
            assert_waiting_for(
                waiting_pid, blocking_pid, lock_table=Order._meta.db_table
            )
        return first_result.status, future.result(timeout=20)


@pytest.mark.parametrize("same_key", [True, False])
def test_two_success_requests_charge_once_stage13(
    order_factory, customer, order_variants, monkeypatch, same_key
):
    order = order_factory(quantities=(2,))
    key = uuid4()
    calls = []
    original = FakePaymentProvider.pay

    def counted(self, **kwargs):
        calls.append(kwargs)
        return original(self, **kwargs)

    monkeypatch.setattr(FakePaymentProvider, "pay", counted)
    first = partial(pay, user=customer, order_id=order.pk, key=key)
    second = partial(
        pay, user=customer, order_id=order.pk, key=key if same_key else uuid4()
    )
    first_status, second_result = run_race(first, second)
    assert first_status == "SUCCESS"
    assert second_result == (
        ("ok", "SUCCESS") if same_key else ("error", 409, "ALREADY_PAID")
    )
    assert len(calls) == 1
    assert Payment.objects.filter(status="SUCCESS").count() == 1
    assert Payment.objects.count() == 1
    assert Order.objects.get(pk=order.pk).status == "PAID"
    assert Inventory.objects.get(variant=order_variants[0]).quantity == 8


@pytest.mark.parametrize("winner", ["payment", "cancel"])
def test_payment_and_cancel_serialize_stage13(
    order_factory, customer, order_variants, winner
):
    order = order_factory(quantities=(2,))
    payment_call = partial(pay, user=customer, order_id=order.pk, key=uuid4())
    cancel_call = partial(cancel_order, user=customer, order_id=order.pk)
    if winner == "payment":
        first, second = run_race(payment_call, cancel_call)
        assert first == "SUCCESS" and second == ("ok", "CANCELLED")
        assert Payment.objects.get().status == "SUCCESS"
    else:
        first, second = run_race(cancel_call, payment_call)
        assert first == "CANCELLED" and second == ("error", 409, "ORDER_NOT_PAYABLE")
        assert not Payment.objects.exists()
    assert Order.objects.get(pk=order.pk).status == "CANCELLED"
    assert Inventory.objects.get(variant=order_variants[0]).quantity == 10


def test_failed_attempt_and_new_success_serialize_stage13(
    order_factory, customer, order_variants
):
    order = order_factory(quantities=(2,))
    failed = partial(
        pay, user=customer, order_id=order.pk, key=uuid4(), outcome="failed"
    )
    success = partial(pay, user=customer, order_id=order.pk, key=uuid4())
    first, second = run_race(failed, success)
    assert first == "FAILED" and second == ("ok", "SUCCESS")
    assert list(Payment.objects.values_list("status", flat=True)) == [
        "FAILED",
        "SUCCESS",
    ]
    assert Order.objects.get(pk=order.pk).status == "PAID"
    assert Inventory.objects.get(variant=order_variants[0]).quantity == 8
