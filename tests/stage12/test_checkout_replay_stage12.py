from uuid import uuid4

import pytest
from django.urls import reverse

from cart.models import CartItem
from catalog.models import Inventory
from orders.models import IdempotencyRecord, Order

pytestmark = pytest.mark.django_db(transaction=True)


def test_checkout_replays_original_json_after_status_change_and_cancel(
    api_client, customer, prepare_order_cart, order_variants
):
    address = prepare_order_cart(owner=customer, quantities=(2,))
    checkout_url = reverse("orders:checkout")
    key = uuid4()
    payload = {"address_id": address.pk}
    headers = {"HTTP_IDEMPOTENCY_KEY": str(key)}

    api_client.force_authenticate(user=customer)
    initial = api_client.post(checkout_url, payload, format="json", **headers)
    assert initial.status_code == 201
    initial_body = initial.json()
    assert initial_body["status"] == "PENDING"
    lookup = {"pk": initial_body["id"]}

    records = IdempotencyRecord.objects.filter(
        user=customer, operation="checkout", key=key
    ).values()
    saved = records.get()
    assert saved["status"] == "COMPLETED"
    assert saved["resource_id"] == lookup["pk"]
    assert saved["response_status_code"] == 201
    assert saved["response_body"] == initial_body
    assert Inventory.objects.get(variant=order_variants[0]).quantity == 8

    changed = api_client.post(
        reverse("orders:payments", kwargs=lookup),
        {"outcome": "success"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    )
    assert changed.status_code == 201
    assert changed.json()["status"] == "SUCCESS"
    assert Order.objects.get(pk=lookup["pk"]).status == "PAID"
    assert records.get() == saved

    api_client.force_authenticate(user=customer)
    cancelled = api_client.post(
        reverse("orders:cancel", kwargs=lookup), {}, format="json"
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"

    repeated_cancel = api_client.post(reverse("orders:cancel", kwargs=lookup))
    assert repeated_cancel.status_code == 200
    assert repeated_cancel.json() == cancelled.json()

    detail = api_client.get(reverse("orders:detail", kwargs=lookup))
    assert detail.status_code == 200
    assert detail.json()["status"] == "CANCELLED"
    assert detail.json()["items"] == initial_body["items"]
    assert detail.json()["shipping_address"] == initial_body["shipping_address"]

    replay = api_client.post(checkout_url, payload, format="json", **headers)
    assert replay.status_code == 201
    assert replay.json() == initial_body
    assert records.get() == saved
    assert Order.objects.count() == 1
    assert Order.objects.get(pk=lookup["pk"]).status == "CANCELLED"
    assert Inventory.objects.get(variant=order_variants[0]).quantity == 10
    assert not CartItem.objects.exists()
