import re
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError

from accounts.models import Address
from catalog.models import ProductVariant
from orders.models import Order, OrderItem, OrderShippingAddress
from orders.snapshots import SHIPPING_FIELDS, build_shipping_snapshot

pytestmark = pytest.mark.django_db


def test_complete_graph_defaults_relations_and_separate_shipping(
    customer, order_factory
):
    order = order_factory()
    order.refresh_from_db()
    assert order.status == Order.Status.PENDING and order.currency == "PLN"
    assert order.user_id == customer.pk
    assert customer.orders.get().pk == order.pk
    assert order.shipping_address.order_id == order.pk
    assert order.items.count() == 2
    assert order.subtotal == order.total == Decimal("77.30")
    fields = {field.name for field in Order._meta.fields}
    assert {name for name in fields if name.startswith("shipping_")} == {
        "shipping_cost"
    }
    shipping_fields = {field.name for field in OrderShippingAddress._meta.fields}
    assert shipping_fields == {"id", "order", *SHIPPING_FIELDS}
    for name in SHIPPING_FIELDS:
        actual = OrderShippingAddress._meta.get_field(name)
        source = Address._meta.get_field(name)
        assert actual.max_length == source.max_length
        assert actual.blank == source.blank and actual.null is False
    second = order.items.get(sku="ORDER-SOCKS-M")
    assert second.variant_description == ""
    assert order.shipping_address.apartment == ""
    order.shipping_address.full_clean()
    assert second.variant.order_items.get(pk=second.pk).order_id == order.pk


def test_order_can_temporarily_exist_without_children(customer):
    order = Order.objects.create(
        user=customer, subtotal=Decimal("0.00"), total=Decimal("0.00")
    )
    assert not order.items.exists()
    with pytest.raises(OrderShippingAddress.DoesNotExist):
        Order.objects.get(pk=order.pk).shipping_address


def test_second_shipping_snapshot_for_same_order_is_rejected(
    order_factory, order_sources
):
    order = order_factory()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            OrderShippingAddress.objects.create(
                order=order, **build_shipping_snapshot(address=order_sources.address)
            )
    assert OrderShippingAddress.objects.filter(order=order).count() == 1


def test_user_deletion_is_protected_and_order_children_belong_to_order(
    customer, order_factory, order_sources
):
    order = order_factory()
    with pytest.raises(ProtectedError):
        customer.delete()
    assert Order.objects.filter(pk=order.pk).exists()
    order_id = order.pk
    order.delete()
    assert not OrderItem.objects.filter(order_id=order_id).exists()
    assert not OrderShippingAddress.objects.filter(order_id=order_id).exists()
    assert type(customer).objects.filter(pk=customer.pk).exists()
    assert Address.objects.filter(pk=order_sources.address.pk).exists()
    assert ProductVariant.objects.filter(pk=order_sources.first.pk).exists()


@pytest.mark.parametrize(
    "status", ["PENDING", "PAID", "PROCESSING", "SHIPPED", "DELIVERED", "CANCELLED"]
)
def test_valid_status_and_number_stability(order_factory, status):
    order = order_factory()
    before = order.order_number
    assert re.fullmatch(r"SP-[0-9]{4}-[0-9a-f]{32}", before)
    order.status = status
    order.full_clean()
    order.save(update_fields=["status", "updated_at"])
    order.refresh_from_db()
    assert order.order_number == before and order.currency == "PLN"


def test_order_number_unique_and_format_validated(order_factory):
    first, second = order_factory(), order_factory()
    before = second.order_number
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Order.objects.filter(pk=second.pk).update(order_number=first.order_number)
    second.refresh_from_db()
    assert second.order_number == before
    second.order_number = "SP-2026-1"
    with pytest.raises(ValidationError) as error:
        second.full_clean()
    assert "order_number" in error.value.message_dict


@pytest.mark.parametrize(
    "updates",
    [
        {"status": "UNKNOWN"},
        {"currency": "USD"},
        {"subtotal": Decimal("-0.01")},
        {"discount": Decimal("-0.01")},
        {"shipping_cost": Decimal("-0.01")},
        {"total": Decimal("-0.01")},
        {
            "discount": Decimal("77.31"),
            "shipping_cost": Decimal("1.00"),
            "total": Decimal("0.99"),
        },
        {"total": Decimal("77.31")},
        {"user_id": None},
    ],
)
def test_database_rejects_invalid_order_rows(order_factory, updates):
    order = order_factory()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Order.objects.filter(pk=order.pk).update(**updates)
    order.refresh_from_db()
    assert order.subtotal == order.total == Decimal("77.30")
    assert order.status == Order.Status.PENDING and order.currency == "PLN"


@pytest.mark.parametrize(
    "updates",
    [
        {"quantity": 0, "total_price": Decimal("0.00")},
        {"quantity": -1, "total_price": Decimal("-19.90")},
        {"quantity": 1000, "total_price": Decimal("19900.00")},
        {"unit_price": Decimal("-0.01"), "total_price": Decimal("-0.02")},
        {"total_price": Decimal("-0.01")},
    ],
)
def test_database_rejects_invalid_item_ranges(order_factory, updates):
    item = order_factory().items.get(sku="ORDER-SHIRT-M")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            OrderItem.objects.filter(pk=item.pk).update(**updates)
    item.refresh_from_db()
    assert item.quantity == 2 and item.total_price == Decimal("39.80")


def test_database_rejects_wrong_line_total(order_factory):
    item = order_factory().items.get(sku="ORDER-SHIRT-M")
    with pytest.raises(IntegrityError) as error:
        with transaction.atomic():
            OrderItem.objects.filter(pk=item.pk).update(total_price=Decimal("39.81"))
    assert error.value.__cause__.diag.constraint_name == "orders_item_total_matches"
    item.refresh_from_db()
    assert item.total_price == Decimal("39.80")


def test_database_money_range_rejects_numeric_nan(order_factory):
    order = order_factory()
    table = connection.ops.quote_name(Order._meta.db_table)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {table} SET subtotal = %s::numeric, total = %s::numeric WHERE id = %s",
                    ["NaN", "NaN", order.pk],
                )
    order.refresh_from_db()
    assert order.subtotal == Decimal("77.30")


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("order", "status", "UNKNOWN"),
        ("order", "currency", "USD"),
        ("order", "total", Decimal("77.31")),
        ("order", "discount", Decimal("77.31")),
        ("order", "subtotal", Decimal("10000000000000000.00")),
        ("item", "quantity", 0),
        ("item", "quantity", 1000),
        ("item", "unit_price", Decimal("1.001")),
        ("item", "unit_price", Decimal("100000000.00")),
        ("item", "total_price", Decimal("100000000000.00")),
        ("item", "total_price", Decimal("39.81")),
    ],
)
def test_model_validation_reports_invalid_values(order_factory, target, field, value):
    order = order_factory()
    obj = order if target == "order" else order.items.get(sku="ORDER-SHIRT-M")
    setattr(obj, field, value)
    with pytest.raises(ValidationError) as error:
        obj.full_clean()
    assert field in error.value.message_dict


@pytest.mark.parametrize(
    "field",
    [
        "first_name",
        "last_name",
        "phone",
        "country",
        "city",
        "postal_code",
        "street",
        "building",
    ],
)
def test_shipping_required_fields_are_validated(order_factory, field):
    shipping = order_factory().shipping_address
    setattr(shipping, field, "")
    with pytest.raises(ValidationError) as error:
        shipping.full_clean()
    assert field in error.value.message_dict


@pytest.mark.parametrize("field", ["product_name", "sku"])
def test_item_required_text_is_validated(order_factory, field):
    item = order_factory().items.first()
    setattr(item, field, "")
    with pytest.raises(ValidationError) as error:
        item.full_clean()
    assert field in error.value.message_dict


def test_shipping_phone_is_not_nullable(order_factory):
    shipping = order_factory().shipping_address
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            OrderShippingAddress.objects.filter(pk=shipping.pk).update(phone=None)
    shipping.refresh_from_db()
    assert shipping.phone == "+48111222333"


@pytest.mark.parametrize(
    "discount,shipping_cost,expected",
    [
        ("0.00", "0.00", "77.30"),
        ("7.30", "10.00", "80.00"),
        ("77.30", "0.00", "0.00"),
        ("77.30", "10.00", "10.00"),
    ],
)
def test_order_money_formula(order_factory, discount, shipping_cost, expected):
    order = order_factory(discount=discount, shipping_cost=shipping_cost)
    order.refresh_from_db()
    assert order.total == Decimal(expected)
    assert order.status == Order.Status.PENDING


def test_zero_prices_are_allowed(order_factory, order_sources):
    ProductVariant.objects.filter(
        pk__in=[order_sources.first.pk, order_sources.second.pk]
    ).update(price=Decimal("0.00"))
    order = order_factory()
    assert order.subtotal == order.total == Decimal("0.00")
    assert all(
        item.unit_price == item.total_price == Decimal("0.00")
        for item in order.items.all()
    )


def test_large_line_and_multiple_lines_fit_chosen_fields(order_factory, order_sources):
    ProductVariant.objects.filter(
        pk__in=[order_sources.first.pk, order_sources.second.pk]
    ).update(price=Decimal("99999999.99"))
    order = order_factory(quantities=(999, 999))
    order.refresh_from_db()
    assert order.subtotal == order.total == Decimal("199799999980.02")
    assert (
        list(order.items.values_list("total_price", flat=True))
        == [Decimal("99899999990.01")] * 2
    )


def test_local_constraints_do_not_prove_subtotal_aggregate(order_factory):
    order = order_factory()
    Order.objects.filter(pk=order.pk).update(
        subtotal=Decimal("1.00"), total=Decimal("1.00")
    )
    order.refresh_from_db()
    order.full_clean()
    actual_items = sum(
        (item.total_price for item in order.items.all()), Decimal("0.00")
    )
    assert actual_items == Decimal("77.30") and order.subtotal != actual_items


def test_missing_parent_fk_is_checked_inside_savepoint(order_factory):
    order = order_factory()
    unknown_id = order.pk + 1000000
    assert not Order.objects.filter(pk=unknown_id).exists()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            OrderShippingAddress.objects.create(
                order_id=unknown_id,
                **build_shipping_snapshot(address=order.shipping_address),
            )
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert Order.objects.filter(pk=order.pk).exists()
    assert OrderShippingAddress.objects.count() == 1
