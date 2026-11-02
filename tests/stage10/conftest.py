from decimal import Decimal
from types import SimpleNamespace

import boto3
import pytest
from botocore.client import BaseClient
from django.db import connection

from accounts.models import Address, User
from cart import services as cart_services
from cart.models import Cart, CartItem
from catalog.models import (
    Attribute,
    Brand,
    Category,
    Inventory,
    Product,
    ProductVariant,
    VariantAttribute,
)
from orders.models import Order, OrderItem, OrderShippingAddress


@pytest.fixture(autouse=True)
def no_checkout_aws(monkeypatch):
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(name, "test-not-real")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")

    def forbidden(*args, **kwargs):
        raise AssertionError("Checkout tests must not call AWS.")

    monkeypatch.setattr(boto3, "client", forbidden)
    monkeypatch.setattr(boto3.session.Session, "client", forbidden)
    monkeypatch.setattr(BaseClient, "_make_api_call", forbidden)


@pytest.fixture
def checkout_catalog(db):
    assert connection.vendor == "postgresql"
    category = Category.objects.create(name="Running", slug="checkout-running")
    brand = Brand.objects.create(name="SportLab", slug="checkout-sportlab")
    products = [
        Product.objects.create(
            category=category, brand=brand, name="Training Shirt", slug="checkout-shirt"
        ),
        Product.objects.create(
            category=category, brand=brand, name="Running Socks", slug="checkout-socks"
        ),
    ]
    variants = [
        ProductVariant.objects.create(
            product=products[0], sku="CHECKOUT-SHIRT-M", price=Decimal("19.90")
        ),
        ProductVariant.objects.create(
            product=products[1], sku="CHECKOUT-SOCKS-M", price=Decimal("12.50")
        ),
    ]
    for variant in variants:
        Inventory.objects.create(variant=variant, quantity=10)
    color = Attribute.objects.create(name="Color", slug="checkout-color")
    size = Attribute.objects.create(name="Size", slug="checkout-size")
    for attribute, value in ((size, "M"), (color, "Black")):
        VariantAttribute.objects.create(
            variant=variants[0], attribute=attribute, value=value
        )
    return SimpleNamespace(
        variants=variants, category=category, brand=brand, color=color, size=size
    )


@pytest.fixture
def other_checkout_customer(db):
    return User.objects.create_user(email="checkout-other@example.com")


@pytest.fixture
def checkout_factory(customer, checkout_catalog):
    def create(*, owner=None, quantities=(2, 3), variants=None):
        owner = customer if owner is None else owner
        selected = (
            checkout_catalog.variants[: len(quantities)]
            if variants is None
            else variants
        )
        address = Address.objects.create(
            user=owner,
            first_name="Dima",
            last_name="Customer",
            phone="+48111222333",
            country="Poland",
            city="Warsaw",
            postal_code="00-001",
            street="Test Street",
            building="10",
            apartment="",
        )
        items = [
            cart_services.add_item(
                user=owner, variant_id=variant.pk, quantity=quantity
            )[0]
            for variant, quantity in zip(selected, quantities, strict=True)
        ]
        return SimpleNamespace(
            user=owner,
            address=address,
            cart=Cart.objects.get(user=owner),
            variants=selected,
            items=items,
        )

    return create


@pytest.fixture
def checkout_state(db):
    models = (Cart, CartItem, Inventory, Order, OrderShippingAddress, OrderItem)

    def capture():
        return {
            model._meta.label: list(model.objects.order_by("pk").values())
            for model in models
        }

    return capture
