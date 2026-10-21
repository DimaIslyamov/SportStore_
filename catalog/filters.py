from decimal import Decimal

from rest_framework import serializers

from .models import catalog_slug_validator


class ProductQuerySerializer(serializers.Serializer):
    category = serializers.SlugField(
        max_length=100, validators=[catalog_slug_validator], required=False
    )
    brand = serializers.SlugField(
        max_length=100, validators=[catalog_slug_validator], required=False
    )
    min_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0.00"), required=False
    )
    max_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0.00"), required=False
    )
    in_stock = serializers.BooleanField(required=False)
    search = serializers.CharField(max_length=200, allow_blank=True, required=False)
    ordering = serializers.ChoiceField(
        choices=("name", "price", "-price"), default="name"
    )

    def validate(self, attrs):
        minimum = attrs.get("min_price")
        maximum = attrs.get("max_price")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise serializers.ValidationError(
                {"max_price": "Must be greater than or equal to min_price."}
            )
        return attrs
