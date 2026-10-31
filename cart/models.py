from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from .rules import MAX_CART_QUANTITY


class Cart(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cart",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Cart {self.pk} for user {self.user_id}"


class CartItem(models.Model):
    cart = models.ForeignKey(
        Cart,
        on_delete=models.CASCADE,
        related_name="items",
    )
    variant = models.ForeignKey(
        "catalog.ProductVariant",
        on_delete=models.PROTECT,
        related_name="cart_items",
    )
    quantity = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(MAX_CART_QUANTITY)]
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("id",)
        constraints = [
            models.UniqueConstraint(
                fields=["cart", "variant"], name="cart_item_unique_variant"
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1, quantity__lte=MAX_CART_QUANTITY),
                name="cart_item_quantity_range",
            ),
        ]

    def __str__(self):
        return f"Cart {self.cart_id}: variant {self.variant_id} x {self.quantity}"
