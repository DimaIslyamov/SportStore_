from rest_framework import serializers

from core.serializers import StrictInputMixin

from .models import Order, OrderItem, OrderShippingAddress
from .snapshots import SHIPPING_FIELDS


class CheckoutAddressIdField(serializers.IntegerField):
    def to_internal_value(self, data):
        if type(data) is int:
            return data

        if not isinstance(data, str):
            self.fail("invalid")

        if len(data) > self.MAX_STRING_LENGTH:
            self.fail("max_string_length")

        try:
            return int(data)
        except ValueError:
            self.fail("invalid")


class CheckoutInputSerializer(StrictInputMixin, serializers.Serializer):
    address_id = CheckoutAddressIdField(
        min_value=1,
        max_value=9223372036854775807,
    )


class ShippingSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderShippingAddress
        fields = SHIPPING_FIELDS
        read_only_fields = fields


class CheckoutItemOutputSerializer(serializers.ModelSerializer):
    unit_price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        coerce_to_string=True,
        read_only=True,
    )
    total_price = serializers.DecimalField(
        max_digits=13,
        decimal_places=2,
        coerce_to_string=True,
        read_only=True,
    )

    class Meta:
        model = OrderItem
        fields = (
            "id",
            "product_name",
            "variant_description",
            "sku",
            "unit_price",
            "quantity",
            "total_price",
        )
        read_only_fields = fields


class CheckoutOrderOutputSerializer(serializers.ModelSerializer):
    shipping_address = ShippingSnapshotSerializer(read_only=True)
    items = CheckoutItemOutputSerializer(
        source="loaded_items",
        many=True,
        read_only=True,
    )
    subtotal = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        coerce_to_string=True,
        read_only=True,
    )
    discount = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        coerce_to_string=True,
        read_only=True,
    )
    shipping_cost = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        coerce_to_string=True,
        read_only=True,
    )
    total = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        coerce_to_string=True,
        read_only=True,
    )

    class Meta:
        model = Order
        fields = (
            "id",
            "order_number",
            "status",
            "currency",
            "subtotal",
            "discount",
            "shipping_cost",
            "total",
            "shipping_address",
            "items",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class OrderSummarySerializer(CheckoutOrderOutputSerializer):
    class Meta(CheckoutOrderOutputSerializer.Meta):
        fields = (
            "id",
            "order_number",
            "status",
            "currency",
            "total",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class OrderCancelInputSerializer(StrictInputMixin, serializers.Serializer):
    pass


class OrderStatusInputSerializer(StrictInputMixin, serializers.Serializer):
    status = serializers.ChoiceField(choices=Order.Status.choices)
