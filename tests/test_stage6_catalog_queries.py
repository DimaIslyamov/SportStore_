from decimal import Decimal

import pytest
from django.db import connection
from django.urls import reverse
from rest_framework.test import APIClient

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


@pytest.fixture
def query_catalog(db):
    assert connection.vendor == "postgresql", "Run these tests on PostgreSQL."
    category = Category.objects.create(name="Running", slug="running")
    brand = Brand.objects.create(name="SportLab", slug="sportlab")
    attributes = [
        Attribute.objects.create(name=f"Metric {i}", slug=f"metric-{i}", unit="cm")
        for i in range(3)
    ]
    return category, brand, attributes


def add_children(product, attributes, start, stop):
    for number in range(start, stop):
        variant = ProductVariant.objects.create(
            product=product,
            sku=f"QUERY-{product.pk}-{number}",
            price=Decimal("10.00") + number,
        )
        if number % 2 == 0:
            Inventory.objects.create(variant=variant, quantity=number + 1)
        for attribute in attributes:
            VariantAttribute.objects.create(
                variant=variant, attribute=attribute, value=f"V{number}"
            )
        ProductImage.objects.create(
            product=product,
            image_url=f"https://images.example.com/query-{product.pk}-{number}.jpg",
            position=number,
            is_primary=number == 0,
        )


def add_product(query_catalog, number):
    category, brand, attributes = query_catalog
    product = Product.objects.create(
        name=f"Query product {number:02d}",
        slug=f"query-{number}",
        category=category,
        brand=brand,
    )
    for attribute in attributes:
        ProductAttributeValue.objects.create(
            product=product, attribute=attribute, value="Product value"
        )
    add_children(product, attributes, 0, 2)
    return product


def test_list_query_count_does_not_grow(query_catalog, django_assert_num_queries):
    client = APIClient()
    first_product = add_product(query_catalog, 1)
    url = reverse("catalog:product-list")

    with django_assert_num_queries(3):
        first_response = client.get(url)
        first_body = first_response.json()

    assert first_response.status_code == 200
    assert first_body["count"] == 1
    assert first_body["results"][0]["id"] == first_product.pk
    assert first_body["results"][0]["primary_image"]["image_url"].endswith("-0.jpg")
    for number in range(2, 9):
        add_product(query_catalog, number)

    with django_assert_num_queries(3):
        larger_response = client.get(url)
        larger_body = larger_response.json()

    assert larger_response.status_code == 200
    assert larger_body["count"] == 8
    assert len(larger_body["results"]) == 8
    assert len({row["id"] for row in larger_body["results"]}) == 8
    assert larger_body["next"] is None
    for row in larger_body["results"]:
        assert row["brand"]["slug"] == "sportlab"
        assert row["category"]["slug"] == "running"
        assert row["from_price"] == "10.00"
        assert row["in_stock"] is True
        assert row["primary_image"]["image_url"].endswith("-0.jpg")


def test_detail_query_count_does_not_grow(query_catalog, django_assert_num_queries):
    category, brand, attributes = query_catalog
    product = Product.objects.create(
        name="Detail", slug="detail", category=category, brand=brand
    )
    ProductAttributeValue.objects.create(
        product=product, attribute=attributes[0], value="Product value"
    )
    add_children(product, attributes[:1], 0, 1)
    client = APIClient()
    url = reverse("catalog:product-detail", kwargs={"slug": product.slug})

    with django_assert_num_queries(5):
        first_response = client.get(url)
        first_body = first_response.json()

    assert first_response.status_code == 200
    assert first_body["id"] == product.pk
    assert len(first_body["images"]) == 1
    assert len(first_body["attributes"]) == 1
    assert len(first_body["variants"]) == 1
    assert len(first_body["variants"][0]["attributes"]) == 1

    for attribute in attributes[1:]:
        ProductAttributeValue.objects.create(
            product=product, attribute=attribute, value="Product value"
        )
        VariantAttribute.objects.create(
            variant_id=first_body["variants"][0]["id"], attribute=attribute, value="V0"
        )
    add_children(product, attributes, 1, 4)

    with django_assert_num_queries(5):
        larger_response = client.get(url)
        larger_body = larger_response.json()

    assert larger_response.status_code == 200
    assert larger_body["id"] == product.pk
    assert larger_body["from_price"] == "10.00" and larger_body["in_stock"] is True
    assert len(larger_body["images"]) == 4
    assert len(larger_body["attributes"]) == 3
    assert len(larger_body["variants"]) == 4
    assert [row["position"] for row in larger_body["images"]] == [0, 1, 2, 3]
    assert all(
        row["image_url"].startswith("https://images.example.com/")
        for row in larger_body["images"]
    )
    for row in larger_body["attributes"]:
        assert row["attribute"]["unit"] == "cm"
        assert row["value"] == "Product value"
    for number, variant in enumerate(larger_body["variants"]):
        assert variant["price"] == f"{10 + number}.00"
        assert variant["in_stock"] is (number % 2 == 0)
        assert len(variant["attributes"]) == 3
        assert {row["attribute"]["id"] for row in variant["attributes"]} == {
            attribute.pk for attribute in attributes
        }
        assert all(row["attribute"]["unit"] == "cm" for row in variant["attributes"])
        assert all(row["value"] == f"V{number}" for row in variant["attributes"])