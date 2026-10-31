from decimal import Decimal
from itertools import count

import boto3
import pytest
from botocore.client import BaseClient
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from catalog.models import Brand, Category, Inventory, Product, ProductVariant


@pytest.fixture(autouse=True)
def no_cart_aws(monkeypatch):
    for key in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"]:
        monkeypatch.setenv(key, "test-not-real")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")

    def forbidden(*args, **kwargs):
        raise AssertionError("Cart tests must not call AWS.")

    monkeypatch.setattr(boto3, "client", forbidden)
    monkeypatch.setattr(boto3.session.Session, "client", forbidden)
    monkeypatch.setattr(BaseClient, "_make_api_call", forbidden)


@pytest.fixture
def other_customer(db):
    return get_user_model().objects.create_user(email="cart-other@example.com")


@pytest.fixture
def customer_client(customer):
    client = APIClient()
    client.force_authenticate(user=customer)
    return client


@pytest.fixture
def cart_variant_factory(db):
    category = Category.objects.create(name="Running", slug="cart-running")
    brand = Brand.objects.create(name="SportLab", slug="cart-sportlab")
    sequence = count(1)

    def create(price="19.90", stock=None, **overrides):
        number = next(sequence)
        product = overrides.pop("product", None)
        if product is None:
            product = Product.objects.create(
                name=f"Cart Product {number}",
                slug=f"cart-product-{number}",
                category=category,
                brand=brand,
            )
        variant = ProductVariant.objects.create(
            product=product, sku=f"CART-{number}", price=Decimal(price), **overrides
        )
        if stock is not None:
            Inventory.objects.create(variant=variant, quantity=stock)
        return variant

    return create
