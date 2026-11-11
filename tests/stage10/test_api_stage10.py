from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Address, User
from cart import services as cart_services
from catalog.models import Inventory, ProductVariant
from orders import services
from orders.models import Order
from orders.snapshots import build_shipping_snapshot

pytestmark = pytest.mark.django_db(transaction=True)


def checkout_url():
    return reverse("orders:checkout")


def checkout_client():
    return APIClient(HTTP_IDEMPOTENCY_KEY=str(uuid4()))


@pytest.mark.parametrize(
    "actor,status_code",
    [
        ("anonymous", 401),
        ("customer", 201),
        ("manager", 403),
        ("staff-customer", 201),
        ("inactive", 401),
        ("super-manager", 403),
        ("super-customer", 201),
    ],
)
def test_real_jwt_role_matrix(checkout_factory, checkout_state, actor, status_code):
    case = checkout_factory()
    flags = {
        "manager": {"role": User.Role.MANAGER},
        "staff-customer": {"is_staff": True},
        "inactive": {"is_active": False},
        "super-manager": {
            "role": User.Role.MANAGER,
            "is_staff": True,
            "is_superuser": True,
        },
        "super-customer": {"is_staff": True, "is_superuser": True},
    }
    if actor in flags:
        User.objects.filter(pk=case.user.pk).update(**flags[actor])

    client = checkout_client()
    if actor != "anonymous":
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(case.user)}"
        )

    before = checkout_state()
    response = client.post(
        checkout_url(), {"address_id": case.address.pk}, format="json"
    )

    assert response.status_code == status_code
    if status_code != 201:
        assert checkout_state() == before
    else:
        assert response.json()["status"] == "PENDING" and Order.objects.count() == 1


@pytest.mark.parametrize(
    "payload,key",
    [
        ({}, "address_id"),
        ({"address_id": 0}, "address_id"),
        ({"address_id": -1}, "address_id"),
        ({"address_id": None}, "address_id"),
        ({"address_id": True}, "address_id"),
        ({"address_id": False}, "address_id"),
        ({"address_id": []}, "address_id"),
        ({"address_id": {}}, "address_id"),
        ({"address_id": 1.5}, "address_id"),
        ({"address_id": "oops"}, "address_id"),
        ({"address_id": 9223372036854775808}, "address_id"),
        ({"address_id": "9223372036854775808"}, "address_id"),
        ({"price": "0.01"}, "price"),
        ({"total": "0.01"}, "total"),
        ({"discount": "999.00"}, "discount"),
        ({"shipping_cost": "0.00"}, "shipping_cost"),
        ({"user": 1}, "user"),
        ({"cart_id": 1}, "cart_id"),
        ({"items": []}, "items"),
        ({"currency": "USD"}, "currency"),
        ({"status": "PAID"}, "status"),
    ],
)
def test_strict_input_cannot_change_server_money_or_owner(
    checkout_factory, checkout_state, django_assert_num_queries, payload, key
):
    case = checkout_factory()
    client = checkout_client()
    client.force_authenticate(user=case.user)

    data = dict(payload)
    if key != "address_id":
        data["address_id"] = case.address.pk

    before = checkout_state()
    with django_assert_num_queries(0):
        response = client.post(checkout_url(), data, format="json")
        assert response.status_code == 400

    assert set(response.json()) == {key}
    assert isinstance(response.json()[key], list)
    assert checkout_state() == before


@pytest.mark.parametrize(
    "literal", ["41.0", "41.000000000000001", "41e0", '"41.0"', '"41.5"', '"41e0"']
)
def test_checkout_rejects_non_integer_json_address_id(
    checkout_factory, checkout_state, django_assert_num_queries, literal
):
    case = checkout_factory()
    Address.objects.filter(pk=case.address.pk).update(id=41)

    client = checkout_client()
    client.force_authenticate(user=case.user)
    before = checkout_state()

    # Send the literal directly so a JSON encoder cannot round it before the request.
    with django_assert_num_queries(0):
        response = client.post(
            checkout_url(),
            f'{{"address_id": {literal}}}',
            content_type="application/json",
        )
        assert response.status_code == 400

    assert set(response.json()) == {"address_id"}
    assert isinstance(response.json()["address_id"], list)
    assert checkout_state() == before


@pytest.mark.parametrize(
    "scenario,code,status_code",
    [
        ("missing-cart", "EMPTY_CART", 400),
        ("empty-cart", "EMPTY_CART", 400),
        ("stock", "OUT_OF_STOCK", 409),
        ("missing-stock", "OUT_OF_STOCK", 409),
        ("unavailable", "PRODUCT_UNAVAILABLE", 409),
        ("foreign-address", "ADDRESS_NOT_FOUND", 404),
        ("missing-address", "ADDRESS_NOT_FOUND", 404),
        ("quantity", "INVALID_QUANTITY", 400),
        ("bad-address-data", "CHECKOUT_DATA_INVALID", 409),
    ],
)
def test_local_business_errors_have_codes_without_side_effects(
    checkout_factory,
    checkout_state,
    other_checkout_customer,
    monkeypatch,
    scenario,
    code,
    status_code,
):
    case = checkout_factory()
    address_id = case.address.pk

    if scenario == "missing-cart":
        case.cart.delete()
    elif scenario == "empty-cart":
        cart_services.clear_cart(user=case.user)
    elif scenario == "stock":
        Inventory.objects.filter(variant=case.variants[0]).update(quantity=0)
    elif scenario == "missing-stock":
        Inventory.objects.filter(variant=case.variants[0]).delete()
    elif scenario == "unavailable":
        ProductVariant.objects.filter(pk=case.variants[0].pk).update(is_active=False)
    elif scenario == "foreign-address":
        address_id = Address.objects.create(
            user=other_checkout_customer,
            **build_shipping_snapshot(address=case.address),
        ).pk
    elif scenario == "missing-address":
        address_id = 9223372036854775807
    elif scenario == "quantity":
        bad = SimpleNamespace(variant_id=case.variants[0].pk, quantity=0)
        monkeypatch.setattr(
            services,
            "cart_items_for_checkout",
            lambda **kwargs: [bad],
        )
    elif scenario == "bad-address-data":
        Address.objects.filter(pk=case.address.pk).update(city="")

    before = checkout_state()

    client = checkout_client()
    client.force_authenticate(user=case.user)
    response = client.post(checkout_url(), {"address_id": address_id}, format="json")

    assert response.status_code == status_code
    assert response.json()["code"] == code
    assert isinstance(response.json()["detail"], str)
    assert checkout_state() == before


@pytest.mark.parametrize("address_id_type", [int, str], ids=["integer", "string"])
def test_success_response_is_complete_snapshot_not_live_catalog(
    checkout_factory, address_id_type
):
    case = checkout_factory()

    client = checkout_client()
    client.force_authenticate(user=case.user)
    response = client.post(
        checkout_url(),
        {"address_id": address_id_type(case.address.pk)},
        format="json",
    )

    assert response.status_code == 201
    body = response.json()

    assert set(body) == {
        "id",
        "order_number",
        "status",
        "currency",
        "subtotal",
        "discount",
        "shipping_cost",
        "total",
        "shipping_address",
        "items",
        "created_at",
        "updated_at",
    }
    assert body["subtotal"] == body["total"] == "77.30"
    assert body["discount"] == body["shipping_cost"] == "0.00"
    assert body["status"] == "PENDING" and body["currency"] == "PLN"
    assert body["shipping_address"] == build_shipping_snapshot(address=case.address)
    assert len(body["items"]) == 2

    first = body["items"][0]
    assert (
        first["sku"] == "CHECKOUT-SHIRT-M" and first["product_name"] == "Training Shirt"
    )
    assert first["unit_price"] == "19.90" and first["total_price"] == "39.80"
    assert (
        first["quantity"] == 2
        and first["variant_description"] == "Color: Black; Size: M"
    )
    assert "variant" not in first and "user" not in body
    assert body["created_at"] and body["updated_at"] and "Location" not in response


def test_checkout_json_parser_and_method(checkout_factory, checkout_state):
    case = checkout_factory()

    client = checkout_client()
    client.force_authenticate(user=case.user)
    before = checkout_state()

    assert (
        client.post(
            checkout_url(),
            '{"address_id":',
            content_type="application/json",
        ).status_code
        == 400
    )
    assert (
        client.post(
            checkout_url(),
            "plain",
            content_type="text/plain",
        ).status_code
        == 415
    )
    assert client.post(checkout_url(), [], format="json").status_code == 400
    assert client.get(checkout_url()).status_code == 405
    assert checkout_state() == before
