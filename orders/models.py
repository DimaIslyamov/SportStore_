from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q

from .numbers import generate_order_number

ZERO = Decimal("0.00")
MAX_ORDER_ITEM_QUANTITY = 999
MAX_UNIT_PRICE = Decimal("99999999.99")
MAX_LINE_TOTAL = Decimal("99999999999.99")
MAX_ORDER_MONEY = Decimal("9999999999999999.99")


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PAID = "PAID", "Paid"
        PROCESSING = "PROCESSING", "Processing"
        SHIPPED = "SHIPPED", "Shipped"
        DELIVERED = "DELIVERED", "Delivered"
        CANCELLED = "CANCELLED", "Cancelled"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    order_number = models.CharField(
        max_length=40,
        unique=True,
        default=generate_order_number,
        validators=[
            RegexValidator(regex=r"\ASP-[0-9]{4}-[0-9a-f]{32}\Z"),
        ],
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    currency = models.CharField(
        max_length=3,
        choices=[("PLN", "PLN")],
        default="PLN",
    )
    subtotal = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        validators=[MinValueValidator(ZERO)],
    )
    discount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
    )
    shipping_cost = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=ZERO,
        validators=[
            MinValueValidator(ZERO),
        ],
    )
    total = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        validators=[
            MinValueValidator(ZERO),
        ],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    subtotal__gte=ZERO,
                    subtotal__lte=MAX_ORDER_MONEY,
                    discount__gte=ZERO,
                    discount__lte=MAX_ORDER_MONEY,
                    shipping_cost__gte=ZERO,
                    shipping_cost__lte=MAX_ORDER_MONEY,
                    total__gte=ZERO,
                    total__lte=MAX_ORDER_MONEY,
                ),
                name="orders_order_money_range",
            ),
            models.CheckConstraint(
                condition=Q(discount__lte=F("subtotal")),
                name="orders_order_discount_valid",
            ),
            models.CheckConstraint(
                condition=Q(total=F("subtotal") - F("discount") + F("shipping_cost")),
                name="orders_order_total_matches",
            ),
            models.CheckConstraint(
                condition=Q(
                    status__in=(
                        "PENDING",
                        "PAID",
                        "PROCESSING",
                        "SHIPPED",
                        "DELIVERED",
                        "CANCELLED",
                    )
                ),
                name="orders_order_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(currency="PLN"), name="orders_order_currency_pln"
            ),
        ]

    def clean(self):
        super().clean()

        amounts = (
            self.subtotal,
            self.discount,
            self.shipping_cost,
            self.total,
        )

        if not all(
            isinstance(value, Decimal) and value.is_finite() for value in amounts
        ):
            return

        errors = {}

        if self.discount > self.subtotal:
            errors["discount"] = "Discount cannot exceed subtotal."
        if self.total != self.subtotal - self.discount + self.shipping_cost:
            errors["total"] = "Total must equal subtotal - discount + shipping cost."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.order_number


class OrderShippingAddress(models.Model):
    order = models.OneToOneField(
        Order,
        on_delete=models.CASCADE,
        related_name="shipping_address",
    )
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=32)
    country = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20)
    street = models.CharField(max_length=255)
    building = models.CharField(max_length=20)
    apartment = models.CharField(max_length=20, blank=True, default="")

    def __str__(self):
        return f"Shipping address for order {self.order_id}"


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )
    variant = models.ForeignKey(
        "catalog.ProductVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_items",
    )
    product_name = models.CharField(max_length=200)
    variant_description = models.TextField(blank=True, default="")
    sku = models.CharField(max_length=64)
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[
            MinValueValidator(ZERO),
        ],
    )
    quantity = models.PositiveIntegerField(
        validators=[
            MinValueValidator(1),
            MaxValueValidator(MAX_ORDER_ITEM_QUANTITY),
        ]
    )
    total_price = models.DecimalField(
        max_digits=13,
        decimal_places=2,
        validators=[
            MinValueValidator(ZERO),
        ],
    )

    class Meta:
        ordering = ("id",)
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    quantity__gte=1,
                    quantity__lte=MAX_ORDER_ITEM_QUANTITY,
                ),
                name="orders_item_quantity_range",
            ),
            models.CheckConstraint(
                condition=Q(
                    unit_price__gte=ZERO,
                    unit_price__lte=MAX_UNIT_PRICE,
                    total_price__gte=ZERO,
                    total_price__lte=MAX_LINE_TOTAL,
                ),
                name="orders_item_money_range",
            ),
            models.CheckConstraint(
                condition=Q(total_price=F("unit_price") * F("quantity")),
                name="orders_item_total_matches",
            ),
        ]

    def clean(self):
        super().clean()
        amounts = (self.unit_price, self.total_price)

        if (
            all(isinstance(value, Decimal) and value.is_finite() for value in amounts)
            and type(self.quantity) is int
            and self.total_price != self.unit_price * self.quantity
        ):
            raise ValidationError(
                {"total_price": "Line total must equal price * quantity."}
            )

    def __str__(self):
        return f"{self.sku} x {self.quantity}"
