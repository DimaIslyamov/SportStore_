from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from .managers import UserManager


class User(AbstractUser):
    class Role(models.TextChoices):
        CUSTOMER = "CUSTOMER", "Customer"
        MANAGER = "MANAGER", "Manager"

    username = None
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=32, blank=True, default="")
    role = models.CharField(
        max_length=16,
        choices=Role.choices,
        default=Role.CUSTOMER,
        db_index=True,
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"
        abstract = False
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                name="accounts_user_email_ci_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=["CUSTOMER", "MANAGER"]),
                name="accounts_user_role_valid",
            ),
        ]

    def save(self, *args, **kwargs):
        self.email = type(self).objects.normalize_email(self.email)
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.email


class Address(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="addresses",
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
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_default=True),
                name="accounts_address_one_default_per_user",
            ),
        ]
