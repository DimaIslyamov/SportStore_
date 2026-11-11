from decimal import Decimal

import pytest

from accounts.models import Address, User
from cart.services import add_item
from catalog.models import Brand, Category, Inventory, Product, ProductVariant
from orders.services import checkout


@pytest.fixture
def other_customer(db):
    return User.objects.create_user(email="stage12-other@example.com")


@pytest.fixture
def order_variants(db):
    category = Category.objects.create(name="Running", slug="stage12-running")
    brand = Brand.objects.create(name="SportLab", slug="stage12-sportlab")
    product = Product.objects.create(
        category=category, brand=brand, name="Training Shirt", slug="stage12-shirt"
    )
    variants = [
        ProductVariant.objects.create(product=product, sku=sku, price=Decimal(price))
        for sku, price in (("STAGE12-SHIRT-M", "19.90"), ("STAGE12-SHIRT-L", "23.50"))
    ]
    for variant in variants:
        Inventory.objects.create(variant=variant, quantity=10)
    return variants


@pytest.fixture
def prepare_order_cart(order_variants):
    def prepare(*, owner, quantities=(2, 3)):
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
        )
        variants = order_variants[: len(quantities)]
        for variant, quantity in zip(variants, quantities, strict=True):
            add_item(user=owner, variant_id=variant.pk, quantity=quantity)
        return address

    return prepare


@pytest.fixture
def order_factory(customer, prepare_order_cart):
    def create(*, owner=None, quantities=(2, 3)):
        owner = customer if owner is None else owner
        address = prepare_order_cart(owner=owner, quantities=quantities)
        return checkout(user=owner, address_id=address.pk)

    return create
