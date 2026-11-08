from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from threading import Event
from time import monotonic
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.db import (
    IntegrityError,
    OperationalError,
    close_old_connections,
    connection,
    transaction,
)
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from cart.models import Cart, CartItem
from catalog.models import Inventory
from orders import idempotency, services, views
from orders.errors import CheckoutError
from orders.models import IdempotencyRecord, Order
from orders.serializers import CheckoutInputSerializer

pytestmark = pytest.mark.django_db(transaction=True)
State = IdempotencyRecord.Status


def token(user):
    return str(AccessToken.for_user(user))


def post(access, key, address_id, **extra):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    headers = {} if key is None else {"HTTP_IDEMPOTENCY_KEY": str(key)}
    return client.post(
        reverse("orders:checkout"),
        {"address_id": address_id, **extra},
        format="json",
        **headers,
    )


@pytest.mark.parametrize("value", [41, "41", "  +0041  ", "4_1", "٤١"])
def test_canonical_checkout_hash(value):
    serializer = CheckoutInputSerializer(data={"address_id": value})
    assert serializer.is_valid(), serializer.errors
    address_id = serializer.validated_data["address_id"]
    assert type(address_id) is int and address_id == 41
    assert idempotency.checkout_request_hash(address_id) == (
        "183dbef43a28f7373e7628679de28d9890d778c5b0d247ec5c18d882ab4f3b79"
    )
    assert idempotency.checkout_request_hash(address_id) != (
        idempotency.checkout_request_hash(42)
    )


def test_invalid_header_and_body_do_not_reserve_key(checkout_factory, checkout_state):
    case = checkout_factory()
    access, key = token(case.user), uuid4()
    before = checkout_state()
    bad = [
        (post(access, None, case.address.pk), "idempotency_key"),
        (post(access, "bad-uuid", case.address.pk), "idempotency_key"),
        (post(access, key, 41.0), "address_id"),
        (post(access, key, case.address.pk, total="0.01"), "total"),
    ]
    for response, field in bad:
        assert response.status_code == 400 and field in response.json()
    assert not IdempotencyRecord.objects.exists()
    assert checkout_state() == before


def test_success_replay_and_payload_conflict(checkout_factory, checkout_state):
    case = checkout_factory()
    access, key = token(case.user), uuid4()
    first = post(access, key, case.address.pk)
    assert first.status_code == 201
    body = first.json()
    record = IdempotencyRecord.objects.get(user=case.user, key=key)
    assert record.status == State.COMPLETED
    assert record.resource_id == body["id"] and record.resource.user_id == case.user.pk
    assert record.response_status_code == 201 and record.response_body == body
    assert record.expires_at >= record.created_at + idempotency.RESULT_TTL

    Order.objects.filter(pk=record.resource_id).update(status=Order.Status.PAID)
    before = checkout_state()
    replay = post(access, str(key).upper(), str(case.address.pk))
    assert replay.status_code == 201 and replay.json() == body
    conflict = post(access, key, case.address.pk + 1)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert checkout_state() == before


def test_late_business_failure_rolls_back_and_replays(
    checkout_factory, checkout_state, monkeypatch
):
    case = checkout_factory()
    access, key = token(case.user), uuid4()
    before = checkout_state()
    original_clear = services.clear_cart

    def clear_then_fail(**kwargs):
        original_clear(**kwargs)
        raise CheckoutError("CHECKOUT_DATA_INVALID", "Injected late failure.")

    with monkeypatch.context() as patch:
        patch.setattr(services, "clear_cart", clear_then_fail)
        failed = post(access, key, case.address.pk)
    assert failed.status_code == 409
    assert failed.json()["code"] == "CHECKOUT_DATA_INVALID"
    assert checkout_state() == before
    record = IdempotencyRecord.objects.get(key=key)
    assert record.status == State.FAILED and record.resource_id is None
    assert record.response_status_code == 409 and record.response_body == failed.json()
    replay = post(access, key, case.address.pk)
    assert replay.status_code == 409 and replay.json() == failed.json()
    assert checkout_state() == before
    assert post(access, uuid4(), case.address.pk).status_code == 201


def test_command_recovers_orphan_and_purges_only_results(checkout_factory):
    case = checkout_factory()
    access, key = token(case.user), uuid4()
    old = timezone.now() - idempotency.RECOVERY_AFTER - timedelta(seconds=1)
    orphan = IdempotencyRecord.objects.create(
        user=case.user,
        key=key,
        operation=idempotency.OPERATION,
        request_hash=idempotency.checkout_request_hash(case.address.pk),
        created_at=old,
        expires_at=old + idempotency.RESULT_TTL,
    )
    pending = post(access, key, case.address.pk)
    assert pending.status_code == 409
    assert pending.json()["code"] == "IDEMPOTENCY_IN_PROGRESS"

    call_command("cleanup_checkout_idempotency", "--batch-size", "10")
    failed = post(access, key, case.address.pk)
    assert (
        failed.status_code == 503 and failed.json()["code"] == idempotency.ABORTED_CODE
    )
    delayed = idempotency._execute(
        record_id=orphan.pk, user=case.user, address_id=case.address.pk
    )
    assert delayed.status_code == 503 and delayed.body == failed.json()
    assert not Order.objects.exists()

    success = post(access, uuid4(), case.address.pk)
    assert success.status_code == 201
    IdempotencyRecord.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    call_command("cleanup_checkout_idempotency", "--batch-size", "10")
    assert not IdempotencyRecord.objects.exists()
    assert Order.objects.filter(pk=success.json()["id"]).exists()


def test_database_scope_and_completed_shape(customer):
    now, key = timezone.now(), uuid4()
    fields = {
        "user": customer,
        "key": key,
        "request_hash": idempotency.checkout_request_hash(41),
        "created_at": now,
        "expires_at": now + idempotency.RESULT_TTL,
    }
    record = IdempotencyRecord.objects.create(operation="checkout", **fields)
    IdempotencyRecord.objects.create(operation="another_operation", **fields)
    with pytest.raises(IntegrityError), transaction.atomic():
        IdempotencyRecord.objects.create(operation="checkout", **fields)
    with pytest.raises(IntegrityError), transaction.atomic():
        IdempotencyRecord.objects.filter(pk=record.pk).update(status=State.COMPLETED)
    assert IdempotencyRecord.objects.filter(user=customer, key=key).count() == 2


def worker(pids, call, wrapper=None):
    close_old_connections()
    try:
        assert connection.vendor == "postgresql"
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout = '30s'")
            cursor.execute("SELECT pg_backend_pid()")
            pids.put(cursor.fetchone()[0])
        if wrapper is None:
            return call()
        with connection.execute_wrapper(wrapper):
            return call()
    finally:
        connection.close()


def start(pool, call, wrapper=None):
    pids = Queue()
    return pool.submit(worker, pids, call, wrapper), pids.get(timeout=5)


def current_pid():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


def assert_blocked(waiting_pid, blocking_pid):
    assert waiting_pid != blocking_pid
    deadline = monotonic() + 5
    tick = Event()
    while monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT %s = ANY(pg_blocking_pids(%s))", [blocking_pid, waiting_pid]
            )
            if cursor.fetchone()[0]:
                return
        tick.wait(0.025)
    pytest.fail(f"PID {waiting_pid} did not wait for PID {blocking_pid}")


def test_first_insert_race_and_processing_while_cart_locked(
    checkout_factory, monkeypatch
):
    case = checkout_factory()
    access, key, address_id = token(case.user), uuid4(), case.address.pk
    inserted, release = Event(), Event()
    original_save = IdempotencyRecord.save
    monkeypatch.setattr(idempotency, "RESERVATION_LOCK_TIMEOUT", "15s")

    def save_and_hold(record, *args, **kwargs):
        creating = record._state.adding
        original_save(record, *args, **kwargs)
        if creating:
            inserted.set()
            assert release.wait(20)

    def changed_payload():
        assert idempotency.cleanup_expired_checkout_keys(batch_size=10) == (0, 0)
        return post(access, key, address_id + 1)

    monkeypatch.setattr(IdempotencyRecord, "save", save_and_hold)
    holder_pid = current_pid()
    with ThreadPoolExecutor(max_workers=3) as pool:
        with transaction.atomic():
            Cart.objects.select_for_update().get(pk=case.cart.pk)
            first, first_pid = start(pool, lambda: post(access, key, address_id))
            try:
                assert inserted.wait(5)
                assert not IdempotencyRecord.objects.filter(key=key).exists()
                repeat, repeat_pid = start(pool, lambda: post(access, key, address_id))
                assert_blocked(repeat_pid, first_pid)

                monkeypatch.setattr(idempotency, "RESERVATION_LOCK_TIMEOUT", "250ms")
                busy, _ = start(pool, lambda: post(access, key, address_id))
                result = busy.result(timeout=5)
                assert result.status_code == 503
                assert result.json()["code"] == "IDEMPOTENCY_RESERVATION_BUSY"
            finally:
                release.set()

            assert_blocked(first_pid, holder_pid)
            collision = repeat.result(timeout=5)
            assert collision.status_code == 409
            assert collision.json()["code"] == "IDEMPOTENCY_IN_PROGRESS"
            assert IdempotencyRecord.objects.get(key=key).status == State.PROCESSING
            monkeypatch.setattr(idempotency, "RECOVERY_AFTER", timedelta(0))
            changed, _ = start(pool, changed_payload)
            response = changed.result(timeout=5)
            assert response.status_code == 409
            assert response.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
            assert not Order.objects.exists()
        assert first.result(timeout=10).status_code == 201

    assert Order.objects.count() == IdempotencyRecord.objects.count() == 1
    assert Inventory.objects.get(variant=case.variants[0]).quantity == 8
    assert not CartItem.objects.filter(cart=case.cart).exists()


def test_late_unique_collision_times_out_then_reads_processing(
    checkout_factory, monkeypatch
):
    case = checkout_factory()
    access, key, address_id = token(case.user), uuid4(), case.address.pk
    at_insert, allow_insert, wrote_result, allow_commit, timed_out = (
        Event() for _ in range(5)
    )
    original_finish = idempotency._finish
    monkeypatch.setattr(idempotency, "RESERVATION_LOCK_TIMEOUT", "250ms")

    def pause_insert(execute, sql, params, many, context):
        if not sql.startswith('INSERT INTO "orders_idempotencyrecord"'):
            return execute(sql, params, many, context)
        at_insert.set()
        assert allow_insert.wait(20)
        try:
            return execute(sql, params, many, context)
        except OperationalError as exc:
            if getattr(exc.__cause__, "sqlstate", None) == "55P03":
                timed_out.set()
            raise

    def pause_commit(*args, **kwargs):
        result = original_finish(*args, **kwargs)
        wrote_result.set()
        assert allow_commit.wait(20)
        return result

    monkeypatch.setattr(idempotency, "_finish", pause_commit)
    with ThreadPoolExecutor(max_workers=2) as pool:
        repeat, repeat_pid = start(
            pool, lambda: post(access, key, address_id), wrapper=pause_insert
        )
        try:
            assert at_insert.wait(5)
            first, first_pid = start(pool, lambda: post(access, key, address_id))
            assert first_pid != repeat_pid
            assert wrote_result.wait(5)
            allow_insert.set()
            collision = repeat.result(timeout=5)
            assert timed_out.is_set()
            assert collision.status_code == 409
            assert collision.json()["code"] == "IDEMPOTENCY_IN_PROGRESS"
            assert not Order.objects.exists()
        finally:
            allow_insert.set()
            allow_commit.set()
        assert first.result(timeout=10).status_code == 201
    assert Order.objects.count() == IdempotencyRecord.objects.count() == 1


def test_two_users_compete_for_same_inventory(
    checkout_factory, other_checkout_customer, monkeypatch
):
    first_case = checkout_factory(quantities=(1,))
    second_case = checkout_factory(owner=other_checkout_customer, quantities=(1,))
    variant = first_case.variants[0]
    assert second_case.variants[0].pk == variant.pk
    Inventory.objects.filter(variant=variant).update(quantity=1)
    key = uuid4()
    first_access, second_access = token(first_case.user), token(second_case.user)
    wrote, release = Event(), Event()
    original_clear = services.clear_cart

    def clear_and_hold(*, user):
        original_clear(user=user)
        if user.pk == first_case.user.pk:
            wrote.set()
            assert release.wait(20)

    monkeypatch.setattr(services, "clear_cart", clear_and_hold)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, first_pid = start(
            pool, lambda: post(first_access, key, first_case.address.pk)
        )
        try:
            assert wrote.wait(5)
            second, second_pid = start(
                pool, lambda: post(second_access, key, second_case.address.pk)
            )
            assert_blocked(second_pid, first_pid)
        finally:
            release.set()
        assert first.result(timeout=10).status_code == 201
        failed = second.result(timeout=10)
    assert failed.status_code == 409 and failed.json()["code"] == "OUT_OF_STOCK"
    loser = IdempotencyRecord.objects.get(key=key, user=second_case.user)
    assert loser.status == State.FAILED and loser.response_body == failed.json()
    assert IdempotencyRecord.objects.filter(key=key).count() == 2
    assert Order.objects.count() == 1
    assert Inventory.objects.get(variant=variant).quantity == 0
    assert CartItem.objects.filter(cart=second_case.cart).count() == 1


@pytest.mark.parametrize(
    "after_commit", [True, False], ids=["lost-response", "rollback"]
)
def test_interruption_around_commit(
    checkout_factory, checkout_state, monkeypatch, after_commit
):
    case = checkout_factory()
    access, key, address_id = token(case.user), uuid4(), case.address.pk
    before = checkout_state()
    original_finish = idempotency._finish

    def drop_response(*args, **kwargs):
        raise RuntimeError("Injected interruption")

    def fail_before_commit(*args, **kwargs):
        original_finish(*args, **kwargs)
        raise RuntimeError("Injected interruption")

    with ThreadPoolExecutor(max_workers=2) as pool:
        with monkeypatch.context() as patch:
            if after_commit:
                patch.setattr(views, "Response", drop_response)
            else:
                patch.setattr(idempotency, "_finish", fail_before_commit)
            first, first_pid = start(pool, lambda: post(access, key, address_id))
            with pytest.raises(RuntimeError, match="Injected interruption"):
                first.result(timeout=10)

        record = IdempotencyRecord.objects.get(key=key)
        after = checkout_state()
        replay, replay_pid = start(pool, lambda: post(access, key, address_id))
        assert first_pid != replay_pid
        response = replay.result(timeout=10)

    if after_commit:
        assert record.status == State.COMPLETED and Order.objects.count() == 1
        assert response.status_code == 201 and response.json() == record.response_body
        assert Inventory.objects.get(variant=case.variants[0]).quantity == 8
    else:
        assert record.status == State.PROCESSING and record.resource_id is None
        assert response.status_code == 409 and after == before
    assert checkout_state() == after
    