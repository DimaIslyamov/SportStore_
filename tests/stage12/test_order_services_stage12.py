from decimal import Decimal
from itertools import product
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from catalog.models import Inventory
from orders import services
from orders.errors import OrderError
from orders.models import Order
from payments.services import create_payment

pytestmark = pytest.mark.django_db

ALLOWED_PAIRS = {
    ("PENDING", "CANCELLED"),
    ("PAID", "PROCESSING"),
    ("PAID", "CANCELLED"),
    ("PROCESSING", "SHIPPED"),
    ("SHIPPED", "DELIVERED"),
}


def inventory_quantities():
    return list(
        Inventory.objects.order_by("variant_id").values_list("quantity", flat=True)
    )


@pytest.mark.parametrize(
    ("source", "target"), list(product(Order.Status.values, repeat=2))
)
def test_every_transition(order_factory, source, target):
    order = order_factory(quantities=(2,))
    Order.objects.filter(pk=order.pk).update(status=source)

    if (source, target) in ALLOWED_PAIRS:
        result = services.change_order_status(order_id=order.pk, target_status=target)
        assert result.status == target
        order.refresh_from_db()
        assert order.status == target
        assert inventory_quantities() == (
            [10, 10] if target == "CANCELLED" else [8, 10]
        )
    else:
        with pytest.raises(OrderError) as caught:
            services.change_order_status(order_id=order.pk, target_status=target)

        assert caught.value.status_code == 409
        expected_code = (
            "PAYMENT_CONFIRMATION_REQUIRED"
            if (source, target) == ("PENDING", "PAID")
            else "INVALID_ORDER_STATUS_TRANSITION"
        )
        assert caught.value.detail["code"] == expected_code
        order.refresh_from_db()
        assert order.status == source
        assert inventory_quantities() == [8, 10]


@pytest.mark.parametrize("source", ["PENDING", "PAID"])
def test_customer_cancel_restores_once(order_factory, customer, source):
    order = order_factory()
    if source == "PAID":
        create_payment(user=customer, order_id=order.pk, key=uuid4())

    assert inventory_quantities() == [8, 7]
    first = services.cancel_order(user=customer, order_id=order.pk)
    assert first.status == "CANCELLED"
    assert inventory_quantities() == [10, 10]

    repeated = services.cancel_order(user=customer, order_id=order.pk)
    order.refresh_from_db()
    assert repeated.status == order.status == "CANCELLED"
    assert repeated.updated_at == first.updated_at == order.updated_at
    assert inventory_quantities() == [10, 10]


@pytest.mark.parametrize("source", ["PROCESSING", "SHIPPED", "DELIVERED"])
def test_customer_cannot_cancel_later_states(order_factory, customer, source):
    order = order_factory()
    Order.objects.filter(pk=order.pk).update(status=source)

    with pytest.raises(OrderError) as caught:
        services.cancel_order(user=customer, order_id=order.pk)

    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "ORDER_CANNOT_BE_CANCELLED"
    order.refresh_from_db()
    assert order.status == source
    assert inventory_quantities() == [8, 7]


def test_restores_sum_of_snapshot_quantities_into_current_inventory(
    order_factory, order_variants, customer
):
    order = order_factory(quantities=(3,))
    line = order.items.get()
    line.quantity = 1
    line.total_price = line.unit_price
    line.save(update_fields=["quantity", "total_price"])

    line.pk = None
    line.quantity = 2
    line.total_price = line.unit_price * line.quantity
    line.save()

    variant = order_variants[0]
    type(variant).objects.filter(pk=variant.pk).update(is_active=False)
    Inventory.objects.filter(variant=variant).update(quantity=4)

    services.cancel_order(user=customer, order_id=order.pk)
    assert inventory_quantities() == [7, 10]


@pytest.mark.parametrize("missing", ["variant", "inventory", "items"])
def test_missing_stock_target_prevents_partial_cancellation(
    order_factory, order_variants, customer, missing
):
    order = order_factory()
    if missing == "variant":
        variant_id = order_variants[1].pk
        line = order.items.get(variant_id=variant_id)
        order_variants[1].delete()
        line.refresh_from_db()
        assert line.variant_id is None
        assert line.sku == "STAGE12-SHIRT-L"
    elif missing == "inventory":
        Inventory.objects.filter(variant=order_variants[1]).delete()
    else:
        order.items.all().delete()

    before = inventory_quantities()
    with pytest.raises(OrderError) as caught:
        services.cancel_order(user=customer, order_id=order.pk)

    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "INVENTORY_RESTORE_UNAVAILABLE"
    order.refresh_from_db()
    assert order.status == "PENDING"
    assert inventory_quantities() == before


def test_cancel_of_checked_out_order_does_not_depend_on_money(order_factory, customer):
    order = order_factory(quantities=(2,))
    discount = Decimal("1.00")
    shipping_cost = Decimal("2.00")
    Order.objects.filter(pk=order.pk).update(
        discount=discount,
        shipping_cost=shipping_cost,
        total=order.subtotal - discount + shipping_cost,
    )
    assert inventory_quantities() == [8, 10]

    services.cancel_order(user=customer, order_id=order.pk)

    order.refresh_from_db()
    assert order.status == "CANCELLED"
    assert order.discount == discount
    assert order.shipping_cost == shipping_cost
    assert order.total == order.subtotal - discount + shipping_cost
    assert inventory_quantities() == [10, 10]


def test_late_error_rolls_back_first_inventory_update(
    order_factory, order_variants, customer
):
    order = order_factory()
    inventories = Inventory.objects.order_by("variant_id")
    inventories.filter(variant=order_variants[1]).update(quantity=2_147_483_647)
    before = list(inventories.values_list("quantity", "updated_at"))
    order_updated_at = order.updated_at

    with CaptureQueriesContext(connection) as captured:
        with pytest.raises(OrderError) as caught:
            services.cancel_order(user=customer, order_id=order.pk)

    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "INVENTORY_RESTORE_UNAVAILABLE"
    updates = [
        query
        for query in captured.captured_queries
        if query["sql"].startswith('UPDATE "catalog_inventory"')
    ]
    assert len(updates) == 1
    assert list(inventories.values_list("quantity", "updated_at")) == before
    order.refresh_from_db()
    assert order.status == "PENDING"
    assert order.updated_at == order_updated_at
