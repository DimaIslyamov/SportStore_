from decimal import Decimal
from types import SimpleNamespace

import pytest
from rest_framework.exceptions import PermissionDenied

from accounts.models import Address, User
from cart import services as cart_services
from cart.models import Cart, CartItem
from catalog import services as catalog_services
from catalog.models import (
    Attribute,
    Inventory,
    Product,
    ProductVariant,
    VariantAttribute,
)
from orders import services
from orders.errors import CheckoutError
from orders.models import Order, OrderItem, OrderShippingAddress
from orders.serializers import CheckoutOrderOutputSerializer
from orders.snapshots import build_shipping_snapshot

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("quantities,expected", [((2,), "39.80"), ((2, 3), "77.30")])
def test_successful_checkout_creates_graph_decrements_and_keeps_cart(
    checkout_factory, quantities, expected, django_assert_num_queries
):
    case = checkout_factory(quantities=quantities)
    cart_id = case.cart.pk
    order = services.checkout(user=case.user, address_id=case.address.pk)
    assert order.user_id == case.user.pk and order.status == Order.Status.PENDING
    assert order.currency == "PLN" and order.subtotal == order.total == Decimal(
        expected
    )
    assert order.discount == order.shipping_cost == Decimal("0.00")
    assert len(order.loaded_items) == len(quantities)
    assert Order.objects.count() == OrderShippingAddress.objects.count() == 1
    assert OrderItem.objects.count() == len(quantities)
    assert build_shipping_snapshot(
        address=order.shipping_address
    ) == build_shipping_snapshot(address=case.address)
    for variant, quantity in zip(case.variants, quantities, strict=True):
        assert Inventory.objects.get(variant=variant).quantity == 10 - quantity
    assert Cart.objects.get(user=case.user).pk == cart_id
    assert not CartItem.objects.filter(cart_id=cart_id).exists()
    with django_assert_num_queries(0):
        body = CheckoutOrderOutputSerializer(order).data
    assert body["subtotal"] == body["total"] == expected
    assert body["items"][0]["variant_description"] == "Color: Black; Size: M"


@pytest.mark.parametrize("existing", [False, True])
def test_empty_or_missing_cart_is_not_created(
    customer, checkout_factory, checkout_state, existing
):
    case = checkout_factory(quantities=(1,))
    if existing:
        cart_services.clear_cart(user=customer)
    else:
        case.cart.delete()
    before = checkout_state()
    with pytest.raises(CheckoutError) as error:
        services.checkout(user=customer, address_id=case.address.pk)
    assert error.value.detail["code"] == "EMPTY_CART"
    assert error.value.status_code == 400 and checkout_state() == before
    assert Cart.objects.filter(user=customer).exists() is existing


@pytest.mark.parametrize("missing", [False, True])
def test_insufficient_or_missing_inventory_preserves_all_state(
    checkout_factory, checkout_state, missing
):
    case = checkout_factory()
    stock = Inventory.objects.get(variant=case.variants[-1])
    stock.delete() if missing else Inventory.objects.filter(pk=stock.pk).update(
        quantity=2
    )
    before = checkout_state()
    with pytest.raises(CheckoutError) as error:
        services.checkout(user=case.user, address_id=case.address.pk)
    assert error.value.detail["code"] == "OUT_OF_STOCK"
    assert error.value.status_code == 409 and checkout_state() == before


@pytest.mark.parametrize("target", ["variant", "product", "category", "brand"])
def test_unavailable_catalog_is_rejected(
    checkout_factory, checkout_catalog, checkout_state, target
):
    case = checkout_factory(quantities=(1,))
    variant = case.variants[0]
    obj = {
        "variant": variant,
        "product": variant.product,
        "category": checkout_catalog.category,
        "brand": checkout_catalog.brand,
    }[target]
    type(obj).objects.filter(pk=obj.pk).update(is_active=False)
    before = checkout_state()
    with pytest.raises(CheckoutError) as error:
        services.checkout(user=case.user, address_id=case.address.pk)
    assert error.value.detail["code"] == "PRODUCT_UNAVAILABLE"
    assert checkout_state() == before


@pytest.mark.parametrize("quantity", [0, -1, 1000, True, 1.5])
def test_defensive_quantity_check_without_disabling_db_constraints(
    checkout_factory, checkout_state, monkeypatch, quantity
):
    case = checkout_factory(quantities=(1,))
    bad = SimpleNamespace(variant_id=case.variants[0].pk, quantity=quantity)
    monkeypatch.setattr(services, "cart_items_for_checkout", lambda **kwargs: [bad])
    before = checkout_state()
    with pytest.raises(CheckoutError) as error:
        services.checkout(user=case.user, address_id=case.address.pk)
    assert error.value.detail["code"] == "INVALID_QUANTITY"
    assert checkout_state() == before


@pytest.mark.parametrize("foreign", [False, True])
def test_missing_and_foreign_address_are_indistinguishable(
    checkout_factory, other_checkout_customer, checkout_state, foreign
):
    case = checkout_factory()
    address_id = 9223372036854775807
    if foreign:
        address_id = Address.objects.create(
            user=other_checkout_customer,
            **build_shipping_snapshot(address=case.address),
        ).pk
    before = checkout_state()
    with pytest.raises(CheckoutError) as error:
        services.checkout(user=case.user, address_id=address_id)
    assert error.value.detail["code"] == "ADDRESS_NOT_FOUND"
    assert error.value.status_code == 404 and checkout_state() == before


def test_customer_role_is_rechecked_after_locked_read(checkout_factory, checkout_state):
    case = checkout_factory()
    User.objects.filter(pk=case.user.pk).update(role=User.Role.MANAGER)
    before = checkout_state()
    with pytest.raises(PermissionDenied):
        services.checkout(user=case.user, address_id=case.address.pk)
    assert checkout_state() == before


def test_uses_current_price_and_preserves_history_after_source_changes(
    checkout_factory, checkout_catalog
):
    case = checkout_factory(quantities=(2,))
    variant = case.variants[0]
    catalog_services.update_variant(
        variant.product_id, variant.pk, price=Decimal("20.25")
    )
    order = services.checkout(user=case.user, address_id=case.address.pk)
    item = order.loaded_items[0]
    expected = (
        item.product_name,
        item.variant_description,
        item.sku,
        item.unit_price,
        item.quantity,
        item.total_price,
    )
    assert item.unit_price == Decimal("20.25") and order.total == Decimal("40.50")
    Address.objects.filter(pk=case.address.pk).update(city="Changed source")
    Product.objects.filter(pk=variant.product_id).update(
        name="Changed product", is_active=False
    )
    ProductVariant.objects.filter(pk=variant.pk).update(
        price=Decimal("99.99"), sku="CHANGED-CHECKOUT-SKU", is_active=False
    )
    Attribute.objects.filter(pk=checkout_catalog.color.pk).update(name="Changed color")
    VariantAttribute.objects.filter(variant=variant).update(value="Changed value")
    item.refresh_from_db()
    order.refresh_from_db()
    shipping = OrderShippingAddress.objects.get(order=order)
    assert shipping.city == "Warsaw"
    assert (
        item.product_name,
        item.variant_description,
        item.sku,
        item.unit_price,
        item.quantity,
        item.total_price,
    ) == expected
    assert order.total == Decimal("40.50")


@pytest.mark.parametrize("large", [False, True])
def test_zero_and_large_amounts(checkout_factory, checkout_catalog, large):
    price, quantity, stock = ("99999999.99", 999, 999) if large else ("0.00", 2, 10)
    ProductVariant.objects.filter(
        pk__in=[v.pk for v in checkout_catalog.variants]
    ).update(price=Decimal(price))
    Inventory.objects.update(quantity=stock)
    case = checkout_factory(quantities=(quantity, quantity))
    order = services.checkout(user=case.user, address_id=case.address.pk)
    assert order.total == Decimal("199799999980.02" if large else "0.00")
    assert all(item.quantity == quantity for item in order.loaded_items)


def test_amount_overflow_is_rejected_before_writes(
    checkout_factory, checkout_state, monkeypatch
):
    case = checkout_factory()
    monkeypatch.setattr(services, "MAX_ORDER_MONEY", Decimal("77.29"))
    before = checkout_state()
    with pytest.raises(CheckoutError) as error:
        services.checkout(user=case.user, address_id=case.address.pk)
    assert error.value.detail["code"] == "AMOUNT_OUT_OF_RANGE"
    assert checkout_state() == before


def test_invalid_saved_address_is_reported_without_partial_order(
    checkout_factory, checkout_state
):
    case = checkout_factory()
    Address.objects.filter(pk=case.address.pk).update(city="")
    before = checkout_state()
    with pytest.raises(CheckoutError) as error:
        services.checkout(user=case.user, address_id=case.address.pk)
    assert error.value.detail["code"] == "CHECKOUT_DATA_INVALID"
    assert checkout_state() == before


@pytest.mark.parametrize("phase", ["last-item", "stock", "clear"])
def test_late_failure_rolls_back_real_writes_and_timestamps(
    checkout_factory, checkout_state, monkeypatch, phase
):
    case = checkout_factory()
    before = checkout_state()
    written = {"items": [], "stock": [], "clear": 0}
    original_item_save, original_inventory_save, original_clear = (
        OrderItem.save,
        Inventory.save,
        services.clear_cart,
    )

    def item_save(self, *args, **kwargs):
        result = original_item_save(self, *args, **kwargs)
        written["items"].append(self.variant_id)
        if phase == "last-item" and len(written["items"]) == 2:
            raise RuntimeError("Injected failure after second item INSERT")
        return result

    def inventory_save(self, *args, **kwargs):
        result = original_inventory_save(self, *args, **kwargs)
        written["stock"].append(self.variant_id)
        if phase == "stock":
            raise RuntimeError("Injected failure after stock UPDATE")
        return result

    def clear(**kwargs):
        result = original_clear(**kwargs)
        written["clear"] += 1
        if phase == "clear":
            raise RuntimeError(
                "Injected failure after Cart DELETE and timestamp UPDATE"
            )
        return result

    monkeypatch.setattr(OrderItem, "save", item_save)
    monkeypatch.setattr(Inventory, "save", inventory_save)
    monkeypatch.setattr(services, "clear_cart", clear)
    with pytest.raises(RuntimeError, match="Injected failure"):
        services.checkout(user=case.user, address_id=case.address.pk)
    assert written["items"] and written["stock"]
    if phase == "last-item":
        assert len(written["items"]) == 2 and len(written["stock"]) == 1
    if phase == "clear":
        assert len(written["stock"]) == 2 and written["clear"] == 1
    assert checkout_state() == before


def test_second_sequential_checkout_does_not_create_second_order(
    checkout_factory, checkout_state
):
    case = checkout_factory()
    services.checkout(user=case.user, address_id=case.address.pk)
    before = checkout_state()
    with pytest.raises(CheckoutError) as error:
        services.checkout(user=case.user, address_id=case.address.pk)
    assert error.value.detail["code"] == "EMPTY_CART"
    assert Order.objects.count() == 1 and checkout_state() == before
