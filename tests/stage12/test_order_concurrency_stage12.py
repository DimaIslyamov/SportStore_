from concurrent.futures import ThreadPoolExecutor
from functools import partial
from queue import Queue
from time import monotonic, sleep
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection, transaction

from catalog.models import Inventory
from orders import services
from orders.errors import OrderError
from orders.models import Order
from payments.services import create_payment

pytestmark = pytest.mark.django_db(transaction=True)


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
        except OrderError as exc:
            return ("error", exc.status_code, str(exc.detail["code"]))
    finally:
        connection.close()


def assert_waiting_for(waiting_pid, blocking_pid, *, lock_table):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT %s = ANY(pg_blocking_pids(%s))", [blocking_pid, waiting_pid]
            )
            if cursor.fetchone()[0]:
                cursor.execute(
                    "SELECT query FROM pg_stat_activity WHERE pid = %s",
                    [waiting_pid],
                )
                row = cursor.fetchone()
                assert row is not None, "Waiting connection disappeared"
                query = row[0]
                assert f'FROM "{lock_table}"' in query and "FOR UPDATE" in query, query
                return
        sleep(0.025)
    raise AssertionError("Second connection did not wait for the first transaction")


def run_race(first, second, *, lock_table=Order._meta.db_table):
    assert connection.vendor == "postgresql"
    pids = Queue()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                blocking_pid = cursor.fetchone()[0]

            first_order = first()
            future = pool.submit(worker, second, pids)
            waiting_pid = pids.get(timeout=5)
            assert waiting_pid != blocking_pid
            assert_waiting_for(waiting_pid, blocking_pid, lock_table=lock_table)

        return first_order.status, future.result(timeout=20)


def test_concurrent_cancel_restores_once(order_factory, customer, order_variants):
    order = order_factory(quantities=(2,))
    cancel = partial(services.cancel_order, user=customer, order_id=order.pk)

    first, second = run_race(cancel, cancel)

    assert first == "CANCELLED"
    assert second == ("ok", "CANCELLED")
    order.refresh_from_db()
    assert order.status == "CANCELLED"
    assert Inventory.objects.get(variant=order_variants[0]).quantity == 10


@pytest.mark.parametrize("winner", ["cancel", "status"])
def test_cancel_and_status_transition_serialize(
    order_factory, customer, order_variants, winner
):
    order = order_factory(quantities=(2,))
    create_payment(user=customer, order_id=order.pk, key=uuid4())
    cancel = partial(services.cancel_order, user=customer, order_id=order.pk)
    process = partial(
        services.change_order_status, order_id=order.pk, target_status="PROCESSING"
    )

    if winner == "cancel":
        first, second = run_race(cancel, process)
        assert first == "CANCELLED"
        assert second == ("error", 409, "INVALID_ORDER_STATUS_TRANSITION")
        expected_quantity = 10
    else:
        first, second = run_race(process, cancel)
        assert first == "PROCESSING"
        assert second == ("error", 409, "ORDER_CANNOT_BE_CANCELLED")
        expected_quantity = 8

    order.refresh_from_db()
    assert order.status == first
    assert (
        Inventory.objects.get(variant=order_variants[0]).quantity == expected_quantity
    )


def test_concurrent_cancellation_of_distinct_orders_restores_shared_stock(
    order_factory, customer, other_customer, order_variants
):
    inventory = Inventory.objects.get(variant=order_variants[0])
    assert inventory.quantity == 10
    order_a = order_factory(quantities=(2,))
    order_b = order_factory(owner=other_customer, quantities=(3,))
    assert order_a.pk != order_b.pk
    inventory.refresh_from_db()
    assert inventory.quantity == 5

    cancel_a = partial(services.cancel_order, user=customer, order_id=order_a.pk)
    cancel_b = partial(services.cancel_order, user=other_customer, order_id=order_b.pk)

    first, second = run_race(cancel_a, cancel_b, lock_table=Inventory._meta.db_table)

    assert first == "CANCELLED"
    assert second == ("ok", "CANCELLED")
    order_a.refresh_from_db()
    order_b.refresh_from_db()
    assert order_a.status == order_b.status == "CANCELLED"
    inventory.refresh_from_db()
    assert inventory.quantity == 10

    assert cancel_a().status == cancel_b().status == "CANCELLED"
    inventory.refresh_from_db()
    assert inventory.quantity == 10
