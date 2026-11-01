from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import MAX_ORDER_ITEM_QUANTITY, OrderItem

SHIPPING_FIELDS = (
    "first_name",
    "last_name",
    "phone",
    "country",
    "city",
    "postal_code",
    "street",
    "building",
    "apartment",
)


def build_shipping_snapshot(*, address):
    return {field: getattr(address, field) for field in SHIPPING_FIELDS}


def build_item_snapshot(*, variant, quantity, attributes):
    if variant.pk is None:
        raise ValidationError({"variant": "Use a persisted variant."})

    if type(quantity) is not int or not 1 <= quantity <= MAX_ORDER_ITEM_QUANTITY:
        raise ValidationError({"quantity": "Use an integer from 1 to 999."})

    if not isinstance(variant.price, Decimal) or not variant.price.is_finite():
        raise ValidationError({"unit_price": "Use a finite Decimal price."})

    unit_price = OrderItem._meta.get_field("unit_price").clean(
        variant.price,
        None,
    )
    values = sorted(
        attributes,
        key=lambda row: (
            row.attribute.slug,
            row.attribute_id,
        ),
    )

    if any(row.variant_id != variant.pk for row in values):
        raise ValidationError({"attributes": "Attributes must belong to this variant."})

    description = "; ".join(
        f"{row.attribute.name}: {row.value}"
        + (f" {row.attribute.unit}" if row.attribute.unit else "")
        for row in values
    )

    return {
        "variant": variant,
        "product_name": variant.product.name,
        "variant_description": description,
        "sku": variant.sku,
        "unit_price": unit_price,
        "quantity": quantity,
        "total_price": unit_price * quantity,
    }
