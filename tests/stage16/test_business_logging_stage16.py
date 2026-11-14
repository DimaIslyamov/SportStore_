from uuid import uuid4

import pytest
from django.db import transaction

from catalog.models import Inventory
from core.logging import get_request_id, log_on_commit, request_id_context
from orders import services
from orders.models import IdempotencyRecord, Order
from payments.services import create_payment

pytestmark = pytest.mark.django_db(transaction=True)


def business(events):
    return [
        r
        for r in events.records
        if r.name == "sportstore" and r.msg not in {"request_completed", "api_error"}
    ]


def test_order_payment_cancel_replay_events_stage16(
    api_client, customer, prepare_order_cart, order_variants, events
):
    address = prepare_order_cart(owner=customer, quantities=(2,))
    api_client.force_authenticate(user=customer)
    key = str(uuid4())
    headers = {"HTTP_IDEMPOTENCY_KEY": key}
    checkout = api_client.post(
        "/api/v1/orders/checkout/", {"address_id": address.pk}, format="json", **headers
    )
    assert checkout.status_code == 201
    order_id = checkout.json()["id"]
    url = f"/api/v1/orders/{order_id}/payments/"
    payment_key = {"HTTP_IDEMPOTENCY_KEY": str(uuid4())}
    paid = api_client.post(url, {}, format="json", **payment_key)
    assert paid.status_code == 201
    api_client.post(url, {}, format="json", **payment_key)
    api_client.post(
        "/api/v1/orders/checkout/", {"address_id": address.pk}, format="json", **headers
    )
    cancel = api_client.post(f"/api/v1/orders/{order_id}/cancel/", {}, format="json")
    api_client.post(f"/api/v1/orders/{order_id}/cancel/", {}, format="json")
    records = business(events)
    assert [r.msg for r in records] == [
        "order_created",
        "order_status_changed",
        "payment_succeeded",
        "order_cancelled",
    ]
    assert records[0].request_id == checkout["X-Request-ID"]
    assert records[1].request_id == records[2].request_id == paid["X-Request-ID"]
    assert records[3].request_id == cancel["X-Request-ID"]
    assert all(r.levelname == "INFO" for r in records)
    assert Order.objects.get(pk=order_id).status == "CANCELLED"
    assert Inventory.objects.get(variant=order_variants[0]).quantity == 10


def test_failed_checkout_replay_gets_fresh_id_but_same_stored_result_stage16(
    api_client, customer, prepare_order_cart, order_variants, events
):
    address = prepare_order_cart(owner=customer, quantities=(2,))
    Inventory.objects.filter(variant=order_variants[0]).update(quantity=0)
    api_client.force_authenticate(user=customer)
    headers = {"HTTP_IDEMPOTENCY_KEY": str(uuid4())}

    def post():
        return api_client.post(
            "/api/v1/orders/checkout/",
            {"address_id": address.pk},
            format="json",
            **headers,
        )

    first, replay = post(), post()
    for response in (first, replay):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "OUT_OF_STOCK"
        assert response.json()["error"]["request_id"] == response["X-Request-ID"]
    assert first["X-Request-ID"] != replay["X-Request-ID"]
    record = IdempotencyRecord.objects.get()
    assert record.response_body == {
        "code": "OUT_OF_STOCK",
        "detail": f"Not enough stock for variant {order_variants[0].pk}.",
    }
    assert not business(events)
    assert not Order.objects.exists()


def test_failed_fake_is_warning_not_http_error_stage16(order_factory, customer, events):
    order = order_factory(quantities=(1,))
    events.clear()
    payment, _ = create_payment(
        user=customer, order_id=order.pk, key=uuid4(), outcome="failed"
    )
    records = business(events)
    assert len(records) == 1
    assert records[0].msg == "payment_failed" and records[0].levelname == "WARNING"
    assert payment.status == "FAILED"
    assert Order.objects.get(pk=order.pk).status == "PENDING"


def test_outer_rollback_discards_all_success_logs_stage16(
    order_factory, customer, events
):
    with pytest.raises(RuntimeError), transaction.atomic():
        order = order_factory(quantities=(1,))
        create_payment(user=customer, order_id=order.pk, key=uuid4())
        services.cancel_order(user=customer, order_id=order.pk)
        assert not business(events)
        raise RuntimeError("rollback")
    assert not business(events)
    assert not Order.objects.exists()


def test_late_payment_error_discards_success_logs_stage16(
    order_factory, customer, events, monkeypatch
):
    order = order_factory(quantities=(1,))
    events.clear()
    original = services.change_order_status

    def late_error(**kwargs):
        original(**kwargs)
        raise RuntimeError("unexpected")

    monkeypatch.setattr(services, "change_order_status", late_error)
    with pytest.raises(RuntimeError):
        create_payment(user=customer, order_id=order.pk, key=uuid4())
    assert not business(events)
    assert Order.objects.get(pk=order.pk).status == "PENDING"


def test_on_commit_captures_original_request_id_stage16(events):
    with transaction.atomic():
        token = request_id_context.set("captured-id")
        log_on_commit("order_created", order_id=123)
        request_id_context.reset(token)
        assert get_request_id() == "-"
        assert not business(events)
    assert business(events)[0].request_id == "captured-id"
