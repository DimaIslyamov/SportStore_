from concurrent.futures import ThreadPoolExecutor
from functools import partial
from queue import Queue
from threading import Event
from time import monotonic, sleep

import pytest
from django.db import close_old_connections, connection
from django.http import Http404

from accounts.models import User
from cart import services as cart_services
from cart.models import CartItem
from catalog import services as catalog_services
from catalog.models import Inventory, ProductVariant
from orders import services
from orders.errors import CheckoutError
from orders.models import Order

pytestmark = pytest.mark.django_db(transaction=True)


def worker(call, pids, *, table=None, locked=None, release=None):
    close_old_connections()
    try:
        connection.ensure_connection()
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout = '30s'")
            cursor.execute("SET statement_timeout = '35s'")
            cursor.execute("SELECT pg_backend_pid()")
            pids.put(cursor.fetchone()[0])
        paused = False

        def intercept(execute, sql, params, many, context):
            nonlocal paused
            result = execute(sql, params, many, context)
            is_lock = "FOR UPDATE" in sql or "FOR NO KEY UPDATE" in sql
            if (
                table
                and not paused
                and sql.lstrip().upper().startswith("SELECT")
                and f'"{table}"' in sql
                and is_lock
            ):
                paused = True
                locked.set()
                if not release.wait(30):
                    raise TimeoutError("First transaction release timed out")
            return result

        with connection.execute_wrapper(intercept):
            return call()
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
    raise AssertionError("Second PostgreSQL connection did not wait for the first lock")


def outcome(future):
    try:
        return "ok", future.result(timeout=40)
    except CheckoutError as exc:
        return "error", str(exc.detail["code"])
    except Http404:
        return "error", "NOT_FOUND"


def race(first_call, second_call, *, table):
    assert connection.vendor == "postgresql"
    first_pids, second_pids = Queue(), Queue()
    locked, release = Event(), Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            worker, first_call, first_pids, table=table, locked=locked, release=release
        )
        try:
            first_pid = first_pids.get(timeout=5)
            assert locked.wait(5), "First worker did not reach its locking SELECT"
            second = pool.submit(worker, second_call, second_pids)
            second_pid = second_pids.get(timeout=5)
            assert first_pid != second_pid
            assert_blocked(second_pid, first_pid)
        finally:
            release.set()
        return outcome(first), outcome(second)


def do_checkout(user_id, address_id):
    return services.checkout(user=User.objects.get(pk=user_id), address_id=address_id)


def do_cart_update(user_id, item_id, quantity):
    return cart_services.update_item_quantity(
        user=User.objects.get(pk=user_id), item_id=item_id, quantity=quantity
    )


def test_two_customers_buy_last_unit_without_overselling(
    checkout_factory, checkout_catalog, other_checkout_customer
):
    variant = checkout_catalog.variants[0]
    Inventory.objects.filter(variant=variant).update(quantity=1)
    first_case = checkout_factory(quantities=(1,))
    second_case = checkout_factory(owner=other_checkout_customer, quantities=(1,))
    first, second = race(
        partial(do_checkout, first_case.user.pk, first_case.address.pk),
        partial(do_checkout, second_case.user.pk, second_case.address.pk),
        table="catalog_productvariant",
    )
    assert first[0] == "ok" and second == ("error", "OUT_OF_STOCK")
    assert Order.objects.count() == 1
    assert Inventory.objects.get(variant=variant).quantity == 0
    assert not CartItem.objects.filter(cart=first_case.cart).exists()
    assert CartItem.objects.get(cart=second_case.cart).quantity == 1


def test_two_checkouts_of_same_cart_create_only_one_order(checkout_factory):
    case = checkout_factory(quantities=(2,))
    call = partial(do_checkout, case.user.pk, case.address.pk)
    first, second = race(call, call, table="accounts_user")
    assert first[0] == "ok" and second == ("error", "EMPTY_CART")
    assert Order.objects.count() == 1
    assert Inventory.objects.get(variant=case.variants[0]).quantity == 8
    assert not CartItem.objects.filter(cart=case.cart).exists()


@pytest.mark.parametrize("first_writer", ["cart", "checkout"])
def test_cart_change_and_checkout_serialize(checkout_factory, first_writer):
    case = checkout_factory(quantities=(2,))
    checkout = partial(do_checkout, case.user.pk, case.address.pk)
    update = partial(do_cart_update, case.user.pk, case.items[0].pk, 3)
    calls = (update, checkout) if first_writer == "cart" else (checkout, update)
    first, second = race(*calls, table="cart_cart")
    assert first[0] == "ok"
    order = Order.objects.get()
    if first_writer == "cart":
        assert second[0] == "ok"
        assert order.items.get().quantity == 3
        assert Inventory.objects.get(variant=case.variants[0]).quantity == 7
    else:
        assert second == ("error", "NOT_FOUND")
        assert order.items.get().quantity == 2
        assert Inventory.objects.get(variant=case.variants[0]).quantity == 8
    assert not CartItem.objects.filter(cart=case.cart).exists()


@pytest.mark.parametrize("first_writer", ["manager", "checkout"])
def test_manager_absolute_inventory_update_is_serialized(
    checkout_factory, first_writer
):
    case = checkout_factory(quantities=(1,))
    variant = case.variants[0]
    Inventory.objects.filter(variant=variant).update(quantity=1)
    checkout = partial(do_checkout, case.user.pk, case.address.pk)
    manager = partial(
        catalog_services.update_inventory, variant.product_id, variant.pk, quantity=5
    )
    calls = (manager, checkout) if first_writer == "manager" else (checkout, manager)
    first, second = race(*calls, table="catalog_productvariant")
    assert first[0] == second[0] == "ok"
    assert Order.objects.count() == 1 and Order.objects.get().items.get().quantity == 1
    assert Inventory.objects.get(variant=variant).quantity == (
        4 if first_writer == "manager" else 5
    )


@pytest.mark.parametrize("first_writer", ["manager", "checkout"])
def test_manager_price_update_has_a_defined_order(checkout_factory, first_writer):
    from decimal import Decimal

    case = checkout_factory(quantities=(1,))
    variant = case.variants[0]
    checkout = partial(do_checkout, case.user.pk, case.address.pk)
    manager = partial(
        catalog_services.update_variant,
        variant.product_id,
        variant.pk,
        price=Decimal("25.00"),
    )
    calls = (manager, checkout) if first_writer == "manager" else (checkout, manager)
    first, second = race(*calls, table="catalog_productvariant")
    assert first[0] == second[0] == "ok"
    expected = Decimal("25.00" if first_writer == "manager" else "19.90")
    order = Order.objects.get()
    assert order.items.get().unit_price == order.total == expected
    assert ProductVariant.objects.get(pk=variant.pk).price == Decimal("25.00")


def test_shared_variants_use_same_order_despite_reverse_cart_insertion(
    checkout_factory, checkout_catalog, other_checkout_customer
):
    variants = checkout_catalog.variants
    Inventory.objects.update(quantity=2)
    first_case = checkout_factory(quantities=(1, 1), variants=variants)
    second_case = checkout_factory(
        owner=other_checkout_customer,
        quantities=(1, 1),
        variants=list(reversed(variants)),
    )
    first, second = race(
        partial(do_checkout, first_case.user.pk, first_case.address.pk),
        partial(do_checkout, second_case.user.pk, second_case.address.pk),
        table="catalog_productvariant",
    )
    assert first[0] == second[0] == "ok"
    assert Order.objects.count() == 2
    assert list(
        Inventory.objects.order_by("variant_id").values_list("quantity", flat=True)
    ) == [0, 0]
    assert not CartItem.objects.exists()
