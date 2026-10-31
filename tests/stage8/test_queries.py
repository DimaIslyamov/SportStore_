import pytest
from django.db import connection
from django.urls import reverse

from cart import services
from cart.models import Cart, CartItem

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("method", ["get", "head"])
def test_missing_cart_read_uses_one_select_without_writes(
    customer, customer_client, django_assert_num_queries, method
):
    assert connection.vendor == "postgresql"
    assert not Cart.objects.filter(user=customer).exists()
    with django_assert_num_queries(1) as captured:
        response = getattr(customer_client, method)(reverse("cart:detail"))
        body = response.json() if method == "get" else None
    assert response.status_code == 200
    assert all(query["sql"].lstrip().upper().startswith("SELECT") for query in captured)
    if method == "get":
        assert body["id"] is body["created_at"] is body["updated_at"] is None
        assert body["items"] == [] and body["subtotal"] == "0.00"
    else:
        assert response.content == b""
    assert not Cart.objects.filter(user=customer).exists()
    assert not CartItem.objects.exists()


@pytest.mark.parametrize("method", ["get", "head"])
def test_saved_empty_cart_read_uses_two_selects_without_writes(
    customer, customer_client, django_assert_num_queries, method
):
    cart = services.get_or_create_cart(user=customer)
    stamp = cart.updated_at
    with django_assert_num_queries(2) as captured:
        response = getattr(customer_client, method)(reverse("cart:detail"))
        body = response.json() if method == "get" else None
    assert response.status_code == 200
    assert all(query["sql"].lstrip().upper().startswith("SELECT") for query in captured)
    if method == "get":
        assert body["id"] == cart.pk and body["items"] == []
        assert body["created_at"] is not None and body["updated_at"] is not None
    else:
        assert response.content == b""
    cart.refresh_from_db()
    assert cart.updated_at == stamp


def test_cart_get_has_constant_query_count(
    customer, customer_client, cart_variant_factory, django_assert_num_queries
):
    assert connection.vendor == "postgresql"
    first = cart_variant_factory(price="10.00", stock=20)
    services.add_item(user=customer, variant_id=first.pk, quantity=2)
    url = reverse("cart:detail")
    with django_assert_num_queries(2):
        small_response = customer_client.get(url)
        small = small_response.json()
    assert small_response.status_code == 200 and small["items_count"] == 1
    assert small["subtotal"] == "20.00" and small["items"][0]["is_available"] is True
    for number in range(1, 8):
        variant = cart_variant_factory(price="10.00", stock=None if number % 2 else 20)
        services.add_item(user=customer, variant_id=variant.pk, quantity=2)
    with django_assert_num_queries(2):
        large_response = customer_client.get(url)
        large = large_response.json()
    assert large_response.status_code == 200
    assert large["items_count"] == len(large["items"]) == 8
    assert large["total_quantity"] == 16 and large["subtotal"] == "160.00"
    assert len({row["id"] for row in large["items"]}) == 8
    assert sum(row["is_available"] for row in large["items"]) == 4
    for row in large["items"]:
        assert row["product"]["name"]
        assert row["variant"]["sku"]
        assert row["quantity"] == 2
        assert row["unit_price"] == "10.00" and row["line_total"] == "20.00"
        assert row["has_sufficient_stock"] is row["is_available"]
