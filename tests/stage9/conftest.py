from decimal import Decimal
from types import SimpleNamespace

import boto3
import pytest
from botocore.client import BaseClient
from django.db import connection, transaction

from accounts.models import Address
from catalog.models import (
    Attribute,
    Brand,
    Category,
    Product,
    ProductVariant,
    VariantAttribute,
)
from orders.models import Order, OrderItem, OrderShippingAddress
from orders.snapshots import build_item_snapshot, build_shipping_snapshot


@pytest.fixture(autouse=True)
def no_order_aws(monkeypatch):
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(key, "test-not-real")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")

    def forbidden(*args, **kwargs):
        raise AssertionError("Order domain tests must not call AWS.")

    monkeypatch.setattr(boto3, "client", forbidden)
    monkeypatch.setattr(boto3.session.Session, "client", forbidden)
    monkeypatch.setattr(BaseClient, "_make_api_call", forbidden)


@pytest.fixture
def order_sources(customer):
    assert connection.vendor == "postgresql"
    address = Address.objects.create(
        user=customer,
        first_name="Dima",
        last_name="Customer",
        phone="+48111222333",
        country="Poland",
        city="Warsaw",
        postal_code="00-001",
        street="Marszalkowska",
        building="10",
        apartment="",
        is_default=True,
    )
    category = Category.objects.create(name="Running", slug="order-running")
    brand = Brand.objects.create(name="SportLab", slug="order-sportlab")
    shirt = Product.objects.create(
        category=category, brand=brand, name="Training Shirt", slug="order-shirt"
    )
    socks = Product.objects.create(
        category=category, brand=brand, name="Running Socks", slug="order-socks"
    )
    first = ProductVariant.objects.create(
        product=shirt, sku="ORDER-SHIRT-M", price=Decimal("19.90")
    )
    second = ProductVariant.objects.create(
        product=socks, sku="ORDER-SOCKS-M", price=Decimal("12.50")
    )
    color = Attribute.objects.create(name="Color", slug="order-color")
    size = Attribute.objects.create(name="Size", slug="order-size")
    weight = Attribute.objects.create(name="Weight", slug="order-weight", unit="g")
    for attribute, value in ((weight, "200"), (size, "M"), (color, "Black")):
        VariantAttribute.objects.create(variant=first, attribute=attribute, value=value)
    return SimpleNamespace(
        address=address,
        first=first,
        second=second,
        color=color,
        size=size,
        weight=weight,
        category=category,
        brand=brand,
    )


@pytest.fixture
def prepared_variant(order_sources):
    variant = ProductVariant.objects.select_related("product").get(
        pk=order_sources.first.pk
    )
    attributes = list(
        VariantAttribute.objects.filter(variant=variant).select_related("attribute")
    )
    return SimpleNamespace(variant=variant, attributes=attributes)


@pytest.fixture
def order_factory(customer, order_sources):
    def create(*, discount="0.00", shipping_cost="0.00", quantities=(2, 3)):
        rows = []
        for source, quantity in zip(
            (order_sources.first, order_sources.second), quantities, strict=True
        ):
            variant = ProductVariant.objects.select_related("product").get(pk=source.pk)
            attributes = list(
                VariantAttribute.objects.filter(variant=variant).select_related(
                    "attribute"
                )
            )
            rows.append(
                build_item_snapshot(
                    variant=variant, quantity=quantity, attributes=attributes
                )
            )
        subtotal = sum((row["total_price"] for row in rows), Decimal("0.00"))
        discount, shipping_cost = Decimal(discount), Decimal(shipping_cost)
        address = Address.objects.get(pk=order_sources.address.pk)
        with transaction.atomic():
            order = Order(
                user=customer,
                subtotal=subtotal,
                discount=discount,
                shipping_cost=shipping_cost,
                total=subtotal - discount + shipping_cost,
            )
            order.full_clean()
            order.save()
            shipping = OrderShippingAddress(
                order=order, **build_shipping_snapshot(address=address)
            )
            shipping.full_clean()
            shipping.save()
            for row in rows:
                item = OrderItem(order=order, **row)
                item.full_clean()
                item.save()
        return order

    return create
