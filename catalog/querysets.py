from django.db.models import Exists, Min, OuterRef, Prefetch, Q

from .models import (
    Product,
    ProductAttributeValue,
    ProductImage,
    ProductVariant,
    VariantAttribute,
)


def public_product_queryset():
    available_variants = ProductVariant.objects.filter(
        product_id=OuterRef("pk"),
        is_active=True,
        inventory__quantity__gt=0,
    )
    return (
        Product.objects.filter(
            is_active=True,
            category__is_active=True,
            brand__is_active=True,
        )
        .annotate(
            from_price=Min("variants__price", filter=Q(variants__is_active=True)),
            in_stock=Exists(available_variants),
        )
        .filter(from_price__isnull=False)
        .select_related("category", "brand")
        .prefetch_related(
            Prefetch(
                "images",
                queryset=ProductImage.objects.order_by("position", "id"),
                to_attr="public_images",
            )
        )
    )


def public_product_detail_queryset():
    active_variants = (
        ProductVariant.objects.filter(is_active=True)
        .select_related("inventory")
        .order_by("id")
        .prefetch_related(
            Prefetch(
                "attribute_values",
                queryset=VariantAttribute.objects.select_related("attribute").order_by(
                    "attribute__name", "attribute_id"
                ),
                to_attr="public_attributes",
            )
        )
    )
    return public_product_queryset().prefetch_related(
        Prefetch(
            "attribute_values",
            queryset=ProductAttributeValue.objects.select_related("attribute").order_by(
                "attribute__name", "attribute_id"
            ),
            to_attr="public_attributes",
        ),
        Prefetch("variants", queryset=active_variants, to_attr="public_variants"),
    )
