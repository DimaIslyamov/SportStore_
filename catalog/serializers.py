from rest_framework import serializers

from core.serializers import StrictInputMixin

from .models import Brand, Category


class ImmutableSlugMixin(StrictInputMixin):
    def validate_slug(self, value):
        if self.instance is not None and value != self.instance.slug:
            raise serializers.ValidationError("Slug cannot be changed after creation.")
        return value


class CategorySerializer(ImmutableSlugMixin, serializers.ModelSerializer):
    parent = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(),
        allow_null=True,
        required=False,
    )

    class Meta:
        model = Category
        fields = (
            "id",
            "parent",
            "name",
            "slug",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_parent(self, parent):
        visited = set()
        node = parent

        while node is not None:
            if self.instance is not None and node.pk == self.instance.pk:
                raise serializers.ValidationError(
                    "A category cannot be its own parent or ancestor."
                )
            if node.pk in visited:
                raise serializers.ValidationError(
                    "The selected parent already belongs to a cycle."
                )
            visited.add(node.pk)
            node = node.parent

        return parent


class BrandSerializer(ImmutableSlugMixin, serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = ("id", "name", "slug", "description", "is_active")
        read_only_fields = ("id",)
