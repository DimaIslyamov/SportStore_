from uuid import uuid4

import pytest
from django.db import OperationalError, connection

from accounts.models import Address
from catalog.models import ProductImage
from orders.models import Order


@pytest.mark.django_db
def test_schema_generation_does_not_read_or_write_business_data(
    api_client, customer, order_factory, django_assert_num_queries, openapi
):
    order_factory(quantities=(1,))
    assert connection.vendor == "postgresql"
    assert connection.settings_dict["NAME"].startswith("test_")
    with django_assert_num_queries(0):
        response = api_client.get(
            "/api/schema/", HTTP_ACCEPT="application/vnd.oai.openapi+json"
        )
    assert response.status_code == 200
    assert response.json() == openapi


@pytest.mark.django_db
def test_actual_public_catalog_matches_optional_auth_and_nullable_fields(
    api_client, order_variants, check_response
):
    product = order_variants[0].product
    body = check_response("products_list", api_client.get("/api/v1/products/"), 200)
    assert body["results"][0]["primary_image"] is None
    assert body["results"][0]["from_price"] == "19.90"
    check_response(
        "products_retrieve", api_client.get(f"/api/v1/products/{product.slug}/"), 200
    )
    api_client.credentials(HTTP_AUTHORIZATION="Bearer invalid")
    body = check_response("products_list", api_client.get("/api/v1/products/"), 401)
    assert body["error"]["code"] == "token_not_valid"


@pytest.mark.django_db
@pytest.mark.parametrize("command", ["cart", "inventory", "status"])
def test_empty_command_patch_is_validation_error_on_existing_resource(
    api_client,
    customer,
    manager,
    prepare_order_cart,
    order_variants,
    order_factory,
    command,
    check_response,
):
    if command == "cart":
        prepare_order_cart(owner=customer, quantities=(1,))
        item = customer.cart.items.get()
        url, name, field, user = (
            f"/api/v1/cart/items/{item.pk}/",
            "cart_items_update",
            "quantity",
            customer,
        )
    elif command == "inventory":
        variant = order_variants[0]
        url = f"/api/v1/products/{variant.product_id}/variants/{variant.pk}/inventory/"
        name, field, user = "inventory_update", "quantity", manager
    else:
        order = order_factory(quantities=(1,))
        url, name, field, user = (
            f"/api/v1/orders/{order.pk}/status/",
            "orders_status_update",
            "status",
            manager,
        )
    api_client.force_authenticate(user=user)
    body = check_response(name, api_client.patch(url, {}, format="json"), 400)
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert field in body["error"]["details"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "entity", ["profile", "address", "product", "category", "brand", "variant", "image"]
)
def test_normal_patch_accepts_empty_object_on_existing_resource(
    api_client,
    customer,
    manager,
    prepare_order_cart,
    order_variants,
    entity,
    check_response,
):
    variant = order_variants[0]
    product = variant.product
    user = manager
    if entity == "profile":
        user, name, url = customer, "users_me_update", "/api/v1/users/me/"
    elif entity == "address":
        address = prepare_order_cart(owner=customer, quantities=(1,))
        user, name, url = (
            customer,
            "addresses_update",
            f"/api/v1/addresses/{address.pk}/",
        )
    elif entity in {"product", "category", "brand"}:
        obj = {
            "product": product,
            "category": product.category,
            "brand": product.brand,
        }[entity]
        plural = {"product": "products", "category": "categories", "brand": "brands"}[
            entity
        ]
        name, url = f"{plural}_update", f"/api/v1/{plural}/{obj.pk}/"
    elif entity == "variant":
        name, url = (
            "variants_update",
            f"/api/v1/products/{product.pk}/variants/{variant.pk}/",
        )
    else:
        image = ProductImage.objects.create(
            product=product, image_url="https://images.example.com/test.png"
        )
        name, url = "images_update", f"/api/v1/products/{product.pk}/images/{image.pk}/"
    api_client.force_authenticate(user=user)
    check_response(name, api_client.patch(url, {}, format="json"), 200)


@pytest.mark.django_db(transaction=True)
def test_checkout_payment_and_order_responses_match_documented_contracts(
    api_client, customer, manager, prepare_order_cart, check_response
):
    address = prepare_order_cart(owner=customer, quantities=(2,))
    api_client.force_authenticate(user=customer)
    checkout_headers = {"HTTP_IDEMPOTENCY_KEY": str(uuid4())}
    payload = {"address_id": address.pk}
    initial = api_client.post(
        "/api/v1/orders/checkout/", payload, format="json", **checkout_headers
    )
    order = check_response("orders_checkout", initial, 201)
    url = f"/api/v1/orders/{order['id']}/"
    failed_key = {"HTTP_IDEMPOTENCY_KEY": str(uuid4())}
    failed = check_response(
        "payments_create",
        api_client.post(
            url + "payments/", {"outcome": "failed"}, format="json", **failed_key
        ),
        201,
    )
    assert failed["status"] == "FAILED"
    replay = check_response(
        "payments_create",
        api_client.post(
            url + "payments/", {"outcome": "failed"}, format="json", **failed_key
        ),
        200,
    )
    assert replay == failed
    conflict = check_response(
        "payments_create",
        api_client.post(url + "payments/", {}, format="json", **failed_key),
        409,
    )
    assert conflict["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    api_client.force_authenticate(user=manager)
    guard = check_response(
        "orders_status_update",
        api_client.patch(url + "status/", {"status": "PAID"}, format="json"),
        409,
    )
    assert guard["error"]["code"] == "PAYMENT_CONFIRMATION_REQUIRED"
    api_client.force_authenticate(user=customer)
    paid = check_response(
        "payments_create",
        api_client.post(
            url + "payments/", {}, format="json", HTTP_IDEMPOTENCY_KEY=str(uuid4())
        ),
        201,
    )
    assert paid["status"] == "SUCCESS" and paid["amount"] == order["total"]
    replay_response = api_client.post(
        "/api/v1/orders/checkout/", payload, format="json", **checkout_headers
    )
    assert check_response("orders_checkout", replay_response, 201) == order
    assert replay_response["X-Request-ID"] != initial["X-Request-ID"]
    summary = check_response("orders_list", api_client.get("/api/v1/orders/"), 200)[0]
    assert "items" not in summary and "shipping_address" not in summary
    detail = check_response("orders_retrieve", api_client.get(url), 200)
    assert detail["items"] == order["items"]
    assert detail["shipping_address"] == order["shipping_address"]
    cancelled = check_response(
        "orders_cancel", api_client.post(url + "cancel/", {}, format="json"), 200
    )
    assert set(cancelled) == set(summary)
    assert cancelled["status"] == "CANCELLED"


@pytest.mark.django_db
def test_empty_cart_nulls_and_cart_mutation_responses(
    api_client, customer, order_variants, check_response
):
    api_client.force_authenticate(user=customer)
    cart = check_response("cart_retrieve", api_client.get("/api/v1/cart/"), 200)
    assert cart["id"] is cart["created_at"] is cart["updated_at"] is None
    payload = {"variant": order_variants[0].pk, "quantity": 1}
    created = check_response(
        "cart_items_create",
        api_client.post("/api/v1/cart/items/", payload, format="json"),
        201,
    )
    increased = check_response(
        "cart_items_create",
        api_client.post("/api/v1/cart/items/", payload, format="json"),
        200,
    )
    assert increased["id"] == created["id"]
    assert increased["line_total"] == "39.80"
    check_response(
        "cart_items_delete",
        api_client.delete(f"/api/v1/cart/items/{created['id']}/"),
        204,
    )


def test_health_503_is_documented_stage16_envelope(
    api_client, monkeypatch, check_response
):
    def unavailable(*args, **kwargs):
        raise OperationalError("database unavailable")

    monkeypatch.setattr("core.views.connection.cursor", unavailable)
    body = check_response("health_retrieve", api_client.get("/api/v1/health/"), 503)
    assert body["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert body["error"]["details"] == {"status": "error", "database": "unavailable"}


@pytest.mark.django_db
def test_roles_and_ownership_failures_match_documentation(
    api_client, customer, manager, other_customer, order_factory, check_response
):
    check_response("users_me_retrieve", api_client.get("/api/v1/users/me/"), 401)
    order = order_factory(owner=other_customer, quantities=(1,))
    api_client.force_authenticate(user=customer)
    check_response(
        "orders_retrieve", api_client.get(f"/api/v1/orders/{order.pk}/"), 404
    )
    api_client.force_authenticate(user=manager)
    check_response("addresses_list", api_client.get("/api/v1/addresses/"), 403)
    assert Order.objects.filter(pk=order.pk).exists()
    assert Address.objects.filter(user=other_customer).exists()
