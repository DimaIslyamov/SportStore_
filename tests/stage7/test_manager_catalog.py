from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from catalog import services
from catalog.models import Inventory, Product, ProductImage, ProductVariant

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("actor", ["anonymous", "customer", "staff", "inactive"])
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
def test_manager_operations_deny_without_side_effects(
    actor,
    operation,
    customer,
    manager,
    api_client,
    endpoint_urls,
    stage7_product,
    stage7_variant,
    stage7_image,
    fake_storage,
    file_factory,
):
    inventory = Inventory.objects.create(variant=stage7_variant, quantity=3)
    if actor != "anonymous":
        user = manager if actor == "inactive" else customer
        token = str(AccessToken.for_user(user))
        if actor == "staff":
            user.is_staff = True
            user.save(update_fields=["is_staff"])
        if actor == "inactive":
            user.is_active = False
            user.save(update_fields=["is_active"])
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    method, url, data, format = {
        "product-create": (
            "post",
            "/api/v1/products/",
            {
                "name": "New",
                "slug": "new",
                "category": stage7_product.category_id,
                "brand": stage7_product.brand_id,
            },
            "json",
        ),
        "product-patch": ("patch", endpoint_urls.product, {"name": "Changed"}, "json"),
        "variant-create": (
            "post",
            endpoint_urls.variants,
            {"sku": "NEW", "price": "1.00"},
            "json",
        ),
        "variant-patch": ("patch", endpoint_urls.variant, {"price": "1.00"}, "json"),
        "inventory-get": ("get", endpoint_urls.inventory, {}, "json"),
        "inventory-patch": ("patch", endpoint_urls.inventory, {"quantity": 99}, "json"),
        "image-create": (
            "post",
            endpoint_urls.images,
            {"file": file_factory()},
            "multipart",
        ),
        "image-patch": (
            "patch",
            endpoint_urls.image,
            {"file": file_factory()},
            "multipart",
        ),
        "image-delete": ("delete", endpoint_urls.image, {}, "json"),
    }[operation]
    before = [
        model.objects.count()
        for model in [Product, ProductVariant, Inventory, ProductImage]
    ]
    response = getattr(api_client, method)(url, data, format=format)
    assert response.status_code == (401 if actor in {"anonymous", "inactive"} else 403)
    assert [
        model.objects.count()
        for model in [Product, ProductVariant, Inventory, ProductImage]
    ] == before
    stage7_product.refresh_from_db()
    stage7_variant.refresh_from_db()
    stage7_image.refresh_from_db()
    inventory.refresh_from_db()
    assert stage7_product.name == "Stage 7 Shirt"
    assert stage7_variant.price == Decimal("25.00")
    assert stage7_image.image_url.endswith("legacy.png") and inventory.quantity == 3
    assert fake_storage.events == []


def test_manager_creates_then_another_manager_updates(manager_api, stage7_product):
    response = manager_api.post(
        "/api/v1/products/",
        {
            "name": "New",
            "slug": "new",
            "category": stage7_product.category_id,
            "brand": stage7_product.brand_id,
        },
        format="json",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["slug"] == "new" and "from_price" not in body
    other = get_user_model().objects.create_user(
        email="other-manager@example.com", role="MANAGER"
    )
    client = APIClient()
    client.force_authenticate(user=other)
    url = f"/api/v1/products/{body['id']}/"
    updated = client.patch(url, {"description": "Changed"}, format="json")
    assert updated.status_code == 200
    assert updated.json()["name"] == "New"
    assert updated.json()["description"] == "Changed"
    assert APIClient().get("/api/v1/products/new/").status_code == 404
    for active in [False, True]:
        changed = client.patch(url, {"is_active": active}, format="json")
        assert changed.status_code == 200 and changed.json()["is_active"] is active


def test_superuser_customer_can_manage_but_staff_alone_cannot(
    api_client, stage7_product, raw_password
):
    user = get_user_model().objects.create_superuser(
        email="stage7-admin@example.com", password=raw_password, role="CUSTOMER"
    )
    api_client.force_authenticate(user=user)
    response = api_client.patch(
        f"/api/v1/products/{stage7_product.pk}/", {"name": "Admin"}, format="json"
    )
    assert response.status_code == 200


def test_inactive_category_assignment_is_allowed_but_hides_public_product(
    manager_api, endpoint_urls, stage7_product, stage7_variant
):
    category = stage7_product.category
    category.is_active = False
    category.save(update_fields=["is_active"])
    response = manager_api.patch(
        endpoint_urls.product, {"category": category.pk}, format="json"
    )
    assert response.status_code == 200
    assert (
        APIClient().get(f"/api/v1/products/{stage7_product.slug}/").status_code == 404
    )


@pytest.mark.parametrize(
    "target,method",
    [
        ("variants", "get"),
        ("variant", "put"),
        ("variant", "delete"),
        ("images", "get"),
        ("image", "get"),
        ("inventory", "put"),
    ],
)
def test_no_extra_manager_operations(manager_api, endpoint_urls, target, method):
    response = getattr(manager_api, method)(getattr(endpoint_urls, target))
    assert response.status_code == 405


def test_numeric_slug_get_and_id_patch_are_distinct(
    manager_api, stage7_product, stage7_variant
):
    stage7_product.slug = "123456789"
    stage7_product.save(update_fields=["slug"])
    public = APIClient()
    assert public.get("/api/v1/products/123456789/").json()["id"] == stage7_product.pk
    response = manager_api.patch(
        f"/api/v1/products/{stage7_product.pk}/", {"name": "Changed"}, format="json"
    )
    assert response.status_code == 200 and response.json()["name"] == "Changed"
    assert (
        manager_api.patch("/api/v1/products/not-an-id/", {}, format="json").status_code
        == 404
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", 100),
        ("from_price", "1.00"),
        ("created_at", "2026-01-01"),
        ("attributes", []),
        ("slug", "different"),
        ("category", 99999999),
    ],
)
def test_product_rejects_invalid_or_immutable_input(
    manager_api, endpoint_urls, field, value
):
    response = manager_api.patch(endpoint_urls.product, {field: value}, format="json")
    assert response.status_code == 400 and field in response.json()


def test_variant_create_patch_deactivate_and_public_regression(
    manager_api, endpoint_urls, stage7_product, stage7_variant
):
    response = manager_api.post(
        endpoint_urls.variants,
        {
            "sku": "STAGE7-L",
            "price": "0.00",
            "barcode": None,
        },
        format="json",
    )
    assert response.status_code == 201
    data = response.json()
    variant = ProductVariant.objects.get(pk=data["id"])
    assert data["price"] == "0.00" and data["product"] == stage7_product.pk
    assert Inventory.objects.get(variant=variant).quantity == 0
    url = f"{endpoint_urls.variants}{variant.pk}/"
    public_url = f"/api/v1/products/{stage7_product.slug}/"
    public = APIClient()
    assert public.get(public_url).json()["from_price"] == "0.00"
    assert (
        manager_api.patch(url, {"barcode": "012345"}, format="json").status_code == 200
    )
    assert (
        manager_api.patch(url, {"barcode": None}, format="json").json()["barcode"]
        is None
    )
    for active, expected_price in [(False, "25.00"), (True, "0.00")]:
        changed = manager_api.patch(url, {"is_active": active}, format="json")
        assert changed.status_code == 200
        body = public.get(public_url).json()
        assert body["from_price"] == expected_price
        assert (variant.pk in {row["id"] for row in body["variants"]}) is active
    stock_url = f"{url}inventory/"
    for quantity in [7, 0]:
        response = manager_api.patch(stock_url, {"quantity": quantity}, format="json")
        assert response.status_code == 200
        assert public.get(public_url).json()["in_stock"] is (quantity > 0)
    assert (
        manager_api.patch(
            endpoint_urls.product, {"is_active": False}, format="json"
        ).status_code
        == 200
    )
    assert public.get(public_url).status_code == 404
    assert manager_api.patch(url, {"price": "5.00"}, format="json").status_code == 200
    assert (
        manager_api.patch(
            endpoint_urls.product, {"is_active": True}, format="json"
        ).status_code
        == 200
    )
    assert public.get(public_url).json()["from_price"] == "5.00"


@pytest.mark.parametrize(
    "payload,key",
    [
        ({"sku": "lower"}, "sku"),
        ({"sku": "NEW-SKU"}, "sku"),
        ({"barcode": ""}, "barcode"),
        ({"barcode": "abc"}, "barcode"),
        ({"price": "-1"}, "price"),
        ({"price": "NaN"}, "price"),
        ({"price": "1.001"}, "price"),
        ({"product": 1}, "product"),
    ],
)
def test_variant_rejects_invalid_input(manager_api, endpoint_urls, payload, key):
    response = manager_api.patch(endpoint_urls.variant, payload, format="json")
    assert response.status_code == 400 and key in response.json()


def test_api_duplicates_are_normal_400(manager_api, stage7_product, endpoint_urls):
    response = manager_api.post(
        "/api/v1/products/",
        {
            "name": "Duplicate",
            "slug": stage7_product.slug,
            "category": stage7_product.category_id,
            "brand": stage7_product.brand_id,
        },
        format="json",
    )
    assert response.status_code == 400 and "slug" in response.json()
    first = manager_api.post(
        endpoint_urls.variants,
        {"sku": "UNIQUE", "price": "2.00", "barcode": "001"},
        format="json",
    )
    assert first.status_code == 201
    for payload, key in [
        ({"sku": "UNIQUE", "price": "3.00"}, "sku"),
        ({"sku": "OTHER", "price": "3.00", "barcode": "001"}, "barcode"),
    ]:
        response = manager_api.post(endpoint_urls.variants, payload, format="json")
        assert response.status_code == 400 and key in response.json()


@pytest.mark.parametrize(
    "target,value",
    [
        ("product", "UPPERCASE"),
        ("product", "bad slug"),
        ("variant", "lowercase"),
        ("variant", "BAD/SKU"),
    ],
)
def test_creation_identifier_formats_are_validated(
    manager_api, stage7_product, endpoint_urls, target, value
):
    before = (Product.objects.count(), ProductVariant.objects.count())
    if target == "product":
        url = "/api/v1/products/"
        key = "slug"
        payload = {
            "name": "New",
            "slug": value,
            "category": stage7_product.category_id,
            "brand": stage7_product.brand_id,
        }
    else:
        url = endpoint_urls.variants
        key = "sku"
        payload = {"sku": value, "price": "1.00"}
    response = manager_api.post(url, payload, format="json")
    assert response.status_code == 400 and key in response.json()
    assert (Product.objects.count(), ProductVariant.objects.count()) == before


@pytest.mark.parametrize("field", ["slug", "sku", "barcode"])
def test_service_handles_unique_race_after_savepoint(stage7_product, field):
    if field == "slug":

        def call():
            return services.create_product(
                category=stage7_product.category,
                brand=stage7_product.brand,
                name="Duplicate",
                slug=stage7_product.slug,
            )
    else:
        services.create_variant(
            stage7_product.pk, sku="UNIQUE", price=Decimal("2.00"), barcode="001"
        )

        def call():
            return services.create_variant(
                stage7_product.pk,
                sku="UNIQUE" if field == "sku" else "OTHER",
                barcode="002" if field == "sku" else "001",
                price=Decimal("2.00"),
            )

    with pytest.raises(ValidationError) as error:
        call()
    assert field in error.value.detail
    assert Product.objects.filter(pk=stage7_product.pk).exists()


def test_variant_and_inventory_creation_roll_back_together(stage7_product, monkeypatch):
    def fail(*args, **kwargs):
        raise IntegrityError("Injected Inventory failure")

    monkeypatch.setattr(Inventory.objects, "create", fail)
    with pytest.raises(IntegrityError):
        services.create_variant(
            stage7_product.pk, sku="ROLLBACK", price=Decimal("2.00")
        )
    assert not ProductVariant.objects.filter(sku="ROLLBACK").exists()
    assert Inventory.objects.count() == 0


def test_missing_inventory_get_does_not_write_patch_creates_absolute_stock(
    manager_api, endpoint_urls, stage7_variant
):
    assert manager_api.get(endpoint_urls.inventory).status_code == 404
    assert not Inventory.objects.filter(variant=stage7_variant).exists()
    for quantity in [9, 2, 0]:
        response = manager_api.patch(
            endpoint_urls.inventory, {"quantity": quantity}, format="json"
        )
        assert response.status_code == 200 and response.json()["quantity"] == quantity
        assert Inventory.objects.filter(variant=stage7_variant).count() == 1
        read = manager_api.get(endpoint_urls.inventory)
        assert read.status_code == 200 and read.json()["quantity"] == quantity
        assert read.json()["variant"] == stage7_variant.pk
        assert read.json()["updated_at"]
    stage7_variant.is_active = False
    stage7_variant.save(update_fields=["is_active"])
    product = stage7_variant.product
    product.is_active = False
    product.save(update_fields=["is_active"])
    before = Inventory.objects.values_list("pk", "quantity", "updated_at").get(
        variant=stage7_variant
    )
    read = manager_api.get(endpoint_urls.inventory)
    assert read.status_code == 200 and read.json()["quantity"] == 0
    assert (
        Inventory.objects.values_list("pk", "quantity", "updated_at").get(
            variant=stage7_variant
        )
        == before
    )


@pytest.mark.parametrize(
    "payload",
    [{}, {"quantity": -1}, {"quantity": "1.5"}, {"quantity": 0, "variant": 1}],
)
def test_invalid_inventory_input(manager_api, endpoint_urls, payload):
    assert (
        manager_api.patch(endpoint_urls.inventory, payload, format="json").status_code
        == 400
    )


def test_variant_parent_mismatch_is_404(manager_api, stage7_variant, stage7_product):
    other = Product.objects.create(
        name="Other",
        slug="other",
        category=stage7_product.category,
        brand=stage7_product.brand,
    )
    base = f"/api/v1/products/{other.pk}/variants/{stage7_variant.pk}/"
    assert manager_api.patch(base, {"price": "1.00"}, format="json").status_code == 404
    assert manager_api.get(base + "inventory/").status_code == 404
    assert (
        manager_api.patch(
            base + "inventory/", {"quantity": 1}, format="json"
        ).status_code
        == 404
    )
    stage7_variant.refresh_from_db()
    assert stage7_variant.price == Decimal("25.00")
    assert Inventory.objects.count() == 0
