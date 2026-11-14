from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.urls import reverse
from rest_framework_simplejwt.tokens import AccessToken

from cart import services
from cart.models import Cart, CartItem
from cart.permissions import IsCartOwner
from catalog import services as catalog_services
from catalog.models import Inventory

pytestmark = pytest.mark.django_db


def cart_url():
    return reverse("cart:detail")


def add_url():
    return reverse("cart:item-create")


def item_url(item):
    return reverse("cart:item-detail", kwargs={"pk": item.pk})


def test_first_get_and_head_do_not_create_cart_and_repeat_is_stable(
    customer_client, customer
):
    assert not Cart.objects.filter(user=customer).exists()
    head = customer_client.head(cart_url())
    assert head.status_code == 200 and head.content == b""
    assert not Cart.objects.filter(user=customer).exists()
    first = customer_client.get(cart_url())
    assert first.status_code == 200
    data = first.json()
    assert data == {
        "id": None,
        "currency": "PLN",
        "items_count": 0,
        "total_quantity": 0,
        "subtotal": "0.00",
        "items": [],
        "created_at": None,
        "updated_at": None,
    }
    second = customer_client.get(cart_url())
    assert second.status_code == 200 and second.json() == data
    assert not Cart.objects.filter(user=customer).exists()
    assert not CartItem.objects.exists()


def test_first_successful_post_creates_cart_and_empty_cart_is_reused(
    customer_client, customer, cart_variant_factory
):
    variant = cart_variant_factory(stock=None)
    assert customer_client.get(cart_url()).json()["id"] is None
    assert not Cart.objects.filter(user=customer).exists()
    response = customer_client.post(
        add_url(), {"variant": variant.pk, "quantity": 2}, format="json"
    )
    assert response.status_code == 201
    cart = Cart.objects.get(user=customer)
    before = cart.updated_at
    body = customer_client.get(cart_url()).json()
    assert body["id"] == cart.pk
    assert body["created_at"] is not None and body["updated_at"] is not None
    assert body["items_count"] == 1 and body["items"][0]["id"] == response.json()["id"]
    assert customer_client.head(cart_url()).status_code == 200
    cart.refresh_from_db()
    assert cart.updated_at == before
    url = reverse("cart:item-detail", kwargs={"pk": response.json()["id"]})
    assert customer_client.delete(url).status_code == 204
    cart.refresh_from_db()
    empty_stamp = cart.updated_at
    empty = customer_client.get(cart_url()).json()
    assert set(empty) == set(body)
    assert empty["id"] == cart.pk and empty["created_at"] == body["created_at"]
    assert empty["items"] == [] and empty["subtotal"] == "0.00"
    assert empty["items_count"] == empty["total_quantity"] == 0
    assert customer_client.head(cart_url()).content == b""
    cart.refresh_from_db()
    assert cart.updated_at == empty_stamp
    again = customer_client.post(
        add_url(), {"variant": variant.pk, "quantity": 3}, format="json"
    )
    assert again.status_code == 201
    assert Cart.objects.filter(user=customer).count() == 1
    assert CartItem.objects.get(pk=again.json()["id"]).cart_id == cart.pk


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_missing_item_api_does_not_create_cart(customer_client, customer, method):
    url = reverse("cart:item-detail", kwargs={"pk": 999999})
    response = getattr(customer_client, method)(url, {"quantity": 2}, format="json")
    assert response.status_code == 404
    assert not Cart.objects.filter(user=customer).exists()
    assert not CartItem.objects.exists()


def test_add_readd_patch_and_delete_contract(customer_client, cart_variant_factory):
    variant = cart_variant_factory(stock=10)
    response = customer_client.post(
        add_url(), {"variant": variant.pk, "quantity": 2}, format="json"
    )
    assert response.status_code == 201
    first = response.json()
    assert first["quantity"] == 2 and first["unit_price"] == "19.90"
    assert first["line_total"] == "39.80"
    assert first["product"]["id"] == variant.product_id
    assert first["variant"] == {"id": variant.pk, "sku": variant.sku}
    response = customer_client.post(
        add_url(), {"variant": variant.pk, "quantity": 3}, format="json"
    )
    assert response.status_code == 200 and response.json()["id"] == first["id"]
    assert response.json()["quantity"] == 5 and response.json()["line_total"] == "99.50"
    url = reverse("cart:item-detail", kwargs={"pk": first["id"]})
    response = customer_client.patch(url, {"quantity": 1}, format="json")
    assert response.status_code == 200 and response.json()["quantity"] == 1
    assert response.json()["line_total"] == "19.90"
    response = customer_client.delete(url)
    assert response.status_code == 204 and response.content == b""
    assert customer_client.delete(url).status_code == 404


@pytest.mark.parametrize(
    "actor",
    [
        "anonymous",
        "customer",
        "manager",
        "staff-customer",
        "inactive",
        "super-manager",
        "super-customer",
    ],
)
@pytest.mark.parametrize("operation", ["get", "add", "patch", "delete"])
def test_jwt_role_matrix_without_denied_side_effects(
    api_client, customer, manager, cart_variant_factory, actor, operation
):
    user = manager if actor in {"manager", "super-manager"} else customer
    if actor == "staff-customer" or actor.startswith("super-"):
        user.is_staff = True
    if actor.startswith("super-"):
        user.is_superuser = True
    user.save(update_fields=["is_staff", "is_superuser"])
    token = str(AccessToken.for_user(user))
    if actor == "inactive":
        user.is_active = False
        user.save(update_fields=["is_active"])
    if actor != "anonymous":
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    # Raw setup may include a manager Cart: permissions must still reject access.
    cart = Cart.objects.create(user=user)
    first, second = cart_variant_factory(), cart_variant_factory()
    item = CartItem.objects.create(cart=cart, variant=first, quantity=1)
    before = list(CartItem.objects.values("id", "cart_id", "variant_id", "quantity"))
    if operation == "get":
        response = api_client.get(cart_url())
    elif operation == "add":
        response = api_client.post(
            add_url(), {"variant": second.pk, "quantity": 1}, format="json"
        )
    elif operation == "patch":
        response = api_client.patch(item_url(item), {"quantity": 2}, format="json")
    else:
        response = api_client.delete(item_url(item))
    allowed = actor in {"customer", "staff-customer", "super-customer"}
    expected = (
        {"get": 200, "add": 201, "patch": 200, "delete": 204}[operation]
        if allowed
        else (401 if actor in {"anonymous", "inactive"} else 403)
    )
    assert response.status_code == expected
    if not allowed:
        assert (
            list(CartItem.objects.values("id", "cart_id", "variant_id", "quantity"))
            == before
        )
        assert Cart.objects.count() == 1


@pytest.mark.parametrize(
    "payload,key",
    [
        ({}, "variant"),
        ({"variant": 1}, "quantity"),
        ({"variant": "oops", "quantity": 1}, "variant"),
        ({"variant": 0, "quantity": 1}, "variant"),
        ({"variant": True, "quantity": 1}, "variant"),
        ({"variant": 1, "quantity": 0}, "quantity"),
        ({"variant": 1, "quantity": -1}, "quantity"),
        ({"variant": 1, "quantity": 1000}, "quantity"),
        ({"variant": 1, "quantity": "1.5"}, "quantity"),
        ({"variant": 1, "quantity": True}, "quantity"),
        ({"variant": 1, "quantity": None}, "quantity"),
        ({"variant": 1, "quantity": []}, "quantity"),
        ({"variant": 1, "quantity": 1, "price": "0.01"}, "price"),
        ({"variant": 1, "quantity": 1, "cart": 1}, "cart"),
        ({"variant": 1, "quantity": 1, "user": 1}, "user"),
        ({"variant": 1, "quantity": 1, "subtotal": "1.00"}, "subtotal"),
    ],
)
def test_strict_add_input_does_not_create_cart(customer_client, payload, key):
    response = customer_client.post(add_url(), payload, format="json")
    assert response.status_code == 400 and key in response.json()["error"]["details"]
    assert Cart.objects.count() == 0 and CartItem.objects.count() == 0


def test_malformed_json_and_unknown_variant(customer_client):
    response = customer_client.post(
        add_url(), data='{"variant":', content_type="application/json"
    )
    assert response.status_code == 400
    response = customer_client.post(
        add_url(), {"variant": 9223372036854775807, "quantity": 1}, format="json"
    )
    assert response.status_code == 404
    assert Cart.objects.count() == 0


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"quantity": 0},
        {"quantity": 1, "variant": 1},
        {"quantity": 1, "line_total": "0.01"},
    ],
)
def test_patch_only_sets_valid_quantity(
    customer_client, customer, cart_variant_factory, payload
):
    item, _ = services.add_item(
        user=customer, variant_id=cart_variant_factory().pk, quantity=2
    )
    response = customer_client.patch(item_url(item), payload, format="json")
    assert response.status_code == 400
    item.refresh_from_db()
    assert item.quantity == 2


@pytest.mark.parametrize("target", ["variant", "product", "category", "brand"])
def test_inactive_entities_reject_add_and_update_but_allow_read_delete(
    customer_client, customer, cart_variant_factory, target
):
    variant = cart_variant_factory(stock=10)
    item, _ = services.add_item(user=customer, variant_id=variant.pk, quantity=2)
    instance = (
        variant
        if target == "variant"
        else (
            variant.product if target == "product" else getattr(variant.product, target)
        )
    )
    instance.is_active = False
    instance.save(update_fields=["is_active"])
    assert (
        customer_client.post(
            add_url(), {"variant": variant.pk, "quantity": 1}, format="json"
        ).status_code
        == 400
    )
    assert (
        customer_client.patch(
            item_url(item), {"quantity": 3}, format="json"
        ).status_code
        == 400
    )
    body = customer_client.get(cart_url()).json()
    assert body["items_count"] == 1 and body["subtotal"] == "39.80"
    assert body["items"][0]["is_available"] is False
    assert body["items"][0]["has_sufficient_stock"] is False
    assert customer_client.delete(item_url(item)).status_code == 204


@pytest.mark.parametrize(
    "stock,available,sufficient",
    [(None, False, False), (0, False, False), (1, True, False), (3, True, True)],
)
def test_stock_is_information_not_reservation(
    customer_client, cart_variant_factory, stock, available, sufficient
):
    variant = cart_variant_factory(stock=stock)
    before = list(Inventory.objects.values("id", "quantity", "updated_at"))
    response = customer_client.post(
        add_url(), {"variant": variant.pk, "quantity": 3}, format="json"
    )
    assert response.status_code == 201
    row = response.json()
    assert (
        row["is_available"] is available and row["has_sufficient_stock"] is sufficient
    )
    assert list(Inventory.objects.values("id", "quantity", "updated_at")) == before


def test_totals_live_catalog_price_zero_and_large_amounts(
    customer_client, customer, cart_variant_factory
):
    first = cart_variant_factory(price="19.90", stock=10)
    second = cart_variant_factory(price="12.50", stock=10)
    services.add_item(user=customer, variant_id=first.pk, quantity=2)
    services.add_item(user=customer, variant_id=second.pk, quantity=3)
    body = customer_client.get(cart_url()).json()
    assert body["items_count"] == 2 and body["total_quantity"] == 5
    assert body["subtotal"] == "77.30" and body["currency"] == "PLN"
    stamp = body["updated_at"]
    catalog_services.update_variant(first.product_id, first.pk, price=Decimal("20.25"))
    body = customer_client.get(cart_url()).json()
    assert body["subtotal"] == "78.00" and body["updated_at"] == stamp
    assert body["items"][0]["unit_price"] == "20.25"
    catalog_services.update_variant(first.product_id, first.pk, price=Decimal("0.00"))
    assert customer_client.get(cart_url()).json()["subtotal"] == "37.50"
    services.clear_cart(user=customer)
    huge = cart_variant_factory(price="99999999.99")
    services.add_item(user=customer, variant_id=huge.pk, quantity=999)
    assert customer_client.get(cart_url()).json()["subtotal"] == "99899999990.01"


def test_selected_variant_price_is_not_product_minimum(
    customer_client, cart_variant_factory
):
    cheap = cart_variant_factory(price="1.00")
    chosen = cart_variant_factory(price="25.00", product=cheap.product)
    response = customer_client.post(
        add_url(), {"variant": chosen.pk, "quantity": 2}, format="json"
    )
    assert response.status_code == 201
    assert response.json()["variant"]["id"] == chosen.pk
    assert response.json()["unit_price"] == "25.00"
    assert response.json()["line_total"] == "50.00"


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_foreign_item_is_404_and_object_permission_also_rejects(
    customer_client, customer, other_customer, cart_variant_factory, method
):
    item, _ = services.add_item(
        user=other_customer, variant_id=cart_variant_factory().pk, quantity=3
    )
    response = getattr(customer_client, method)(
        item_url(item), {"quantity": 2}, format="json"
    )
    assert response.status_code == 404
    item.refresh_from_db()
    assert item.quantity == 3
    own_body = customer_client.get(cart_url()).json()
    assert own_body["id"] is None and own_body["items"] == []
    assert not Cart.objects.filter(user=customer).exists()
    assert (
        IsCartOwner().has_object_permission(SimpleNamespace(user=customer), None, item)
        is False
    )


def test_no_new_clear_or_item_get_endpoint(
    customer_client, customer, cart_variant_factory
):
    item, _ = services.add_item(
        user=customer, variant_id=cart_variant_factory().pk, quantity=1
    )
    assert customer_client.get(item_url(item)).status_code == 405
    assert (
        customer_client.put(item_url(item), {"quantity": 1}, format="json").status_code
        == 405
    )
    assert customer_client.delete(cart_url()).status_code == 405
