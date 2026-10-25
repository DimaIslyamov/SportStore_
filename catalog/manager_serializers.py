from decimal import Decimal

from rest_framework import serializers

from core.serializers import StrictInputMixin

from .image_validation import prepare_image
from .models import Brand, Category, Inventory, Product, ProductVariant


class ProductInputSerializer(StrictInputMixin, serializers.ModelSerializer):
    category = serializers.PrimaryKeyRelatedField(queryset=Category.objects.all())
    brand = serializers.PrimaryKeyRelatedField(queryset=Brand.objects.all())

    class Meta:
        model = Product
        fields = ("category", "brand", "name", "slug", "description", "is_active")

    def validate_slug(self, value):
        if self.instance is not None and value != self.instance.slug:
            raise serializers.ValidationError("Slug cannot be changed.")
        return value


class VariantInputSerializer(StrictInputMixin, serializers.ModelSerializer):
    price = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0.00")
    )

    class Meta:
        model = ProductVariant
        fields = ("sku", "barcode", "price", "is_active")
        extra_kwargs = {"barcode": {"allow_blank": False}}

    def validate_sku(self, value):
        if self.instance is not None and value != self.instance.sku:
            raise serializers.ValidationError("SKU cannot be changed.")
        return value


class InventoryInputSerializer(StrictInputMixin, serializers.Serializer):
    quantity = serializers.IntegerField(min_value=0, max_value=2147483647)


class ImageInputSerializer(StrictInputMixin, serializers.Serializer):
    file = serializers.FileField(required=True, allow_empty_file=False)
    alt_text = serializers.CharField(max_length=255, allow_blank=True, required=False)
    position = serializers.IntegerField(
        min_value=0, max_value=2147483647, required=False
    )
    is_primary = serializers.BooleanField(required=False)

    def validate_file(self, value):
        return prepare_image(value)


class ProductOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = (
            "id",
            "category",
            "brand",
            "name",
            "slug",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class VariantOutputSerializer(serializers.ModelSerializer):
    price = serializers.DecimalField(
        max_digits=10, decimal_places=2, coerce_to_string=True, read_only=True
    )

    class Meta:
        model = ProductVariant
        fields = ("id", "product", "sku", "barcode", "price", "is_active")
        read_only_fields = fields


class InventoryOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Inventory
        fields = ("variant", "quantity", "updated_at")
        read_only_fields = fields
