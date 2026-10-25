from concurrent.futures import ThreadPoolExecutor
from functools import partial
from queue import Queue
from threading import Event
from time import monotonic, sleep

import pytest
from django.db import close_old_connections, connection

from catalog import services
from catalog.models import Inventory, ProductImage

pytestmark = pytest.mark.django_db(transaction=True)


def run_worker(call, pids, lock_table=None, locked=None, release=None):
    close_old_connections()
    try:
        connection.ensure_connection()
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout = '8s'")
            cursor.execute("SELECT pg_backend_pid()")
            pids.put(cursor.fetchone()[0])

        def pause_after_lock(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if lock_table and f'"{lock_table}"' in sql and "FOR UPDATE" in sql:
                locked.set()
                if not release.wait(8):
                    raise TimeoutError("Lock test release timed out")
            return result

        with connection.execute_wrapper(pause_after_lock):
            return call()
    finally:
        connection.close()


def assert_postgres_blocked(blocked_pid, blocker_pid):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT %s = ANY(pg_blocking_pids(%s))", [blocker_pid, blocked_pid]
            )
            if cursor.fetchone()[0]:
                return
        sleep(0.025)
    raise AssertionError("Second connection did not wait for the expected row lock")


@pytest.mark.parametrize("operation", ["inventory", "primary", "create-primary"])
def test_compound_writers_serialize_on_existing_parent(
    stage7_product, stage7_variant, stage7_image, operation, file_factory
):
    assert connection.vendor == "postgresql"
    if operation == "inventory":
        assert Inventory.objects.filter(variant=stage7_variant).count() == 0
        table = "catalog_productvariant"
        first_call = partial(
            services.update_inventory, stage7_product.pk, stage7_variant.pk, quantity=3
        )
        second_call = partial(
            services.update_inventory, stage7_product.pk, stage7_variant.pk, quantity=8
        )
    elif operation == "primary":
        second_image = ProductImage.objects.create(
            product=stage7_product, image_url="https://images.example.com/second.png"
        )
        table = "catalog_product"
        first_call = partial(
            services.update_image, stage7_product.pk, stage7_image.pk, is_primary=True
        )
        second_call = partial(
            services.update_image, stage7_product.pk, second_image.pk, is_primary=True
        )
    else:
        from catalog.image_validation import prepare_image

        table = "catalog_product"
        first_call = partial(
            services.create_image,
            stage7_product.pk,
            file=prepare_image(file_factory()),
            is_primary=True,
        )
        second_call = partial(
            services.create_image,
            stage7_product.pk,
            file=prepare_image(file_factory()),
            is_primary=True,
        )
    first_pids, second_pids = Queue(), Queue()
    locked, release = Event(), Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run_worker, first_call, first_pids, table, locked, release)
        try:
            first_pid = first_pids.get(timeout=5)
            assert locked.wait(5), "First writer did not acquire its row lock"
            second = pool.submit(run_worker, second_call, second_pids)
            second_pid = second_pids.get(timeout=5)
            assert first_pid != second_pid
            assert_postgres_blocked(second_pid, first_pid)
        finally:
            release.set()
        first.result(timeout=10)
        last_written = second.result(timeout=10)
    if operation == "inventory":
        assert Inventory.objects.filter(variant=stage7_variant).count() == 1
        assert Inventory.objects.get(variant=stage7_variant).quantity == 8
    else:
        primary_ids = list(
            ProductImage.objects.filter(
                product=stage7_product, is_primary=True
            ).values_list("id", flat=True)
        )
        assert primary_ids == [last_written.pk]
