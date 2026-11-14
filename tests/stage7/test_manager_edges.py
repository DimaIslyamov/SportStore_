"""Manager API edge cases beyond the existing catalog regression tests."""

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from rest_framework_simplejwt.tokens import AccessToken

from catalog import services
from catalog.models import Inventory, Product, ProductImage, ProductVariant


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "operation",
    [
        "product-create",
        "product-patch",
        "variant-create",
        "variant-patch",
        "inventory-get",
        "inventory-patch",
        "image-create",
        "image-patch",
        "image-delete",
    ],
)
def test_active_technical_superuser_allowed_on_every_manager_operation(
    api_client,
    endpoint_urls,
    stage7_product,
    stage7_variant,
    stage7_image,
    operation,
    file_factory,
):
    user = get_user_model().objects.create_superuser(
        email="technical-admin@example.com",
        password="Synthetic-test-password-123!",
        role="CUSTOMER",
    )
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    Inventory.objects.create(variant=stage7_variant, quantity=3)
    method, url, payload, fmt, expected = {
        "product-create": (
            "post",
            "/api/v1/products/",
            {
                "name": "New",
                "slug": "admin-product",
                "category": stage7_product.category_id,
                "brand": stage7_product.brand_id,
            },
            "json",
            201,
        ),
        "product-patch": (
            "patch",
            endpoint_urls.product,
            {"name": "Admin"},
            "json",
            200,
        ),
        "variant-create": (
            "post",
            endpoint_urls.variants,
            {"sku": "ADMIN-V", "price": "1.00"},
            "json",
            201,
        ),
        "variant-patch": (
            "patch",
            endpoint_urls.variant,
            {"price": "1.00"},
            "json",
            200,
        ),
        "inventory-get": ("get", endpoint_urls.inventory, {}, "json", 200),
        "inventory-patch": (
            "patch",
            endpoint_urls.inventory,
            {"quantity": 4},
            "json",
            200,
        ),
        "image-create": (
            "post",
            endpoint_urls.images,
            {"file": file_factory()},
            "multipart",
            201,
        ),
        "image-patch": (
            "patch",
            endpoint_urls.image,
            {"alt_text": "Admin"},
            "json",
            200,
        ),
        "image-delete": ("delete", endpoint_urls.image, {}, "json", 204),
    }[operation]
    response = getattr(api_client, method)(url, payload, format=fmt)
    assert response.status_code == expected
    if operation == "product-create":
        assert Product.objects.get(pk=response.json()["id"]).slug == "admin-product"
    elif operation == "product-patch":
        stage7_product.refresh_from_db()
        assert stage7_product.name == "Admin"
    elif operation == "variant-create":
        assert Inventory.objects.get(variant_id=response.json()["id"]).quantity == 0
    elif operation == "variant-patch":
        stage7_variant.refresh_from_db()
        assert str(stage7_variant.price) == "1.00"
    elif operation in {"inventory-get", "inventory-patch"}:
        assert response.json()["quantity"] == (3 if operation == "inventory-get" else 4)
    elif operation == "image-create":
        assert ProductImage.objects.get(pk=response.json()["id"]).storage_key
    elif operation == "image-patch":
        stage7_image.refresh_from_db()
        assert stage7_image.alt_text == "Admin"
    else:
        assert not ProductImage.objects.filter(pk=stage7_image.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "field,value",
    [
        ("brand", 99999999),
        ("category", None),
        ("name", ""),
        ("name", "x" * 201),
        ("id", 1),
        ("unknown", 1),
        ("images", []),
        ("created_at", "2026-01-01"),
    ],
)
def test_invalid_product_create_has_no_side_effects(
    manager_api, stage7_product, field, value
):
    payload = {
        "name": "New",
        "slug": "invalid-new",
        "category": stage7_product.category_id,
        "brand": stage7_product.brand_id,
    }
    payload[field] = value
    response = manager_api.post("/api/v1/products/", payload, format="json")
    assert response.status_code == 400 and field in response.json()["error"]["details"]
    assert Product.objects.count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "field,value",
    [
        ("price", "Infinity"),
        ("price", "100000000.00"),
        ("price", None),
        ("id", 1),
        ("unknown", 1),
        ("inventory", {}),
        ("sku", ""),
        ("barcode", "x" * 33),
    ],
)
def test_invalid_variant_create_does_not_create_stock(
    manager_api, endpoint_urls, stage7_variant, field, value
):
    payload = {"sku": "NEW-EDGE", "price": "1.00"}
    payload[field] = value
    response = manager_api.post(endpoint_urls.variants, payload, format="json")
    assert response.status_code == 400 and field in response.json()["error"]["details"]
    assert ProductVariant.objects.count() == 1 and Inventory.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("target", ["product", "variant"])
def test_update_sql_failure_rolls_back_previous_write(
    stage7_product, stage7_variant, target, monkeypatch
):
    model = Product if target == "product" else ProductVariant
    original = model.save

    def save_then_fail(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise IntegrityError("Injected update failure")

    monkeypatch.setattr(model, "save", save_then_fail)
    with pytest.raises(IntegrityError):
        if target == "product":
            services.update_product(stage7_product.pk, name="Changed")
        else:
            services.update_variant(stage7_product.pk, stage7_variant.pk, price="9.00")
    stage7_product.refresh_from_db()
    stage7_variant.refresh_from_db()
    assert stage7_product.name == "Stage 7 Shirt"
    assert str(stage7_variant.price) == "25.00"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "method,target", [("patch", "product"), ("post", "variants"), ("patch", "variant")]
)
def test_missing_catalog_objects_return_404(
    manager_api, stage7_product, method, target
):
    url = {
        "product": "/api/v1/products/99999999/",
        "variants": "/api/v1/products/99999999/variants/",
        "variant": f"/api/v1/products/{stage7_product.pk}/variants/99999999/",
    }[target]
    response = getattr(manager_api, method)(
        url, {"sku": "MISSING", "price": "1.00"}, format="json"
    )
    assert response.status_code == 404
    assert Product.objects.count() == 1 and ProductVariant.objects.count() == 0
