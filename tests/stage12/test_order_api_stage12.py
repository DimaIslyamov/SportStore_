from decimal import Decimal

import pytest
from django.urls import reverse
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Address, User
from catalog.models import Inventory
from orders.models import Order, OrderShippingAddress

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "actor, read_code, status_code, cancel_code",
    [
        ("anonymous", 401, 401, 401),
        ("inactive", 401, 401, 401),
        ("customer", 200, 403, 200),
        ("staff-customer", 200, 403, 200),
        ("manager", 200, 409, 403),
        ("super-customer", 200, 409, 200),
    ],
)
def test_jwt_permissions(
    api_client,
    order_factory,
    customer,
    manager,
    actor,
    read_code,
    status_code,
    cancel_code,
):
    order = order_factory(quantities=(2,))
    if actor != "anonymous":
        user = manager if actor == "manager" else customer
        flags = {
            "inactive": {"is_active": False},
            "staff-customer": {"is_staff": True},
            "super-customer": {"is_staff": True, "is_superuser": True},
        }
        token = AccessToken.for_user(user)
        if actor in flags:
            User.objects.filter(pk=user.pk).update(**flags[actor])
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    kwargs = {"pk": order.pk}
    assert api_client.get(reverse("orders:list")).status_code == read_code
    assert (
        api_client.get(reverse("orders:detail", kwargs=kwargs)).status_code == read_code
    )

    status_response = api_client.patch(
        reverse("orders:status", kwargs=kwargs), {"status": "PAID"}, format="json"
    )
    assert status_response.status_code == status_code
    if status_code == 409:
        assert status_response.json()["code"] == "PAYMENT_CONFIRMATION_REQUIRED"

    cancel_response = api_client.post(reverse("orders:cancel", kwargs=kwargs))
    assert cancel_response.status_code == cancel_code
    if cancel_code == 200:
        assert cancel_response.json()["status"] == "CANCELLED"


def test_order_visibility(api_client, customer, other_customer, manager, order_factory):
    own_order = order_factory()
    foreign_order = order_factory(owner=other_customer)
    own = {"pk": own_order.pk}
    foreign = {"pk": foreign_order.pk}
    missing = {"pk": 9_223_372_036_854_775_807}
    not_found = {"code": "ORDER_NOT_FOUND", "detail": "Order was not found."}

    api_client.force_authenticate(user=customer)
    response = api_client.get(reverse("orders:list"))
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [own_order.pk]
    assert set(response.json()[0]) == {
        "id",
        "order_number",
        "status",
        "currency",
        "total",
        "created_at",
        "updated_at",
    }

    detail = api_client.get(reverse("orders:detail", kwargs=own))
    assert detail.status_code == 200
    assert detail.json()["id"] == own_order.pk
    assert len(detail.json()["items"]) == 2
    assert detail.json()["shipping_address"]["city"] == "Warsaw"

    for lookup in (foreign, missing):
        read = api_client.get(reverse("orders:detail", kwargs=lookup))
        cancel = api_client.post(reverse("orders:cancel", kwargs=lookup))
        assert read.status_code == cancel.status_code == 404
        assert read.json() == cancel.json() == not_found

    api_client.force_authenticate(user=manager)
    response = api_client.get(reverse("orders:list"))
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [foreign_order.pk, own_order.pk]

    for lookup in (own, foreign):
        response = api_client.get(reverse("orders:detail", kwargs=lookup))
        assert response.status_code == 200
        assert response.json()["id"] == lookup["pk"]

    read = api_client.get(reverse("orders:detail", kwargs=missing))
    change = api_client.patch(
        reverse("orders:status", kwargs=missing), {"status": "PAID"}, format="json"
    )
    assert read.status_code == change.status_code == 404
    assert read.json() == change.json() == not_found


def test_empty_list_and_superuser_visibility(
    api_client, customer, other_customer, order_factory
):
    foreign_order = order_factory(owner=other_customer)
    api_client.force_authenticate(user=customer)
    response = api_client.get(reverse("orders:list"))
    assert response.status_code == 200
    assert response.json() == []

    customer.is_staff = customer.is_superuser = True
    customer.save(update_fields=["is_staff", "is_superuser"])
    response = api_client.get(reverse("orders:list"))
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [foreign_order.pk]

    response = api_client.post(
        reverse("orders:cancel", kwargs={"pk": foreign_order.pk})
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "payload, field",
    [
        ({}, "status"),
        ({"status": "paid"}, "status"),
        ({"status": None}, "status"),
        ({"status": "PAID", "total": "0.00"}, "total"),
    ],
)
def test_status_payload_is_strict(api_client, manager, order_factory, payload, field):
    order = order_factory(quantities=(2,))
    api_client.force_authenticate(user=manager)
    response = api_client.patch(
        reverse("orders:status", kwargs={"pk": order.pk}), payload, format="json"
    )
    assert response.status_code == 400
    assert field in response.json()
    order.refresh_from_db()
    assert order.status == Order.Status.PENDING


def test_cancel_payload_cannot_set_status(api_client, customer, order_factory):
    order = order_factory(quantities=(2,))
    api_client.force_authenticate(user=customer)
    response = api_client.post(
        reverse("orders:cancel", kwargs={"pk": order.pk}),
        {"status": "PAID"},
        format="json",
    )
    assert response.status_code == 400
    assert "status" in response.json()
    order.refresh_from_db()
    assert order.status == Order.Status.PENDING


def test_status_action_handles_conflict_and_restoration(
    api_client, manager, order_factory, order_variants
):
    order = order_factory(quantities=(2,))
    url = reverse("orders:status", kwargs={"pk": order.pk})
    api_client.force_authenticate(user=manager)

    forbidden = api_client.patch(url, {"status": "PROCESSING"}, format="json")
    assert forbidden.status_code == 409
    assert forbidden.json()["code"] == "INVALID_ORDER_STATUS_TRANSITION"

    cancelled = api_client.patch(url, {"status": "CANCELLED"}, format="json")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"

    repeated = api_client.patch(url, {"status": "CANCELLED"}, format="json")
    assert repeated.status_code == 409
    assert repeated.json()["code"] == "INVALID_ORDER_STATUS_TRANSITION"
    assert Inventory.objects.get(variant=order_variants[0]).quantity == 10


def test_detail_reads_snapshots_after_live_data_changes(
    api_client, customer, order_factory, order_variants
):
    order = order_factory()
    url = reverse("orders:detail", kwargs={"pk": order.pk})
    api_client.force_authenticate(user=customer)

    first = api_client.get(url)
    assert first.status_code == 200
    before = first.json()
    variant = order_variants[0]
    variant.price = Decimal("88.00")
    variant.save(update_fields=["price"])
    variant.product.name = "Renamed Training Shirt"
    variant.product.save(update_fields=["name"])
    addresses = Address.objects.filter(user=customer)
    addresses.update(city="Krakow")
    addresses.delete()

    response = api_client.get(url)
    assert response.status_code == 200
    assert response.json() == before
    assert response.json()["items"][0]["unit_price"] == "19.90"
    assert response.json()["items"][0]["product_name"] == "Training Shirt"
    assert response.json()["shipping_address"]["city"] == "Warsaw"


def test_query_budget(api_client, customer, order_factory, django_assert_num_queries):
    small = order_factory(quantities=(1,))
    api_client.force_authenticate(user=customer)
    list_url = reverse("orders:list")

    with django_assert_num_queries(1):
        response = api_client.get(list_url)
        assert response.status_code == 200
        assert len(response.json()) == 1

    large = order_factory()
    order_factory(quantities=(1,))

    with django_assert_num_queries(1):
        response = api_client.get(list_url)
        assert response.status_code == 200
        assert len(response.json()) == 3

    for order, count in ((small, 1), (large, 2)):
        url = reverse("orders:detail", kwargs={"pk": order.pk})
        with django_assert_num_queries(2):
            response = api_client.get(url)
            assert response.status_code == 200
            assert len(response.json()["items"]) == count
            assert response.json()["shipping_address"]["city"] == "Warsaw"


def test_detail_without_shipping_snapshot(api_client, customer, order_factory):
    order = order_factory(quantities=(1,))
    OrderShippingAddress.objects.filter(order=order).delete()
    api_client.force_authenticate(user=customer)

    response = api_client.get(reverse("orders:detail", kwargs={"pk": order.pk}))
    assert response.status_code == 200
    assert response.json()["shipping_address"] is None
    assert len(response.json()["items"]) == 1
