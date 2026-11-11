from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework.exceptions import PermissionDenied, ValidationError

from accounts.models import Address, User
from cart.models import Cart
from cart.rules import MAX_CART_QUANTITY, variant_is_active
from cart.services import clear_cart
from catalog.models import (
    Brand,
    Category,
    Inventory,
    Product,
    ProductVariant,
    VariantAttribute,
)

from .errors import CheckoutError, OrderError
from .models import (
    MAX_LINE_TOTAL,
    MAX_ORDER_ITEM_QUANTITY,
    MAX_ORDER_MONEY,
    Order,
    OrderItem,
    OrderShippingAddress,
)
from .querysets import (
    cart_items_for_checkout,
    checkout_order_queryset,
    get_order_or_404,
)
from .snapshots import build_item_snapshot, build_shipping_snapshot


ORDER_STATUS_TRANSITIONS = {
    Order.Status.PENDING: frozenset({Order.Status.PAID, Order.Status.CANCELLED}),
    Order.Status.PAID: frozenset({Order.Status.PROCESSING, Order.Status.CANCELLED}),
    Order.Status.PROCESSING: frozenset({Order.Status.SHIPPED}),
    Order.Status.SHIPPED: frozenset({Order.Status.DELIVERED}),
    Order.Status.DELIVERED: frozenset(),
    Order.Status.CANCELLED: frozenset(),
}
MAX_INVENTORY_QUANTITY = 2_147_483_647


def _require_customer(user):
    if user is None or not (
        user.is_authenticated and user.is_active and user.role == User.Role.CUSTOMER
    ):
        raise PermissionDenied("Active customer role is required.")


def _local_clean(instance, *, exclude):
    try:
        instance.full_clean(
            exclude=exclude,
            validate_unique=False,
            validate_constraints=False,
        )
    except DjangoValidationError as exc:
        raise CheckoutError(
            "CHECKOUT_DATA_INVALID",
            "Stored checkout data is invalid.",
        ) from exc


def _check_amount(value, maximum):
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or not Decimal("0.00") <= value <= maximum
    ):
        raise CheckoutError(
            "AMOUNT_OUT_OF_RANGE",
            "Checkout amount is outside the supported range.",
        )


def checkout(*, user, address_id):
    _require_customer(user)

    if type(address_id) is not int or not 1 <= address_id <= 9223372036854775807:
        raise ValidationError({"address_id": ["Use a positive integer address ID."]})

    with transaction.atomic():
        locked_user = User.objects.select_for_update().filter(pk=user.pk).first()
        _require_customer(locked_user)

        address = (
            Address.objects.select_for_update()
            .filter(pk=address_id, user=locked_user)
            .first()
        )

        if address is None:
            raise CheckoutError(
                "ADDRESS_NOT_FOUND",
                "Shipping address was not found.",
                status_code=404,
            )

        cart = Cart.objects.select_for_update().filter(user=locked_user).first()
        if cart is None:
            raise CheckoutError(
                "EMPTY_CART",
                "Cart is empty.",
                status_code=400,
            )

        lines = list(cart_items_for_checkout(cart=cart))
        if not lines:
            raise CheckoutError(
                "EMPTY_CART",
                "Cart is empty.",
                status_code=400,
            )

        for line in lines:
            if type(line.quantity) is not int or not 1 <= line.quantity <= min(
                MAX_CART_QUANTITY, MAX_ORDER_ITEM_QUANTITY
            ):
                raise CheckoutError(
                    "INVALID_QUANTITY",
                    "Cart contains an invalid quantity.",
                    status_code=400,
                )

        variant_ids = sorted({line.variant_id for line in lines})
        variants = {
            variant.pk: variant
            for variant in ProductVariant.objects.select_for_update(no_key=True)
            .filter(pk__in=variant_ids)
            .order_by("pk")
        }

        if len(variants) != len(variant_ids):
            raise CheckoutError(
                "PRODUCT_UNAVAILABLE",
                "A selected variant no longer exists.",
            )

        product_ids = sorted({variant.product_id for variant in variants.values()})
        products = {
            product.pk: product
            for product in Product.objects.select_for_update(no_key=True)
            .filter(pk__in=product_ids)
            .order_by("pk")
        }

        categories = Category.objects.in_bulk(
            {p.category_id for p in products.values()}
        )

        brands = Brand.objects.in_bulk({p.brand_id for p in products.values()})

        for product in products.values():
            product.category = categories[product.category_id]
            product.brand = brands[product.brand_id]

        for variant in variants.values():
            variant.product = products[variant.product_id]

        inventories = {
            inventory.variant_id: inventory
            for inventory in Inventory.objects.select_for_update()
            .filter(variant_id__in=variant_ids)
            .order_by("variant_id", "pk")
        }

        attributes = defaultdict(list)

        for row in VariantAttribute.objects.filter(
            variant_id__in=variant_ids
        ).select_related("attribute"):
            attributes[row.variant_id].append(row)

        rows = []

        for line in lines:
            variant = variants[line.variant_id]

            if not variant_is_active(variant):
                raise CheckoutError(
                    "PRODUCT_UNAVAILABLE",
                    "A selected product is unavailable.",
                )

            inventory = inventories.get(variant.pk)

            if inventory is None or inventory.quantity < line.quantity:
                raise CheckoutError(
                    "OUT_OF_STOCK",
                    f"Not enough stock for variant {variant.pk}.",
                )

            try:
                row = build_item_snapshot(
                    variant=variant,
                    quantity=line.quantity,
                    attributes=attributes[variant.pk],
                )
            except DjangoValidationError as exc:
                raise CheckoutError(
                    "CHECKOUT_DATA_INVALID",
                    "Stored product data is invalid.",
                ) from exc

            _check_amount(row["total_price"], MAX_LINE_TOTAL)
            rows.append(row)

        subtotal = sum((row["total_price"] for row in rows), Decimal("0.00"))
        discount, shipping_cost = Decimal("0.00"), Decimal("0.00")
        total = subtotal - discount + shipping_cost

        _check_amount(subtotal, MAX_ORDER_MONEY)
        _check_amount(total, MAX_ORDER_MONEY)

        order = Order(
            user=locked_user,
            status=Order.Status.PENDING,
            currency="PLN",
            subtotal=subtotal,
            discount=discount,
            shipping_cost=shipping_cost,
            total=total,
        )

        shipping = OrderShippingAddress(**build_shipping_snapshot(address=address))
        items = [OrderItem(**row) for row in rows]

        _local_clean(order, exclude=["user"])
        _local_clean(shipping, exclude=["order"])

        for item in items:
            _local_clean(item, exclude=["order", "variant"])

        order.save()

        shipping.order = order
        shipping.save()

        for item in items:
            item.order = order
            item.save()
            inventory = inventories[item.variant_id]
            inventory.quantity -= item.quantity
            inventory.save(update_fields=["quantity", "updated_at"])

        clear_cart(user=locked_user)

        return checkout_order_queryset().get(pk=order.pk, user=locked_user)


def restore_inventory(*, order):
    """Restore checkout stock inside the transaction holding the Order row lock."""
    quantities = defaultdict(int)
    lines = order.items.order_by().values_list("variant_id", "quantity")
    for variant_id, quantity in lines:
        if variant_id is None:
            raise OrderError(
                "INVENTORY_RESTORE_UNAVAILABLE",
                "An ordered variant no longer exists.",
            )
        quantities[variant_id] += quantity

    if not quantities:
        raise OrderError(
            "INVENTORY_RESTORE_UNAVAILABLE",
            "Order has no stock to restore.",
        )

    inventories = list(
        Inventory.objects.select_for_update()
        .filter(variant_id__in=quantities)
        .order_by("variant_id", "pk")
    )
    if len(inventories) != len(quantities):
        raise OrderError(
            "INVENTORY_RESTORE_UNAVAILABLE",
            "Inventory is missing for an ordered variant.",
        )

    for inventory in inventories:
        restored = inventory.quantity + quantities[inventory.variant_id]
        if restored > MAX_INVENTORY_QUANTITY:
            raise OrderError(
                "INVENTORY_RESTORE_UNAVAILABLE",
                "Restored quantity would exceed the inventory limit.",
            )
        inventory.quantity = restored
        inventory.save(update_fields=["quantity", "updated_at"])


def _apply_order_status(order, target_status, *, invalid_code):
    if target_status not in ORDER_STATUS_TRANSITIONS[order.status]:
        raise OrderError(
            invalid_code,
            f"Cannot transition order from {order.status} to {target_status}.",
        )

    if target_status == Order.Status.CANCELLED:
        restore_inventory(order=order)

    order.status = target_status
    order.save(update_fields=["status", "updated_at"])
    return order


@transaction.atomic
def cancel_order(*, user, order_id):
    _require_customer(user)
    order = get_order_or_404(
        Order.objects.select_for_update().filter(user_id=user.pk),
        order_id=order_id,
    )

    if order.status == Order.Status.CANCELLED:
        return order

    return _apply_order_status(
        order,
        Order.Status.CANCELLED,
        invalid_code="ORDER_CANNOT_BE_CANCELLED",
    )


@transaction.atomic
def change_order_status(*, order_id, target_status):
    """Apply a status command from a caller that already checked access."""
    order = get_order_or_404(
        Order.objects.select_for_update(),
        order_id=order_id,
    )
    return _apply_order_status(
        order,
        target_status,
        invalid_code="INVALID_ORDER_STATUS_TRANSITION",
    )