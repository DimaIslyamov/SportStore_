from rest_framework import serializers

from .models import Inventory, Product, ProductImage, ProductVariant


class CatalogReferenceSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)
    slug = serializers.CharField(read_only=True)


class AttributeSerializer(CatalogReferenceSerializer):
    unit = serializers.CharField(read_only=True)


class AttributeValueSerializer(serializers.Serializer):
    attribute = AttributeSerializer(read_only=True)
    value = serializers.CharField(read_only=True)


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ("id", "image_url", "alt_text", "position", "is_primary")
        read_only_fields = fields


class ProductVariantSerializer(serializers.ModelSerializer):
    price = serializers.DecimalField(
        max_digits=10, decimal_places=2, coerce_to_string=True, read_only=True
    )
    in_stock = serializers.SerializerMethodField()
    attributes = AttributeValueSerializer(
        source="public_attributes", many=True, read_only=True
    )

    class Meta:
        model = ProductVariant
        fields = ("id", "sku", "price", "in_stock", "attributes")
        read_only_fields = fields

    def get_in_stock(self, obj):
        try:
            return obj.inventory.quantity > 0
        except Inventory.DoesNotExist:
            return False


class ProductListSerializer(serializers.ModelSerializer):
    brand = CatalogReferenceSerializer(read_only=True)
    category = CatalogReferenceSerializer(read_only=True)
    from_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, coerce_to_string=True, read_only=True
    )
    currency = serializers.SerializerMethodField()
    in_stock = serializers.BooleanField(read_only=True)
    primary_image = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "slug",
            "brand",
            "category",
            "from_price",
            "currency",
            "in_stock",
            "primary_image",
        )
        read_only_fields = fields

    def get_currency(self, obj):
        return "PLN"

    def get_primary_image(self, obj):
        images = obj.public_images
        chosen = next((image for image in images if image.is_primary), None)
        if chosen is None and images:
            chosen = images[0]
        return ProductImageSerializer(chosen).data if chosen is not None else None


class ProductDetailSerializer(ProductListSerializer):
    images = ProductImageSerializer(source="public_images", many=True, read_only=True)
    attributes = AttributeValueSerializer(
        source="public_attributes", many=True, read_only=True
    )
    variants = ProductVariantSerializer(
        source="public_variants", many=True, read_only=True
    )

    class Meta(ProductListSerializer.Meta):
        fields = ProductListSerializer.Meta.fields + (
            "description",
            "images",
            "attributes",
            "variants",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields
