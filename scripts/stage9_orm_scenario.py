"""Manual Stage 9 exercise; run explicitly through Django's manage.py shell.

Creates one demo order from existing development data. Source edits are rolled
back, while the new order remains. This does not perform checkout or touch stock.
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import Prefetch

from accounts.models import Address, User
from catalog.models import Product, ProductVariant, VariantAttribute
from orders.models import Order, OrderItem, OrderShippingAddress
from orders.snapshots import (
    SHIPPING_FIELDS,
    build_item_snapshot,
    build_shipping_snapshot,
)

ITEM_SNAPSHOT_FIELDS = (
    "product_name",
    "variant_description",
    "sku",
    "unit_price",
    "quantity",
    "total_price",
)


def run():
    customer = User.objects.get(
        pk=int(input("Existing Customer ID: ")), role=User.Role.CUSTOMER, is_active=True
    )
    address = Address.objects.get(pk=int(input("Owned Address ID: ")), user=customer)
    variant_ids = [int(input("Variant A ID: ")), int(input("Variant B ID: "))]
    if len(set(variant_ids)) != 2:
        raise ValueError("Choose two different variants.")

    variants = {
        variant.pk: variant
        for variant in ProductVariant.objects.filter(
            pk__in=variant_ids,
            is_active=True,
            product__is_active=True,
            product__category__is_active=True,
            product__brand__is_active=True,
        )
        .select_related("product")
        .prefetch_related(
            Prefetch(
                "attribute_values",
                queryset=VariantAttribute.objects.select_related("attribute"),
                to_attr="snapshot_attributes",
            )
        )
    }
    if len(variants) != 2:
        raise ValueError(
            "Both variants and their products, categories and brands must be active."
        )
    first, second = (variants[pk] for pk in variant_ids)
    if first.price != Decimal("19.90") or second.price != Decimal("12.50"):
        raise ValueError(
            "This exercise requires prices of 19.90 and 12.50 PLN, in that order."
        )

    rows = [
        build_item_snapshot(
            variant=first, quantity=2, attributes=first.snapshot_attributes
        ),
        build_item_snapshot(
            variant=second, quantity=3, attributes=second.snapshot_attributes
        ),
    ]
    subtotal = sum((row["total_price"] for row in rows), Decimal("0.00"))
    discount, shipping_cost = Decimal("7.30"), Decimal("10.00")
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

    order.refresh_from_db()
    before_items = list(order.items.values(*ITEM_SNAPSHOT_FIELDS))
    before_shipping = OrderShippingAddress.objects.values(*SHIPPING_FIELDS).get(
        order=order
    )
    assert order.subtotal == Decimal("77.30")
    assert order.total == Decimal("80.00")
    assert order.status == Order.Status.PENDING
    assert order.currency == "PLN"
    print(
        "Created demo order:",
        order.order_number,
        order.status,
        order.currency,
        order.total,
    )
    print("Historical items:", before_items)

    with transaction.atomic():
        Address.objects.filter(pk=address.pk).update(city="Changed source city")
        Product.objects.filter(pk=first.product_id).update(
            name="Changed source product"
        )
        ProductVariant.objects.filter(pk=first.pk).update(
            price=Decimal("99.99"), is_active=False
        )
        VariantAttribute.objects.filter(variant=first).update(
            value="Changed source value"
        )

        historical = Order.objects.select_related("shipping_address").get(pk=order.pk)
        assert (
            build_shipping_snapshot(address=historical.shipping_address)
            == before_shipping
        )
        assert list(historical.items.values(*ITEM_SNAPSHOT_FIELDS)) == before_items
        assert historical.subtotal == Decimal("77.30")
        assert historical.discount == Decimal("7.30")
        assert historical.shipping_cost == Decimal("10.00")
        assert historical.total == Decimal("80.00")
        assert historical.order_number == order.order_number
        assert historical.status == Order.Status.PENDING
        assert historical.currency == "PLN"
        transaction.set_rollback(True)

    print("Snapshots unchanged. Source edits rolled back; the demo order remains.")
    return order
