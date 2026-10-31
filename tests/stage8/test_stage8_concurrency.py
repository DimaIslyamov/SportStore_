from concurrent.futures import ThreadPoolExecutor
from functools import partial
from queue import Queue
from threading import Barrier, Event
from time import monotonic, sleep

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.http import Http404

from cart import services
from cart.models import Cart, CartItem

pytestmark = pytest.mark.django_db(transaction=True)


def worker(user_id, call, pids=None, locked=None, release=None, insert_barrier=None):
    close_old_connections()
    try:
        connection.ensure_connection()
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout = '8s'")
            cursor.execute("SELECT pg_backend_pid()")
            pid = cursor.fetchone()[0]
        if pids is not None:
            pids.put(pid)
        user = get_user_model().objects.get(pk=user_id)

        def intercept(execute, sql, params, many, context):
            if insert_barrier is not None and sql.startswith('INSERT INTO "cart_cart"'):
                insert_barrier.wait(5)
            result = execute(sql, params, many, context)
            if locked is not None and '"cart_cart"' in sql and "FOR UPDATE" in sql:
                locked.set()
                if not release.wait(8):
                    raise TimeoutError("Cart lock release timed out")
            return result

        with connection.execute_wrapper(intercept):
            return call(user=user)
    finally:
        connection.close()


def assert_blocked(blocked_pid, blocker_pid):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT %s = ANY(pg_blocking_pids(%s))", [blocker_pid, blocked_pid]
            )
            if cursor.fetchone()[0]:
                return
        sleep(0.025)
    raise AssertionError("Second writer was not blocked by first Cart lock")


def test_two_first_cart_creations_return_one_cart(customer):
    assert connection.vendor == "postgresql"
    assert not Cart.objects.filter(user=customer).exists()
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            worker, customer.pk, services.get_or_create_cart, insert_barrier=barrier
        )
        second = pool.submit(
            worker, customer.pk, services.get_or_create_cart, insert_barrier=barrier
        )
        ids = [first.result(timeout=12).pk, second.result(timeout=12).pk]
    assert ids[0] == ids[1]
    assert Cart.objects.filter(user=customer).count() == 1


def test_two_adds_without_cart_create_one_cart_and_one_item(
    customer, cart_variant_factory
):
    assert connection.vendor == "postgresql"
    variant = cart_variant_factory(stock=None)
    assert not Cart.objects.filter(user=customer).exists()
    assert not CartItem.objects.exists()
    barrier = Barrier(2)
    first_pids, second_pids = Queue(), Queue()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            worker,
            customer.pk,
            partial(services.add_item, variant_id=variant.pk, quantity=2),
            pids=first_pids,
            insert_barrier=barrier,
        )
        second = pool.submit(
            worker,
            customer.pk,
            partial(services.add_item, variant_id=variant.pk, quantity=3),
            pids=second_pids,
            insert_barrier=barrier,
        )
        first_pid = first_pids.get(timeout=5)
        second_pid = second_pids.get(timeout=5)
        assert first_pid != second_pid
        first_item, first_created = first.result(timeout=12)
        second_item, second_created = second.result(timeout=12)
    assert first_item.pk == second_item.pk
    assert sorted([first_created, second_created]) == [False, True]
    assert Cart.objects.count() == 1
    cart = Cart.objects.get(user=customer)
    assert CartItem.objects.count() == 1
    item = CartItem.objects.get(cart=cart, variant=variant)
    assert item.quantity == 5


@pytest.mark.parametrize(
    "scenario", ["first-add", "increment", "patch-first", "delete-first"]
)
def test_cart_writers_are_serialized(customer, cart_variant_factory, scenario):
    assert connection.vendor == "postgresql"
    cart = services.get_or_create_cart(user=customer)
    variant = cart_variant_factory()
    if scenario == "first-add":
        first_call = partial(services.add_item, variant_id=variant.pk, quantity=2)
        second_call = partial(services.add_item, variant_id=variant.pk, quantity=3)
    else:
        item, _ = services.add_item(user=customer, variant_id=variant.pk, quantity=2)
        if scenario == "increment":
            first_call = partial(services.add_item, variant_id=variant.pk, quantity=3)
            second_call = partial(services.add_item, variant_id=variant.pk, quantity=5)
        elif scenario == "patch-first":
            first_call = partial(
                services.update_item_quantity, item_id=item.pk, quantity=5
            )
            second_call = partial(services.remove_item, item_id=item.pk)
        else:
            first_call = partial(services.remove_item, item_id=item.pk)
            second_call = partial(
                services.update_item_quantity, item_id=item.pk, quantity=5
            )
    first_pids, second_pids = Queue(), Queue()
    locked, release = Event(), Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            worker, customer.pk, first_call, first_pids, locked, release
        )
        try:
            first_pid = first_pids.get(timeout=5)
            assert locked.wait(5)
            second = pool.submit(worker, customer.pk, second_call, second_pids)
            second_pid = second_pids.get(timeout=5)
            assert first_pid != second_pid
            assert_blocked(second_pid, first_pid)
        finally:
            release.set()
        first.result(timeout=12)
        if scenario == "delete-first":
            with pytest.raises(Http404):
                second.result(timeout=12)
        else:
            second.result(timeout=12)
    if scenario in {"first-add", "increment"}:
        assert CartItem.objects.filter(cart=cart).count() == 1
        assert CartItem.objects.get(cart=cart).quantity == (
            5 if scenario == "first-add" else 10
        )
    else:
        assert not CartItem.objects.filter(cart=cart).exists()
