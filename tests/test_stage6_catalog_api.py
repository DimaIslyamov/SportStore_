from decimal import Decimal
from itertools import count
from types import SimpleNamespace

import pytest
from django.urls import reverse

from catalog.models import (
    Attribute,
    Brand,
    Category,
    Inventory,
    Product,
    ProductAttributeValue,
    ProductImage,
    ProductVariant,
    VariantAttribute,
)

pytestmark = pytest.mark.django_db


def list_url():
    return reverse("catalog:product-list")


def detail_url(product):
    return reverse("catalog:product-detail", kwargs={"slug": product.slug})


def listing(client, **params):
    response = client.get(list_url(), params)
    assert response.status_code == 200, response.content
    return response.json()


@pytest.fixture
def catalog_refs(db):
    return SimpleNamespace(
        category=Category.objects.create(name="Running", slug="running"),
        brand=Brand.objects.create(name="SportLab", slug="sportlab"),
    )


@pytest.fixture
def product_factory(catalog_refs):
    sequence = count(1)

    def create(**overrides):
        number = next(sequence)
        data = {
            "name": f"Product {number:03d}",
            "slug": f"product-{number}",
            "category": catalog_refs.category,
            "brand": catalog_refs.brand,
        }
        data.update(overrides)
        return Product.objects.create(**data)

    return create


@pytest.fixture
def variant_factory(db):
    sequence = count(1)

    def create(product, price="100.00", quantity=None, **overrides):
        data = {
            "product": product,
            "sku": f"TEST-{next(sequence)}",
            "price": Decimal(price),
        }
        data.update(overrides)
        variant = ProductVariant.objects.create(**data)
        if quantity is not None:
            Inventory.objects.create(variant=variant, quantity=quantity)
        return variant

    return create


@pytest.fixture
def mixed_catalog(product_factory, variant_factory):
    shirt = product_factory(name="Training T-Shirt", description="Light running shirt")
    cheap = variant_factory(shirt, "79.90")
    empty = variant_factory(shirt, "89.90", quantity=0)
    stocked = variant_factory(shirt, "94.90", quantity=7)
    hidden = variant_factory(shirt, "5.00", quantity=50, is_active=False)
    treadmill = product_factory(name="Running Treadmill")
    variant_factory(treadmill, "3499.00", quantity=0)
    return SimpleNamespace(
        shirt=shirt,
        cheap=cheap,
        empty=empty,
        stocked=stocked,
        hidden=hidden,
        treadmill=treadmill,
    )


def test_anonymous_list_contract_and_default_stock_filter(api_client, mixed_catalog):
    body = listing(api_client)
    assert set(body) == {"count", "next", "previous", "results"}
    assert body["count"] == 2
    assert body["next"] is None and body["previous"] is None
    assert [row["id"] for row in body["results"]] == [
        mixed_catalog.treadmill.pk,
        mixed_catalog.shirt.pk,
    ]
    row = body["results"][1]
    assert set(row) == {
        "id",
        "name",
        "slug",
        "brand",
        "category",
        "from_price",
        "currency",
        "in_stock",
        "primary_image",
    }
    assert row["brand"]["slug"] == "sportlab"
    assert row["category"]["slug"] == "running"
    assert row["from_price"] == "79.90"
    assert row["currency"] == "PLN"
    assert row["in_stock"] is True
    assert row["primary_image"] is None
    assert body["results"][0]["in_stock"] is False


def test_empty_catalog_returns_envelope(api_client):
    assert listing(api_client) == {
        "count": 0,
        "next": None,
        "previous": None,
        "results": [],
    }


def test_anonymous_detail_money_stock_and_no_inventory_write(api_client, mixed_catalog):
    before = Inventory.objects.count()
    response = api_client.get(detail_url(mixed_catalog.shirt))
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == mixed_catalog.shirt.pk
    assert body["from_price"] == "79.90"
    assert body["currency"] == "PLN"
    assert body["in_stock"] is True
    assert body["description"] == "Light running shirt"
    assert body["created_at"] and body["updated_at"]
    assert body["images"] == [] and body["attributes"] == []
    assert body["primary_image"] is None
    variants = {row["id"]: row for row in body["variants"]}
    assert set(variants) == {
        mixed_catalog.cheap.pk,
        mixed_catalog.empty.pk,
        mixed_catalog.stocked.pk,
    }
    assert variants[mixed_catalog.cheap.pk]["price"] == "79.90"
    assert variants[mixed_catalog.cheap.pk]["in_stock"] is False
    assert variants[mixed_catalog.empty.pk]["in_stock"] is False
    assert variants[mixed_catalog.stocked.pk]["in_stock"] is True
    assert all(row["attributes"] == [] for row in variants.values())
    assert all(
        "quantity" not in row and "barcode" not in row for row in variants.values()
    )
    assert Inventory.objects.count() == before
    assert not Inventory.objects.filter(variant=mixed_catalog.cheap).exists()


def test_missing_detail_is_404(api_client):
    response = api_client.get(
        reverse("catalog:product-detail", kwargs={"slug": "unknown"})
    )
    assert response.status_code == 404
    assert "detail" in response.json()


@pytest.mark.parametrize("target", ["product", "brand", "category"])
def test_inactive_entities_hide_list_and_detail(
    api_client, product_factory, variant_factory, target
):
    product = product_factory()
    variant_factory(product, quantity=1)
    instance = product if target == "product" else getattr(product, target)
    instance.is_active = False
    instance.save(update_fields=["is_active"])
    assert listing(api_client)["results"] == []
    assert api_client.get(detail_url(product)).status_code == 404


@pytest.mark.parametrize("has_inactive_variant", [False, True])
def test_no_active_variant_hides_product(
    api_client, product_factory, variant_factory, has_inactive_variant
):
    product = product_factory()
    if has_inactive_variant:
        variant_factory(product, quantity=100, is_active=False)
    assert listing(api_client)["count"] == 0
    assert api_client.get(detail_url(product)).status_code == 404


@pytest.mark.parametrize("quantity", [None, 0])
def test_zero_or_missing_inventory_does_not_hide_active_variant(
    api_client, product_factory, variant_factory, quantity
):
    product = product_factory()
    variant = variant_factory(product, quantity=quantity)
    before = Inventory.objects.count()
    assert listing(api_client)["results"][0]["in_stock"] is False
    body = api_client.get(detail_url(product)).json()
    assert body["in_stock"] is False
    assert body["variants"][0]["id"] == variant.pk
    assert body["variants"][0]["in_stock"] is False
    assert Inventory.objects.count() == before


def test_inactive_stock_does_not_make_product_available(
    api_client, product_factory, variant_factory
):
    product = product_factory()
    variant_factory(product, "30.00", quantity=0)
    variant_factory(product, "1.00", quantity=99, is_active=False)
    row = listing(api_client)["results"][0]
    assert row["from_price"] == "30.00" and row["in_stock"] is False


def test_zero_price_remains_visible(api_client, product_factory, variant_factory):
    product = product_factory()
    variant_factory(product, "0.00")
    variant_factory(product, "10.00", quantity=2)
    row = listing(api_client, min_price="0", max_price="0")["results"][0]
    assert row["id"] == product.pk
    assert row["from_price"] == "0.00" and row["in_stock"] is True


def test_inactive_parent_does_not_hide_own_active_category(
    api_client, catalog_refs, product_factory, variant_factory
):
    parent = Category.objects.create(name="Parent", slug="parent", is_active=False)
    catalog_refs.category.parent = parent
    catalog_refs.category.save(update_fields=["parent"])
    product = product_factory()
    variant_factory(product)
    assert listing(api_client)["results"][0]["id"] == product.pk
    assert api_client.get(detail_url(product)).status_code == 200
    assert listing(api_client, category="parent")["results"] == []


@pytest.mark.parametrize("actor", ["anonymous", "customer", "manager"])
@pytest.mark.parametrize(
    ("method", "detail"),
    [("post", False), ("put", True), ("patch", True), ("delete", True)],
)
def test_product_api_is_read_only(
    api_client, request, product_factory, variant_factory, actor, method, detail
):
    product = product_factory()
    variant_factory(product)
    if actor != "anonymous":
        api_client.force_authenticate(user=request.getfixturevalue(actor))
    url = detail_url(product) if detail else list_url()
    response = getattr(api_client, method)(url, {"name": "Changed"}, format="json")
    assert response.status_code == 405
    product.refresh_from_db()
    assert product.name != "Changed"
    assert Product.objects.count() == 1


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"category": "running"}, "both"),
        ({"brand": "sportlab"}, "both"),
        ({"category": "missing"}, "none"),
        ({"brand": "missing"}, "none"),
        ({"min_price": "79.90", "max_price": "79.90"}, "shirt"),
        ({"min_price": "90", "max_price": "100"}, "none"),
        ({"min_price": "3499.00"}, "treadmill"),
        ({"max_price": "79.90"}, "shirt"),
        ({"in_stock": "true"}, "shirt"),
        ({"in_stock": "false"}, "treadmill"),
        ({"search": "RUNNING"}, "both"),
        ({"search": "shirt"}, "shirt"),
        ({"search": "not-found"}, "none"),
        ({"search": "   "}, "both"),
        (
            {
                "category": "running",
                "brand": "sportlab",
                "min_price": "70",
                "max_price": "100",
                "in_stock": "true",
                "search": "shirt",
                "ordering": "price",
            },
            "shirt",
        ),
    ],
)
def test_filters_search_and_combinations(api_client, mixed_catalog, params, expected):
    expected_ids = {
        "both": {mixed_catalog.shirt.pk, mixed_catalog.treadmill.pk},
        "shirt": {mixed_catalog.shirt.pk},
        "treadmill": {mixed_catalog.treadmill.pk},
        "none": set(),
    }[expected]
    body = listing(api_client, **params)
    assert {row["id"] for row in body["results"]} == expected_ids
    assert body["count"] == len(expected_ids)
    for row in body["results"]:
        if row["id"] == mixed_catalog.shirt.pk:
            assert row["from_price"] == "79.90"


def test_category_and_brand_do_not_match_other_products(
    api_client, catalog_refs, product_factory, variant_factory
):
    other_category = Category.objects.create(name="Cycling", slug="cycling")
    other_brand = Brand.objects.create(name="Other", slug="other")
    wanted = product_factory()
    same_brand = product_factory(category=other_category)
    same_category = product_factory(brand=other_brand)
    for product in [wanted, same_brand, same_category]:
        variant_factory(product)
    assert {
        row["id"] for row in listing(api_client, category="running")["results"]
    } == {wanted.pk, same_category.pk}
    assert {row["id"] for row in listing(api_client, brand="sportlab")["results"]} == {
        wanted.pk,
        same_brand.pk,
    }
    body = listing(api_client, category="running", brand="sportlab")
    assert [row["id"] for row in body["results"]] == [wanted.pk]


@pytest.mark.parametrize(
    ("params", "error_key"),
    [
        ({"min_price": "oops"}, "min_price"),
        ({"min_price": "NaN"}, "min_price"),
        ({"min_price": "Infinity"}, "min_price"),
        ({"min_price": "-0.01"}, "min_price"),
        ({"max_price": "-1"}, "max_price"),
        ({"max_price": "1.001"}, "max_price"),
        ({"min_price": "100000000.00"}, "min_price"),
        ({"min_price": ""}, "min_price"),
        ({"min_price": "20", "max_price": "10"}, "max_price"),
        ({"in_stock": "maybe"}, "in_stock"),
        ({"in_stock": ""}, "in_stock"),
        ({"ordering": "sku"}, "ordering"),
        ({"ordering": "-name"}, "ordering"),
        ({"ordering": "price,name"}, "ordering"),
        ({"category": "RUNNING"}, "category"),
        ({"brand": "bad slug"}, "brand"),
        ({"search": "x" * 201}, "search"),
    ],
)
def test_invalid_parameters_return_400(api_client, params, error_key):
    response = api_client.get(list_url(), params)
    assert response.status_code == 400
    assert error_key in response.json()


@pytest.mark.parametrize(
    ("ordering", "indexes"),
    [
        (None, [0, 1, 2]),
        ("name", [0, 1, 2]),
        ("price", [1, 2, 0]),
        ("-price", [0, 1, 2]),
    ],
)
def test_sorting_is_stable(
    api_client, product_factory, variant_factory, ordering, indexes
):
    products = [product_factory(name="Same") for _ in range(3)]
    for product, price in zip(products, ["30.00", "10.00", "10.00"], strict=True):
        variant_factory(product, price)
    params = {} if ordering is None else {"ordering": ordering}
    expected = [products[index].pk for index in indexes]
    assert [row["id"] for row in listing(api_client, **params)["results"]] == expected
    assert [row["id"] for row in listing(api_client, **params)["results"]] == expected


def test_name_ordering_uses_name_before_id(
    api_client, product_factory, variant_factory
):
    zulu = product_factory(name="Zulu")
    alpha = product_factory(name="Alpha")
    for product in [zulu, alpha]:
        variant_factory(product)
    assert [row["id"] for row in listing(api_client, ordering="name")["results"]] == [
        alpha.pk,
        zulu.pk,
    ]


def test_pagination_counts_products_not_related_rows(
    api_client, product_factory, variant_factory
):
    attribute = Attribute.objects.create(name="Material", slug="material")
    products = [product_factory(name="Same") for _ in range(14)]
    for product in products:
        for _ in range(3):
            variant_factory(product, "10.00", quantity=1)
        for position in range(2):
            ProductImage.objects.create(
                product=product,
                position=position,
                image_url=f"https://images.example.com/{product.pk}-{position}.jpg",
            )
        ProductAttributeValue.objects.create(
            product=product, attribute=attribute, value="X"
        )
    first = listing(api_client, ordering="price", max_price="10")
    second = listing(api_client, ordering="price", max_price="10", page="2")
    assert first["count"] == second["count"] == 14
    assert len(first["results"]) == 12 and len(second["results"]) == 2
    assert "page=2" in first["next"] and "max_price=10" in first["next"]
    assert first["previous"] is None
    assert second["next"] is None and second["previous"] is not None
    ids = [row["id"] for row in first["results"] + second["results"]]
    assert ids == [product.pk for product in products]
    assert len(ids) == len(set(ids))
    assert all(row["primary_image"] is not None for row in first["results"])


@pytest.mark.parametrize("page", ["0", "-1", "1.5", "abc", "last", "999"])
def test_invalid_or_out_of_range_page_is_404(api_client, mixed_catalog, page):
    response = api_client.get(list_url(), {"page": page})
    assert response.status_code == 404
    assert "detail" in response.json()


def test_blank_page_is_first_page(api_client, mixed_catalog):
    assert listing(api_client, page="")["results"] == listing(api_client)["results"]


def test_detail_nested_data_units_and_image_order(
    api_client, product_factory, variant_factory
):
    product = product_factory()
    first = variant_factory(product, "40.00", quantity=2)
    second = variant_factory(product, "50.00", quantity=0)
    hidden = variant_factory(product, "1.00", quantity=9, is_active=False)
    weight = Attribute.objects.create(name="Weight", slug="weight", unit="kg")
    length = Attribute.objects.create(name="Length", slug="length", unit="cm")
    ProductAttributeValue.objects.create(
        product=product, attribute=weight, value="12.5"
    )
    for variant, value in [(first, "100"), (second, "120"), (hidden, "999")]:
        VariantAttribute.objects.create(variant=variant, attribute=length, value=value)
    primary = ProductImage.objects.create(
        product=product,
        image_url="https://images.example.com/primary.jpg",
        alt_text="Front",
        position=8,
        is_primary=True,
    )
    early = ProductImage.objects.create(
        product=product,
        image_url="https://images.example.com/early.jpg",
        position=0,
    )
    tie = ProductImage.objects.create(
        product=product,
        image_url="https://images.example.com/tie.jpg",
        position=0,
    )
    response = api_client.get(detail_url(product))
    assert response.status_code == 200
    body = response.json()
    assert [row["id"] for row in body["images"]] == [early.pk, tie.pk, primary.pk]
    assert body["primary_image"] == {
        "id": primary.pk,
        "image_url": primary.image_url,
        "alt_text": "Front",
        "position": 8,
        "is_primary": True,
    }
    assert body["attributes"] == [
        {
            "attribute": {
                "id": weight.pk,
                "name": "Weight",
                "slug": "weight",
                "unit": "kg",
            },
            "value": "12.5",
        }
    ]
    assert [row["id"] for row in body["variants"]] == [first.pk, second.pk]
    for row, value in zip(body["variants"], ["100", "120"], strict=True):
        assert row["attributes"] == [
            {
                "attribute": {
                    "id": length.pk,
                    "name": "Length",
                    "slug": "length",
                    "unit": "cm",
                },
                "value": value,
            }
        ]
    assert listing(api_client)["results"][0]["primary_image"] == body["primary_image"]


def test_image_fallback_uses_position_then_id(
    api_client, product_factory, variant_factory
):
    product = product_factory()
    variant_factory(product)
    ProductImage.objects.create(
        product=product,
        image_url="https://images.example.com/later.jpg",
        position=5,
    )
    chosen = ProductImage.objects.create(
        product=product,
        image_url="https://images.example.com/first.jpg",
        position=0,
    )
    ProductImage.objects.create(
        product=product,
        image_url="https://images.example.com/tie.jpg",
        position=0,
    )
    assert listing(api_client)["results"][0]["primary_image"]["id"] == chosen.pk
    assert (
        api_client.get(detail_url(product)).json()["primary_image"]["id"] == chosen.pk
    )