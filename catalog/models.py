from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q


catalog_slug_validator = RegexValidator(
    regex=r"^[a-z0-9_-]+$",
    message="Use lowercase Latin letters, numbers, hyphens or underscores.",
)

sku_validator = RegexValidator(
    regex=r"\A[A-Z0-9][A-Z0-9_-]*\Z",
    message="Use uppercase Latin letters, numbers, hyphens or underscores.",
)

barcode_validator = RegexValidator(
    regex=r"\A[0-9]+\Z",
    message="Barcode must contain digits only.",
)


class Category(models.Model):
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="children",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=150)
    slug = models.SlugField(
        max_length=100,
        unique=True,
        validators=[catalog_slug_validator],
    )
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "id")
        constraints = [
            models.CheckConstraint(
                condition=Q(parent__isnull=True) | ~Q(parent=F("id")),
                name="catalog_category_parent_not_self",
            ),
        ]

    def __str__(self):
        return self.name


class Brand(models.Model):
    name = models.CharField(max_length=150)
    slug = models.SlugField(
        max_length=100,
        unique=True,
        validators=[catalog_slug_validator],
    )
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name", "id")

    def __str__(self):
        return self.name


class Attribute(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(
        max_length=100,
        unique=True,
        validators=[catalog_slug_validator],
    )
    unit = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        ordering = ("name", "id")

    def __str__(self):
        return self.name


class Product(models.Model):
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="products",
    )
    brand = models.ForeignKey(
        Brand,
        on_delete=models.PROTECT,
        related_name="products",
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField(
        unique=True,
        max_length=100,
        validators=[catalog_slug_validator],
    )
    description = models.TextField(
        blank=True,
        default="",
        null=False,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "id")

    def __str__(self):
        return self.name


class ProductVariant(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="variants",
    )
    sku = models.CharField(
        max_length=64,
        unique=True,
        validators=[sku_validator],
    )
    barcode = models.CharField(
        max_length=32,
        unique=True,
        null=True,
        blank=True,
        default=None,
        validators=[barcode_validator],
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("id",)
        constraints = [
            models.CheckConstraint(
                condition=Q(price__gte=0), name="catalog_variant_price_nonnegative"
            ),
            models.CheckConstraint(
                condition=Q(barcode__isnull=True) | ~Q(barcode=""),
                name="catalog_variant_barcode_not_empty",
            ),
        ]

    def clean(self):
        super().clean()
        if self.barcode == "":
            raise ValidationError({"barcode": "Use None when barcode is absent."})

    def __str__(self):
        return self.sku


class Inventory(models.Model):
    variant = models.OneToOneField(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name="inventory",
    )
    quantity = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gte=0),
                name="catalog_inventory_quantity_nonnegative",
            ),
        ]

    def __str__(self):
        return f"{self.variant_id}: {self.quantity}"


class ProductAttributeValue(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="attribute_values",
    )
    attribute = models.ForeignKey(
        Attribute,
        on_delete=models.PROTECT,
        related_name="product_values",
    )
    value = models.CharField(max_length=255)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "attribute"],
                name="catalog_product_attribute_unique",
            ),
        ]

    def __str__(self):
        return f"{self.attribute_id}: {self.value}"


class VariantAttribute(models.Model):
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name="attribute_values",
    )
    attribute = models.ForeignKey(
        Attribute,
        on_delete=models.PROTECT,
        related_name="variant_values",
    )
    value = models.CharField(max_length=255)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["variant", "attribute"],
                name="catalog_variant_attribute_unique",
            ),
        ]

    def __str__(self):
        return f"{self.attribute_id}: {self.value}"


class ProductImage(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image_url = models.URLField(max_length=2048)
    alt_text = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    position = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
    )
    is_primary = models.BooleanField(default=False)

    class Meta:
        ordering = ("position", "id")
        constraints = [
            models.CheckConstraint(
                condition=Q(position__gte=0),
                name="catalog_image_position_nonnegative",
            ),
            models.UniqueConstraint(
                fields=["product"],
                condition=Q(is_primary=True),
                name="catalog_product_one_primary_image",
            ),
        ]

    def __str__(self):
        return self.image_url
    