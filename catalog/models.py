from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q


catalog_slug_validator = RegexValidator(
    regex=r"^[a-z0-9_-]+$",
    message="Use lowercase Latin letters, numbers, hyphens or underscores.",
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
