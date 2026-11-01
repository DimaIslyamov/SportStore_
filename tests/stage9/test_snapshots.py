from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError

from accounts.models import Address
from cart import services as cart_services
from cart.models import Cart, CartItem
from catalog.models import (
    Attribute,
    Inventory,
    Product,
    ProductVariant,
    VariantAttribute,
)
from orders.models import Order, OrderItem, OrderShippingAddress
from orders.snapshots import build_item_snapshot, build_shipping_snapshot

pytestmark = pytest.mark.django_db

ITEM_SNAPSHOT_FIELDS = (
    "product_name",
    "variant_description",
    "sku",
    "unit_price",
    "quantity",
    "total_price",
)


def item_values(item):
    return {name: getattr(item, name) for name in ITEM_SNAPSHOT_FIELDS}


def test_helpers_copy_expected_values_without_queries(
    order_sources, prepared_variant, django_assert_num_queries
):
    prepared = prepared_variant
    with django_assert_num_queries(0):
        shipping = build_shipping_snapshot(address=order_sources.address)
        row = build_item_snapshot(
            variant=prepared.variant,
            quantity=2,
            attributes=reversed(prepared.attributes),
        )
        normal = build_item_snapshot(
            variant=prepared.variant, quantity=2, attributes=prepared.attributes
        )
    assert shipping == {
        "first_name": "Dima",
        "last_name": "Customer",
        "phone": "+48111222333",
        "country": "Poland",
        "city": "Warsaw",
        "postal_code": "00-001",
        "street": "Marszalkowska",
        "building": "10",
        "apartment": "",
    }
    assert row == normal
    assert row == {
        "variant": prepared.variant,
        "product_name": "Training Shirt",
        "variant_description": "Color: Black; Size: M; Weight: 200 g",
        "sku": "ORDER-SHIRT-M",
        "unit_price": Decimal("19.90"),
        "quantity": 2,
        "total_price": Decimal("39.80"),
    }


def test_helper_accepts_no_variant_attributes(order_sources):
    row = build_item_snapshot(variant=order_sources.second, quantity=1, attributes=[])
    assert row["variant_description"] == ""
    assert row["total_price"] == Decimal("12.50")


@pytest.mark.parametrize("quantity", [0, -1, 1000, True, False, 1.5, "2", None])
def test_item_helper_rejects_invalid_quantity(prepared_variant, quantity):
    with pytest.raises(ValidationError) as error:
        build_item_snapshot(
            variant=prepared_variant.variant,
            quantity=quantity,
            attributes=prepared_variant.attributes,
        )
    assert "quantity" in error.value.message_dict
    assert not Order.objects.exists()


@pytest.mark.parametrize(
    "price",
    [
        Decimal("-0.01"),
        Decimal("1.001"),
        Decimal("100000000.00"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        19.90,
        "19.90",
        None,
    ],
)
def test_item_helper_rejects_bad_price(prepared_variant, price):
    prepared_variant.variant.price = price
    with pytest.raises(ValidationError):
        build_item_snapshot(
            variant=prepared_variant.variant,
            quantity=2,
            attributes=prepared_variant.attributes,
        )
    assert not OrderItem.objects.exists()


def test_helper_rejects_attributes_of_another_variant(order_sources, prepared_variant):
    with pytest.raises(ValidationError) as error:
        build_item_snapshot(
            variant=order_sources.second,
            quantity=1,
            attributes=prepared_variant.attributes,
        )
    assert "attributes" in error.value.message_dict


def test_helper_requires_persisted_variant(order_sources):
    unsaved = ProductVariant(
        product=order_sources.first.product, sku="UNSAVED", price=Decimal("1.00")
    )
    with pytest.raises(ValidationError) as error:
        build_item_snapshot(variant=unsaved, quantity=1, attributes=[])
    assert "variant" in error.value.message_dict


def test_helpers_do_not_create_graph_or_change_cart_inventory(
    customer, order_sources, prepared_variant, django_assert_num_queries
):
    Inventory.objects.create(variant=order_sources.first, quantity=10)
    cart_services.add_item(user=customer, variant_id=order_sources.first.pk, quantity=2)
    models = (Cart, CartItem, Inventory, Order, OrderShippingAddress, OrderItem)
    before = {model: list(model.objects.order_by("pk").values()) for model in models}
    with django_assert_num_queries(0):
        build_shipping_snapshot(address=order_sources.address)
        build_item_snapshot(
            variant=prepared_variant.variant,
            quantity=3,
            attributes=prepared_variant.attributes,
        )
    after = {model: list(model.objects.order_by("pk").values()) for model in models}
    assert after == before
    assert not Order.objects.exists()


def test_shipping_snapshot_survives_source_edit_and_delete(
    order_sources, order_factory
):
    shipping = order_factory().shipping_address
    before = build_shipping_snapshot(address=shipping)
    Address.objects.filter(pk=order_sources.address.pk).update(
        city="Krakow", apartment="99"
    )
    shipping.refresh_from_db()
    assert build_shipping_snapshot(address=shipping) == before
    order_sources.address.delete()
    shipping.refresh_from_db()
    assert build_shipping_snapshot(address=shipping) == before


def test_equal_addresses_and_skus_can_repeat_in_independent_orders(
    order_sources, order_factory
):
    first, second = order_factory(), order_factory()
    left, right = first.shipping_address, second.shipping_address
    assert left.pk != right.pk
    assert build_shipping_snapshot(address=left) == build_shipping_snapshot(
        address=right
    )
    assert list(first.items.values_list("sku", flat=True)) == list(
        second.items.values_list("sku", flat=True)
    )
    assert first.order_number != second.order_number
    left.city = "Snapshot correction"
    left.save(update_fields=["city"])
    right.refresh_from_db()
    order_sources.address.refresh_from_db()
    assert right.city == order_sources.address.city == "Warsaw"


def test_catalog_changes_do_not_rewrite_historical_item_or_order(
    order_sources, order_factory
):
    order = order_factory(discount="7.30", shipping_cost="10.00")
    item = order.items.get(variant=order_sources.first)
    before = item_values(item)
    Product.objects.filter(pk=order_sources.first.product_id).update(
        name="Changed shirt", is_active=False
    )
    ProductVariant.objects.filter(pk=order_sources.first.pk).update(
        sku="CHANGED-SKU", price=Decimal("99.99"), is_active=False
    )
    Attribute.objects.filter(pk=order_sources.color.pk).update(
        name="Changed label", unit="cm"
    )
    VariantAttribute.objects.filter(variant=order_sources.first).update(
        value="Changed value"
    )
    type(order_sources.category).objects.filter(pk=order_sources.category.pk).update(
        is_active=False
    )
    type(order_sources.brand).objects.filter(pk=order_sources.brand.pk).update(
        is_active=False
    )
    item.refresh_from_db()
    order.refresh_from_db()
    assert item_values(item) == before
    assert item.variant_id == order_sources.first.pk
    assert order.subtotal == Decimal("77.30")
    assert order.total == Decimal("80.00")
    assert order.status == Order.Status.PENDING
    assert order.currency == "PLN"


def test_variant_delete_sets_null_without_losing_snapshots(
    order_sources, order_factory
):
    order = order_factory()
    item = order.items.get(variant=order_sources.first)
    before = item_values(item)
    variant_id = order_sources.first.pk
    assert not CartItem.objects.filter(variant_id=variant_id).exists()
    order_sources.first.delete()
    item.refresh_from_db()
    order.refresh_from_db()
    assert item.variant_id is None
    assert item_values(item) == before
    assert order.subtotal == order.total == Decimal("77.30")
    assert order.items.count() == 2


def test_cart_protect_is_not_overridden_by_order_set_null(
    customer, order_sources, order_factory
):
    order = order_factory()
    cart_services.add_item(user=customer, variant_id=order_sources.first.pk, quantity=1)
    with pytest.raises(ProtectedError):
        order_sources.first.delete()
    item = order.items.get(sku="ORDER-SHIRT-M")
    assert item.variant_id == order_sources.first.pk
    assert CartItem.objects.filter(
        cart__user=customer, variant=order_sources.first
    ).exists()
