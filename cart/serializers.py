from decimal import Decimal

from rest_framework import serializers

from catalog.models import Inventory
from core.serializers import StrictInputMixin

from .models import Cart, CartItem
from .rules import MAX_CART_QUANTITY, variant_is_active


class CartItemAddInputSerializer(StrictInputMixin, serializers.Serializer):
    variant = serializers.IntegerField(min_value=1, max_value=9223372036854775807)
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_CART_QUANTITY)


class CartItemQuantityInputSerializer(StrictInputMixin, serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_CART_QUANTITY)


class CartProductSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)
    slug = serializers.CharField(read_only=True)


class CartVariantSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    sku = serializers.CharField(read_only=True)


def _inventory_quantity(variant):
    try:
        return variant.inventory.quantity
    except Inventory.DoesNotExist:
        return 0


def _line_total(item):
    return item.variant.price * item.quantity


class CartItemOutputSerializer(serializers.ModelSerializer):
    product = CartProductSerializer(source="variant.product", read_only=True)
    variant = CartVariantSerializer(read_only=True)
    unit_price = serializers.DecimalField(
        source="variant.price",
        max_digits=10,
        decimal_places=2,
        coerce_to_string=True,
        read_only=True,
    )
    line_total = serializers.SerializerMethodField()
    is_available = serializers.SerializerMethodField()
    has_sufficient_stock = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = (
            "id",
            "product",
            "variant",
            "quantity",
            "unit_price",
            "line_total",
            "is_available",
            "has_sufficient_stock",
        )
        read_only_fields = fields

    def get_line_total(self, obj):
        return format(_line_total(obj), ".2f")

    def get_is_available(self, obj):
        return variant_is_active(obj.variant) and _inventory_quantity(obj.variant) > 0

    def get_has_sufficient_stock(self, obj):
        return (
            variant_is_active(obj.variant)
            and _inventory_quantity(obj.variant) >= obj.quantity
        )


class CartOutputSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True, allow_null=True)
    updated_at = serializers.DateTimeField(read_only=True, allow_null=True)
    items = CartItemOutputSerializer(source="loaded_items", many=True, read_only=True)
    currency = serializers.SerializerMethodField()
    items_count = serializers.SerializerMethodField()
    total_quantity = serializers.SerializerMethodField()
    subtotal = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = (
            "id",
            "currency",
            "items_count",
            "total_quantity",
            "subtotal",
            "items",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_currency(self, obj):
        return "PLN"

    def get_items_count(self, obj):
        return len(obj.loaded_items)

    def get_total_quantity(self, obj):
        return sum(item.quantity for item in obj.loaded_items)

    def get_subtotal(self, obj):
        total = sum((_line_total(item) for item in obj.loaded_items), Decimal("0.00"))
        return format(total, ".2f")
